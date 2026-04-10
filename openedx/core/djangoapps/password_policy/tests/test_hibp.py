"""
Test 'have i been pwned' password service
"""


from unittest.mock import Mock, patch

from django.test import TestCase
from edx_toggles.toggles.testutils import override_waffle_switch
from requests.exceptions import ReadTimeout
from testfixtures import LogCapture

from openedx.core.djangoapps.password_policy.hibp import PwnedPasswordsAPI, log
from openedx.core.djangoapps.user_authn.config.waffle import ENABLE_PWNED_PASSWORD_API


class PwnedPasswordsAPITest(TestCase):
    """
    Tests pwned password service
    """
    @override_waffle_switch(ENABLE_PWNED_PASSWORD_API, True)
    @patch('requests.get')
    def test_matched_pwned_passwords(self, mock_get):
        """
        Test that pwned service returns pwned passwords dict
        """
        response_string = "7ecd77ecd7:341\r\n7ecd77ecd77ecd7:12"
        pwned_password = {
            "7ecd77ecd7": 341,
            "7ecd77ecd77ecd7": 12,
        }
        response = Mock()
        response.text = response_string
        mock_get.return_value = response
        response = PwnedPasswordsAPI.range('7ecd7')

        self.assertEqual(response, pwned_password)  # noqa: PT009

    @override_waffle_switch(ENABLE_PWNED_PASSWORD_API, True)
    @patch('requests.get', side_effect=ReadTimeout)
    def test_warning_log_on_timeout(self, mock_get):  # pylint: disable=unused-argument
        """
        Test that captures the warning log on timeout
        """
        password = 'testpassword'
        password_hash_hex = '8BB6118F8FD6935AD0876A3BE34A717D32708FFD'
        with LogCapture(log.name) as log_capture:
            PwnedPasswordsAPI.range(password)
            log_capture.check_present(
                (
                    log.name,
                    'WARNING',
                    'Request timed out for {}'.format(password_hash_hex)  # noqa: UP032
                )
            )
        assert 'Request timed out for {}'.format(password_hash_hex) in log_capture.records[0].getMessage()  # noqa: UP032  # pylint: disable=line-too-long

    def test_provided_string_is_sha1_or_not(self):
        hashed_password = '8BB6118F8FD6935AD0876A3BE34A717D32708FFD'
        self.assertTrue(PwnedPasswordsAPI.is_sha1(hashed_password))  # noqa: PT009

        raw_password = 'testpassword'
        self.assertFalse(PwnedPasswordsAPI.is_sha1(raw_password))  # noqa: PT009
