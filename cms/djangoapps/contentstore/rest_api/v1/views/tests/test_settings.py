"""
Unit tests for course settings views.
"""
from unittest.mock import patch

import ddt
from django.conf import settings
from django.urls import reverse
from openedx_authz.constants.roles import COURSE_EDITOR
from rest_framework import status

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import get_proctored_exam_settings_url
from common.djangoapps.util.course import get_link_for_about_page
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.credit.tests.factories import CreditCourseFactory

from ...mixins import PermissionAccessMixin


@ddt.ddt
class CourseSettingsViewTest(CourseTestCase, PermissionAccessMixin):
    """
    Tests for CourseSettingsView.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:course_settings",
            kwargs={"course_id": self.course.id},
        )

    def test_course_settings_response(self):
        """Check successful response content"""
        response = self.client.get(self.url)
        expected_response = {
            "about_page_editable": True,
            "can_show_certificate_available_date_field": False,
            "course_display_name": self.course.display_name,
            "course_display_name_with_default": self.course.display_name_with_default,
            "credit_eligibility_enabled": True,
            "enrollment_end_editable": True,
            "enable_extended_course_details": False,
            "is_credit_course": False,
            "is_entrance_exams_enabled": True,
            "is_prerequisite_courses_enabled": False,
            "language_options": settings.ALL_LANGUAGES,
            "lms_link_for_about_page": get_link_for_about_page(self.course),
            "marketing_enabled": False,
            "mfe_proctored_exam_settings_url": get_proctored_exam_settings_url(
                self.course.id
            ),
            "platform_name": settings.PLATFORM_NAME,
            "short_description_editable": True,
            "sidebar_html_enabled": False,
            "show_min_grade_warning": False,
            "upgrade_deadline": None,
            "licensing_enabled": False,
        }

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertDictEqual(expected_response, response.data)  # noqa: PT009

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_CREDIT_ELIGIBILITY": True})
    def test_credit_eligibility_setting(self):
        """
        Make sure if the feature flag is enabled we have updated the dict keys in response.
        """
        _ = CreditCourseFactory(course_key=self.course.id, enabled=True)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn("credit_requirements", response.data)  # noqa: PT009
        self.assertTrue(response.data["is_credit_course"])  # noqa: PT009

    @patch.dict(
        "django.conf.settings.FEATURES",
        {
            "ENABLE_PREREQUISITE_COURSES": True,
            "MILESTONES_APP": True,
        },
    )
    def test_prerequisite_courses_enabled_setting(self):
        """
        Make sure if the feature flags are enabled we have updated the dict keys in response.
        """
        response = self.client.get(self.url)
        self.assertIn("possible_pre_requisite_courses", response.data)  # noqa: PT009
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009


@ddt.ddt
class CourseSettingsAuthzViewTest(CourseAuthoringAuthzTestMixin, CourseTestCase):
    """
    Tests for CourseSettingsView using AuthZ permissions.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:course_settings",
            kwargs={"course_id": self.course.id},
        )

    def test_authorized_user_can_access_course_settings(self):
        """Authorized user with COURSE_EDITOR role can access course settings."""
        self.add_user_to_role_in_course(self.authorized_user, COURSE_EDITOR.external_key, self.course.id)
        response = self.authorized_client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn("course_display_name", response.data)  # noqa: PT009

    def test_unauthorized_user_cannot_access_course_settings(self):
        """Unauthorized user should receive 403."""
        response = self.unauthorized_client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_user_without_role_then_added_can_access(self):
        """
        Validate dynamic role assignment works as expected.
        """
        # Initially unauthorized
        response = self.unauthorized_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

        # Assign role dynamically
        self.add_user_to_role_in_course(
            self.unauthorized_user,
            COURSE_EDITOR.external_key,
            self.course.id
        )

        response = self.unauthorized_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_CREDIT_ELIGIBILITY": True})
    def test_credit_eligibility_setting_with_authz(self):
        """
        Ensure feature flags still affect response under AuthZ.
        """
        _ = CreditCourseFactory(course_key=self.course.id, enabled=True)

        self.add_user_to_role_in_course(self.authorized_user, COURSE_EDITOR.external_key, self.course.id)
        response = self.authorized_client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn("credit_requirements", response.data)  # noqa: PT009
        self.assertTrue(response.data["is_credit_course"])  # noqa: PT009

    def test_staff_user_can_access_without_authz_role(self):
        """Django staff user should access course settings without AuthZ role."""

        response = self.staff_client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn("course_display_name", response.data)  # noqa: PT009

    def test_superuser_can_access_without_authz_role(self):
        """Superuser should access course settings without AuthZ role."""
        response = self.super_client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn("course_display_name", response.data)  # noqa: PT009
