"""
Tests for `update_new_apple_ids_in_social_auth` management command
"""

from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from social_django.models import UserSocialAuth

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth.appleid import AppleIdAuth
from common.djangoapps.third_party_auth.management.commands import update_new_apple_ids_in_social_auth
from common.djangoapps.third_party_auth.models import AppleMigrationUserIdInfo
from common.djangoapps.third_party_auth.tests.factories import OAuth2ProviderConfigFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
class TestGenerateAndStoreAppleIds(TestCase):
    """
    Test Django management command
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.command = update_new_apple_ids_in_social_auth.Command()

    def setUp(self):
        super().setUp()
        self.slug = 'garfield'
        self.provider_garfield = OAuth2ProviderConfigFactory.create(slug='garfield')
        self.user = UserFactory(username='fleur')
        self.create_social_auth_entry(self.user, self.provider_garfield)
        self.create_apple_migration_user_info_entry()

    def create_social_auth_entry(self, user, provider):
        external_id = 'sample_old_apple_id'
        UserSocialAuth.objects.create(
            user=user,
            uid=f'{external_id}',
            provider=provider.slug,
        )

    def create_apple_migration_user_info_entry(self):
        AppleMigrationUserIdInfo.objects.create(
            old_apple_id='sample_old_apple_id',
            transfer_id='sample_transfer_sub',
            new_apple_id='sample_new_apple_id'
        )

    def test_new_apple_id_updated_in_social_auth(self):
        self.assertTrue(UserSocialAuth.objects.filter(uid='sample_old_apple_id', provider=self.slug).exists())
        self.assertFalse(UserSocialAuth.objects.filter(uid='sample_new_apple_id', provider=self.slug).exists())

        with mock.patch.object(AppleIdAuth, 'name', self.slug):
            call_command(self.command)

        self.assertTrue(UserSocialAuth.objects.filter(uid='sample_new_apple_id', provider=self.slug).exists())
        self.assertFalse(UserSocialAuth.objects.filter(uid='sample_old_apple_id', provider=self.slug).exists())
