"""
Unit tests for the course's certificate.
"""
from django.urls import reverse
from openedx_authz.constants.roles import COURSE_EDITOR, COURSE_STAFF
from rest_framework import status

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.views.tests.test_certificates import HelperMethods
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin

from ...mixins import PermissionAccessMixin


class CourseCertificatesViewTest(CourseTestCase, PermissionAccessMixin, HelperMethods):
    """
    Tests for CourseCertificatesView.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:certificates",
            kwargs={"course_id": self.course.id},
        )

    def test_success_response(self):
        """
        Check that endpoint is valid and success response.
        """
        self._add_course_certificates(count=2, signatory_count=2)
        response = self.client.get(self.url)
        response_data = response.data
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_data["certificates"]), 2)
        self.assertEqual(len(response_data["certificates"][0]["signatories"]), 2)
        self.assertEqual(len(response_data["certificates"][1]["signatories"]), 2)
        self.assertEqual(response_data["course_number_override"], self.course.display_coursenumber)
        self.assertEqual(response_data["course_title"], self.course.display_name_with_default)
        self.assertEqual(response_data["course_number"], self.course.number)


class CourseCertificatesAuthzViewTest(
        CourseAuthoringAuthzTestMixin, CourseTestCase, PermissionAccessMixin, HelperMethods
    ):
    """
    Tests for CourseCertificatesView with AuthZ enabled.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:certificates",
            kwargs={"course_id": self.course.id},
        )

    def test_authorized_user_can_access(self):
        """User with COURSE_STAFF role can access."""
        self._add_course_certificates(count=2, signatory_count=2)
        self.add_user_to_role_in_course(self.authorized_user, COURSE_STAFF.external_key, self.course.id)
        resp = self.authorized_client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_non_staff_user_cannot_access(self):
        """
        User without permissions should be denied.
        This case validates that a non-staff user cannot access.
        """
        self._add_course_certificates(count=2, signatory_count=2)
        self.add_user_to_role_in_course(self.authorized_user, COURSE_EDITOR.external_key, self.course.id)
        resp = self.authorized_client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
