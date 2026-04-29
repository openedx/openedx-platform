"""
Tests for management command backfill_sso_verifications_for_old_account_links
"""

from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from common.djangoapps.third_party_auth.tests.testutil import TestCase
from lms.djangoapps.program_enrollments.management.commands.tests.utils import UserSocialAuthFactory
from lms.djangoapps.verify_student.models import SSOVerification
from lms.djangoapps.verify_student.tests.factories import SSOVerificationFactory


class TestBackfillSSOVerificationsCommand(TestCase):
    """
    Tests for management command for backfilling SSO verification records
    """
    slug = 'test'

    def setUp(self):
        super().setUp()
        self.enable_saml()
        self.provider = self.configure_saml_provider(
            name="Test",
            slug=self.slug,
            enabled=True,
            enable_sso_id_verification=True,
        )
        self.user_social_auth1 = UserSocialAuthFactory(slug=self.slug, provider=self.provider.backend_name)
        self.user_social_auth1.save()
        self.user1 = self.user_social_auth1.user

    def test_fails_without_required_param(self):
        with pytest.raises(CommandError):
            call_command('backfill_sso_verifications_for_old_account_links')

    def test_fails_without_named_provider_config(self):
        with pytest.raises(CommandError):
            call_command('backfill_sso_verifications_for_old_account_links', '--provider-slug', 'gatech')

    def test_sso_updated_single_user(self):
        assert SSOVerification.objects.count() == 0
        call_command('backfill_sso_verifications_for_old_account_links', '--provider-slug', self.provider.provider_id)
        assert SSOVerification.objects.count() > 0
        assert SSOVerification.objects.get().user.id == self.user1.id

    def test_performance(self):
        # TODO
        #self.assertNumQueries(1)
        call_command('backfill_sso_verifications_for_old_account_links', '--provider-slug', self.provider.provider_id)
        #self.assertNumQueries(100)

    def test_signal_called(self):
        with patch('openedx.core.djangoapps.signals.signals.LEARNER_SSO_VERIFIED.send_robust') as mock_signal:
            call_command('backfill_sso_verifications_for_old_account_links', '--provider-slug', self.provider.provider_id)  # pylint: disable=line-too-long
        assert mock_signal.call_count == 1

    def test_fine_with_multiple_verification_records(self):
        """
        Testing there are no issues with excluding learners with multiple sso verifications
        """
        SSOVerificationFactory(
            status='approved',
            user=self.user1,
        )
        SSOVerificationFactory(
            status='approved',
            user=self.user1,
        )
        assert SSOVerification.objects.count() == 2
        call_command('backfill_sso_verifications_for_old_account_links', '--provider-slug', self.provider.provider_id)
        assert SSOVerification.objects.count() == 2
