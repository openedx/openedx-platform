"""
Unit tests for the course's textbooks.
"""
from django.urls import reverse
from openedx_authz.constants.roles import COURSE_AUDITOR, COURSE_LIMITED_STAFF, COURSE_STAFF
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin

from ...mixins import PermissionAccessMixin


class CourseTextbooksViewTest(CourseTestCase, PermissionAccessMixin):
    """
    Tests for CourseTextbooksView.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:textbooks",
            kwargs={"course_id": self.course.id},
        )

    def test_success_response(self):
        """
        Check that endpoint is valid and success response.
        """
        expected_textbook = [
            {
                "tab_title": "Textbook Name",
                "chapters": [
                    {"title": "Chapter 1", "url": "/static/book.pdf"},
                    {"title": "Chapter 2", "url": "/static/story.pdf"},
                ],
                "id": "Textbook_Name",
            }
        ]
        self.course.pdf_textbooks = expected_textbook
        self.save_course()

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        self.assertEqual(response.data["textbooks"], expected_textbook)  # noqa: PT009


class CourseTextbooksAuthzTest(CourseAuthoringAuthzTestMixin, CourseTestCase):
    """
    Integration tests for CourseTextbooksView authz permissions.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:textbooks",
            kwargs={"course_id": self.course.id},
        )

    def test_staff_can_view_textbooks(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_STAFF.external_key, self.course.id)
        resp = self.authorized_client.get(self.url)
        assert resp.status_code == 200

    def test_auditor_can_view_textbooks(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, self.course.id)
        resp = self.authorized_client.get(self.url)
        assert resp.status_code == 200

    def test_limited_staff_cannot_view_textbooks(self):
        self.add_user_to_role_in_course(self.authorized_user, COURSE_LIMITED_STAFF.external_key, self.course.id)
        resp = self.authorized_client.get(self.url)
        assert resp.status_code == 403

    def test_unauthorized_cannot_view_textbooks(self):
        resp = self.unauthorized_client.get(self.url)
        assert resp.status_code == 403

    def test_superuser_can_view_textbooks(self):
        superuser = UserFactory(is_superuser=True)
        client = APIClient()
        client.force_authenticate(user=superuser)
        resp = client.get(self.url)
        assert resp.status_code == 200
