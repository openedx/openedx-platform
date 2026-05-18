"""
Tests for user_api accounts signals.
"""

import logging
from unittest.mock import patch

from django.test import TestCase
from social_django.models import UserSocialAuth

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.user_api.accounts.signals import get_redacted_social_auth_uid
from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
class RedactSocialAuthPIIOnDeleteSignalTest(TestCase):
    """
    Tests for the redact_social_auth_pii_before_deletion pre_delete signal handler.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(username='testuser', email='testuser@example.com')

    def _create_social_auth(self, uid='user@example.com', extra_data=None):
        if extra_data is None:
            extra_data = {'email': 'user@example.com', 'name': 'Test User'}
        return UserSocialAuth.objects.create(
            user=self.user,
            provider='google-oauth2',
            uid=uid,
            extra_data=extra_data,
        )

    def test_get_redacted_social_auth_uid_format(self):
        """
        Test that get_redacted_social_auth_uid returns the expected string format.

        This is the single source of truth for the redacted uid format.
        """
        assert get_redacted_social_auth_uid(42) == 'redacted-before-delete-42@safe.com'
        assert get_redacted_social_auth_uid(1) == 'redacted-before-delete-1@safe.com'

    @patch('openedx.core.djangoapps.user_api.accounts.signals.redact_and_delete_social_auth')
    def test_signal_warns_and_redacts_when_not_already_redacted(self, mock_redact):
        """
        When a UserSocialAuth is deleted without prior redaction, the signal handler
        should log a warning and call redact_and_delete_social_auth with skip_delete=True.
        """
        social_auth = self._create_social_auth()

        with self.assertLogs(
            'openedx.core.djangoapps.user_api.accounts.signals', level=logging.WARNING
        ) as log_ctx:
            social_auth.delete()

        mock_redact.assert_called_once_with(self.user.id, skip_delete=True)
        assert any('was deleted without first being redacted' in msg for msg in log_ctx.output)

    @patch('openedx.core.djangoapps.user_api.accounts.signals.redact_and_delete_social_auth')
    def test_signal_skips_warning_and_redaction_when_already_redacted(self, mock_redact):
        """
        When a UserSocialAuth is already redacted before deletion, the signal handler
        should not log a warning and should not call redact_and_delete_social_auth.
        """
        social_auth = self._create_social_auth()
        social_auth.uid = get_redacted_social_auth_uid(social_auth.pk)
        social_auth.extra_data = {}
        social_auth.save(update_fields=['uid', 'extra_data'])
        social_auth_id = social_auth.id

        social_auth.delete()

        mock_redact.assert_not_called()
        assert not UserSocialAuth.objects.filter(id=social_auth_id).exists()
