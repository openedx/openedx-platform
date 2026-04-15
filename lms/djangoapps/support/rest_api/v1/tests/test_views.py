"""
Tests for support views.
"""

import ddt
from django.urls import reverse
from rest_framework.test import APIClient

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.models.user import CourseAccessRole
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import (
    TEST_PASSWORD,
    AdminFactory,
    InstructorFactory,
    StaffFactory,
    SuperuserFactory,
    UserFactory,
)
from lms.djangoapps.support.tests.test_views import SupportViewTestCase
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory


@ddt.ddt
class CourseTeamManageAPIViewTest(SupportViewTestCase):
    """
    Tests for CourseTeamManagementAPIView.

    Covers:
    - GET: Viewing user's course team roles by authenticated users.
    - PUT: Assigning/revoking user roles by authorized users.
    """

    def setUp(self):
        super().setUp()
        self.staff = AdminFactory()
        self.superuser = SuperuserFactory()
        self.user = UserFactory()
        self.org = "TestOrg"
        self.client = APIClient()
        self.url = reverse("lms.djangoapps.support.rest_api:v1:manage_course_team")

        self.extra_courses = [
            CourseOverviewFactory.create(
                org=self.org, run=f"CS10{i}", display_name=f"Test-Course-{i}"
            )
            for i in range(3)
        ]
        self.instructor_user = InstructorFactory.create(
            course_key=self.extra_courses[0].id
        )
        self.staff_user = StaffFactory.create(course_key=self.extra_courses[1].id)

    # --- GET API TEST CASES ---

    def test_get_api_missing_query_params_returns_400(self):
        """GET API: Returns 400 if no query parameters are provided."""
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 400)  # noqa: PT009

    def test_get_api_with_username_parameter(self):
        """GET API: Can query by username parameter."""
        self.client.login(username=self.staff.username, password=TEST_PASSWORD)
        resp = self.client.get(self.url, {"username": self.user.username})
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        self.assertIsInstance(resp.data, list)  # noqa: PT009

    def test_get_api_with_user_id_parameter(self):
        """GET API: Can query by user_id parameter."""
        self.client.login(username=self.staff.username, password=TEST_PASSWORD)
        resp = self.client.get(self.url, {"user_id": self.user.id})
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        self.assertIsInstance(resp.data, list)  # noqa: PT009

    def test_get_api_nonexistent_user_returns_404(self):
        """GET API: Returns 404 for a nonexistent user email."""
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        resp = self.client.get(self.url, {"email": "notfound@example.com"})
        self.assertEqual(resp.status_code, 404)  # noqa: PT009

    def test_get_api_unauthenticated_user_returns_401(self):
        """GET API: Unauthenticated users receive 401 Unauthorized."""
        resp = self.client.get(self.url, {"email": self.user.email})
        self.assertEqual(resp.status_code, 401)  # noqa: PT009

    @ddt.data(
        ("instructor", "instructor"),
        ("staff", "staff"),
    )
    @ddt.unpack
    def test_get_api_admin_can_fetch_course_roles(self, assigned_role, expected_role):
        """GET API: Admin/staff user can view roles for any course for target user."""
        self.client.login(username=self.staff.username, password=TEST_PASSWORD)
        CourseAccessRole.objects.filter(user=self.user, org=self.org).delete()

        if assigned_role == "instructor":
            CourseInstructorRole(self.extra_courses[0].id).add_users(self.user)
        elif assigned_role == "staff":
            CourseStaffRole(self.extra_courses[0].id).add_users(self.user)

        response = self.client.get(self.url, {"email": self.user.email})
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        self.assertIsInstance(response.data, list)  # noqa: PT009

        course_found = False
        for course in response.data:
            if course["course_id"] == str(self.extra_courses[0].id):
                with self.subTest(role=assigned_role):
                    self.assertEqual(course["role"], expected_role)  # noqa: PT009
                    # Verify new fields are present
                    self.assertIn("status", course)  # noqa: PT009
                    self.assertIn("org", course)  # noqa: PT009
                    self.assertIn("run", course)  # noqa: PT009
                    self.assertIn("number", course)  # noqa: PT009
                    self.assertIn("course_name", course)  # noqa: PT009
                    self.assertIn("course_url", course)  # noqa: PT009
                course_found = True
        self.assertTrue(course_found, "Expected course not found in response.")  # noqa: PT009

    def test_get_api_instructor_can_only_see_their_courses(self):
        """GET API: Course instructor sees only courses they have access to."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        resp = self.client.get(self.url, {"email": self.user.email})
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

        course_ids = [course["course_id"] for course in resp.data]
        self.assertIn(str(self.extra_courses[0].id), course_ids)  # noqa: PT009
        for i in range(1, 3):
            self.assertNotIn(str(self.extra_courses[i].id), course_ids)  # noqa: PT009

    def test_get_api_user_with_no_access_sees_no_courses(self):
        """GET API: Non-instructor users see no courses in the response."""
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        resp = self.client.get(self.url, {"email": self.user.email})
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        self.assertEqual(resp.data, [])  # noqa: PT009

    # --- PUT API TEST CASES ---

    def test_put_api_missing_email_returns_400(self):
        """PUT API: Sending request without email returns 400 with an error message."""
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        data = {"bulk_role_operations": []}
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 400)  # noqa: PT009
        self.assertEqual(resp.data["email"], "")  # noqa: PT009
        result = resp.data["results"][0]
        self.assertEqual(result["status"], "failed")  # noqa: PT009
        self.assertIn("error", result)  # noqa: PT009
        self.assertIn("email", result["error"])  # noqa: PT009

    def test_put_api_empty_operations_returns_400(self):
        """PUT API: Sending empty bulk_role_operations returns 400 with an error message."""
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        data = {"email": "test@example.com", "bulk_role_operations": []}
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 400)  # noqa: PT009
        self.assertEqual(resp.data["email"], "test@example.com")  # noqa: PT009
        result = resp.data["results"][0]
        self.assertEqual(result["status"], "failed")  # noqa: PT009
        self.assertIn("error", result)  # noqa: PT009
        self.assertIn("bulk_role_operations", result["error"])  # noqa: PT009

    def test_put_api_non_dict_input_returns_400(self):
        """PUT API: Sending a non-dict input returns 400 with an error message."""
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        resp = self.client.put(self.url, ["invalid"], format="json")
        self.assertEqual(resp.status_code, 400)  # noqa: PT009
        result = resp.data["results"][0]
        self.assertEqual(result["status"], "failed")  # noqa: PT009
        self.assertIn("error", result)  # noqa: PT009
        self.assertIn("JSON object", result["error"])  # noqa: PT009

    def test_put_api_missing_fields_returns_error(self):
        """PUT API: Request with missing required fields returns field-level errors."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        data = {
            "email": self.user.email,
            "bulk_role_operations": [{"course_id": "", "role": "", "action": ""}],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        self.assertEqual(resp.data["email"], self.user.email)  # noqa: PT009
        result = resp.data["results"][0]
        self.assertEqual(result["status"], "failed")  # noqa: PT009
        for field in ("course_id", "role", "action"):
            self.assertIn(field, result)  # noqa: PT009

    def test_put_api_invalid_user_email_returns_error(self):
        """PUT API: Invalid user email in request returns an error."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        data = {
            "email": "notfound@example.com",
            "bulk_role_operations": [
                {
                    "course_id": str(self.extra_courses[0].id),
                    "role": "instructor",
                    "action": "assign",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 404)  # noqa: PT009
        self.assertEqual(resp.data["email"], "notfound@example.com")  # noqa: PT009
        result = resp.data["results"][0]
        self.assertEqual(result["status"], "failed")  # noqa: PT009
        self.assertIn("User not found", result["error"])  # noqa: PT009

    def test_put_api_invalid_course_id_returns_error(self):
        """PUT API: Invalid course_id in request returns an error."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": "invalid-course-id",
                    "role": "instructor",
                    "action": "assign",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        self.assertEqual(resp.data["email"], self.user.email)  # noqa: PT009
        result = resp.data["results"][0]
        self.assertEqual(result["status"], "failed")  # noqa: PT009
        self.assertIn("Invalid course_id", result["error"])  # noqa: PT009

    def test_put_api_invalid_action_returns_error(self):
        """PUT API: Invalid action value in request returns an error."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": str(self.extra_courses[0].id),
                    "role": "instructor",
                    "action": "invalid_action",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        self.assertEqual(resp.data["email"], self.user.email)  # noqa: PT009
        result = resp.data["results"][0]
        self.assertEqual(result["status"], "failed")  # noqa: PT009
        self.assertIn("Invalid action", result["error"])  # noqa: PT009

    def test_put_api_assign_role_enrolls_user(self):
        """PUT API: Assigning a role enrolls user in the course."""
        self.client.login(username=self.staff.username, password=TEST_PASSWORD)
        course = self.extra_courses[2]
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": str(course.id),
                    "role": "instructor",
                    "action": "assign",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        self.assertEqual(resp.data["email"], self.user.email)  # noqa: PT009
        self.assertEqual(resp.data["results"][0]["status"], "success")  # noqa: PT009
        self.assertTrue(CourseInstructorRole(course.id).has_user(self.user))  # noqa: PT009
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, course.id))  # noqa: PT009

    def test_put_api_revoke_role_removes_user(self):
        """PUT API: Revoking a role removes user from course team."""
        self.client.login(username=self.staff.username, password=TEST_PASSWORD)
        course = self.extra_courses[1]
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": str(course.id),
                    "role": "staff",
                    "action": "revoke",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        self.assertEqual(resp.data["email"], self.user.email)  # noqa: PT009
        self.assertEqual(resp.data["results"][0]["status"], "success")  # noqa: PT009
        self.assertFalse(CourseStaffRole(course.id).has_user(self.user))  # noqa: PT009

    def test_put_api_course_instructor_can_manage_own_courses(self):
        """PUT API: Course instructor can assign roles for courses they manage."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": str(self.extra_courses[0].id),
                    "role": "staff",
                    "action": "assign",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        self.assertEqual(resp.data["email"], self.user.email)  # noqa: PT009
        self.assertEqual(resp.data["results"][0]["status"], "success")  # noqa: PT009
        self.assertTrue(CourseStaffRole(self.extra_courses[0].id).has_user(self.user))  # noqa: PT009

    def test_put_api_non_instructor_user_forbidden(self):
        """PUT API: Non-instructor users receive 403 Forbidden when assigning roles."""
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": str(self.extra_courses[0].id),
                    "role": "staff",
                    "action": "assign",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 403)  # noqa: PT009
        self.assertEqual(resp.data["email"], self.user.email)  # noqa: PT009
        self.assertEqual(resp.data["results"][0]["status"], "failed")  # noqa: PT009
        self.assertIn("do not have permission", resp.data["results"][0]["error"])  # noqa: PT009

    def test_put_api_org_level_instructor_can_manage_all_org_courses(self):
        """PUT API: Org-level instructors can manage roles for all courses in their org."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        CourseAccessRole.objects.create(
            user=self.instructor_user, role="instructor", org=self.org, course_id=None
        )
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": str(self.extra_courses[1].id),
                    "role": "staff",
                    "action": "assign",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        self.assertEqual(resp.data["email"], self.user.email)  # noqa: PT009
        self.assertEqual(resp.data["results"][0]["status"], "success")  # noqa: PT009
        self.assertTrue(CourseStaffRole(self.extra_courses[1].id).has_user(self.user))  # noqa: PT009

    def test_put_api_course_instructor_cannot_manage_other_courses(self):
        """PUT API: Course instructor cannot assign roles for courses they don't manage."""
        self.client.login(
            username=self.instructor_user.username, password=TEST_PASSWORD
        )
        data = {
            "email": self.user.email,
            "bulk_role_operations": [
                {
                    "course_id": str(self.extra_courses[1].id),
                    "role": "staff",
                    "action": "assign",
                }
            ],
        }
        resp = self.client.put(self.url, data, format="json")
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        self.assertEqual(resp.data["email"], self.user.email)  # noqa: PT009
        self.assertEqual(resp.data["results"][0]["status"], "failed")  # noqa: PT009
        self.assertIn("do not have instructor access", resp.data["results"][0]["error"])  # noqa: PT009
