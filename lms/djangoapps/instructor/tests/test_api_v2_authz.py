"""
Tests for the AuthZ enforcement of the Instructor Dashboard API v2.

They cover the tabs, ``authz_enabled`` and the migrated endpoints.
"""

from datetime import datetime
from unittest.mock import patch

import ddt
from django.urls import reverse
from openedx_authz.authz import (
    COURSE_ADMIN,
    COURSE_AUDITOR,
    COURSE_BETA_TESTER,
    COURSE_DATA_RESEARCHER,
    COURSE_EDITOR,
    COURSE_LIMITED_STAFF,
    COURSE_STAFF,
    COURSES_VIEW_COURSE_INFO,
)
from pytz import UTC
from rest_framework import status
from rest_framework.test import APIClient

from common.djangoapps.student.roles import (
    CourseBetaTesterRole,
    CourseDataResearcherRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
)
from common.djangoapps.student.tests.factories import (
    CourseEnrollmentFactory,
    InstructorFactory,
    StaffFactory,
    UserFactory,
)
from lms.djangoapps.courseware.tabs import get_course_tab_list
from lms.djangoapps.instructor.authz import constants
from lms.djangoapps.instructor.authz.tests.mixins import InstructorAuthzTestMixin
from openedx.core.djangoapps.course_date_signals.handlers import extract_dates
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

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


