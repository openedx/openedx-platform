"""Test to verify tracking logs are emitted correctly"""
from unittest.mock import Mock, patch

from django.test import TestCase

from common.djangoapps.student.models.user import (
    USER_LOGGED_IN_EVENT_NAME,
    USER_LOGGED_OUT_EVENT_NAME,
    log_successful_login,
    log_successful_logout,
)
from common.djangoapps.student.tests.factories import UserFactory


class TestTrackingLog(TestCase):
    """
    Tests for tracking log
    """

    def setUp(self):
        self.user = UserFactory()

    @patch("common.djangoapps.student.models.user.tracker")
    def test_log_successful_login(self, patched_tracker):
        """
        Test log_successful_login
        """
        log_successful_login(
            sender="dummy_sender", request="dummy_request", user=self.user
        )

        patched_tracker.emit.assert_called_once_with(
            USER_LOGGED_IN_EVENT_NAME,
            {
                "user_id": self.user.id,
                "event_type": "login",
            },
        )

    @patch("common.djangoapps.student.models.user.tracker")
    def test_log_successful_logout(self, patched_tracker):
        """
        Test log_successful_logout
        """
        log_successful_logout(
            sender="dummy_sender", request=Mock(user=self.user), user=self.user
        )

        patched_tracker.emit.assert_called_once_with(
            USER_LOGGED_OUT_EVENT_NAME,
            {
                "user_id": self.user.id,
                "event_type": "logout",
            },
        )
