"""
Shared test utilities for Safe Sessions tests
"""
from contextlib import contextmanager  # lint-amnesty, pylint: disable=wrong-import-order
from unittest.mock import patch  # lint-amnesty, pylint: disable=wrong-import-order

import pytest


class TestSafeSessionsLogMixin:
    """
    Test Mixin class with helpers for testing log method
    calls in the safe sessions middleware.
    """
    @contextmanager
    def assert_logged(self, log_string, log_level='error'):
        """
        Asserts that the logger was called with the given
        log_level and with a regex of the given string.
        """
        with patch('openedx.core.djangoapps.safe_sessions.middleware.log.' + log_level) as mock_log:
            yield
            assert mock_log.called
            self.assertRegex(mock_log.call_args_list[0][0][0], log_string)

    @contextmanager
    def assert_regex_not_logged(self, log_string, log_level):
        """
        Asserts that the logger was not called with the given
        log_level and with a regex of the given string.
        """
        with pytest.raises(AssertionError):
            with self.assert_logged(log_string, log_level=log_level):
                yield

    @contextmanager
    def assert_logged_with_message(self, log_substring, log_level='error'):
        """
        Asserts that the logger with the given log_level was called
        with a substring.
        """
        with patch('openedx.core.djangoapps.safe_sessions.middleware.log.' + log_level) as mock_log:
            yield
            log_messages = [call.args[0] for call in mock_log.call_args_list]
            assert any(log_substring in msg for msg in log_messages), \
                f"Expected to find log substring in one of: {log_messages}"

    @contextmanager
    def assert_not_logged(self):
        """
        Asserts that the logger was not called with either a warning
        or an error.
        """
        with self.assert_no_error_logged():
            with self.assert_no_warning_logged():
                yield

    @contextmanager
    def assert_no_warning_logged(self):
        """
        Asserts that the logger was not called with a warning.
        """
        with patch('openedx.core.djangoapps.safe_sessions.middleware.log.warning') as mock_log:
            yield
            assert not mock_log.called

    @contextmanager
    def assert_no_error_logged(self):
        """
        Asserts that the logger was not called with an error.
        """
        with patch('openedx.core.djangoapps.safe_sessions.middleware.log.error') as mock_log:
            yield
            assert not mock_log.called

    @contextmanager
    def assert_signature_error_logged(self, sig_error_string):
        """
        Asserts that the logger was called when signature
        verification failed on a SafeCookieData object,
        either because of a parse error or a cryptographic
        failure.

        The sig_error_string is the expected additional
        context logged with the error.
        """
        with self.assert_logged(r'SafeCookieData signature error .*|test_session_id|.*: ' + sig_error_string):
            yield

    @contextmanager
    def assert_incorrect_signature_logged(self):
        """
        Asserts that the logger was called when signature
        verification failed on a SafeCookieData object
        due to a cryptographic failure.
        """
        with self.assert_signature_error_logged('Signature .* does not match'):
            yield

    @contextmanager
    def assert_incorrect_user_logged(self):
        """
        Asserts that the logger was called upon finding that
        the SafeCookieData object is not bound to the expected
        user.
        """
        with self.assert_logged(r'SafeCookieData .* is not bound to user'):
            yield

    @contextmanager
    def assert_parse_error(self):
        """
        Asserts that the logger was called when the
        SafeCookieData object could not be parsed successfully.
        """
        with self.assert_logged('SafeCookieData BWC parse error'):
            yield

    @contextmanager
    def assert_invalid_session_id(self):
        """
        Asserts that the logger was called when a
        SafeCookieData was created with a Falsey value for
        the session_id.
        """
        with self.assert_logged('SafeCookieData not created due to invalid value for session_id'):
            yield

    @contextmanager
    def assert_logged_for_request_user_mismatch(self, user_at_request, user_at_response, log_level, request_path,
                                                session_changed):
        """
        Asserts that warning was logged when request.user
        was not equal to user at response
        """
        session_suffix = 'Session changed.' if session_changed else 'Session did not change.'
        with self.assert_logged_with_message(
            (
                "SafeCookieData user at initial request '{}' does not match user at response time: '{}' "
                "for request path '{}'.\n{}"
            ).format(
                user_at_request, user_at_response, request_path, session_suffix
            ),
            log_level=log_level,
        ):
            yield

    @contextmanager
    def assert_logged_for_session_user_mismatch(self, user_at_request, user_in_session, request_path, session_changed):
        """
        Asserts that warning was logged when request.user
        was not equal to user at session
        """
        session_suffix = 'Session changed.' if session_changed else 'Session did not change.'

        with self.assert_logged_with_message(
            (
                "SafeCookieData user at initial request '{}' does not match user in session: '{}' "
                "for request path '{}'.\n{}"
            ).format(
                user_at_request, user_in_session, request_path, session_suffix
            ),
            log_level='warning',
        ):
            yield

    @contextmanager
    def assert_logged_for_both_mismatch(self, user_at_request, user_in_session, user_at_response, request_path,
                                        session_changed):
        """
        Asserts that warning was logged when request.user
        was not equal to user at session
        """
        session_suffix = 'Session changed.' if session_changed else 'Session did not change.'

        with self.assert_logged_with_message(
            (
                "SafeCookieData user at initial request '{}' matches neither user in session: '{}' "
                "nor user at response time: '{}' for request path '{}'.\n{}"
            ).format(
                user_at_request, user_in_session, user_at_response, request_path, session_suffix
            ),
            log_level='warning',
        ):
            yield
