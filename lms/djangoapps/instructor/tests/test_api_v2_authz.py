"""
Tests for the AuthZ enforcement of the Instructor Dashboard API v2.

They cover the tabs, ``authz_enabled`` and the migrated endpoints.
"""

from unittest.mock import patch

import ddt
from ccx_keys.locator import CCXLocator
from django.test import override_settings
from django.urls import reverse
from openedx_authz.api import users as authz_users_api
from openedx_authz.api.data import ActionData, CourseOverviewData, RoleData
from openedx_authz.authz import (
    COURSE_ADMIN,
    COURSE_AUDITOR,
    COURSE_BETA_TESTER,
    COURSE_DATA_RESEARCHER,
    COURSE_EDITOR,
    COURSE_LIMITED_STAFF,
    COURSE_STAFF,
    COURSES_VIEW_COURSE_INFO,
    COURSES_VIEW_GRADEBOOK,
)
from openedx_authz.engine.enforcer import AuthzEnforcer
from rest_framework import status
from rest_framework.test import APIClient

from common.djangoapps.student.roles import (
    CourseDataResearcherRole,
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
)
from common.djangoapps.student.tests.factories import (
    InstructorFactory,
    StaffFactory,
    UserFactory,
)
from lms.djangoapps.ccx.tests.utils import CcxTestCase
from lms.djangoapps.courseware.tabs import get_course_tab_list
from lms.djangoapps.instructor.authz import constants
from lms.djangoapps.instructor.authz.tests.mixins import InstructorAuthzTestMixin
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

API = "lms.djangoapps.instructor.authz.api"

# Tabs migrated to AuthZ, each with one of the permissions that show it.
MIGRATED_TABS = {tab: permissions[0] for tab, permissions in constants.TAB_PERMISSIONS.items()}

# Every (tab, permission) pair: a single permission of the tab is enough to show it.
TAB_PERMISSION_PAIRS = [
    (tab, permission) for tab, permissions in constants.TAB_PERMISSIONS.items() for permission in permissions
]


