"""
Test the views served by third_party_auth.
"""


import unittest
from unittest.mock import patch

import ddt
import pytest
from django.conf import settings
from django.test import TestCase, override_settings
from django.test.client import RequestFactory
from django.urls import reverse
from lxml import etree
from onelogin.saml2.errors import OneLogin_Saml2_Error

from common.djangoapps.student.models import Registration
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth import pipeline

# Define some XML namespaces:
from common.djangoapps.third_party_auth.utils import SAML_XML_NS
from common.djangoapps.third_party_auth.views import inactive_user_view

from .testutil import AUTH_FEATURE_ENABLED, AUTH_FEATURES_KEY, SAMLTestCase

XMLDSIG_XML_NS = 'http://www.w3.org/2000/09/xmldsig#'


@unittest.skipUnless(AUTH_FEATURE_ENABLED, AUTH_FEATURES_KEY + ' not enabled')
@ddt.ddt
class SAMLMetadataTest(SAMLTestCase):
    """
    Test the SAML metadata view
    """
    METADATA_URL = '/auth/saml/metadata.xml'

    def test_saml_disabled(self):
        """ When SAML is not enabled, the metadata view should return 404 """
        self.enable_saml(enabled=False)
        response = self.client.get(self.METADATA_URL)
        assert response.status_code == 404

    def test_metadata(self):
        self.enable_saml()
        doc = self._fetch_metadata()
        # Check the ACS URL:
        acs_node = doc.find(".//{}".format(etree.QName(SAML_XML_NS, 'AssertionConsumerService')))
        assert acs_node is not None
        assert acs_node.attrib['Location'] == 'http://example.none/auth/complete/tpa-saml/'

    def test_default_contact_info(self):
        self.enable_saml()
        self.check_metadata_contacts(
            xml=self._fetch_metadata(),
            tech_name=f"{settings.PLATFORM_NAME} Support",
            tech_email="technical@example.com",
            support_name=f"{settings.PLATFORM_NAME} Support",
            support_email="technical@example.com"
        )

    def test_custom_contact_info(self):
        self.enable_saml(
            other_config_str=(
                '{'
                '"TECHNICAL_CONTACT": {"givenName": "Jane Tech", "emailAddress": "jane@example.com"},'
                '"SUPPORT_CONTACT": {"givenName": "Joe Support", "emailAddress": "joe@example.com"}'
                '}'
            )
        )
        self.check_metadata_contacts(
            xml=self._fetch_metadata(),
            tech_name="Jane Tech",
            tech_email="jane@example.com",
            support_name="Joe Support",
            support_email="joe@example.com"
        )

    @ddt.data(
        # Test two slightly different key pair export formats
        ('saml_key', 'MIICsDCCAhmgAw'),
        ('saml_key_alt', 'MIICWDCCAcGgAw'),
    )
    @ddt.unpack
    def test_signed_metadata(self, key_name, pub_key_starts_with):
        self.enable_saml(
            private_key=self._get_private_key(key_name),
            public_key=self._get_public_key(key_name),
            other_config_str='{"SECURITY_CONFIG": {"signMetadata": true} }',
        )
        self._validate_signed_metadata(pub_key_starts_with=pub_key_starts_with)

    def test_secure_key_configuration(self):
        """ Test that the SAML private key can be stored in Django settings and not the DB """
        self.enable_saml(
            public_key='',
            private_key='',
            other_config_str='{"SECURITY_CONFIG": {"signMetadata": true} }',
        )
        with pytest.raises(OneLogin_Saml2_Error):
            self._fetch_metadata()  # OneLogin_Saml2_Error: Cannot sign metadata: missing SP private key.
        with self.settings(
            SOCIAL_AUTH_SAML_SP_PRIVATE_KEY=self._get_private_key('saml_key'),
            SOCIAL_AUTH_SAML_SP_PUBLIC_CERT=self._get_public_key('saml_key'),
        ):
            self._validate_signed_metadata()

    def _validate_signed_metadata(self, pub_key_starts_with='MIICsDCCAhmgAw'):
        """ Fetch the SAML metadata and do some validation """
        doc = self._fetch_metadata()
        sig_node = doc.find(".//{}".format(etree.QName(XMLDSIG_XML_NS, 'SignatureValue')))
        assert sig_node is not None
        # Check that the right public key was used:
        pub_key_node = doc.find(".//{}".format(etree.QName(XMLDSIG_XML_NS, 'X509Certificate')))
        assert pub_key_node is not None
        assert pub_key_starts_with in pub_key_node.text

    def _fetch_metadata(self):
        """ Fetch and parse the metadata XML at self.METADATA_URL """
        response = self.client.get(self.METADATA_URL)
        assert response.status_code == 200
        assert response['Content-Type'] == 'text/xml'
        # The result should be valid XML:
        try:
            metadata_doc = etree.fromstring(response.content)
        except etree.LxmlError:
            self.fail('SAML metadata must be valid XML')
        assert metadata_doc.tag == etree.QName(SAML_XML_NS, 'EntityDescriptor')
        return metadata_doc

    def check_metadata_contacts(self, xml, tech_name, tech_email, support_name, support_email):
        """ Validate that the contact info in the metadata has the expected values """
        technical_node = xml.find(".//{}[@contactType='technical']".format(etree.QName(SAML_XML_NS, 'ContactPerson')))
        assert technical_node is not None
        tech_name_node = technical_node.find(etree.QName(SAML_XML_NS, 'GivenName'))
        assert tech_name_node.text == tech_name
        tech_email_node = technical_node.find(etree.QName(SAML_XML_NS, 'EmailAddress'))
        assert tech_email_node.text == tech_email

        support_node = xml.find(".//{}[@contactType='support']".format(etree.QName(SAML_XML_NS, 'ContactPerson')))
        assert support_node is not None
        support_name_node = support_node.find(etree.QName(SAML_XML_NS, 'GivenName'))
        assert support_name_node.text == support_name
        support_email_node = support_node.find(etree.QName(SAML_XML_NS, 'EmailAddress'))
        assert support_email_node.text == support_email


