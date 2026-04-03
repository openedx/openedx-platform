"""
Tests for the notifications handlers.
"""
from unittest.mock import patch

from django.test import TestCase
from openedx_events.learning.data import CourseNotificationData
from openedx_events.learning.signals import COURSE_NOTIFICATION_REQUESTED


class CourseNotificationsTest(TestCase):
    """
    Tests for the course notifications.
    """

    @patch('openedx.core.djangoapps.notifications.handlers.calculate_course_wide_notification_audience')
    @patch('openedx.core.djangoapps.notifications.tasks.send_notifications')
    def test_generate_course_notifications(self, mock_send_notifications, mock_calculate_audience):
        # Set up mock objects
        mock_calculate_audience.return_value = [1, 2, 3, 4]  # Example user IDs
        notification_data = CourseNotificationData(
            course_key='abc/123',
            content_context={
                "replier_name": 'name',
                "post_title": 'title',
                "course_name": 'course',
                "sender_id": 3,
            },
            notification_type='new_discussion_post',
            content_url="https://example.com",
            app_name="discussion",
            audience_filters={},
        )
        COURSE_NOTIFICATION_REQUESTED.send_event(course_notification_data=notification_data)

        # Check if the sender_id was removed from the user_ids
        expected_user_ids = [1, 2, 4]  # 3 should be removed
        notification_data = mock_send_notifications.delay.call_args[1]
        self.assertEqual(notification_data['user_ids'], expected_user_ids)
