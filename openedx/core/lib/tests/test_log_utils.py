"""
Tests for openedx.core.lib.log_utils.
"""
from unittest.mock import Mock

from django.test import SimpleTestCase, override_settings

from openedx.core.lib.log_utils import get_username_or_pii_safe_user_id_for_log


class GetUsernameOrPiiSafeUserIdForLogTest(SimpleTestCase):
    """
    Tests for get_username_or_pii_safe_user_id_for_log.
    """

    def setUp(self):
        super().setUp()
        self.user = Mock(id=42, username='squelchy')

    @override_settings(SQUELCH_PII_IN_LOGS=True)
    def test_returns_user_id_when_squelching_pii(self):
        assert get_username_or_pii_safe_user_id_for_log(self.user) == 42

    @override_settings(SQUELCH_PII_IN_LOGS=False)
    def test_returns_username_when_not_squelching_pii(self):
        assert get_username_or_pii_safe_user_id_for_log(self.user) == 'squelchy'

    def test_returns_username_when_setting_is_missing(self):
        with self.settings():
            from django.conf import settings  # pylint: disable=import-outside-toplevel
            del settings.SQUELCH_PII_IN_LOGS
            assert get_username_or_pii_safe_user_id_for_log(self.user) == 'squelchy'
