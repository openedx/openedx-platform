"""
Tests for third_party_auth utility functions.
"""

from unittest import mock
from unittest.mock import MagicMock

import ddt
from lxml import etree

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth.tests.testutil import TestCase
from common.djangoapps.third_party_auth.utils import (
    convert_saml_slug_provider_id,
    get_associated_user_by_email_response,
    get_user_from_email,
    is_enterprise_customer_user,
    is_oauth_provider,
    parse_metadata_xml,
    user_exists,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.features.enterprise_support.tests.factories import (
    EnterpriseCustomerIdentityProviderFactory,
    EnterpriseCustomerUserFactory,
)


@ddt.ddt
@skip_unless_lms
class TestUtils(TestCase):
    """
    Test the utility functions.
    """

    def test_user_exists(self):
        """
        Verify that user_exists function returns correct response.
        """
        # Create users from factory
        UserFactory(username='test_user', email='test_user@example.com')
        assert user_exists({'username': 'test_user', 'email': 'test_user@example.com'})
        assert user_exists({'username': 'test_user'})
        assert user_exists({'email': 'test_user@example.com'})
        assert not user_exists({'username': 'invalid_user'})
        assert user_exists({'username': 'TesT_User'})

    def test_convert_saml_slug_provider_id(self):
        """
        Verify saml provider id/slug map to each other correctly.
        """
        provider_names = {'saml-samltest': 'samltest', 'saml-example': 'example'}
        for provider_id in provider_names:
            # provider_id -> slug
            assert convert_saml_slug_provider_id(provider_id) == provider_names[provider_id]
            # slug -> provider_id
            assert convert_saml_slug_provider_id(provider_names[provider_id]) == provider_id

    def test_get_user(self):
        """
        Match the email and return user if exists.
        """
        # Create users from factory
        UserFactory(username='test_user', email='test_user@example.com')
        assert get_user_from_email({'email': 'test_user@example.com'})
        assert not get_user_from_email({'email': 'invalid@example.com'})

    def test_is_enterprise_customer_user(self):
        """
        Verify that if user is an enterprise learner.
        """
        # Create users from factory

        user = UserFactory(username='test_user', email='test_user@example.com')
        other_user = UserFactory(username='other_user', email='other_user@example.com')
        customer_idp = EnterpriseCustomerIdentityProviderFactory.create(
            provider_id='the-provider',
        )
        customer = customer_idp.enterprise_customer
        EnterpriseCustomerUserFactory.create(
            enterprise_customer=customer,
            user_id=user.id,
        )

        assert is_enterprise_customer_user('the-provider', user)
        assert not is_enterprise_customer_user('the-provider', other_user)

    @ddt.data(
        ('saml-farkle', False),
        ('oa2-fergus', True),
        ('oa2-felicia', True),
    )
    @ddt.unpack
    def test_is_oauth_provider(self, provider_id, oauth_provider):
        """
        Tests if the backend name is that of an auth provider or not
        """
        with mock.patch(
            'common.djangoapps.third_party_auth.utils.provider.Registry.get_from_pipeline'
        ) as get_from_pipeline:
            get_from_pipeline.return_value.provider_id = provider_id

            self.assertEqual(is_oauth_provider('backend_name'), oauth_provider)

    @ddt.data(
        (None, False),
        (None, False),
        ('The Muffin Man', True),
        ('Gingerbread Man', False),
    )
    @ddt.unpack
    def test_get_associated_user_by_email_response(self, user, user_is_active):
        """
        Tests if an association response is returned for a user
        """
        with mock.patch(
            'common.djangoapps.third_party_auth.utils.associate_by_email',
            side_effect=lambda _b, _d, u, *_a, **_k: {'user': u} if u else None,
        ):
            mock_user = MagicMock(return_value=user)
            mock_user.is_active = user_is_active

            association_response, user_is_active_resonse = get_associated_user_by_email_response(
                backend=None, details=None, user=mock_user)

            if association_response:
                self.assertEqual(association_response['user'](), user)
                self.assertEqual(user_is_active_resonse, user_is_active)
            else:
                self.assertIsNone(association_response)
                self.assertFalse(user_is_active_resonse)

    def test_parse_metadata_uses_signing_cert(self):
        entity_id = 'http://testid'
        parser = etree.XMLParser(remove_comments=True)
        xml_text = '''<?xml version="1.0"?>
            <md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" xmlns:ds="http://www.w3.org/2000/09/xmldsig#" entityID="http://testid">
                <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
                    <md:KeyDescriptor use="signing">
                        <ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                            <ds:X509Data>
                            <ds:X509Certificate>abc+hkIuUktxkg=</ds:X509Certificate>
                            </ds:X509Data>
                        </ds:KeyInfo>
                    </md:KeyDescriptor>
                    <md:KeyDescriptor use="encryption">
                        <ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                            <ds:X509Data>
                            <ds:X509Certificate>blachabc+hkIuUktxkg=blaal;skdjf;ksd</ds:X509Certificate>
                            </ds:X509Data>
                        </ds:KeyInfo>
                    </md:KeyDescriptor>
                    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp/SSOService.php"/>
                </md:IDPSSODescriptor>
            </md:EntityDescriptor>
        '''
        xml = etree.fromstring(xml_text, parser)
        public_keys, sso_url, _ = parse_metadata_xml(xml, entity_id)
        assert public_keys == ['abc+hkIuUktxkg=']
        assert sso_url == 'https://idp/SSOService.php'

    def test_parse_metadata_uses_multiple_signing_cert(self):
        entity_id = 'http://testid'
        parser = etree.XMLParser(remove_comments=True)
        xml_text = '''<?xml version="1.0"?>
            <md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" xmlns:ds="http://www.w3.org/2000/09/xmldsig#" entityID="http://testid">
                <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
                    <md:KeyDescriptor use="signing">
                        <ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                            <ds:X509Data>
                            <ds:X509Certificate>abc+hkIuUktxkg=</ds:X509Certificate>
                            </ds:X509Data>
                        </ds:KeyInfo>
                    </md:KeyDescriptor>
                    <md:KeyDescriptor use="signing">
                        <ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                            <ds:X509Data>
                            <ds:X509Certificate>xyz+ayylmao=</ds:X509Certificate>
                            </ds:X509Data>
                        </ds:KeyInfo>
                    </md:KeyDescriptor>
                    <md:KeyDescriptor use="encryption">
                        <ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                            <ds:X509Data>
                            <ds:X509Certificate>blachabc+hkIuUktxkg=blaal;skdjf;ksd</ds:X509Certificate>
                            </ds:X509Data>
                        </ds:KeyInfo>
                    </md:KeyDescriptor>
                    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp/SSOService.php"/>
                </md:IDPSSODescriptor>
            </md:EntityDescriptor>
        '''
        xml = etree.fromstring(xml_text, parser)
        public_keys, sso_url, _ = parse_metadata_xml(xml, entity_id)
        assert public_keys == ['abc+hkIuUktxkg=', 'xyz+ayylmao=']
        assert sso_url == 'https://idp/SSOService.php'

    def test_parse_metadata_with_use_attribute_missing(self):
        entity_id = 'http://testid'
        parser = etree.XMLParser(remove_comments=True)
        xml_text = '''<?xml version="1.0"?>
            <md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" xmlns:ds="http://www.w3.org/2000/09/xmldsig#" entityID="http://testid">
                <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
                    <md:KeyDescriptor>
                        <ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
                            <ds:X509Data>
                            <ds:X509Certificate>abc+hkIuUktxkg=</ds:X509Certificate>
                            </ds:X509Data>
                        </ds:KeyInfo>
                    </md:KeyDescriptor>
                    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp/SSOService.php"/>
                </md:IDPSSODescriptor>
            </md:EntityDescriptor>
        '''
        xml = etree.fromstring(xml_text, parser)
        public_keys, sso_url, _ = parse_metadata_xml(xml, entity_id)
        assert public_keys == ['abc+hkIuUktxkg=']
        assert sso_url == 'https://idp/SSOService.php'
