"""
Tests for `generate_and_store_apple_transfer_id` management command
"""

import json
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from requests.models import Response
from social_django.models import UserSocialAuth

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth.appleid import AppleIdAuth
from common.djangoapps.third_party_auth.management.commands import generate_and_store_apple_transfer_ids
from common.djangoapps.third_party_auth.models import AppleMigrationUserIdInfo
from common.djangoapps.third_party_auth.tests.factories import OAuth2ProviderConfigFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
class TestGenerateAndStoreTransferIds(TestCase):
    """
    Test Django management command
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.command = generate_and_store_apple_transfer_ids.Command()

    def setUp(self):
        super().setUp()
        self.slug = 'garfield'
        self.provider_garfield = OAuth2ProviderConfigFactory.create(slug='garfield')
        self.user = UserFactory(username='fleur')
        self.create_social_auth_entry(self.user, self.provider_garfield)

    def create_social_auth_entry(self, user, provider):
        external_id = 'sample_old_apple_id'
        UserSocialAuth.objects.create(
            user=user,
            uid=f'{external_id}',
            provider=provider.slug,
        )

    @mock.patch('common.djangoapps.third_party_auth.management.commands.'
                'generate_and_store_apple_transfer_ids.Command._generate_access_token')
    @mock.patch('common.djangoapps.third_party_auth.management.commands.'
                'generate_and_store_apple_transfer_ids.Command._generate_client_secret')
    def test_access_token_error(self, mock_generate_client_secret, mock_generate_access_token):
        mock_generate_client_secret.return_value = 'sample_client_secret'
        mock_generate_access_token.return_value = None

        error_string = 'Failed to create access token.'
        with self.assertRaisesRegex(CommandError, error_string):
            call_command(self.command, 'sample_team_id')

    @mock.patch('common.djangoapps.third_party_auth.management.commands.'
                'generate_and_store_apple_transfer_ids.Command._generate_access_token')
    @mock.patch('common.djangoapps.third_party_auth.management.commands.'
                'generate_and_store_apple_transfer_ids.Command._generate_client_secret')
    @mock.patch('requests.post')
    def test_transfer_id_created(self, mock_post, mock_generate_client_secret, mock_generate_access_token):
        response = Response()
        response.status_code = 200
        response_content = {'transfer_sub': 'sample_transfer_sub'}
        response._content = json.dumps(response_content).encode('utf-8')  # pylint: disable=protected-access

        mock_post.return_value = response
        mock_generate_client_secret.return_value = 'sample_client_secret'
        mock_generate_access_token.return_value = 'sample_access_token'

        self.assertEqual(0, AppleMigrationUserIdInfo.objects.all().count())

        with mock.patch.object(AppleIdAuth, 'name', self.slug):
            call_command(self.command, 'sample_team_id')

        self.assertTrue(AppleMigrationUserIdInfo.objects.filter(
            old_apple_id='sample_old_apple_id').exists())
        expected_transfer_sub = 'sample_transfer_sub'
        actual_transfer_sub = AppleMigrationUserIdInfo.objects.filter(
            old_apple_id='sample_old_apple_id').first().transfer_id
        self.assertEqual(expected_transfer_sub, actual_transfer_sub)
