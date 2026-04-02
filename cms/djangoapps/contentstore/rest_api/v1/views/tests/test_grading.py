"""
Unit tests for course grading views.
"""
import json
from unittest.mock import patch

import ddt
from django.urls import reverse
from openedx_authz.constants.roles import COURSE_DATA_RESEARCHER, COURSE_STAFF
from rest_framework import status
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.api.tests.base import BaseCourseViewTest
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import get_proctored_exam_settings_url
from cms.djangoapps.models.settings.course_grading import CourseGradingModel
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthzTestMixin
from openedx.core.djangoapps.credit.tests.factories import CreditCourseFactory

from ...mixins import PermissionAccessMixin


@ddt.ddt
class CourseGradingViewTest(CourseTestCase, PermissionAccessMixin):
    """
    Tests for CourseGradingView.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:course_grading",
            kwargs={"course_id": self.course.id},
        )

    def test_course_grading_response(self):
        """Check successful response content"""
        response = self.client.get(self.url)
        grading_data = CourseGradingModel.fetch(self.course.id)

        expected_response = {
            "mfe_proctored_exam_settings_url": get_proctored_exam_settings_url(
                self.course.id
            ),
            "course_assignment_lists": {},
            "course_details": grading_data.__dict__,
            "show_credit_eligibility": False,
            "is_credit_course": False,
            "default_grade_designations": ['A', 'B', 'C', 'D'],
        }

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertDictEqual(expected_response, response.data)

    @patch("django.conf.settings.DEFAULT_GRADE_DESIGNATIONS", ['A', 'B'])
    def test_default_grade_designations_setting(self):
        """
        Check that DEFAULT_GRADE_DESIGNATIONS setting reflects correctly in API.
        """
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(['A', 'B'], response.data["default_grade_designations"])

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_CREDIT_ELIGIBILITY": True})
    def test_credit_eligibility_setting(self):
        """
        Make sure if the feature flag is enabled we have enabled values in response.
        """
        _ = CreditCourseFactory(course_key=self.course.id, enabled=True)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["show_credit_eligibility"])
        self.assertTrue(response.data["is_credit_course"])

    def test_post_permissions_unauthenticated(self):
        """
        Test that an error is returned in the absence of auth credentials.
        """
        self.client.logout()
        response = self.client.post(self.url)
        error = self.get_and_check_developer_response(response)
        self.assertEqual(error, "Authentication credentials were not provided.")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_post_permissions_unauthorized(self):
        """
        Test that an error is returned if the user is unauthorised.
        """
        client, _ = self.create_non_staff_authed_user_client()
        response = client.post(self.url)
        error = self.get_and_check_developer_response(response)
        self.assertEqual(error, "You do not have permission to perform this action.")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch(
        "openedx.core.djangoapps.credit.tasks.update_credit_course_requirements.delay"
    )
    def test_post_course_grading(self, mock_update_credit_course_requirements):
        """Check successful request with called task"""
        request_data = {
            "graders": [
                {
                    "type": "Homework",
                    "min_count": 1,
                    "drop_count": 0,
                    "short_label": "",
                    "weight": 100,
                    "id": 0,
                }
            ],
            "grade_cutoffs": {"A": 0.75, "B": 0.63, "C": 0.57, "D": 0.5},
            "grace_period": {"hours": 12, "minutes": 0},
            "minimum_grade_credit": 0.7,
            "is_credit_course": True,
        }
        response = self.client.post(
            path=self.url,
            data=json.dumps(request_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_update_credit_course_requirements.assert_called_once()


class CourseGradingViewAuthzTest(CourseAuthzTestMixin, BaseCourseViewTest):
    """
    Tests Course Grading Configuration API authorization using openedx-authz.
    The endpoint uses COURSES_VIEW_GRADING_SETTINGS and COURSES_EDIT_GRADING_SETTINGS permissions.
    """

    view_name = "cms.djangoapps.contentstore:v1:course_grading"
    authz_roles_to_assign = [COURSE_STAFF.external_key]
    post_data = json.dumps({
        "graders": [{
            "type": "Homework",
            "min_count": 1,
            "drop_count": 0,
            "short_label": "",
            "weight": 100,
            "id": 0
        }],
        "grade_cutoffs": {"A": 0.75, "B": 0.63, "C": 0.57, "D": 0.5},
        "grace_period": {"hours": 12, "minutes": 0},
        "minimum_grade_credit": 0.7,
        "is_credit_course": False,
    })

    def test_authorized_user_can_access_get(self):
        """User with COURSE_STAFF role can access."""
        resp = self.authorized_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_unauthorized_user_cannot_access_get(self):
        """User without role cannot access."""
        resp = self.unauthorized_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_role_scoped_to_course_get(self):
        """Authorization should only apply to the assigned course."""
        other_course = self.store.create_course("OtherOrg", "OtherCourse", "Run", self.staff.id)

        resp = self.authorized_client.get(self.get_url(other_course.id))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_user_allowed_via_legacy_get(self):
        """
        Staff users should still pass through legacy fallback.
        """
        self.client.login(username=self.staff.username, password=self.password)

        resp = self.client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_superuser_allowed_get(self):
        """Superusers should always be allowed."""
        superuser = UserFactory(is_superuser=True)

        client = APIClient()
        client.force_authenticate(user=superuser)

        resp = client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_non_staff_user_cannot_access_get(self):
        """
        User without required permissions should be denied.
        This case validates that a non-staff user doesn't get access.
        """
        non_staff_user = UserFactory()
        non_staff_client = APIClient()
        self.add_user_to_role(non_staff_user, COURSE_DATA_RESEARCHER.external_key)
        non_staff_client.force_authenticate(user=non_staff_user)

        resp = non_staff_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

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
