"""
Unit tests for SessionInactivityTimeout middleware.
"""

from datetime import datetime, timedelta
from unittest.mock import ANY, call, patch

import ddt
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import TestCase, override_settings

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.session_inactivity_timeout.middleware import LAST_TOUCH_KEYNAME, SessionInactivityTimeout
from openedx.core.djangolib.testing.utils import get_mock_request


@ddt.ddt
class SessionInactivityTimeoutTestCase(TestCase):
    """
    Test case for SessionInactivityTimeout middleware
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.middleware = SessionInactivityTimeout(get_response=lambda request: None)
        self.request = get_mock_request(self.user)

        self.request.session = SessionStore()
        self.request.session.create()
        self.request.session.modified = False

    def test_process_request_unauthenticated_user_does_nothing(self):
        self.request.user = AnonymousUser()
        response = self.middleware.process_request(self.request)  # pylint: disable=assignment-from-none
        assert response is None
        assert LAST_TOUCH_KEYNAME not in self.request.session

    @ddt.data(
        None,  # No timestamp key in session
        "",    # Empty string timestamp in session
    )
    @override_settings(SESSION_INACTIVITY_TIMEOUT_IN_SECONDS=300)
    @patch("openedx.core.djangoapps.session_inactivity_timeout.middleware.datetime")
    @patch("openedx.core.djangoapps.session_inactivity_timeout.middleware.log")
    @patch(
        "openedx.core.djangoapps.session_inactivity_timeout.middleware.monitoring_utils"
    )
    def test_process_request_first_visit_sets_timestamp(
        self, timestamp_value, mock_monitoring, mock_log, mock_datetime
    ):
        if timestamp_value is not None:
            self.request.session[LAST_TOUCH_KEYNAME] = timestamp_value
        # else: leave the session without the timestamp key (None case)

        mock_now = datetime(2025, 6, 16, 12, 0, 0)
        mock_datetime.utcnow.return_value = mock_now

        response = self.middleware.process_request(self.request)  # pylint: disable=assignment-from-none

        assert response is None
        assert self.request.session[LAST_TOUCH_KEYNAME] == mock_now.isoformat()
        mock_log.debug.assert_called_once_with("No previous activity timestamp found (first login)")

        mock_monitoring.set_custom_attribute.assert_has_calls([
            call("session_inactivity.first_login", True),
            call("session_inactivity.proceed_with_period_save", True),
        ], any_order=True)

    @ddt.data(
        "invalid-timestamp",     # Invalid ISO format
        "2025-13-01T12:00:00",  # Invalid date
    )
    @override_settings(SESSION_INACTIVITY_TIMEOUT_IN_SECONDS=300)
    @patch("openedx.core.djangoapps.session_inactivity_timeout.middleware.log")
    @patch("openedx.core.djangoapps.session_inactivity_timeout.middleware.monitoring_utils")
    def test_process_request_invalid_timestamp_handling(
        self, invalid_timestamp, mock_monitoring, mock_log
    ):
        self.request.session[LAST_TOUCH_KEYNAME] = invalid_timestamp

        # The middleware should raise an exception when it tries to parse the invalid timestamp
        # in the save delay logic (after already catching it once in the timeout logic)
        with self.assertRaises(ValueError):  # noqa: PT027
            self.middleware.process_request(self.request)

        mock_log.warning.assert_called_once()

        mock_monitoring.set_custom_attribute.assert_any_call("session_inactivity.last_touch_error", ANY)
        mock_monitoring.record_exception.assert_called_once()

    @ddt.data(
        # (timeout_seconds, seconds_elapsed, should_logout)
        (300, 299, False),   # 299 sec < 300 sec timeout, no logout
        (300, 300, False),   # 300 sec = 300 sec timeout, no logout (not exceeded)
        (300, 301, True),    # 301 sec > 300 sec timeout, logout occurs
    )
    @ddt.unpack
    @patch("openedx.core.djangoapps.session_inactivity_timeout.middleware.datetime")
    @patch("openedx.core.djangoapps.session_inactivity_timeout.middleware.monitoring_utils")
    def test_process_request_timeout_behavior(
        self, timeout_seconds, seconds_elapsed, should_logout,
        mock_monitoring, mock_datetime
    ):
        with override_settings(SESSION_INACTIVITY_TIMEOUT_IN_SECONDS=timeout_seconds):
            assert self.request.user.is_authenticated

            last_touch = datetime(2025, 6, 16, 12, 0, 0)
            current_time = last_touch + timedelta(seconds=seconds_elapsed)
            self.request.session[LAST_TOUCH_KEYNAME] = last_touch.isoformat()
            mock_datetime.utcnow.return_value = current_time
            mock_datetime.fromisoformat = datetime.fromisoformat

            response = self.middleware.process_request(self.request)  # pylint: disable=assignment-from-none

            assert response is None

            if should_logout:
                assert LAST_TOUCH_KEYNAME not in self.request.session
                assert not self.request.user.is_authenticated
                mock_monitoring.set_custom_attribute.assert_any_call(
                    "session_inactivity.has_exceeded_timeout_limit", True
                )
            else:
                assert self.request.user.is_authenticated
                assert LAST_TOUCH_KEYNAME in self.request.session
                mock_monitoring.set_custom_attribute.assert_any_call(
                    "session_inactivity.has_exceeded_timeout_limit", False
                )

    @ddt.data(
        # (save_delay_seconds, seconds_elapsed, should_save)
        # Test save delay behavior (with long timeout to avoid logout)
        (900, 899, False),   # 899 sec < 900 sec save delay, no save
        (900, 900, False),   # 900 sec = 900 sec save delay, no save (not exceeded)
        (900, 901, True),    # 901 sec > 900 sec save delay, save occurs
    )
    @ddt.unpack
    @patch("openedx.core.djangoapps.session_inactivity_timeout.middleware.datetime")
    @patch("openedx.core.djangoapps.session_inactivity_timeout.middleware.monitoring_utils")
    def test_process_request_save_behavior(
        self, save_delay_seconds, seconds_elapsed, should_save,
        mock_monitoring, mock_datetime
    ):
        with override_settings(
            SESSION_INACTIVITY_TIMEOUT_IN_SECONDS=3600,  # Long timeout to avoid logout
            SESSION_ACTIVITY_SAVE_DELAY_SECONDS=save_delay_seconds
        ):
            assert self.request.user.is_authenticated

            last_touch = datetime(2025, 6, 16, 12, 0, 0)
            current_time = last_touch + timedelta(seconds=seconds_elapsed)
            self.request.session[LAST_TOUCH_KEYNAME] = last_touch.isoformat()
            mock_datetime.utcnow.return_value = current_time
            mock_datetime.fromisoformat = datetime.fromisoformat

            response = self.middleware.process_request(self.request)  # pylint: disable=assignment-from-none

            assert response is None
            assert self.request.user.is_authenticated
            assert LAST_TOUCH_KEYNAME in self.request.session

            if should_save:
                assert self.request.session[LAST_TOUCH_KEYNAME] == current_time.isoformat()
                mock_monitoring.set_custom_attribute.assert_has_calls([
                    call("session_inactivity.has_exceeded_timeout_limit", False),
                    call("session_inactivity.proceed_with_period_save", True),
                ], any_order=True)
            else:
                # Session should not be saved, timestamp remains the same
                assert self.request.session[LAST_TOUCH_KEYNAME] == last_touch.isoformat()
                mock_monitoring.set_custom_attribute.assert_has_calls([
                    call("session_inactivity.has_exceeded_timeout_limit", False),
                    call("session_inactivity.proceed_with_period_save", False),
                ], any_order=True)

    @ddt.data(
        # (seconds_elapsed, should_save) - testing around default 900 second save delay
        (899, False),   # 899 sec < 900 sec default, no save
        (900, False),   # 900 sec = 900 sec default, no save (not exceeded)
        (901, True),    # 901 sec > 900 sec default, save occurs
    )
    @ddt.unpack
    @override_settings(SESSION_INACTIVITY_TIMEOUT_IN_SECONDS=3600)
    @patch("openedx.core.djangoapps.session_inactivity_timeout.middleware.datetime")
    @patch("openedx.core.djangoapps.session_inactivity_timeout.middleware.monitoring_utils")
    def test_process_request_uses_default_save_frequency(
        self, seconds_elapsed, should_save, mock_monitoring, mock_datetime
    ):
        # Don't set SESSION_ACTIVITY_SAVE_DELAY_SECONDS to test default behavior
        last_touch = datetime(2025, 6, 16, 12, 0, 0)
        current_time = last_touch + timedelta(seconds=seconds_elapsed)
        self.request.session[LAST_TOUCH_KEYNAME] = last_touch.isoformat()
        mock_datetime.utcnow.return_value = current_time
        mock_datetime.fromisoformat = datetime.fromisoformat

        response = self.middleware.process_request(self.request)  # pylint: disable=assignment-from-none

        assert response is None

        if should_save:
            assert self.request.session[LAST_TOUCH_KEYNAME] == current_time.isoformat()
            mock_monitoring.set_custom_attribute.assert_has_calls([
                call("session_inactivity.has_exceeded_timeout_limit", False),
                call("session_inactivity.proceed_with_period_save", True),
            ], any_order=True)
        else:
            assert self.request.session[LAST_TOUCH_KEYNAME] == last_touch.isoformat()
            mock_monitoring.set_custom_attribute.assert_has_calls([
                call("session_inactivity.has_exceeded_timeout_limit", False),
                call("session_inactivity.proceed_with_period_save", False),
            ], any_order=True)
