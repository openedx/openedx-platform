"""Content search model tests"""
from __future__ import annotations

from unittest import mock

import ddt
import pytest
from django.db import OperationalError
from django.test import RequestFactory
from django.utils.crypto import get_random_string
from edx_django_utils.cache import RequestCache
from edx_toggles.toggles.testutils import override_waffle_flag
from organizations.models import Organization

from common.djangoapps.student.auth import update_org_role
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole, OrgInstructorRole, OrgStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.content_libraries import api as library_api
from openedx.core.djangoapps.waffle_utils.models import WaffleFlagCourseOverrideModel, WaffleFlagOrgOverrideModel
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

try:
    # This import errors in the lms because content.search is not an installed app there.
    from openedx_authz.api.data import CourseOverviewData, OrgCourseOverviewGlobData

    from openedx.core.djangoapps.content.search.models import (
        SearchAccess,
        _get_authz_course_keys,
        authz_has_platform_access,
        get_access_ids_for_request,
        get_authz_org_keys,
        get_authz_platform_course_keys,
        get_authz_platform_orgs,
    )
except RuntimeError:
    SearchAccess = {}
    CourseOverviewData = OrgCourseOverviewGlobData = None
    _get_authz_course_keys = lambda username, omit_orgs: set()
    get_access_ids_for_request = lambda request: []
    get_authz_org_keys = lambda username, omit_orgs: set()
    authz_has_platform_access = lambda username: False
    get_authz_platform_orgs = lambda user, omit_orgs: set()
    get_authz_platform_course_keys = lambda user, omit_orgs: set()

try:
    # Real-policy authz test helpers. Guarded for the same reason as above (the
    # openedx_authz engine is only wired up where content.search is installed).
    from openedx_authz.api.users import assign_role_to_user_in_scope
    from openedx_authz.constants.roles import COURSE_EDITOR
    from openedx_authz.engine.enforcer import AuthzEnforcer

    from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
    _HAS_AUTHZ_TEST_SUPPORT = True
except (RuntimeError, ImportError):
    # Placeholder base when authz test support is unavailable (e.g. under lms,
    # where content.search is not installed). Must be an empty *mixin* class, not
    # ``object``: the real class lists it BEFORE SharedModuleStoreTestCase, and a
    # bare ``object`` in that position is an inconsistent MRO (object must resolve
    # last). The class is never instantiated -- the pytest.mark.skipif below skips
    # the whole class on this path -- but it must still linearize cleanly so the
    # module imports and pylint's MRO check passes.
    class CourseAuthoringAuthzTestMixin:  # pylint: disable=too-few-public-methods
        """No-op placeholder used when openedx_authz test support is unavailable."""

    assign_role_to_user_in_scope = None
    COURSE_EDITOR = None
    AuthzEnforcer = None
    _HAS_AUTHZ_TEST_SUPPORT = False


def _fake_authz_assignment(course_key):
    """
    Build a stand-in for openedx_authz's RoleAssignmentData whose scope is a
    real ``CourseOverviewData`` (so it passes the ``isinstance`` scope-type
    filter in ``_get_authz_course_keys``) exposing the ``external_key`` the
    helper reads.

    We stub the assignment rather than provision real authz role rows because
    the model helper only cares about the scope's type and external key; the
    shape of the authz storage is exercised by openedx-authz's own test suite.
    """
    return mock.Mock(scope=CourseOverviewData(external_key=str(course_key)))


def _fake_authz_org_glob_assignment(org):
    """
    Build a stand-in for openedx_authz's RoleAssignmentData whose scope is a
    real org-wide (glob) ``OrgCourseOverviewGlobData`` (so it passes the
    ``isinstance`` scope-type filter in ``get_authz_org_keys``), exposing the
    ``org`` property the helper reads (e.g. ``course-v1:Org+*`` -> ``'Org'``).
    """
    return mock.Mock(scope=OrgCourseOverviewGlobData(external_key=f'course-v1:{org}+*'))


