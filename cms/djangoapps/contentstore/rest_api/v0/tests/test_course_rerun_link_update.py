"""
Unit tests for Course Rerun Link Update API
"""

import json
from unittest.mock import Mock, patch

from django.urls import reverse
from user_tasks.models import UserTaskStatus

from cms.djangoapps.contentstore.tests.utils import CourseTestCase


class TestCourseLinkUpdateAPI(CourseTestCase):
    """
    Tests for the Course Rerun Link Update API endpoints
    """

    def setUp(self):
        super().setUp()
        self.sample_links_data = [
            {
                "url": "http://localhost:18000/course/course-v1:edX+DemoX+Demo_Course_2023/course",
                "type": "course_content",
                "id": "block-v1:edX+DemoX+Demo_Course+type@html+block@intro",
            },
            {
                "url": "http://localhost:18000/course/course-v1:edX+DemoX+Demo_Course_2023/progress",
                "type": "course_updates",
                "id": "1",
            },
            {
                "url": "http://localhost:18000/course/course-v1:edX+DemoX+Demo_Course_2023/handouts",
                "type": "handouts",
                "id": "block-v1:edX+DemoX+Demo_Course+type@course_info+block@handouts",
            },
        ]

        self.enable_optimizer_patch = (
            "cms.djangoapps.contentstore.rest_api.v0.views.course_optimizer."
            "enable_course_optimizer_check_prev_run_links"
        )
        self.update_links_patch = (
            "cms.djangoapps.contentstore.rest_api.v0.views.course_optimizer."
            "update_course_rerun_links"
        )
        self.task_status_patch = (
            "cms.djangoapps.contentstore.core.course_optimizer_provider."
            "_latest_course_link_update_task_status"
        )
        self.user_task_artifact_patch = (
            "cms.djangoapps.contentstore.core.course_optimizer_provider."
            "UserTaskArtifact"
        )

    def make_post_request(self, course_id=None, data=None, **kwargs):
        """Helper method to make POST requests to the link update endpoint"""
        url = self.get_update_url(course_id or self.course.id)
        response = self.client.post(
            url,
            data=json.dumps(data) if data else None,
            content_type="application/json",
        )
        return response

    def get_update_url(self, course_key):
        """Get the update endpoint URL"""
        return reverse(
            "cms.djangoapps.contentstore:v0:rerun_link_update",
            kwargs={"course_id": str(course_key)},
        )

    def get_status_url(self, course_key):
        """Get the status endpoint URL"""
        return reverse(
            "cms.djangoapps.contentstore:v0:rerun_link_update_status",
            kwargs={"course_id": str(course_key)},
        )

    def test_post_update_all_links_success(self):
        """Test successful request to update all links"""
        with patch(self.enable_optimizer_patch, return_value=True):
            with patch(self.update_links_patch) as mock_task:
                mock_task.delay.return_value = Mock()

                data = {"action": "all"}
                response = self.make_post_request(data=data)

                self.assertEqual(response.status_code, 200)  # noqa: PT009
                self.assertIn("status", response.json())  # noqa: PT009
                mock_task.delay.assert_called_once()

    def test_post_update_single_links_success(self):
        """Test successful request to update single links"""
        with patch(self.enable_optimizer_patch, return_value=True):
            with patch(self.update_links_patch) as mock_task:
                mock_task.delay.return_value = Mock()

                data = {
                    "action": "single",
                    "data": [
                        {
                            "url": "http://localhost:18000/course/course-v1:edX+DemoX+Demo_Course/course",
                            "type": "course_content",
                            "id": "block-v1:edX+DemoX+Demo_Course+type@html+block@abc123",
                        },
                        {
                            "url": "http://localhost:18000/course/course-v1:edX+DemoX+Demo_Course/progress",
                            "type": "course_updates",
                            "id": "1",
                        },
                    ],
                }
                response = self.make_post_request(data=data)

                self.assertEqual(response.status_code, 200)  # noqa: PT009
                self.assertIn("status", response.json())  # noqa: PT009
                mock_task.delay.assert_called_once()

    def test_post_update_missing_action_returns_400(self):
        """Test that missing action parameter returns 400"""
        with patch(
            self.enable_optimizer_patch,
            return_value=True,
        ):
            data = {}
            response = self.make_post_request(data=data)

            self.assertEqual(response.status_code, 400)  # noqa: PT009
            self.assertIn("error", response.json())  # noqa: PT009
            self.assertIn("action", response.json()["error"])  # noqa: PT009

    def test_error_handling_workflow(self):
        """Test error handling in the complete workflow"""
        with patch(
            self.enable_optimizer_patch,
            return_value=True,
        ):
            with patch(self.update_links_patch) as mock_task:
                # Step 1: Start task
                mock_task.delay.return_value = Mock()

                data = {"action": "all"}
                response = self.make_post_request(data=data)
                self.assertEqual(response.status_code, 200)  # noqa: PT009

                # Step 2: Check failed status
                with patch(self.task_status_patch) as mock_status:
                    with patch(self.user_task_artifact_patch) as mock_artifact:  # noqa: F841
                        mock_task_status = Mock()
                        mock_task_status.state = UserTaskStatus.FAILED
                        mock_status.return_value = mock_task_status

                        status_url = self.get_status_url(self.course.id)
                        status_response = self.client.get(status_url)

                        self.assertEqual(status_response.status_code, 200)  # noqa: PT009
                        status_data = status_response.json()
                        self.assertEqual(status_data["status"], "Failed")  # noqa: PT009
                        self.assertEqual(status_data["results"], [])  # noqa: PT009
