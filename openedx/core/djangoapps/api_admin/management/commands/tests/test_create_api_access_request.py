# pylint: disable=missing-module-docstring

from unittest.mock import patch

import ddt
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.api_admin.models import ApiAccessConfig, ApiAccessRequest
from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
@ddt.ddt
class TestCreateApiAccessRequest(TestCase):
    """ Test create_api_access_request command """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.command = 'create_api_access_request'
        cls.user = UserFactory()

    def assert_models_exist(self, expect_request_exists, expect_config_exists):
        assert ApiAccessRequest.objects.filter(user=self.user).exists() == expect_request_exists
        assert ApiAccessConfig.objects.filter(enabled=True).exists() == expect_config_exists

    @ddt.data(False, True)
    def test_create_api_access_request(self, create_config):
        self.assert_models_exist(False, False)
        call_command(self.command, self.user.username, create_config=create_config)
        self.assert_models_exist(True, create_config)

    @patch('openedx.core.djangoapps.api_admin.models._send_new_pending_email')
    def test_create_api_access_request_signals_disconnected(self, mock_send_new_pending_email):
        self.assert_models_exist(False, False)
        call_command(self.command, self.user.username, create_config=True, disconnect_signals=True)
        self.assert_models_exist(True, True)
        assert not mock_send_new_pending_email.called

    @patch('openedx.core.djangoapps.api_admin.models._send_new_pending_email')
    def test_create_api_access_request_signals_connected(self, mock_send_new_pending_email):
        self.assert_models_exist(False, False)
        call_command(self.command, self.user.username, create_config=True, disconnect_signals=False)
        self.assert_models_exist(True, True)
        assert mock_send_new_pending_email.called

    def test_config_already_exists(self):
        ApiAccessConfig.objects.create(enabled=True)
        self.assert_models_exist(False, True)
        call_command(self.command, self.user.username, create_config=True)
        self.assert_models_exist(True, True)

    def test_user_not_found(self):
        with self.assertRaisesRegex(CommandError, r'User .*? not found'):
            call_command(self.command, 'not-a-user-notfound-nope')

    @patch('openedx.core.djangoapps.api_admin.models.ApiAccessRequest.objects.create')
    def test_api_request_error(self, mocked_method):
        mocked_method.side_effect = Exception()

        self.assert_models_exist(False, False)

        with self.assertRaisesRegex(CommandError, r'Unable to create ApiAccessRequest .*'):
            call_command(self.command, self.user.username)

        self.assert_models_exist(False, False)

    @patch('openedx.core.djangoapps.api_admin.models.send_request_email')
    def test_api_request_permission_denied_error(self, mocked_method):
        """
        When a Permission denied OSError with 'mako_lms' in the message occurs in the post_save receiver,
        the models should still be created and the command should finish without raising.
        """
        mocked_method.side_effect = OSError('Permission denied: something something in /tmp/mako_lms')

        self.assert_models_exist(False, False)

        call_command(self.command, self.user.username, create_config=True)

        self.assert_models_exist(True, True)

    @patch('openedx.core.djangoapps.api_admin.models.ApiAccessRequest.objects.create')
    def test_api_request_other_oserrors_raise(self, mocked_method):
        """
        When some other Permission denied OSError occurs, we should still raise.
        """
        mocked_method.side_effect = OSError('out of disk space')

        self.assert_models_exist(False, False)

        with self.assertRaisesRegex(CommandError, 'out of disk space'):
            call_command(self.command, self.user.username)

        self.assert_models_exist(False, False)

    @patch('openedx.core.djangoapps.api_admin.models.ApiAccessConfig.objects.get_or_create')
    def test_api_config_error(self, mocked_method):
        mocked_method.side_effect = Exception()
        self.assert_models_exist(False, False)

        with self.assertRaisesRegex(CommandError, r'Unable to create ApiAccessConfig\. .*'):
            call_command(self.command, self.user.username, create_config=True)

        self.assert_models_exist(False, False)

    def test_optional_fields(self):
        self.assert_models_exist(False, False)

        call_command(
            self.command,
            self.user.username,
            status='approved',
            reason='whatever',
            website='test-site.edx.horse'
        )
        self.assert_models_exist(True, False)
        request = ApiAccessRequest.objects.get(user=self.user)
        assert request.status == 'approved'
        assert request.reason == 'whatever'
        assert request.website == 'test-site.edx.horse'

    def test_default_values(self):
        call_command(self.command, self.user.username)
        request = ApiAccessRequest.objects.get(user=self.user)
        assert request.site == Site.objects.get_current()
        assert request.status == ApiAccessRequest.APPROVED
