"""
Tests for openedx.core.lib.log_utils.
"""
from unittest.mock import Mock

import ddt
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, override_settings
from django.utils.functional import SimpleLazyObject

from openedx.core.lib.log_utils import (
    ANONYMOUS_USER_FOR_LOG,
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
        self.user = Mock(id=42, username='squelchy', email='squelchy@example.com', is_anonymous=False)

    @ddt.data(
        (get_username_or_pii_safe_user_id_for_log, True, '42'),
        (get_username_or_pii_safe_user_id_for_log, False, 'squelchy'),
        (get_email_or_pii_safe_user_id_for_log, True, '42'),
        (get_email_or_pii_safe_user_id_for_log, False, 'squelchy@example.com'),
    )
    @ddt.unpack
    def test_identifier_follows_setting(self, helper, squelch_pii, expected_identifier):
        """The identifier is always a string, so log output has a consistent type."""
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
        (get_username_or_pii_safe_user_id_for_log, True),
        (get_username_or_pii_safe_user_id_for_log, False),
        (get_email_or_pii_safe_user_id_for_log, True),
        (get_email_or_pii_safe_user_id_for_log, False),
    )
    @ddt.unpack
    def test_anonymous_user(self, helper, squelch_pii):
        """An AnonymousUser is logged as '<AnonymousUser>' rather than an empty or missing identifier."""
        with override_settings(SQUELCH_PII_IN_LOGS=squelch_pii):
            assert helper(AnonymousUser()) == ANONYMOUS_USER_FOR_LOG

    @ddt.data(get_username_or_pii_safe_user_id_for_log, get_email_or_pii_safe_user_id_for_log)
    def test_lazy_anonymous_user(self, helper):
        """request.user is a SimpleLazyObject; an anonymous one must still be detected."""
        assert helper(SimpleLazyObject(AnonymousUser)) == ANONYMOUS_USER_FOR_LOG

    @ddt.data(get_username_or_pii_safe_user_id_for_log, get_email_or_pii_safe_user_id_for_log)
    def test_missing_attribute_does_not_raise(self, helper):
        """A user object without the PII attribute logs an empty string instead of raising."""
        user = Mock(spec=['id', 'is_anonymous'], id=42, is_anonymous=False)
        with override_settings(SQUELCH_PII_IN_LOGS=False):
            assert helper(user) == ''
