"""
Unit tests for third_party_auth SAML auth providers
"""

from unittest import mock

from django.utils.datastructures import MultiValueDictKeyError
from social_core.exceptions import AuthMissingParameter

from common.djangoapps.third_party_auth.dummy import DummyBackend
from common.djangoapps.third_party_auth.saml import EdXSAMLIdentityProvider, SAMLAuthBackend, get_saml_idp_class
from common.djangoapps.third_party_auth.tests.data.saml_identity_provider_mock_data import (
    expected_user_details,
    mock_attributes,
    mock_conf,
)
from common.djangoapps.third_party_auth.tests.testutil import SAMLTestCase


class TestEdXSAMLIdentityProvider(SAMLTestCase):
    """
        Test EdXSAMLIdentityProvider.
    """

    @mock.patch('common.djangoapps.third_party_auth.saml.log')
    def test_get_saml_idp_class_with_fake_identifier(self, log_mock):
        error_mock = log_mock.error
        idp_class = get_saml_idp_class('fake_idp_class_option')
        error_mock.assert_called_once_with(
            '[THIRD_PARTY_AUTH] Invalid EdXSAMLIdentityProvider subclass--'
            'using EdXSAMLIdentityProvider base class. Provider: {provider}'.format(provider='fake_idp_class_option')
        )
        assert idp_class is EdXSAMLIdentityProvider

    def test_get_user_details(self):
        """ test get_attr and get_user_details of EdXSAMLIdentityProvider"""
        edx_saml_identity_provider = EdXSAMLIdentityProvider(DummyBackend, 'demo', **mock_conf)
        assert edx_saml_identity_provider.get_user_details(mock_attributes) == expected_user_details


class TestSAMLAuthBackend(SAMLTestCase):
    """ Tests for the SAML backend. """

    @mock.patch('common.djangoapps.third_party_auth.saml.SAMLAuth.auth_complete')
    def test_saml_auth_complete(self, super_auth_complete):
        super_auth_complete.side_effect = MultiValueDictKeyError('RelayState')
        backend = SAMLAuthBackend()
        with self.assertRaises(AuthMissingParameter) as cm:  # noqa: PT027
            backend.auth_complete()

        assert cm.exception.parameter == 'RelayState'
