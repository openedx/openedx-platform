"""
Tests for :mod:`openedx.core.djangoapps.authz.scopes`.

These exercise the general authz-grant -> scope resolution helpers by seeding
REAL role assignments into the enforcer (via ``assign_role_to_user_in_scope``)
rather than mocking the scope lookup -- so they cover the concrete
``view_course`` -> scope resolution and that a real per-course / org-glob /
platform-glob grant lands in the right scope class (the seeded ``course_editor``
role carries ``courses.view_course``).

The unit-level filtering behaviour of the search *adapters* over these helpers
(``omit_orgs`` handling, string serialization, fail-open on ``DatabaseError``)
lives with the search app, in
``openedx.core.djangoapps.content.search.tests.test_models``.
"""
from __future__ import annotations

from unittest import mock

import pytest
from edx_django_utils.cache import RequestCache

from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.waffle_utils.models import WaffleFlagCourseOverrideModel, WaffleFlagOrgOverrideModel
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase

try:
    # The openedx_authz engine is only wired up where content.search is installed
    # (i.e. cms), matching the skip_unless_cms guard below.
    from opaque_keys.edx.keys import CourseKey
    from openedx_authz.api.users import assign_role_to_user_in_scope
    from openedx_authz.constants.permissions import COURSES_VIEW_COURSE
    from openedx_authz.constants.roles import COURSE_EDITOR
    from openedx_authz.engine.enforcer import AuthzEnforcer

    from openedx.core.djangoapps.authz.scopes import (
        authz_grants_platform_access,
        get_authz_flag_enabled_org_keys,
        get_authz_org_scoped_course_keys,
        get_authz_platform_course_keys,
        get_authz_platform_orgs,
    )
    from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
    VIEW_COURSE_ACTION = COURSES_VIEW_COURSE.action.external_key
    _HAS_AUTHZ_TEST_SUPPORT = True
except (RuntimeError, ImportError):
    # Placeholder base when authz test support is unavailable (e.g. under lms).
    # Must be an empty *mixin* class, not ``object``: the real classes list it
    # BEFORE SharedModuleStoreTestCase, and a bare ``object`` in that position is
    # an inconsistent MRO. The class is never instantiated (the skipif below skips
    # the whole class), but it must still linearize cleanly so the module imports
    # and pylint's MRO check passes.
    class CourseAuthoringAuthzTestMixin:  # pylint: disable=too-few-public-methods
        """No-op placeholder used when openedx_authz test support is unavailable."""

    CourseKey = None
    assign_role_to_user_in_scope = None
    COURSE_EDITOR = None
    AuthzEnforcer = None
    VIEW_COURSE_ACTION = "courses.view_course"
    authz_grants_platform_access = lambda user, action: False
    get_authz_flag_enabled_org_keys = lambda user, action: set()
    get_authz_org_scoped_course_keys = lambda user, action: set()
    get_authz_platform_orgs = lambda user, action: set()
    get_authz_platform_course_keys = lambda user, action: set()
    _HAS_AUTHZ_TEST_SUPPORT = False


@skip_unless_cms
@pytest.mark.skipif(not _HAS_AUTHZ_TEST_SUPPORT, reason="openedx_authz test support unavailable")
class AuthzScopesRealPolicyTest(CourseAuthoringAuthzTestMixin, SharedModuleStoreTestCase):
    """
    End-to-end coverage that seeds REAL role assignments into the enforcer
    (via ``assign_role_to_user_in_scope``) and asserts the scopes helpers resolve
    them to the right orgs / platform-access verdict.

    ``CourseAuthoringAuthzTestMixin`` patches ``AUTHZ_COURSE_AUTHORING_FLAG``
    ``is_enabled`` on for the whole class, so happy-path grants resolve on without
    a per-method ``@override_waffle_flag``.
    """

    def setUp(self):
        super().setUp()
        # Synthetic requests never cross RequestCacheMiddleware, so clear the
        # per-request authz cache between tests explicitly.
        RequestCache.clear_all_namespaces()

    def _assign_scope(self, user, scope_external_key, role=None):
        """Seed a real authz grant for ``user`` at ``scope_external_key`` and reload policy."""
        assign_role_to_user_in_scope(
            user.username,
            (role or COURSE_EDITOR).external_key,
            scope_external_key,
        )
        AuthzEnforcer.get_enforcer().load_policy()

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

        orgs = get_authz_flag_enabled_org_keys(self.authorized_user, VIEW_COURSE_ACTION)

        assert orgs == {'org1'}

    def test_real_platform_glob_grant_grants_all_access(self):
        """
        A real platform-wide grant (``course-v1:*``) is recognized as unrestricted
        access -- the authz analogue of global staff (openedx-authz#417 follow-up:
        a ``course-v1:*`` user should see all orgs and all courses).
        """
        self._assign_scope(self.authorized_user, 'course-v1:*')

        assert authz_grants_platform_access(self.authorized_user, VIEW_COURSE_ACTION) is True

    def test_no_grant_has_no_platform_access(self):
        """A user with no platform-wide grant is not treated as unrestricted."""
        assert authz_grants_platform_access(self.unauthorized_user, VIEW_COURSE_ACTION) is False