@unittest.skipUnless(AUTH_FEATURE_ENABLED, AUTH_FEATURES_KEY + ' not enabled')
class SAMLAuthTest(SAMLTestCase):
    """
    Test the SAML auth views
    """
    LOGIN_URL = '/auth/login/tpa-saml/'

    def test_login_without_idp(self):
        """ Accessing the login endpoint without an idp query param should return 302 """
        self.enable_saml()
        response = self.client.get(self.LOGIN_URL)
        assert response.status_code == 302

    def test_login_disabled(self):
        """ When SAML is not enabled, the login view should return 404 """
        self.enable_saml(enabled=False)
        response = self.client.get(self.LOGIN_URL)
        assert response.status_code == 404


@unittest.skipUnless(AUTH_FEATURE_ENABLED, AUTH_FEATURES_KEY + ' not enabled')
class IdPRedirectViewTest(SAMLTestCase):
    """
        Test IdPRedirectView.
    """

    def setUp(self):
        super().setUp()

        self.enable_saml()
        self.configure_saml_provider(
            name="Test",
            slug="test",
            enabled=True,
        )

    def test_with_valid_provider_slug(self):
        endpoint_url = self.get_idp_redirect_url('saml-test')
        expected_url = pipeline.get_login_url('saml-test', pipeline.AUTH_ENTRY_LOGIN, reverse('dashboard'))

        response = self.client.get(endpoint_url)

        assert response.status_code == 302
        assert response.url == expected_url

    def test_with_invalid_provider_slug(self):
        endpoint_url = self.get_idp_redirect_url('saml-test-invalid')

        response = self.client.get(endpoint_url)

        assert response.status_code == 404

    @staticmethod
    def get_idp_redirect_url(provider_slug, next_destination=None):
        return '{idp_redirect_url}?{next_destination}'.format(
            idp_redirect_url=reverse('idp_redirect', kwargs={'provider_slug': provider_slug}),
            next_destination=next_destination,
        )


@unittest.skipUnless(AUTH_FEATURE_ENABLED, AUTH_FEATURES_KEY + ' not enabled')
class InactiveUserViewTests(TestCase):
    """Test inactive user view """
    @patch('common.djangoapps.third_party_auth.views.redirect')
    @override_settings(LOGIN_REDIRECT_WHITELIST=['courses.edx.org'])
    def test_inactive_user_view_allows_valid_redirect(self, mock_redirect):
        inactive_user = UserFactory(is_active=False)
        Registration().register(inactive_user)
        request = RequestFactory().get(settings.SOCIAL_AUTH_INACTIVE_USER_URL, {'next': 'https://courses.edx.org'})
        request.user = inactive_user
        with patch('common.djangoapps.edxmako.request_context.get_current_request', return_value=request):
            with patch('common.djangoapps.third_party_auth.pipeline.running', return_value=False):
                inactive_user_view(request)
                mock_redirect.assert_called_with('https://courses.edx.org')

    @patch('common.djangoapps.third_party_auth.views.redirect')
    def test_inactive_user_view_prevents_invalid_redirect(self, mock_redirect):
        inactive_user = UserFactory(is_active=False)
        Registration().register(inactive_user)
        request = RequestFactory().get(settings.SOCIAL_AUTH_INACTIVE_USER_URL, {'next': 'https://evil.com'})
        request.user = inactive_user
        with patch('common.djangoapps.edxmako.request_context.get_current_request', return_value=request):
            with patch('common.djangoapps.third_party_auth.pipeline.running', return_value=False):
                inactive_user_view(request)
                mock_redirect.assert_called_with('dashboard')

    @patch('common.djangoapps.third_party_auth.views.redirect')
    def test_inactive_user_view_redirects_back_to_host(self, mock_redirect):
        inactive_user = UserFactory(is_active=False)
        Registration().register(inactive_user)
        request = RequestFactory().get(settings.SOCIAL_AUTH_INACTIVE_USER_URL, {'next': 'https://myedxhost.com'},
                                       HTTP_HOST='myedxhost.com')
        request.user = inactive_user
        with patch('common.djangoapps.edxmako.request_context.get_current_request', return_value=request):
            with patch('common.djangoapps.third_party_auth.pipeline.running', return_value=False):
                inactive_user_view(request)
                mock_redirect.assert_called_with('https://myedxhost.com')

    @patch('common.djangoapps.third_party_auth.views.redirect')
    @override_settings(LOGIN_REDIRECT_WHITELIST=['courses.edx.org'])
    def test_inactive_user_view_does_not_redirect_https_to_http(self, mock_redirect):
        inactive_user = UserFactory(is_active=False)
        Registration().register(inactive_user)
        request = RequestFactory().get(settings.SOCIAL_AUTH_INACTIVE_USER_URL, {'next': 'http://courses.edx.org'},
                                       secure=True)
        request.user = inactive_user
        with patch('common.djangoapps.edxmako.request_context.get_current_request', return_value=request):
            with patch('common.djangoapps.third_party_auth.pipeline.running', return_value=False):
                inactive_user_view(request)
                mock_redirect.assert_called_with('dashboard')
