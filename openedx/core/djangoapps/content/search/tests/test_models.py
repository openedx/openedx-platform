"""Content search model tests"""
from __future__ import annotations

from unittest import mock

import ddt
import pytest
from django.db import OperationalError
from django.test import RequestFactory
from django.utils.crypto import get_random_string
from edx_toggles.toggles.testutils import override_waffle_flag
from organizations.models import Organization

from common.djangoapps.student.auth import update_org_role
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole, OrgInstructorRole, OrgStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.content_libraries import api as library_api
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

try:
    # This import errors in the lms because content.search is not an installed app there.
    from openedx.core.djangoapps.content.search.models import SearchAccess, get_access_ids_for_request
except RuntimeError:
    SearchAccess = {}
    get_access_ids_for_request = lambda request: []


def _fake_authz_assignment(course_key):
    """
    Build a stand-in for openedx_authz's RoleAssignmentData that exposes only
    the ``scope.external_key`` attribute that ``_get_authz_course_keys`` reads.

    We stub the assignment rather than provision real authz role rows because
    the model helper only cares about the scope's external key; the shape of
    the authz storage is exercised by openedx-authz's own test suite.
    """
    return mock.Mock(scope=mock.Mock(external_key=str(course_key)))


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

    AUTHZ_PATH = 'openedx.core.djangoapps.content.search.models.get_user_role_assignments_per_scope_type'

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
