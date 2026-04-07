"""
Tests for the course import API views
"""

from openedx_authz.constants.roles import COURSE_DATA_RESEARCHER, COURSE_STAFF
from rest_framework import status
from rest_framework.test import APIClient

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthzTestMixin

from .base import BaseCourseViewTest


class CourseQualityViewTest(BaseCourseViewTest):
    """
    Test course quality view via a RESTful API
    """
    view_name = 'courses_api:course_quality'

    def test_staff_succeeds(self):
        self.client.login(username=self.staff.username, password=self.password)
        resp = self.client.get(self.get_url(self.course_key), {'all': 'true'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        expected_data = {
            'units': {
                'num_blocks': {
                    'max': 2,
                    'mean': 1.0,
                    'median': 2.0,
                    'mode': 2.0,
                    'min': 0,
                },
                'total_visible': 3,
            },
            'videos': {
                'durations': {
                    'max': None,
                    'mean': None,
                    'median': None,
                    'mode': None,
                    'min': None,
                },
                'num_mobile_encoded': 0,
                'num_with_val_id': 0,
                'total_number': 3,
            },
            'sections': {
                'number_with_highlights': 0,
                'total_visible': 1,
                'total_number': 1,
                'highlights_enabled': True,
                'highlights_active_for_course': False,
            },
            'subsections': {
                'num_with_one_block_type': 1,
                'num_block_types': {
                    'max': 2,
                    'mean': 2.0,
                    'median': 2.0,
                    'mode': 1.0,
                    'min': 1,
                },
                'total_visible': 2,
            },
            'is_self_paced': True,
        }
        self.assertDictEqual(resp.data, expected_data)

    def test_student_fails(self):
        self.client.login(username=self.student.username, password=self.password)
        resp = self.client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class CourseQualityAuthzTest(CourseAuthzTestMixin, BaseCourseViewTest):
    """
    Tests Course Quality API authorization using openedx-authz.
    The endpoint uses COURSES_VIEW_COURSE permission.
    """

    view_name = "courses_api:course_quality"
    authz_roles_to_assign = [COURSE_STAFF.external_key]

    def test_authorized_user_can_access(self):
        """User with COURSE_STAFF role can access."""
        resp = self.authorized_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_unauthorized_user_cannot_access(self):
        """User without role cannot access."""
        resp = self.unauthorized_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_role_scoped_to_course(self):
        """Authorization should only apply to the assigned course."""
        other_course = self.store.create_course("OtherOrg", "OtherCourse", "Run", self.staff.id)

        resp = self.authorized_client.get(self.get_url(other_course.id))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_user_allowed_via_legacy(self):
        """
        Staff users should still pass through legacy fallback.
        """
        self.client.login(username=self.staff.username, password=self.password)

        resp = self.client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_superuser_allowed(self):
        """Superusers should always be allowed."""
        superuser = UserFactory(is_superuser=True)

        client = APIClient()
        client.force_authenticate(user=superuser)

        resp = client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_non_staff_user_cannot_access(self):
        """
        User without permissions should be denied.
        This case validates that a non-staff user cannot access even
        if they have course author access to the course.
        """
        non_staff_user = UserFactory()
        non_staff_client = APIClient()
        self.add_user_to_role(non_staff_user, COURSE_DATA_RESEARCHER.external_key)
        non_staff_client.force_authenticate(user=non_staff_user)

        resp = non_staff_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
