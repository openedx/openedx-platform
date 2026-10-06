"""
Unit tests for third_party_auth SAML auth providers
"""

from unittest import mock

from django.test import RequestFactory
from django.utils.datastructures import MultiValueDictKeyError
from django.contrib.sessions.middleware import SessionMiddleware
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

    @staticmethod
    def _add_session(request):
        """Attach a Django session to a RequestFactory request."""
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        return request

    @mock.patch('common.djangoapps.third_party_auth.saml.SAMLAuth.auth_complete')
    def test_saml_auth_complete(self, super_auth_complete):
        super_auth_complete.side_effect = MultiValueDictKeyError('RelayState')
        backend = SAMLAuthBackend()
        with self.assertRaises(AuthMissingParameter) as cm:  # noqa: PT027
            backend.auth_complete()

        assert cm.exception.parameter == 'RelayState'

    @mock.patch('common.djangoapps.third_party_auth.saml.get_current_request')
    @mock.patch('common.djangoapps.third_party_auth.saml.SAMLAuth.auth_complete')
    def test_relaystate_splits_and_sets_next_when_safe(self, super_auth_complete, get_current_request_mock):
        """RelayState may include both the IdP slug and a safe `next` destination."""
        rf = RequestFactory()
        request = rf.post(
            '/auth/complete/tpa-saml/',
            data={
                'SAMLResponse': 'ignored',
                'RelayState': 'example-idp|/courses/course-v1:edX+DemoX+Demo_Course/course/',
            },
            HTTP_HOST=self.hostname,
        )
        self._add_session(request)
        get_current_request_mock.return_value = request

        super_auth_complete.return_value = 'ok'
        backend = SAMLAuthBackend()
        assert backend.auth_complete() == 'ok'

        assert request.POST.get('RelayState') == 'example-idp'
        assert request.session.get('next') == '/courses/course-v1:edX+DemoX+Demo_Course/course/'

    @mock.patch('common.djangoapps.third_party_auth.saml.get_current_request')
    @mock.patch('common.djangoapps.third_party_auth.saml.SAMLAuth.auth_complete')
    def test_relaystate_drops_unsafe_next(self, super_auth_complete, get_current_request_mock):
        """If RelayState contains an unsafe `next`, it is ignored but the slug is preserved."""
        rf = RequestFactory()
        request = rf.post(
            '/auth/complete/tpa-saml/',
            data={
                'SAMLResponse': 'ignored',
                'RelayState': 'example-idp|https%3A%2F%2Fevil.example.com%2Fpwn',
            },
            HTTP_HOST=self.hostname,
        )
        self._add_session(request)
        get_current_request_mock.return_value = request

        super_auth_complete.return_value = 'ok'
        backend = SAMLAuthBackend()
        assert backend.auth_complete() == 'ok'

        assert request.POST.get('RelayState') == 'example-idp'
        assert request.session.get('next') is None
