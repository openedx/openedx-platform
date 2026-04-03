"""
Unit tests for authoring grading views.
"""
import json

from openedx_authz.constants.roles import COURSE_DATA_RESEARCHER, COURSE_STAFF
from rest_framework import status
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.api.tests.base import BaseCourseViewTest
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthzTestMixin


class AuthoringGradingViewAuthzTest(CourseAuthzTestMixin, BaseCourseViewTest):
    """
    Tests Authoring Grading configuration API authorization using openedx-authz.
    The endpoint uses the COURSES_EDIT_GRADING_SETTINGS permission.
    """

    view_name = "cms.djangoapps.contentstore:v0:cms_api_update_grading"
    authz_roles_to_assign = [COURSE_STAFF.external_key]
    post_data = json.dumps({
        "graders": [
            {
                "type": "Homework",
                "min_count": 1,
                "drop_count": 0,
                "short_label": "",
                "weight": 100,
                "id": 0
            }
        ],
        "grade_cutoffs": {
            "A": 0.75,
            "B": 0.63,
            "C": 0.57,
            "D": 0.5
        },
        "grace_period": {
            "hours": 12,
            "minutes": 0
        },
        "minimum_grade_credit": 0.7,
        "is_credit_course": True
    })

    def test_authorized_user_can_access_post(self):
        """User with COURSE_STAFF role can access."""
        resp = self.authorized_client.post(
            self.get_url(self.course_key),
            data=self.post_data,
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_unauthorized_user_cannot_access_post(self):
        """User without role cannot access."""
        resp = self.unauthorized_client.post(
            self.get_url(self.course_key),
            data=self.post_data,
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_role_scoped_to_course_post(self):
        """Authorization should only apply to the assigned course."""
        other_course = self.store.create_course("OtherOrg", "OtherCourse", "Run", self.staff.id)

        resp = self.authorized_client.post(
            self.get_url(other_course.id),
            data=self.post_data,
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_user_allowed_via_legacy_post(self):
        """
        Staff users should still pass through legacy fallback.
        """
        self.client.login(username=self.staff.username, password=self.password)

        resp = self.client.post(
            self.get_url(self.course_key),
            data=self.post_data,
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_superuser_allowed_post(self):
        """Superusers should always be allowed."""
        superuser = UserFactory(is_superuser=True)

        client = APIClient()
        client.force_authenticate(user=superuser)

        resp = client.post(
            self.get_url(self.course_key),
            data=self.post_data,
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_non_staff_user_cannot_access_post(self):
        """
        User without required permissions should be denied.
        This case validates that a non-staff user doesn't get access.
        """
        non_staff_user = UserFactory()
        non_staff_client = APIClient()
        self.add_user_to_role(non_staff_user, COURSE_DATA_RESEARCHER.external_key)
        non_staff_client.force_authenticate(user=non_staff_user)

        resp = non_staff_client.post(
            self.get_url(self.course_key),
            data=self.post_data,
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
