"""
Tests for verify_student email logging.
"""
from unittest.mock import patch

import ddt
from django.test import TestCase, override_settings

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.verify_student.emails import (
    send_verification_approved_email,
    send_verification_confirmation_email,
)


@ddt.ddt
@patch('lms.djangoapps.verify_student.emails.log')
@patch('lms.djangoapps.verify_student.emails.ace')
class VerificationEmailLogTest(TestCase):
    """
    The verification email logs identify the user by id when SQUELCH_PII_IN_LOGS is enabled.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.context = {'user': self.user}

    def _expected_identifier(self, squelch_pii):
        return str(self.user.id) if squelch_pii else self.user.username

    @ddt.data(
        (True, send_verification_confirmation_email, 'Verification confirmation email sent to user: %r'),
        (False, send_verification_confirmation_email, 'Verification confirmation email sent to user: %r'),
        (True, send_verification_approved_email, 'Verification approved email sent to user: %r'),
        (False, send_verification_approved_email, 'Verification approved email sent to user: %r'),
    )
    @ddt.unpack
    def test_success_log(self, squelch_pii, send_email, message, mock_ace, mock_log):
        with override_settings(SQUELCH_PII_IN_LOGS=squelch_pii):
            assert send_email(self.context) is True

        mock_ace.send.assert_called_once()
        mock_log.info.assert_called_once_with(message, self._expected_identifier(squelch_pii))

    @ddt.data(
        (True, send_verification_confirmation_email, 'Could not send email for verification confirmation to user %s'),
        (False, send_verification_confirmation_email, 'Could not send email for verification confirmation to user %s'),
        (True, send_verification_approved_email, 'Could not send email for verification approved to user %s'),
        (False, send_verification_approved_email, 'Could not send email for verification approved to user %s'),
    )
    @ddt.unpack
    def test_failure_log(self, squelch_pii, send_email, message, mock_ace, mock_log):
        mock_ace.send.side_effect = Exception('ace failure')
        with override_settings(SQUELCH_PII_IN_LOGS=squelch_pii):
            assert send_email(self.context) is False

        mock_log.exception.assert_called_once_with(message, self._expected_identifier(squelch_pii))
