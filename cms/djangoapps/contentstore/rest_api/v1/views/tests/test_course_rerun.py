"""
Unit tests for course rerun.
"""
from django.urls import reverse
from rest_framework import status

from cms.djangoapps.contentstore.rest_api.v1.mixins import PermissionAccessMixin
from cms.djangoapps.contentstore.tests.utils import CourseTestCase


class CourseRerunViewTest(CourseTestCase, PermissionAccessMixin):
    """
    Tests for CourseRerunView.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:course_rerun",
            kwargs={"course_id": self.course.id},
        )

    def test_course_rerun_response(self):
        """Check successful response content"""
        response = self.client.get(self.url)
        expected_response = {
            "allow_unicode_course_id": False,
            "course_creator_status": "granted",
            "display_name": self.course.display_name,
            "number": self.course.id.course,
            "org": self.course.id.org,
            "run": self.course.id.run,
        }

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertDictEqual(expected_response, response.data)