@ddt.ddt
class DateExtensionsAuthzTest(InstructorAuthzTestMixin, SharedModuleStoreTestCase):
    """
    AuthZ enforcement of the Date Extensions tab endpoints: ``GradedSubsectionsView``
    and ``UnitExtensionsView`` (``courses.view_learner_extensions``), and
    ``ChangeDueDateView`` (``courses.manage_learner_extensions``).
    """

    # (url_name, "get" or "post")
    VIEW_ENDPOINTS = (("graded_subsections", "get"), ("unit_extensions", "get"))
    MANAGE_ENDPOINTS = (("change_due_date", "post"),)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.course_key = cls.course.id
        cls.chapter = BlockFactory.create(parent=cls.course, category="chapter")
        cls.sequential = BlockFactory.create(
            parent=cls.chapter,
            category="sequential",
            due=datetime(2030, 1, 1, tzinfo=UTC),
            metadata={"graded": True, "format": "Homework"},
        )
        # ChangeDueDateView extends a date through edx-when, which needs the course's
        # dates extracted into its own store before it recognizes this due date exists.
        extract_dates(None, cls.course_key)

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.learner = UserFactory()
        CourseEnrollmentFactory.create(user=self.learner, course_id=self.course_key)

    def _request(self, user, url_name, method):
        """Call the given endpoint as ``user``, GET or POST depending on ``method``."""
        self.client.force_authenticate(user=user)
        url = reverse(f"instructor_api_v2:{url_name}", kwargs={"course_id": str(self.course_key)})
        if method == "get":
            return self.client.get(url)
        return self.client.post(
            url,
            {
                "block_id": str(self.sequential.location),
                "due_datetime": "01/01/2030 00:00",
                "email_or_username": self.learner.username,
            },
            format="json",
        )

    @ddt.data(*VIEW_ENDPOINTS, *MANAGE_ENDPOINTS)
    @ddt.unpack
    def test_authz_course_admin_has_access(self, url_name, method):
        """A Course Admin can view and manage date extensions."""
        user = self.create_user_with_authz_role(COURSE_ADMIN.external_key, self.course_key)

        response = self._request(user, url_name, method)

        assert response.status_code == status.HTTP_200_OK

    @ddt.data(*VIEW_ENDPOINTS, *MANAGE_ENDPOINTS)
    @ddt.unpack
    def test_authz_course_staff_has_access(self, url_name, method):
        """A Course Staff can view and manage date extensions."""
        user = self.create_user_with_authz_role(COURSE_STAFF.external_key, self.course_key)

        response = self._request(user, url_name, method)

        assert response.status_code == status.HTTP_200_OK

    @ddt.data(*VIEW_ENDPOINTS, *MANAGE_ENDPOINTS)
    @ddt.unpack
    def test_authz_user_without_permission_is_denied(self, url_name, method):
        """A user with no AuthZ role in the course is denied."""
        user = UserFactory()

        response = self._request(user, url_name, method)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @ddt.data(*VIEW_ENDPOINTS, *MANAGE_ENDPOINTS)
    @ddt.unpack
    def test_authz_denied_overrides_legacy_roles(self, url_name, method):
        """With the flag on, AuthZ denying a legacy instructor means no access."""
        instructor = InstructorFactory(course_key=self.course_key)

        with patch(f"{API}.authz_api.is_user_allowed", return_value=False):
            response = self._request(instructor, url_name, method)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @ddt.data(*VIEW_ENDPOINTS, *MANAGE_ENDPOINTS)
    @ddt.unpack
    def test_legacy_instructor_keeps_access(self, url_name, method):
        """With the flag off, a legacy instructor keeps the access it always had."""
        self.set_authz_enabled(False)
        instructor = InstructorFactory(course_key=self.course_key)

        response = self._request(instructor, url_name, method)

        assert response.status_code == status.HTTP_200_OK

    @ddt.data(*VIEW_ENDPOINTS, *MANAGE_ENDPOINTS)
    @ddt.unpack
    def test_legacy_student_is_denied(self, url_name, method):
        """With the flag off, an enrolled learner with no staff role is denied."""
        self.set_authz_enabled(False)

        response = self._request(self.learner, url_name, method)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @ddt.data(COURSE_AUDITOR.external_key, COURSE_EDITOR.external_key)
    def test_authz_roles_without_legacy_equivalent_are_denied(self, role):
        """
        With the flag on, the AuthZ roles that have no legacy equivalent hold no date
        extensions permission, so they are denied on every endpoint.
        """
        user = self.create_user_with_authz_role(role, self.course_key)

        for url_name, method in (*self.VIEW_ENDPOINTS, *self.MANAGE_ENDPOINTS):
            response = self._request(user, url_name, method)

            assert response.status_code == status.HTTP_403_FORBIDDEN, url_name

    @ddt.data(
        # (AuthZ role, status of the view endpoints, status of the manage endpoint)
        (COURSE_LIMITED_STAFF.external_key, status.HTTP_200_OK, status.HTTP_200_OK),
        (COURSE_DATA_RESEARCHER.external_key, status.HTTP_200_OK, status.HTTP_403_FORBIDDEN),
        (COURSE_BETA_TESTER.external_key, status.HTTP_403_FORBIDDEN, status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_authz_legacy_roles_keep_their_legacy_access(self, authz_role, view_status, manage_status):
        """
        With the flag on, the AuthZ roles that stand for a legacy role are evaluated with
        its legacy rule, so they keep exactly the access it had.
        """
        user = self.create_user_with_authz_role(authz_role, self.course_key)

        self._assert_statuses(user, view_status, manage_status)

    @ddt.data(
        (CourseStaffRole, status.HTTP_200_OK, status.HTTP_200_OK),
        (CourseLimitedStaffRole, status.HTTP_200_OK, status.HTTP_200_OK),
        (CourseDataResearcherRole, status.HTTP_200_OK, status.HTTP_403_FORBIDDEN),
        (CourseBetaTesterRole, status.HTTP_403_FORBIDDEN, status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_legacy_roles_keep_their_access(self, role_class, view_status, manage_status):
        """With the flag off, each legacy role keeps the access it had before AuthZ."""
        self.set_authz_enabled(False)
        user = UserFactory()
        role_class(self.course_key).add_users(user)

        self._assert_statuses(user, view_status, manage_status)

    def _assert_statuses(self, user, view_status, manage_status):
        """Assert the status ``user`` gets on the view endpoints and on the manage endpoint."""
        for url_name, method in self.VIEW_ENDPOINTS:
            assert self._request(user, url_name, method).status_code == view_status, url_name
        for url_name, method in self.MANAGE_ENDPOINTS:
            assert self._request(user, url_name, method).status_code == manage_status, url_name

    def test_legacy_does_not_query_authz(self):
        """With the flag off, AuthZ is not queried."""
        self.set_authz_enabled(False)
        instructor = InstructorFactory(course_key=self.course_key)

        with patch(f"{API}.authz_api.is_user_allowed") as is_user_allowed:
            response = self._request(instructor, "graded_subsections", "get")

        assert response.status_code == status.HTTP_200_OK
        is_user_allowed.assert_not_called()
