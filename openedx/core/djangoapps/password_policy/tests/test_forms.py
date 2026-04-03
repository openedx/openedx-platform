"""
Test password policy forms
"""
from unittest import mock

import pytest
from django.forms import ValidationError
from django.test import TestCase
from django.test.utils import override_settings

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.password_policy.compliance import (
    NonCompliantPasswordException,
    NonCompliantPasswordWarning,
)
from openedx.core.djangoapps.password_policy.forms import PasswordPolicyAwareAdminAuthForm


class PasswordPolicyAwareAdminAuthFormTests(TestCase):
    """
    Tests the custom form for enforcing password policy rules
    """
    def setUp(self):
        super().setUp()
        self.auth_form = PasswordPolicyAwareAdminAuthForm()
        self.user = UserFactory.create(username='test_user', password='test_password', is_staff=True)
        self.auth_form.cleaned_data = {
            'username': 'test_user',
            'password': 'test_password'
        }

    @override_settings(PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG={'ENFORCE_COMPLIANCE_ON_LOGIN': False})
    def test_auth_form_policy_disabled(self):
        """
        Verify that the username and password are returned when compliance is disabled
        """
        cleaned_data = self.auth_form.clean()
        assert cleaned_data.get('username') == 'test_user'
        assert cleaned_data.get('password'), 'test_password'

    @override_settings(PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG={'ENFORCE_COMPLIANCE_ON_LOGIN': True})
    def test_auth_form_policy_enabled(self):
        """
        Verify that the username and password are returned when compliance is enabled
        """
        with mock.patch(
                'openedx.core.djangoapps.password_policy.forms.password_policy_compliance.enforce_compliance_on_login'
        ) as mock_enforce_compliance_on_login:
            mock_enforce_compliance_on_login.return_value = True
            cleaned_data = self.auth_form.clean()
        assert cleaned_data.get('username') == self.user.username
        assert cleaned_data.get('password'), self.user.password

    @override_settings(PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG={'ENFORCE_COMPLIANCE_ON_LOGIN': True})
    def test_auth_form_policy_enabled_with_warning(self):
        """
        Verify that the username and password are returned when compliance is
        enabled despite a NonCompliantPasswordWarning being thrown
        """
        # Need to mock messages here as it will fail due to a lack of requests on this unit test
        with mock.patch('openedx.core.djangoapps.password_policy.forms.messages') as mock_messages:
            mock_messages.return_value = True
            with mock.patch(
                'openedx.core.djangoapps.password_policy.forms.password_policy_compliance.enforce_compliance_on_login'
            ) as mock_enforce_compliance_on_login:
                mock_enforce_compliance_on_login.side_effect = NonCompliantPasswordWarning('Test warning')
                cleaned_data = self.auth_form.clean()
            assert cleaned_data.get('username') == self.user.username
            assert cleaned_data.get('password')

    @override_settings(PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG={'ENFORCE_COMPLIANCE_ON_LOGIN': True})
    def test_auth_form_policy_enabled_with_exception(self):
        """
        Verify that an exception is raised when enforce_compliance_on_login throws a NonCompliantPasswordException
        """
        with mock.patch(
                'openedx.core.djangoapps.password_policy.forms.password_policy_compliance.enforce_compliance_on_login'
        ) as mock_enforce_compliance_on_login:
            mock_enforce_compliance_on_login.side_effect = NonCompliantPasswordException('Test exception')
            pytest.raises(ValidationError, self.auth_form.clean)