class StudioSearchTestMixin:
    """
    Sets up user, org, course, library, and access for studio search tests.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.global_staff = UserFactory(
            username='staff', email='staff@example.com', is_staff=True, password='staff_pass'
        )
        cls.student = UserFactory.create(
            username='student', email='student@example.com', is_staff=False, password='student_pass'
        )
        cls.course_staff = UserFactory.create(
            username='course_staff', email='course_staff@example.com', is_staff=False, password='course_staff_pass'
        )
        cls.course_instructor = UserFactory.create(
            username='course_instr', email='course_instr@example.com', is_staff=False, password='course_instr_pass'
        )
        cls.org_staff = UserFactory.create(
            username='org_staff', email='org_staff@example.com', is_staff=False, password='org_staff_pass'
        )
        cls.org_instructor = UserFactory.create(
            username='org_instr', email='org_instr@example.com', is_staff=False, password='org_instr_pass'
        )

    def setUp(self):
        """
        Add users, orgs, courses, and libraries.
        """
        super().setUp()

        # The authz assignment fetch is memoized per request via
        # @request_cached. These tests exercise the helpers with synthetic
        # RequestFactory requests that never cross RequestCacheMiddleware (which
        # normally clears the cache at request boundaries), so clear it here to
        # keep each test's mocked fetch from leaking into the next.
        RequestCache.clear_all_namespaces()

        self.course_user_keys = []
        self.staff_user_keys = []

        # Create a few courses that global_staff, course_staff and course_instructor can access
        for num in range(3):
            course_location = self.store.make_course_key('Org', 'CreatedCourse' + str(num), 'Run')
            self.last_course = self._create_course(course_location)
            CourseStaffRole(course_location).add_users(self.course_staff)
            CourseInstructorRole(course_location).add_users(self.course_instructor)
            self.course_user_keys.append(course_location)

        # Create a few courses that only global_staff can access
        for num in range(3):
            course_location = self.store.make_course_key('Org', 'StaffCourse' + str(num), 'Run')
            self._create_course(course_location)

        # Create orgs to test library access
        self.org1, _ = Organization.objects.get_or_create(
            short_name='org1',
            defaults={'name': "Org One"},
        )
        self.org2, _ = Organization.objects.get_or_create(
            short_name='org2',
            defaults={'name': "Org Two"},
        )
        update_org_role(caller=self.global_staff, role=OrgStaffRole, user=self.org_staff, orgs=['org1'])
        update_org_role(caller=self.global_staff, role=OrgInstructorRole, user=self.org_instructor, orgs=['org1'])

        # Create a few libraries that global_staff, course_staff and course_instructor can access
        for num in range(2):
            self.last_library = self._create_library(self.org1, num)
            library_api.set_library_user_permissions(
                self.last_library.key,
                self.course_staff,
                library_api.AccessLevel.READ_LEVEL,
            )
            library_api.set_library_user_permissions(
                self.last_library.key,
                self.course_instructor,
                library_api.AccessLevel.READ_LEVEL,
            )
            self.course_user_keys.append(self.last_library.key)
            self.staff_user_keys.append(self.last_library.key)

        # Create a few libraries in org2, which only global_staff can access.
        for num in range(2):
            library = self._create_library(self.org2, num)
            self.staff_user_keys.append(library.key)

    def _create_course(self, course_location):
        """
        Create dummy course and overview.
        """
        CourseFactory.create(
            org=course_location.org,
            number=course_location.course,
            run=course_location.run
        )
        course = CourseOverviewFactory.create(id=course_location, org=course_location.org)
        return course

    def _create_library(self, org, num):
        """
        Create dummy library.
        """
        slug = get_random_string(4)
        library = library_api.create_library(
            org=org,
            slug=slug,
            title=f"Dummy Library {num}",
        )
        return library


@ddt.ddt
@skip_unless_cms
class StudioSearchAccessTest(StudioSearchTestMixin, SharedModuleStoreTestCase):
    """
    Tests the SearchAccess model, handlers, and helper functions.
    """

    def _create_course(self, course_location):
        """
        Creates a SearchAccess object for each new course.

        Usually these are created when documents are indexed, but in these model tests, we need to create them manually.
        """
        course = super()._create_course(course_location)
        SearchAccess.objects.create(context_key=course.id)
        return course

    def _create_library(self, org, num):
        """
        Creates a SearchAccess object for each new library.

        Usually these are created when documents are indexed, but in these model tests, we need to create them manually.
        """
        library = super()._create_library(org, num)
        SearchAccess.objects.create(context_key=library.key)
        return library

    def _check_access_ids(self, access_ids, expected_keys):
        """
        Checks the returned list of access_ids to ensure:

        * no duplicates
        * sorted descending order (i.e. most recently-created first)
        * expected keys match access_ids
        """
        assert len(set(access_ids)) == len(access_ids)

        sorted_access_ids = access_ids
        sorted_access_ids.sort(reverse=True)
        assert access_ids == sorted_access_ids

        access_keys = SearchAccess.objects.filter(
            id__in=access_ids
        ).only('context_key').values_list('context_key', flat=True)
        assert set(access_keys) == set(expected_keys)

    def test_course_staff_get_access_ids_for_request(self):
        """Course staff can access the courses and libraries in org1."""
        request = RequestFactory().get('/course')
        request.user = self.course_staff

        access_ids = get_access_ids_for_request(request)
        self._check_access_ids(access_ids, self.course_user_keys)

    def test_course_instructor_get_access_ids_for_request(self):
        """Course instructor can access the courses and libraries in org1."""
        request = RequestFactory().get('/course')
        request.user = self.course_instructor

        access_ids = get_access_ids_for_request(request)
        self._check_access_ids(access_ids, self.course_user_keys)

    @ddt.data(
        'org_staff',
        'org_instructor',
    )
    def test_org_get_access_ids_for_request(self, user_attr):
        """
        Org staff & instructors can see all courses and libraries in their org.
        But if they don't have any individual access granted, then no access_ids will be returned.
        """
        request = RequestFactory().get('/course')
        request.user = getattr(self, user_attr)

        access_ids = get_access_ids_for_request(request)
        self._check_access_ids(access_ids, [])

    def test_staff_get_access_ids_for_request(self):
        """
        Global staff can see all courses and libraries, but they only have individual access granted for libraries.
        """
        request = RequestFactory().get('/course')
        request.user = self.global_staff

        access_ids = get_access_ids_for_request(request)
        self._check_access_ids(access_ids, self.staff_user_keys)

    def test_get_access_ids_for_request_omit_orgs(self):
        """
        Omit the org1 library keys from the returned list.
        """
        request = RequestFactory().get('/course')
        request.user = self.global_staff

        access_ids = get_access_ids_for_request(request, omit_orgs=['org1'])
        self._check_access_ids(access_ids, self.staff_user_keys[-2:])

    def test_delete_removes_access_ids_for_request(self):
        """Removing courses and library should remove their associated access_ids."""
        remaining_keys = self.staff_user_keys
        remaining_keys.remove(self.last_library.key)
        self.last_course.delete()
        library_api.delete_library(self.last_library.key)

        request = RequestFactory().get('/course')
        request.user = self.global_staff

        access_ids = get_access_ids_for_request(request)
        self._check_access_ids(access_ids, remaining_keys)

    def test_no_access_ids_for_request(self):
        """Users without special access cannot see any courses or libraries."""
        request = RequestFactory().get('/course')
        request.user = self.student
        access_ids = get_access_ids_for_request(request)
        assert not access_ids


@ddt.ddt
@skip_unless_cms
class StudioSearchAuthzAccessTest(StudioSearchTestMixin, SharedModuleStoreTestCase):
    """
    Tests that ``get_access_ids_for_request`` includes courses granted through
    openedx-authz role assignments, not just legacy CourseStaffRole /
    CourseInstructorRole (openedx/openedx-authz#417).
    """

    AUTHZ_PATH = 'openedx.core.djangoapps.content.search.models.authz_get_all_course_assignments_for_user'

    def _create_course(self, course_location):
        """Create a SearchAccess row per course so access_ids can resolve."""
        course = super()._create_course(course_location)
        SearchAccess.objects.create(context_key=course.id)
        return course

    def _create_library(self, org, num):
        """Create a SearchAccess row per library so access_ids can resolve."""
        library = super()._create_library(org, num)
        SearchAccess.objects.create(context_key=library.key)
        return library

    def _authz_only_user(self):
        """A user with no legacy course role — access can only come from authz."""
        return UserFactory.create(
            username='authz_editor',
            email='authz_editor@example.com',
            is_staff=False,
            password='authz_editor_pass',
        )

    def _course_access_ids(self, course_keys):
        """Resolve the SearchAccess ids for the given course keys."""
        return set(
            SearchAccess.objects.filter(context_key__in=course_keys).values_list('id', flat=True)
        )

    @override_waffle_flag(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, active=True)
    def test_authz_only_user_sees_authz_courses(self):
        """
        A user with an authz role assignment but no legacy role gets the
        matching course access_ids when the flag is enabled.
        """
        user = self._authz_only_user()
        granted = self.course_user_keys[:2]  # first two are CourseKeys (libraries come later)
        request = RequestFactory().get('/course')
        request.user = user

        with mock.patch(
            self.AUTHZ_PATH,
            return_value=[_fake_authz_assignment(key) for key in granted],
        ):
            access_ids = get_access_ids_for_request(request)

        assert set(access_ids) == self._course_access_ids(granted)

    @override_waffle_flag(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, active=True)
    def test_authz_courses_respect_omit_orgs(self):
        """Authz-granted courses in an omitted org are excluded."""
        user = self._authz_only_user()
        granted = self.course_user_keys[:2]
        request = RequestFactory().get('/course')
        request.user = user

        with mock.patch(
            self.AUTHZ_PATH,
            return_value=[_fake_authz_assignment(key) for key in granted],
        ):
            access_ids = get_access_ids_for_request(request, omit_orgs=['Org'])

        assert not access_ids

    @override_waffle_flag(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, active=False)
    def test_authz_courses_excluded_when_flag_off(self):
        """With the flag disabled, authz assignments contribute no access_ids."""
        user = self._authz_only_user()
        granted = self.course_user_keys[:2]
        request = RequestFactory().get('/course')
        request.user = user

        with mock.patch(
            self.AUTHZ_PATH,
            return_value=[_fake_authz_assignment(key) for key in granted],
        ):
            access_ids = get_access_ids_for_request(request)

        assert not access_ids

    @override_waffle_flag(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, active=True)
    def test_authz_db_failure_is_swallowed(self):
        """
        A database failure in the authz query must not break search; the
        request falls back to legacy roles only (here: none, so no access_ids).
        """
        user = self._authz_only_user()
        request = RequestFactory().get('/course')
        request.user = user

        with mock.patch(self.AUTHZ_PATH, side_effect=OperationalError('authz db down')):
            access_ids = get_access_ids_for_request(request)

        assert not access_ids

    @override_waffle_flag(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, active=True)
    def test_authz_unexpected_error_propagates(self):
        """
        Only known operational (DatabaseError) failures are swallowed. An
        unexpected error is NOT masked — it propagates so real bugs surface.
        """
        user = self._authz_only_user()
        request = RequestFactory().get('/course')
        request.user = user

        with mock.patch(self.AUTHZ_PATH, side_effect=RuntimeError('unexpected')):
            with pytest.raises(RuntimeError):
                get_access_ids_for_request(request)

    @override_waffle_flag(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, active=True)
    def test_authz_ignores_unparseable_scope(self):
        """A scope external_key that is not a course key is skipped, not fatal."""
        user = self._authz_only_user()
        granted = self.course_user_keys[:1]
        request = RequestFactory().get('/course')
        request.user = user
        assignments = [
            _fake_authz_assignment(granted[0]),
            _fake_authz_assignment('not-a-course-key'),
        ]

        with mock.patch(self.AUTHZ_PATH, return_value=assignments):
            access_ids = get_access_ids_for_request(request)

        assert set(access_ids) == self._course_access_ids(granted)

    @override_waffle_flag(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, active=True)
    def test_authz_union_with_legacy_roles_no_duplicates(self):
        """
        When a course is granted through BOTH a legacy role and authz, its
        access_id appears exactly once.
        """
        request = RequestFactory().get('/course')
        request.user = self.course_staff  # already has legacy CourseStaffRole on course_user_keys
        legacy_courses = self.course_user_keys[:2]

        with mock.patch(
            self.AUTHZ_PATH,
            return_value=[_fake_authz_assignment(key) for key in legacy_courses],
        ):
            access_ids = get_access_ids_for_request(request)

        assert len(access_ids) == len(set(access_ids))
        assert self._course_access_ids(legacy_courses).issubset(set(access_ids))

    @override_waffle_flag(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, active=True)
    def test_authz_course_keys_are_strings_matching_legacy(self):
        """
        _get_authz_course_keys returns serialized (str) keys, matching the
        legacy get_course_roles branch (whose course_id is a str). This keeps
        the unioned course_keys set type-homogeneous: a course held via both a
        legacy role and an authz role collapses to ONE set member rather than
        two (a str and a CourseKey object hash/compare unequal). The downstream
        SQL ``IN`` coerces both forms, so the query works either way -- this
        guards the set-union de-dup invariant itself, at the source.
        """
        user = self._authz_only_user()
        granted = self.course_user_keys[:1]

        with mock.patch(
            self.AUTHZ_PATH,
            return_value=[_fake_authz_assignment(key) for key in granted],
        ):
            keys = _get_authz_course_keys(user, omit_orgs=[])

        assert keys == {str(key) for key in granted}
        assert all(isinstance(key, str) for key in keys)


@ddt.ddt
@skip_unless_cms
class StudioSearchAuthzOrgAccessTest(StudioSearchTestMixin, SharedModuleStoreTestCase):
    """
    Tests that ``get_authz_org_keys`` surfaces org-wide (glob) authz course
    role assignments -- e.g. a ``course_editor`` granted at ``course-v1:Org+*``
    -- so that org-level authz-only users are covered by the ``org IN [...]``
    search filter clause (openedx/openedx-authz#417).

    An org glob returns an ``OrgCourseOverviewGlobData`` scope, which the
    per-course path (``get_access_ids_for_request`` -> ``CourseOverviewData``)
    deliberately skips; it must be resolved to an org short_name here instead.
    """

    AUTHZ_PATH = 'openedx.core.djangoapps.content.search.models.authz_get_all_course_assignments_for_user'

    def setUp(self):
        super().setUp()
        self.authz_org_user = UserFactory.create(
            username='authz_org_editor',
            email='authz_org_editor@example.com',
            is_staff=False,
            password='authz_org_editor_pass',
        )

    @override_waffle_flag(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, active=True)
    def test_org_glob_grant_returns_org(self):
        """An org-wide authz grant yields that org's short_name when the flag is on."""
        with mock.patch(
            self.AUTHZ_PATH,
            return_value=[_fake_authz_org_glob_assignment('org1')],
        ):
            orgs = get_authz_org_keys(self.authz_org_user, omit_orgs=[])

        assert orgs == {'org1'}

    @override_waffle_flag(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, active=True)
    def test_org_glob_respects_omit_orgs(self):
        """An org already covered by the legacy org clause is not duplicated."""
        with mock.patch(
            self.AUTHZ_PATH,
            return_value=[_fake_authz_org_glob_assignment('org1')],
        ):
            orgs = get_authz_org_keys(self.authz_org_user, omit_orgs=['org1'])

        assert orgs == set()

    @override_waffle_flag(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, active=False)
    def test_org_glob_excluded_when_flag_off(self):
        """With the flag globally off (and no org override), org globs contribute nothing."""
        with mock.patch(
            self.AUTHZ_PATH,
            return_value=[_fake_authz_org_glob_assignment('org1')],
        ):
            orgs = get_authz_org_keys(self.authz_org_user, omit_orgs=[])

        assert orgs == set()

    def test_org_glob_enabled_by_org_override_when_flag_off_globally(self):
        """
        A per-org waffle override forces the org on even when the global flag is
        off -- the same resolution CourseWaffleFlag applies at the org tier.
        """
        WaffleFlagOrgOverrideModel.objects.create(
            waffle_flag=core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.name,
            org='org1',
            override_choice=WaffleFlagOrgOverrideModel.ALL_CHOICES.on,
            enabled=True,
        )
        with override_waffle_flag(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, active=False):
            with mock.patch(
                self.AUTHZ_PATH,
                return_value=[_fake_authz_org_glob_assignment('org1')],
            ):
                orgs = get_authz_org_keys(self.authz_org_user, omit_orgs=[])

        assert orgs == {'org1'}

    @override_waffle_flag(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, active=True)
    def test_org_glob_db_failure_is_swallowed(self):
        """A DatabaseError in the authz org query fails open (empty set, no raise)."""
        with mock.patch(self.AUTHZ_PATH, side_effect=OperationalError('authz db down')):
            orgs = get_authz_org_keys(self.authz_org_user, omit_orgs=[])

        assert orgs == set()

    @override_waffle_flag(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, active=True)
    def test_org_glob_unexpected_error_propagates(self):
        """An unexpected error is not masked -- it propagates so real bugs surface."""
        with mock.patch(self.AUTHZ_PATH, side_effect=RuntimeError('unexpected')):
            with pytest.raises(RuntimeError):
                get_authz_org_keys(self.authz_org_user, omit_orgs=[])


@skip_unless_cms
@pytest.mark.skipif(not _HAS_AUTHZ_TEST_SUPPORT, reason="openedx_authz test support unavailable")
class StudioSearchAuthzRealPolicyTest(CourseAuthoringAuthzTestMixin, SharedModuleStoreTestCase):
    """
    End-to-end authz coverage that seeds REAL role assignments into the enforcer
    (via ``assign_role_to_user_in_scope``) instead of mocking
    ``get_user_role_assignments``.

    The mocked tests above prove the helper logic in isolation, but they stub the
    assignment set -- so they never exercise the real
    ``RoleAssignmentData.scope.external_key`` -> ``CourseKey`` contract, nor that
    a real per-course / org-glob / platform-glob grant lands in the right scope
    class. These tests close that gap by driving the actual openedx-authz storage,
    which is the concrete contract search depends on.

    Deliberately self-contained rather than reusing ``StudioSearchTestMixin``:
    ``CourseAuthoringAuthzTestMixin`` patches ``AUTHZ_COURSE_AUTHORING_FLAG`` on
    for the whole class, which routes the mixin's legacy org-role seeding through
    the authz compatibility layer -- an unrelated path this test does not need.
    So we build only the minimal course fixture the authz helpers read. The flag
    being on for the class is exactly what these happy-path grants want, so no
    per-method ``@override_waffle_flag`` is required.
    """

    def setUp(self):
        super().setUp()
        # Synthetic RequestFactory requests never cross RequestCacheMiddleware,
        # so clear the per-request authz cache between tests explicitly.
        RequestCache.clear_all_namespaces()

        self.course_keys = []
        for num in range(2):
            course_location = self.store.make_course_key('org1', f'AuthzCourse{num}', 'Run')
            CourseFactory.create(
                org=course_location.org,
                number=course_location.course,
                run=course_location.run,
            )
            course = CourseOverviewFactory.create(id=course_location, org=course_location.org)
            SearchAccess.objects.create(context_key=course.id)
            self.course_keys.append(course_location)

    def _course_access_ids(self, course_keys):
        """Resolve the SearchAccess ids for the given course keys."""
        return set(
            SearchAccess.objects.filter(context_key__in=course_keys).values_list('id', flat=True)
        )

    def _assign_scope(self, user, scope_external_key, role=None):
        """Seed a real authz grant for ``user`` at ``scope_external_key`` and reload policy."""
        assign_role_to_user_in_scope(
            user.username,
            (role or COURSE_EDITOR).external_key,
            scope_external_key,
        )
        AuthzEnforcer.get_enforcer().load_policy()

    def test_real_per_course_grant_surfaces_course(self):
        """
        A real per-course authz grant (``course-v1:Org+C+R``) resolves through
        the actual ``scope.external_key`` -> ``CourseKey`` path and yields that
        course's access_id -- no legacy role involved.
        """
        for course_key in self.course_keys:
            self._assign_scope(self.authorized_user, str(course_key))

        request = RequestFactory().get('/course')
        request.user = self.authorized_user
        access_ids = get_access_ids_for_request(request)

        assert set(access_ids) == self._course_access_ids(self.course_keys)

    def test_real_org_glob_grant_surfaces_org(self):
        """A real org-wide grant (``course-v1:Org+*``) resolves to that org short_name."""
        # The org-glob path resolves the flag via ``is_enabled_for_org`` (org
        # override, else global switch). The mixin patches ``is_enabled`` on but
        # not ``is_enabled_for_org``, so seed a real per-org override to turn the
        # org on -- exercising the actual org-tier resolution end to end.
        WaffleFlagOrgOverrideModel.objects.create(
            waffle_flag=core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.name,
            org='org1',
            override_choice=WaffleFlagOrgOverrideModel.ALL_CHOICES.on,
            enabled=True,
        )
        self._assign_scope(self.authorized_user, 'course-v1:org1+*')

        orgs = get_authz_org_keys(self.authorized_user, omit_orgs=[])

        assert orgs == {'org1'}

    def test_real_platform_glob_grant_grants_all_access(self):
        """
        A real platform-wide grant (``course-v1:*``) is recognized as "see
        everything" -- the authz analogue of global staff (openedx-authz#417
        follow-up: a ``course-v1:*`` user should see all orgs and all courses).
        """
        self._assign_scope(self.authorized_user, 'course-v1:*')

        assert authz_has_platform_access(self.authorized_user) is True

    def test_no_grant_has_no_platform_access(self):
        """A user with no platform-wide grant is not treated as see-everything."""
        assert authz_has_platform_access(self.unauthorized_user) is False


@skip_unless_cms
@pytest.mark.skipif(not _HAS_AUTHZ_TEST_SUPPORT, reason="openedx_authz test support unavailable")
class StudioSearchAuthzPlatformExpansionTest(CourseAuthoringAuthzTestMixin, SharedModuleStoreTestCase):
    """
    Covers the option-B behavior of a platform-wide (``course-v1:*``) authz grant
    when the authz course authoring flag is NOT globally on: the grant does not
    "see everything", it expands to only the orgs/courses that carry a force-on
    override (openedx/openedx-authz#417 follow-up).

    ``CourseAuthoringAuthzTestMixin`` seeds the enforcer policy and patches
    ``is_enabled`` on for the class; these tests locally re-patch ``is_enabled`` to
    False to simulate the global switch being off, while real
    ``WaffleFlagOrgOverrideModel`` / ``WaffleFlagCourseOverrideModel`` rows drive
    the per-scope resolution the expansion helpers read (those models are not
    patched by the mixin).
    """

    def setUp(self):
        super().setUp()
        RequestCache.clear_all_namespaces()
        self._assign_scope(self.authorized_user, 'course-v1:*')

    def _assign_scope(self, user, scope_external_key, role=None):
        """Seed a real authz grant for ``user`` and reload policy."""
        assign_role_to_user_in_scope(
            user.username,
            (role or COURSE_EDITOR).external_key,
            scope_external_key,
        )
        AuthzEnforcer.get_enforcer().load_policy()

    def _flag_off(self):
        """Locally force the class-level is_enabled patch to report the global switch OFF."""
        return mock.patch.object(
            core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, "is_enabled", return_value=False
        )

    def test_global_on_platform_grant_is_see_everything(self):
        """With the flag globally on (mixin default), the platform grant is see-everything."""
        assert authz_has_platform_access(self.authorized_user) is True
        # See-everything handles it, so the per-scope expansion contributes nothing.
        assert get_authz_platform_orgs(self.authorized_user, omit_orgs=[]) == set()
        assert get_authz_platform_course_keys(self.authorized_user, omit_orgs=[]) == set()

    def test_global_off_platform_grant_is_not_see_everything(self):
        """With the flag globally off, the platform grant is NOT see-everything."""
        with self._flag_off():
            assert authz_has_platform_access(self.authorized_user) is False

    def test_global_off_expands_to_force_on_org(self):
        """A force-on org override surfaces that org for the platform-grant holder."""
        WaffleFlagOrgOverrideModel.objects.create(
            waffle_flag=core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.name,
            org='org1',
            override_choice=WaffleFlagOrgOverrideModel.ALL_CHOICES.on,
            enabled=True,
        )
        with self._flag_off():
            orgs = get_authz_platform_orgs(self.authorized_user, omit_orgs=[])

        assert orgs == {'org1'}

    def test_global_off_expands_to_force_on_course(self):
        """A force-on course override surfaces that course for the platform-grant holder."""
        course_key = 'course-v1:orgX+C1+R'
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag=core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.name,
            course_id=course_key,
            override_choice=WaffleFlagCourseOverrideModel.ALL_CHOICES.on,
            enabled=True,
        )
        with self._flag_off():
            course_keys = get_authz_platform_course_keys(self.authorized_user, omit_orgs=[])

        assert course_keys == {course_key}

    def test_global_off_course_in_omitted_org_is_skipped(self):
        """A force-on course whose org is already covered (omit_orgs) is not duplicated."""
        course_key = 'course-v1:orgX+C1+R'
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag=core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.name,
            course_id=course_key,
            override_choice=WaffleFlagCourseOverrideModel.ALL_CHOICES.on,
            enabled=True,
        )
        with self._flag_off():
            course_keys = get_authz_platform_course_keys(self.authorized_user, omit_orgs=['orgX'])

        assert course_keys == set()

    def test_global_off_no_platform_grant_expands_to_nothing(self):
        """A user without the platform grant gets no expansion even with force-on overrides."""
        WaffleFlagOrgOverrideModel.objects.create(
            waffle_flag=core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.name,
            org='org1',
            override_choice=WaffleFlagOrgOverrideModel.ALL_CHOICES.on,
            enabled=True,
        )
        with self._flag_off():
            orgs = get_authz_platform_orgs(self.unauthorized_user, omit_orgs=[])
            course_keys = get_authz_platform_course_keys(self.unauthorized_user, omit_orgs=[])

        assert orgs == set()
        assert course_keys == set()