@ddt.ddt
class CourseMetadataViewAuthzTest(InstructorAuthzTestMixin, SharedModuleStoreTestCase):
    """
    ``CourseMetadataView`` tabs and permissions follow the AuthZ flag.

    With the flag on they are resolved through AuthZ, and through the legacy
    roles when it is off.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        # The Date Extensions tab is only shown when edx-when is enabled for the course.
        patcher = patch("lms.djangoapps.instructor.views.serializers_v2.is_enabled_for_course", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.url = reverse("instructor_api_v2:course_metadata", kwargs={"course_id": str(self.course_key)})

    def _get(self, user):
        self.client.force_authenticate(user=user)
        return self.client.get(self.url)

    def _tab_ids(self, response):
        return {tab["tab_id"] for tab in response.data["tabs"]}

    def test_existing_permissions_field_is_unchanged(self):
        """The legacy ``permissions`` field keeps the same keys."""
        user = self.create_user_with_authz_role(COURSE_ADMIN.external_key, self.course_key)

        response = self._get(user)

        assert response.status_code == status.HTTP_200_OK
        assert set(response.data["permissions"]) == {
            "admin",
            "instructor",
            "finance_admin",
            "sales_admin",
            "staff",
            "forum_admin",
            "data_researcher",
        }

    @ddt.data(COURSE_ADMIN, COURSE_STAFF)
    def test_authz_role_sees_migrated_tabs(self, role):
        """A Course Admin and a Course Staff see exactly the migrated tabs, and AuthZ is reported as enabled."""
        user = self.create_user_with_authz_role(role.external_key, self.course_key)

        response = self._get(user)

        assert response.status_code == status.HTTP_200_OK
        assert self._tab_ids(response) == set(MIGRATED_TABS)
        assert response.data["authz_enabled"] is True

    def test_authz_denied_overrides_legacy_roles(self):
        """
        With the flag on, AuthZ denying a legacy instructor means no access.
        """
        instructor = InstructorFactory(course_key=self.course_key)

        with patch(f"{API}.authz_api.is_user_allowed", return_value=False):
            response = self._get(instructor)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error_code"] == "permission_denied"
        assert response.data["developer_message"]

    def test_authz_limited_staff_has_the_legacy_access(self):
        """
        With the flag on, the legacy limited staff gets the same tabs as with the flag off.

        AuthZ defines no dashboard permission for ``course_limited_staff``, which had the
        access of the course staff before AuthZ.
        """
        self.set_authz_enabled(False)
        legacy_user = UserFactory()
        CourseLimitedStaffRole(self.course_key).add_users(legacy_user)
        legacy_response = self._get(legacy_user)

        self.set_authz_enabled(True)
        user = self.create_user_with_authz_role(COURSE_LIMITED_STAFF.external_key, self.course_key)
        response = self._get(user)

        assert legacy_response.status_code == status.HTTP_200_OK
        assert response.status_code == status.HTTP_200_OK
        assert self._tab_ids(response)
        assert self._tab_ids(response) == self._tab_ids(legacy_response)

    def test_authz_beta_tester_keeps_the_legacy_denial(self):
        """With the flag on, the legacy beta tester is denied, as it was before AuthZ."""
        user = self.create_user_with_authz_role(COURSE_BETA_TESTER.external_key, self.course_key)

        assert self._get(user).status_code == status.HTTP_403_FORBIDDEN

    def test_authz_data_researcher_sees_only_data_downloads(self):
        """
        With the flag on, a data researcher keeps the access it had before AuthZ.

        It opens the dashboard, but only sees the Data Downloads tab.
        """
        user = self.create_user_with_authz_role(COURSE_DATA_RESEARCHER.external_key, self.course_key)

        response = self._get(user)

        assert response.status_code == status.HTTP_200_OK
        assert self._tab_ids(response) == {"data_downloads"}

    def test_legacy_data_researcher_sees_only_data_downloads(self):
        """With the flag off, a data researcher sees the same tab through the legacy role."""
        self.set_authz_enabled(False)
        user = UserFactory()
        CourseDataResearcherRole(self.course_key).add_users(user)

        response = self._get(user)

        assert response.status_code == status.HTTP_200_OK
        assert self._tab_ids(response) == {"data_downloads"}

    @ddt.data(COURSE_AUDITOR.external_key, COURSE_EDITOR.external_key, COURSE_STAFF.external_key)
    def test_authz_other_roles_do_not_get_data_downloads(self, role):
        """With the flag on, the data researcher permission does not leak into other roles."""
        user = self.create_user_with_authz_role(role, self.course_key)

        response = self._get(user)

        assert "data_downloads" not in self._tab_ids(response)

    @ddt.data(*constants.INSTRUCTOR_DASHBOARD_PERMISSIONS)
    def test_authz_any_dashboard_permission_grants_access(self, permission):
        """With the flag on, a single dashboard permission, not only ``view_course_info``, grants access."""
        user = UserFactory()

        with patch(f"{API}.authz_api.is_user_allowed", side_effect=lambda _u, perm, _s: perm == permission):
            response = self._get(user)

        assert response.status_code == status.HTTP_200_OK

    @ddt.data(*TAB_PERMISSION_PAIRS)
    @ddt.unpack
    def test_authz_tab_shown_with_any_of_its_permissions(self, tab_id, tab_permission):
        """
        A tab is shown with any one of its permissions, and hidden without all of them.
        """
        user = UserFactory()

        def allowed_only(*permissions):
            return lambda _username, permission, _scope: permission in permissions

        with patch(
            f"{API}.authz_api.is_user_allowed",
            side_effect=allowed_only(COURSES_VIEW_COURSE_INFO.identifier),
        ):
            tabs_without_permission = self._tab_ids(self._get(user))
        with patch(
            f"{API}.authz_api.is_user_allowed",
            side_effect=allowed_only(COURSES_VIEW_COURSE_INFO.identifier, tab_permission),
        ):
            tabs_with_permission = self._tab_ids(self._get(user))

        assert tab_id in tabs_with_permission
        if tab_id != "course_info":
            assert tab_id not in tabs_without_permission

    @ddt.data(
        (InstructorFactory, set()),
        # Staff without the forum Administrator role does not see Course Team, and without
        # instructor access does not see Date Extensions.
        (StaffFactory, {"course_team", "date_extensions"}),
    )
    @ddt.unpack
    def test_legacy_role_sees_its_migrated_tabs(self, user_factory, hidden_tabs):
        """With the flag off, the legacy role decides the migrated tabs and AuthZ is reported as disabled."""
        self.set_authz_enabled(False)
        user = user_factory(course_key=self.course_key)

        response = self._get(user)

        assert response.status_code == status.HTTP_200_OK
        assert self._tab_ids(response) & set(MIGRATED_TABS) == set(MIGRATED_TABS) - hidden_tabs
        assert response.data["authz_enabled"] is False

    def test_legacy_does_not_query_authz(self):
        """With the flag off, AuthZ is not queried."""
        self.set_authz_enabled(False)
        instructor = InstructorFactory(course_key=self.course_key)

        with patch(f"{API}.authz_api.is_user_allowed") as is_user_allowed:
            response = self._get(instructor)

        assert response.status_code == status.HTTP_200_OK
        is_user_allowed.assert_not_called()


class GradebookAccessTestMixin:
    """
    Compare ``has_gradebook_access`` with the answer of the Gradebook API.
    """

    def assert_gradebook_access(self, user, course_key, expected) -> None:
        """
        Assert the course info value, and that the Gradebook API agrees with it.

        Args:
            user (User): The user making both requests.
            course_key (CourseKey): The course to check.
            expected (bool): Whether the user should have Gradebook access.
        """
        self.client.force_authenticate(user=user)

        course_info = self.client.get(
            reverse("instructor_api_v2:course_metadata", kwargs={"course_id": str(course_key)})
        )
        gradebook = self.client.get(
            reverse("grades_api:v1:course_gradebook_grading_info", kwargs={"course_id": str(course_key)})
        )

        assert course_info.status_code == status.HTTP_200_OK
        assert course_info.data["has_gradebook_access"] is expected
        assert gradebook.status_code == (status.HTTP_200_OK if expected else status.HTTP_403_FORBIDDEN)


@ddt.ddt
class CourseMetadataViewGradebookAccessTest(
    GradebookAccessTestMixin, InstructorAuthzTestMixin, SharedModuleStoreTestCase
):
    """
    ``has_gradebook_access`` follows the access check of the Gradebook API.
    """

    @classmethod
    def setUpClass(cls) -> None:
        """Create the course shared by the tests."""
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.course_key = cls.course.id

    def setUp(self) -> None:
        """Create the API client."""
        super().setUp()
        self.client = APIClient()

    @ddt.data(
        (COURSE_ADMIN, True),
        (COURSE_STAFF, True),
        # Has course author access in the LMS through its legacy equivalent.
        (COURSE_LIMITED_STAFF, True),
        (COURSE_EDITOR, False),
        (COURSE_AUDITOR, False),
    )
    @ddt.unpack
    def test_authz_role(self, role, expected) -> None:
        """
        With the flag on, only the roles the Gradebook API accepts get ``true``.

        Args:
            role (RoleData): The AuthZ role assigned to the user.
            expected (bool): Whether the role should have Gradebook access.
        """
        user = self.create_user_with_authz_role(role.external_key, self.course_key)

        self.assert_gradebook_access(user, self.course_key, expected)

    def test_authz_custom_role_with_view_gradebook(self) -> None:
        """The Gradebook API does not check ``courses.view_gradebook``, so a role granting it gets ``false``."""
        AuthzEnforcer.get_enforcer().add_policy(
            RoleData(external_key="gradebook_viewer").namespaced_key,
            ActionData(external_key=COURSES_VIEW_GRADEBOOK.identifier).namespaced_key,
            f"{CourseOverviewData.NAMESPACE}^*",
            "allow",
        )
        user = self.create_user_with_authz_role("gradebook_viewer", self.course_key)
        assert authz_users_api.is_user_allowed(user.username, COURSES_VIEW_GRADEBOOK.identifier, str(self.course_key))

        self.assert_gradebook_access(user, self.course_key, False)

    @ddt.data(COURSE_EDITOR, COURSE_AUDITOR)
    def test_authz_one_role_with_access_is_enough(self, role_without_access) -> None:
        """
        A role without Gradebook access does not take away the access given by another role.

        Args:
            role_without_access (RoleData): The AuthZ role combined with Course Staff.
        """
        user = self.create_user_with_authz_role(role_without_access.external_key, self.course_key)
        self.assign_authz_role(user, COURSE_STAFF.external_key, self.course_key)

        self.assert_gradebook_access(user, self.course_key, True)

    @ddt.data(True, False)
    def test_global_staff(self, authz_enabled) -> None:
        """
        Global staff can access the Gradebook without a course role.

        Args:
            authz_enabled (bool): The value of the AuthZ flag.
        """
        self.set_authz_enabled(authz_enabled)

        self.assert_gradebook_access(UserFactory(is_staff=True), self.course_key, True)

    @ddt.data(StaffFactory, InstructorFactory)
    def test_legacy_course_team(self, user_factory) -> None:
        """
        With the flag off, the legacy course team can access the Gradebook.

        Args:
            user_factory (type): The factory that creates the course team member.
        """
        self.set_authz_enabled(False)

        self.assert_gradebook_access(user_factory(course_key=self.course_key), self.course_key, True)

    def test_legacy_data_researcher(self) -> None:
        """With the flag off, a dashboard user without course author access gets ``false``."""
        self.set_authz_enabled(False)
        user = UserFactory()
        CourseDataResearcherRole(self.course_key).add_users(user)

        self.assert_gradebook_access(user, self.course_key, False)


@ddt.ddt
@override_settings(CUSTOM_COURSES_EDX=True)
class CourseMetadataViewCcxGradebookAccessTest(GradebookAccessTestMixin, InstructorAuthzTestMixin, CcxTestCase):
    """
    ``has_gradebook_access`` follows the CCX rule of the Gradebook API.
    """

    def setUp(self) -> None:
        """Create a CCX course, with the AuthZ flag off, and the API client."""
        super().setUp()
        self.set_authz_enabled(False)
        self.make_coach()
        ccx = self.make_ccx()
        self.ccx_key = CCXLocator.from_course_locator(self.course.id, str(ccx.id))
        self.client = APIClient()

    @ddt.data(CourseStaffRole, CourseInstructorRole)
    def test_ccx_course_team(self, role_class) -> None:
        """
        The CCX course team can access its Gradebook, although CCX courses have no course authors.

        Args:
            role_class (type): The legacy role class assigned on the CCX course.
        """
        user = UserFactory()
        role_class(self.ccx_key).add_users(user)

        self.assert_gradebook_access(user, self.ccx_key, True)

    def test_custom_courses_disabled(self) -> None:
        """With CCX disabled on the platform, the Gradebook API rejects even global staff."""
        user = UserFactory(is_staff=True)

        with override_settings(CUSTOM_COURSES_EDX=False):
            self.assert_gradebook_access(user, self.ccx_key, False)


@ddt.ddt
class InstructorDashboardTabAuthzTest(InstructorAuthzTestMixin, SharedModuleStoreTestCase):
    """
    The "Instructor" course tab of the LMS follows the AuthZ flag.

    With the flag on it is shown when the user holds at least one of the Instructor
    Dashboard permissions, and through the legacy roles when it is off.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def _has_instructor_tab(self, user):
        return any(tab.type == "instructor" for tab in get_course_tab_list(user, self.course))

    def test_authz_role_with_permissions_shown(self):
        """With the flag on, a role that holds dashboard permissions sees the tab."""
        user = self.create_user_with_authz_role(COURSE_STAFF.external_key, self.course.id)

        assert self._has_instructor_tab(user)

    def test_authz_limited_staff_shown(self):
        """With the flag on, the legacy limited staff sees the tab, as it did before AuthZ."""
        user = self.create_user_with_authz_role(COURSE_LIMITED_STAFF.external_key, self.course.id)

        assert self._has_instructor_tab(user)

    def test_authz_data_researcher_shown(self):
        """With the flag on, a data researcher sees the tab, as it did before AuthZ."""
        user = self.create_user_with_authz_role(COURSE_DATA_RESEARCHER.external_key, self.course.id)

        assert self._has_instructor_tab(user)

    @ddt.data(*constants.INSTRUCTOR_DASHBOARD_PERMISSIONS)
    def test_authz_instructor_course_tab_shown_with_any_dashboard_permission(self, permission):
        """With the flag on, a single dashboard permission is enough to show the "Instructor" course tab."""
        user = UserFactory()

        with patch(f"{API}.authz_api.is_user_allowed", side_effect=lambda _u, perm, _c: perm == permission):
            assert self._has_instructor_tab(user)

    def test_authz_without_permissions_hidden(self):
        """With the flag on, a user holding none of the permissions does not see the tab."""
        user = UserFactory()

        assert not self._has_instructor_tab(user)

    def test_authz_denied_overrides_legacy_staff(self):
        """With the flag on, legacy staff without AuthZ permissions does not see the tab."""
        staff = StaffFactory(course_key=self.course.id)

        with patch(f"{API}.authz_api.is_user_allowed", return_value=False):
            assert not self._has_instructor_tab(staff)

    def test_legacy_staff_shown(self):
        """With the flag off, a legacy staff user sees the tab without querying AuthZ."""
        self.set_authz_enabled(False)
        staff = StaffFactory(course_key=self.course.id)

        with patch(f"{API}.authz_api.is_user_allowed") as is_user_allowed:
            assert self._has_instructor_tab(staff)

        is_user_allowed.assert_not_called()

    def test_legacy_without_role_hidden(self):
        """With the flag off, a user without any role does not see the tab."""
        self.set_authz_enabled(False)

        assert not self._has_instructor_tab(UserFactory())
