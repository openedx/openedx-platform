"""
Unit tests for the course's setting group configuration.
"""
from django.urls import reverse
from openedx_authz.constants.roles import COURSE_DATA_RESEARCHER, COURSE_STAFF
from rest_framework import status
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.api.tests.base import BaseCourseViewTest
from cms.djangoapps.contentstore.course_group_config import CONTENT_GROUP_CONFIGURATION_NAME
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthzTestMixin
from xmodule.partitions.partitions import Group, UserPartition  # pylint: disable=wrong-import-order

from ...mixins import PermissionAccessMixin


class CourseGroupConfigurationsViewTest(CourseTestCase, PermissionAccessMixin):
    """
    Tests for CourseGroupConfigurationsView.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:group_configurations",
            kwargs={"course_id": self.course.id},
        )

    def test_success_response(self):
        """
        Check that endpoint is valid and success response.
        """
        self.course.user_partitions = [
            UserPartition(
                0,
                "First name",
                "First description",
                [Group(0, "Group A"), Group(1, "Group B"), Group(2, "Group C")],
            ),  # pylint: disable=line-too-long
        ]
        self.save_course()

        if "split_test" not in self.course.advanced_modules:
            self.course.advanced_modules.append("split_test")
            self.store.update_item(self.course, self.user.id)

        response = self.client.get(self.url)
        self.assertEqual(len(response.data["all_group_configurations"]), 1)  # noqa: PT009
        self.assertEqual(len(response.data["experiment_group_configurations"]), 1)  # noqa: PT009
        self.assertContains(response, "First name", count=1)
        self.assertContains(response, "Group C")
        self.assertContains(response, CONTENT_GROUP_CONFIGURATION_NAME)
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009

class CourseGroupConfigurationsAuthzTest(CourseAuthzTestMixin, BaseCourseViewTest):
    """
    Tests Course Group Configuration API authorization using openedx-authz.
    The endpoint uses COURSES_MANAGE_GROUP_CONFIGURATIONS permission.
    """

    view_name = "cms.djangoapps.contentstore:v1:group_configurations"
    authz_roles_to_assign = [COURSE_STAFF.external_key]

    def test_authorized_user_can_access(self):
        """User with COURSE_STAFF role can access."""
        resp = self.authorized_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_unauthorized_user_cannot_access(self):
        """User without role cannot access."""
        resp = self.unauthorized_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_role_scoped_to_course(self):
        """Authorization should only apply to the assigned course."""
        other_course = self.store.create_course("OtherOrg", "OtherCourse", "Run", self.staff.id)

        resp = self.authorized_client.get(self.get_url(other_course.id))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_staff_user_allowed_via_legacy(self):
        """
        Staff users should still pass through legacy fallback.
        """
        self.client.login(username=self.staff.username, password=self.password)

        resp = self.client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_superuser_allowed(self):
        """Superusers should always be allowed."""
        superuser = UserFactory(is_superuser=True)

        client = APIClient()
        client.force_authenticate(user=superuser)

        resp = client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_non_staff_user_cannot_access(self):
        """
        User without required permissions should be denied.
        This case validates that a non-staff user doesn't get access.
        """
        non_staff_user = UserFactory()
        non_staff_client = APIClient()
        self.add_user_to_role(non_staff_user, COURSE_DATA_RESEARCHER.external_key)
        non_staff_client.force_authenticate(user=non_staff_user)

        resp = non_staff_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009
