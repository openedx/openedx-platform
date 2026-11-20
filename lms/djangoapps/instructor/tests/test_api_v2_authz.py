"""
Tests for the AuthZ enforcement of the Instructor Dashboard API v2.

They cover the tabs, ``authz_enabled`` and the migrated endpoints.
"""

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
    COURSES_VIEW_ENROLLMENTS,
)
from rest_framework import status
from rest_framework.test import APIClient

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseDataResearcherRole, CourseLimitedStaffRole
from common.djangoapps.student.tests.factories import (
    InstructorFactory,
    StaffFactory,
    UserFactory,
)
from lms.djangoapps.courseware.tabs import get_course_tab_list
from lms.djangoapps.instructor.authz import constants
from lms.djangoapps.instructor.authz.api import user_has_instructor_permission
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
class CourseMetadataViewCourseInfoAuthzTest(InstructorAuthzTestMixin, SharedModuleStoreTestCase):
    """
    ``CourseMetadataView`` returns the Course Info fields only with ``courses.view_course_info``.

    Without it, a user who can open the dashboard gets ``200`` with those fields set to ``null``,
    and the header and tab fields unchanged.
    """

    # Fields only the Course Info tab reads.
    COURSE_INFO_FIELDS = (
        "enrollment_start",
        "enrollment_end",
        "start",
        "end",
        "pacing",
        "has_started",
        "has_ended",
        "total_enrollment",
        "learner_count",
        "staff_count",
        "enrollment_counts",
        "num_sections",
        "grade_cutoffs",
        "course_errors",
        "studio_url",
    )
    # Course Info fields that always have a value for the test course when shown.
    VALUED_COURSE_INFO_FIELDS = (
        "start",
        "pacing",
        "has_started",
        "has_ended",
        "total_enrollment",
        "learner_count",
        "staff_count",
        "enrollment_counts",
        "num_sections",
        "grade_cutoffs",
        "course_errors",
    )
    # Fields the page header and the other tabs read, always returned.
    SHARED_FIELDS = (
        "course_id",
        "username",
        "display_name",
        "org",
        "course_number",
        "course_run",
        "tabs",
        "permissions",
        "authz_enabled",
        "disable_buttons",
        "gradebook_url",
        "studio_grading_url",
        "admin_console_url",
        "certificates_enabled",
        "analytics_dashboard_message",
        "proctored_exams_enabled",
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.url = reverse("instructor_api_v2:course_metadata", kwargs={"course_id": str(self.course_key)})

    def _get(self, user):
        self.client.force_authenticate(user=user)
        return self.client.get(self.url)

    @staticmethod
    def _allow_only(*permissions):
        """Return an ``is_user_allowed`` side effect that grants only the given permissions."""
        identifiers = {permission.identifier for permission in permissions}
        return lambda _user, permission, _scope: permission in identifiers

    def _assert_course_info_shown(self, response):
        """Assert a ``200`` with the Course Info fields filled in."""
        assert response.status_code == status.HTTP_200_OK
        for field in self.VALUED_COURSE_INFO_FIELDS:
            assert response.data[field] is not None, field

    def _assert_course_info_hidden(self, response):
        """Assert a ``200`` with the Course Info fields ``null`` and the shared fields returned."""
        assert response.status_code == status.HTTP_200_OK
        for field in self.COURSE_INFO_FIELDS:
            assert response.data[field] is None, field
        for field in self.SHARED_FIELDS:
            assert field in response.data, field
        assert response.data["course_id"] == str(self.course_key)
        assert response.data["display_name"] == self.course.display_name
        assert response.data["org"] == self.course_key.org
        assert response.data["course_number"] == self.course_key.course

    @ddt.data(COURSE_ADMIN, COURSE_STAFF)
    def test_authz_role_sees_course_info(self, role):
        """With the flag on, Course Admin and Course Staff get the Course Info fields."""
        user = self.create_user_with_authz_role(role.external_key, self.course_key)

        self._assert_course_info_shown(self._get(user))

    def test_authz_view_course_info_alone_shows_course_info(self):
        """With the flag on, ``courses.view_course_info`` alone is enough to get the Course Info fields."""
        user = UserFactory()

        with patch(f"{API}.authz_api.is_user_allowed", side_effect=self._allow_only(COURSES_VIEW_COURSE_INFO)):
            self._assert_course_info_shown(self._get(user))

    @ddt.data(*(p for p in constants.INSTRUCTOR_DASHBOARD_PERMISSIONS if p != COURSES_VIEW_COURSE_INFO.identifier))
    def test_authz_other_dashboard_permission_hides_course_info(self, permission):
        """
        With the flag on, a user who opens the dashboard without ``courses.view_course_info``
        gets the Course Info fields as ``null`` and the shared fields unchanged.
        """
        user = UserFactory()

        with patch(f"{API}.authz_api.is_user_allowed", side_effect=lambda _u, perm, _s: perm == permission):
            self._assert_course_info_hidden(self._get(user))

    def test_authz_denied_hides_course_info_from_legacy_staff(self):
        """With the flag on, AuthZ decides: legacy staff without ``courses.view_course_info`` gets no fields."""
        staff = StaffFactory(course_key=self.course_key)

        with patch(f"{API}.authz_api.is_user_allowed", side_effect=self._allow_only(COURSES_VIEW_ENROLLMENTS)):
            self._assert_course_info_hidden(self._get(staff))

    def test_authz_data_researcher_keeps_course_info(self):
        """
        With the flag on, a data researcher keeps the Course Info fields it had before AuthZ.

        Its AuthZ role holds no dashboard permission, so the legacy ``VIEW_DASHBOARD`` rule decides.
        """
        user = self.create_user_with_authz_role(COURSE_DATA_RESEARCHER.external_key, self.course_key)

        self._assert_course_info_shown(self._get(user))

    def test_hidden_fields_are_not_computed(self):
        """Without ``courses.view_course_info``, the enrollment counts are not queried."""
        user = UserFactory()

        with patch(f"{API}.authz_api.is_user_allowed", side_effect=self._allow_only(COURSES_VIEW_ENROLLMENTS)):
            with patch.object(CourseEnrollment.objects, "enrollment_counts") as enrollment_counts:
                self._assert_course_info_hidden(self._get(user))

        enrollment_counts.assert_not_called()

    def test_view_course_info_is_resolved_once(self):
        """``courses.view_course_info`` is checked once per response, not once per field."""
        user = self.create_user_with_authz_role(COURSE_STAFF.external_key, self.course_key)

        with patch(
            "lms.djangoapps.instructor.views.serializers_v2.user_has_instructor_permission",
            wraps=user_has_instructor_permission,
        ) as check:
            self._assert_course_info_shown(self._get(user))

        assert check.call_count == 1

    @ddt.data(StaffFactory, InstructorFactory)
    def test_legacy_role_sees_course_info(self, user_factory):
        """With the flag off, legacy staff and instructors get the Course Info fields without querying AuthZ."""
        self.set_authz_enabled(False)
        user = user_factory(course_key=self.course_key)

        with patch(f"{API}.authz_api.is_user_allowed") as is_user_allowed:
            self._assert_course_info_shown(self._get(user))

        is_user_allowed.assert_not_called()

    def test_legacy_data_researcher_sees_course_info(self):
        """With the flag off, a data researcher keeps the Course Info fields through ``VIEW_DASHBOARD``."""
        self.set_authz_enabled(False)
        user = UserFactory()
        CourseDataResearcherRole(self.course_key).add_users(user)

        self._assert_course_info_shown(self._get(user))
