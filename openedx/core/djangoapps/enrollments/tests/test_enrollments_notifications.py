"""
Unit tests for the EnrollmentsNotificationSender class
"""
import unittest
import datetime
from unittest.mock import MagicMock, patch

from django.test.utils import override_settings
import pytest

from openedx.core.djangoapps.enrollments.enrollments_notifications import EnrollmentNotificationSender
from openedx_events.learning.data import UserNotificationData

DEFAULT_MFE_URL = "https://learning.default"
SITE_CONF_MFE_URL = "https://learning.siteconf"


@pytest.mark.django_db
class TestEnrollmentsNotificationSender(unittest.TestCase):
    """
    Tests for the EnrollmentsNotificationSender class
    """

    def setUp(self):
        self.course = MagicMock()
        self.course.name = "test course"
        self.course.id = 1
        self.expiry_date = datetime.date.today() + datetime.timedelta(days=1)
        self.user_id = '123'
        self.notification_sender = EnrollmentNotificationSender(self.course, self.user_id, self.expiry_date)

    @override_settings(LEARNING_MICROFRONTEND_URL=DEFAULT_MFE_URL)
    @patch(
        'openedx.core.djangoapps.enrollments.enrollments_notifications.configuration_helpers.get_value',
        return_value=SITE_CONF_MFE_URL,
    )
    @patch('openedx.core.djangoapps.enrollments.enrollments_notifications.USER_NOTIFICATION_REQUESTED.send_event')
    def test_send_audit_access_expiring_soon_notification(self, mock_send_notification, mock_get_value):
        """
        Test that audit access expiring soon notification event is sent with correct parameters.
        """

        self.notification_sender.send_audit_access_expiring_soon_notification()

        mock_get_value.assert_called_once_with('LEARNING_MICROFRONTEND_URL', DEFAULT_MFE_URL)
        mock_send_notification.assert_called_once()
        notification_data = UserNotificationData(
            user_ids=[int(self.user_id)],
            context={
                'course': self.course.name,
                'audit_access_expiry': self.expiry_date,
            },
            notification_type='audit_access_expiring_soon',
            content_url=f"{SITE_CONF_MFE_URL}/course/{str(self.course.id)}/home",
            app_name="enrollments",
            course_key=self.course.id,
        )
        mock_send_notification.assert_called_with(notification_data=notification_data)

    @override_settings(LEARNING_MICROFRONTEND_URL=DEFAULT_MFE_URL)
    @patch(
        'openedx.core.djangoapps.enrollments.enrollments_notifications.configuration_helpers.get_value',
        side_effect=lambda key, default: default,
    )
    @patch('openedx.core.djangoapps.enrollments.enrollments_notifications.USER_NOTIFICATION_REQUESTED.send_event')
    def test_send_audit_access_expiring_soon_notification_falls_back_to_settings(
        self,
        mock_send_event,
        mock_get_value
    ):
        """
        Test mocks missing site-config value and verifies default URL and get_value args.
        """
        self.notification_sender.send_audit_access_expiring_soon_notification()

        mock_get_value.assert_called_once_with('LEARNING_MICROFRONTEND_URL', DEFAULT_MFE_URL)

        expected_notification = UserNotificationData(
            user_ids=[int(self.user_id)],
            context={
                'course': self.course.name,
                'audit_access_expiry': self.expiry_date,
            },
            notification_type='audit_access_expiring_soon',
            content_url=f"{DEFAULT_MFE_URL}/course/{str(self.course.id)}/home",
            app_name="enrollments",
            course_key=self.course.id,
        )
        mock_send_event.assert_called_once_with(notification_data=expected_notification)
