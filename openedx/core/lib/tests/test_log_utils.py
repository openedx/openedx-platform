"""
Tests for openedx.core.lib.log_utils.
"""
from unittest.mock import Mock

import ddt
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, override_settings

from openedx.core.lib.log_utils import (
    get_email_or_pii_safe_user_id_for_log,
    get_username_or_pii_safe_user_id_for_log,
)


@ddt.ddt
class PiiSafeUserIdForLogTest(SimpleTestCase):
    """
    Tests for get_username_or_pii_safe_user_id_for_log and get_email_or_pii_safe_user_id_for_log.
    """

    def setUp(self):
        super().setUp()
        self.user = Mock(id=42, username='squelchy', email='squelchy@example.com')

    @ddt.data(
        (get_username_or_pii_safe_user_id_for_log, True, 42),
        (get_username_or_pii_safe_user_id_for_log, False, 'squelchy'),
        (get_email_or_pii_safe_user_id_for_log, True, 42),
        (get_email_or_pii_safe_user_id_for_log, False, 'squelchy@example.com'),
    )
    @ddt.unpack
    def test_identifier_follows_setting(self, helper, squelch_pii, expected_identifier):
        with override_settings(SQUELCH_PII_IN_LOGS=squelch_pii):
            assert helper(self.user) == expected_identifier

    @ddt.data(
        (get_username_or_pii_safe_user_id_for_log, 'squelchy'),
        (get_email_or_pii_safe_user_id_for_log, 'squelchy@example.com'),
    )
    @ddt.unpack
    def test_returns_pii_when_setting_is_missing(self, helper, expected_identifier):
        with self.settings():
            from django.conf import settings  # pylint: disable=import-outside-toplevel
            del settings.SQUELCH_PII_IN_LOGS
            assert helper(self.user) == expected_identifier

    @ddt.data(
        (get_username_or_pii_safe_user_id_for_log, True, None),
        (get_username_or_pii_safe_user_id_for_log, False, ''),
        (get_email_or_pii_safe_user_id_for_log, True, None),
        (get_email_or_pii_safe_user_id_for_log, False, ''),
    )
    @ddt.unpack
    def test_anonymous_user(self, helper, squelch_pii, expected_identifier):
        """AnonymousUser has no id and no email; the helpers must not raise from inside a log call."""
        with override_settings(SQUELCH_PII_IN_LOGS=squelch_pii):
            assert helper(AnonymousUser()) == expected_identifier