@skip_unless_cms
@pytest.mark.skipif(not _HAS_AUTHZ_TEST_SUPPORT, reason="openedx_authz test support unavailable")
class AuthzScopesPlatformExpansionTest(CourseAuthoringAuthzTestMixin, SharedModuleStoreTestCase):
    """
    Covers a platform-wide (``course-v1:*``) authz grant when the authz course
    authoring flag is NOT globally on: the grant does not grant unrestricted
    access, it expands to only the orgs/courses that carry a force-on override
    (openedx/openedx-authz#417 follow-up).

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

    def test_global_on_platform_grant_is_unrestricted(self):
        """With the flag globally on (mixin default), the platform grant is unrestricted."""
        assert authz_grants_platform_access(self.authorized_user, VIEW_COURSE_ACTION) is True
        # Unrestricted access handles it, so the per-scope expansion contributes nothing.
        assert get_authz_platform_orgs(self.authorized_user, VIEW_COURSE_ACTION) == set()
        assert get_authz_platform_course_keys(self.authorized_user, VIEW_COURSE_ACTION) == set()

    def test_global_off_platform_grant_is_not_unrestricted(self):
        """With the flag globally off, the platform grant is NOT unrestricted."""
        with self._flag_off():
            assert authz_grants_platform_access(self.authorized_user, VIEW_COURSE_ACTION) is False

    def test_global_off_expands_to_force_on_org(self):
        """A force-on org override surfaces that org for the platform-grant holder."""
        WaffleFlagOrgOverrideModel.objects.create(
            waffle_flag=core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.name,
            org='org1',
            override_choice=WaffleFlagOrgOverrideModel.ALL_CHOICES.on,
            enabled=True,
        )
        with self._flag_off():
            orgs = get_authz_platform_orgs(self.authorized_user, VIEW_COURSE_ACTION)

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
            course_keys = get_authz_platform_course_keys(self.authorized_user, VIEW_COURSE_ACTION)

        # The scopes helper returns native CourseKey objects; the search adapter is
        # what serializes them.
        assert course_keys == {CourseKey.from_string(course_key)}

    def test_global_off_no_platform_grant_expands_to_nothing(self):
        """A user without the platform grant gets no expansion even with force-on overrides."""
        WaffleFlagOrgOverrideModel.objects.create(
            waffle_flag=core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.name,
            org='org1',
            override_choice=WaffleFlagOrgOverrideModel.ALL_CHOICES.on,
            enabled=True,
        )
        with self._flag_off():
            orgs = get_authz_platform_orgs(self.unauthorized_user, VIEW_COURSE_ACTION)
            course_keys = get_authz_platform_course_keys(self.unauthorized_user, VIEW_COURSE_ACTION)

        assert orgs == set()
        assert course_keys == set()


@skip_unless_cms
@pytest.mark.skipif(not _HAS_AUTHZ_TEST_SUPPORT, reason="openedx_authz test support unavailable")
class AuthzScopesOrgScopedCourseRescueTest(CourseAuthoringAuthzTestMixin, SharedModuleStoreTestCase):
    """
    Covers ``get_authz_org_scoped_course_keys``: a user with an org-wide grant
    should still see a course that is force-on via a per-course override even when
    that course's org flag is off -- matching the platform expansion and the Studio
    authoring-home listing (which resolve org grants at course granularity).
    """

    def setUp(self):
        super().setUp()
        RequestCache.clear_all_namespaces()
        # Grant the user an org-wide scope on org1 (not a platform or per-course grant).
        assign_role_to_user_in_scope(
            self.authorized_user.username,
            COURSE_EDITOR.external_key,
            'course-v1:org1+*',
        )
        AuthzEnforcer.get_enforcer().load_policy()

    def _flag_off(self):
        """Locally force the class-level is_enabled patch to report the global switch OFF."""
        return mock.patch.object(
            core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, "is_enabled", return_value=False
        )

    def test_force_on_course_in_granted_org_is_rescued(self):
        """A force-on course in the granted org (org flag off) surfaces for the org grant."""
        course_key = 'course-v1:org1+C1+R'
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag=core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.name,
            course_id=course_key,
            override_choice=WaffleFlagCourseOverrideModel.ALL_CHOICES.on,
            enabled=True,
        )
        with self._flag_off():
            course_keys = get_authz_org_scoped_course_keys(self.authorized_user, VIEW_COURSE_ACTION)

        assert course_keys == {CourseKey.from_string(course_key)}

    def test_force_on_course_in_other_org_is_not_rescued(self):
        """A force-on course outside the granted org is NOT surfaced by the org grant."""
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag=core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.name,
            course_id='course-v1:org2+C1+R',
            override_choice=WaffleFlagCourseOverrideModel.ALL_CHOICES.on,
            enabled=True,
        )
        with self._flag_off():
            course_keys = get_authz_org_scoped_course_keys(self.authorized_user, VIEW_COURSE_ACTION)

        assert course_keys == set()

    def test_course_skipped_when_its_org_is_flag_enabled(self):
        """
        A force-on course is NOT surfaced individually when its org is itself
        flag-enabled -- that org is already covered wholesale by the org clause, so
        surfacing the course would waste an access_id slot.
        """
        course_key = 'course-v1:org1+C1+R'
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag=core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.name,
            course_id=course_key,
            override_choice=WaffleFlagCourseOverrideModel.ALL_CHOICES.on,
            enabled=True,
        )
        WaffleFlagOrgOverrideModel.objects.create(
            waffle_flag=core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.name,
            org='org1',
            override_choice=WaffleFlagOrgOverrideModel.ALL_CHOICES.on,
            enabled=True,
        )
        with self._flag_off():
            course_keys = get_authz_org_scoped_course_keys(self.authorized_user, VIEW_COURSE_ACTION)

        assert course_keys == set()

    def test_no_org_grant_rescues_nothing(self):
        """A user with no org-wide grant gets no org-scoped course rescue."""
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag=core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.name,
            course_id='course-v1:org1+C1+R',
            override_choice=WaffleFlagCourseOverrideModel.ALL_CHOICES.on,
            enabled=True,
        )
        with self._flag_off():
            course_keys = get_authz_org_scoped_course_keys(self.unauthorized_user, VIEW_COURSE_ACTION)

        assert course_keys == set()
