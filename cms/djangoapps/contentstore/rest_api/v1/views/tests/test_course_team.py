"""
Unit tests for course team.
"""
import ddt
from django.urls import reverse
from rest_framework import status

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory

from ...mixins import PermissionAccessMixin


@ddt.ddt
class CourseTeamViewTest(CourseTestCase, PermissionAccessMixin):
    """
    Tests for CourseTeamView.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:course_team",
            kwargs={"course_id": self.course.id},
        )

    def get_expected_course_data(self, instructor=None, staff=None):
        """Utils is used to get expected data for course team"""
        users = []

        if instructor:
            users.append({
                "email": instructor.email,
                "id": instructor.id,
                "role": "instructor",
                "username": instructor.username
            })

        if staff:
            users.append({
                "email": staff.email,
                "id": staff.id,
                "role": "staff",
                "username": staff.username
            })

        return {
            "show_transfer_ownership_hint": False,
            "users": users,
            "allow_actions": True,
        }

    def create_course_user_roles(self, course_id):
        """Get course staff and instructor roles user"""
        instructor = UserFactory()
        CourseInstructorRole(course_id).add_users(instructor)
        staff = UserFactory()
        CourseStaffRole(course_id).add_users(staff)

        return instructor, staff

    def test_course_team_response(self):
        """Check successful response content"""
        response = self.client.get(self.url)
        expected_response = self.get_expected_course_data()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertDictEqual(expected_response, response.data)

    def test_users_response(self):
        """Test the response for users in the course."""
        instructor, staff = self.create_course_user_roles(self.course.id)
        response = self.client.get(self.url)
        users_response = [dict(item) for item in response.data["users"]]
        expected_response = self.get_expected_course_data(instructor, staff)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertListEqual(expected_response["users"], users_response)
