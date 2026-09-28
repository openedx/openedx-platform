"""
Tests for openedx.core.lib.log_utils.
"""
from unittest.mock import Mock

import ddt
from django.test import SimpleTestCase, override_settings

from openedx.core.lib.log_utils import get_username_or_pii_safe_user_id_for_log


@ddt.ddt
class GetUsernameOrPiiSafeUserIdForLogTest(SimpleTestCase):
    """
    Tests for get_username_or_pii_safe_user_id_for_log.
    """

    def setUp(self):
        super().setUp()
        self.user = Mock(id=42, username='squelchy')

    @ddt.data((True, 42), (False, 'squelchy'))
    @ddt.unpack
    def test_identifier_follows_setting(self, squelch_pii, expected_identifier):
        with override_settings(SQUELCH_PII_IN_LOGS=squelch_pii):
            assert get_username_or_pii_safe_user_id_for_log(self.user) == expected_identifier

    def test_returns_username_when_setting_is_missing(self):
        with self.settings():
            from django.conf import settings  # pylint: disable=import-outside-toplevel
            del settings.SQUELCH_PII_IN_LOGS
            assert get_username_or_pii_safe_user_id_for_log(self.user) == 'squelchy'
