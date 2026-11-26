"""
Tests for third_party_auth utility functions.
"""

import io
import socket
from unittest import mock
from unittest.mock import MagicMock

import ddt
import pytest
from django.test import override_settings
from lxml import etree
from requests.models import Response

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth.models import SAMLProviderData
from common.djangoapps.third_party_auth.tests.testutil import TestCase, mock_saml_metadata_dns
from common.djangoapps.third_party_auth.utils import (
    SAML_METADATA_MAX_REDIRECTS,
    SAMLMetadataURLError,
    create_or_update_bulk_saml_provider_data,
    fetch_saml_metadata_response,
    get_associated_user_by_email_response,
    get_user_from_email,
    is_oauth_provider,
    parse_metadata_xml,
    user_exists,
    validate_saml_metadata_url,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms


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

    def test_get_user(self):
        """
        Match the email and return user if exists.
        """
        # Create users from factory
        UserFactory(username='test_user', email='test_user@example.com')
        assert get_user_from_email({'email': 'test_user@example.com'})
        assert not get_user_from_email({'email': 'invalid@example.com'})

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

            self.assertEqual(is_oauth_provider('backend_name'), oauth_provider)  # noqa: PT009

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
                self.assertEqual(association_response['user'](), user)  # noqa: PT009
                self.assertEqual(user_is_active_resonse, user_is_active)  # noqa: PT009
            else:
                self.assertIsNone(association_response)  # noqa: PT009
                self.assertFalse(user_is_active_resonse)  # noqa: PT009

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


@skip_unless_lms
class TestCreateOrUpdateBulkSAMLProviderData(TestCase):
    """Tests for create_or_update_bulk_saml_provider_data."""

    def test_creates_new_record(self):
        result = create_or_update_bulk_saml_provider_data(
            'http://entity1', ['pubkey1'],
            'https://sso.example.com', None,
        )
        assert result is True
        assert SAMLProviderData.objects.filter(
            entity_id='http://entity1', public_key='pubkey1',
        ).exists()

    def test_updates_existing_record(self):
        create_or_update_bulk_saml_provider_data(
            'http://entity1', ['pubkey1'],
            'https://sso.example.com', None,
        )
        result = create_or_update_bulk_saml_provider_data(
            'http://entity1', ['pubkey1'],
            'https://sso2.example.com', None,
        )
        assert result is False
        data = SAMLProviderData.objects.get(
            entity_id='http://entity1', public_key='pubkey1',
        )
        assert data.sso_url == 'https://sso2.example.com'

    def test_bulk_updates_multiple_existing(self):
        from django.utils.timezone import now
        SAMLProviderData.objects.create(
            entity_id='http://entity1',
            public_key='pubkey1',
            sso_url='https://old.example.com',
            fetched_at=now(),
        )
        SAMLProviderData.objects.create(
            entity_id='http://entity1',
            public_key='pubkey1',
            sso_url='https://old.example.com',
            fetched_at=now(),
        )
        result = create_or_update_bulk_saml_provider_data(
            'http://entity1', ['pubkey1'],
            'https://new.example.com', None,
        )
        assert result is True
        qs = SAMLProviderData.objects.filter(
            entity_id='http://entity1', public_key='pubkey1',
        )
        for obj in qs:
            assert obj.sso_url == 'https://new.example.com'

    def test_multiple_keys(self):
        result = create_or_update_bulk_saml_provider_data(
            'http://entity1', ['key1', 'key2'],
            'https://sso.example.com', None,
        )
        assert result is True
        count = SAMLProviderData.objects.filter(
            entity_id='http://entity1',
        ).count()
        assert count == 2


@ddt.ddt
@skip_unless_lms
class TestValidateSAMLMetadataURL(TestCase):
    """Tests for validate_saml_metadata_url."""

    @ddt.data(
        'https://idp.example.com/metadata',
        'https://1.1.1.1/metadata',
    )
    def test_valid_urls_pass(self, url):
        with mock_saml_metadata_dns():
            validate_saml_metadata_url(url)  # should not raise

    @ddt.data(
        ('http://idp.example.com/metadata', 'must use HTTPS'),
        ('ftp://idp.example.com/metadata', 'must use HTTPS'),
        ('https://', 'no hostname'),
    )
    @ddt.unpack
    def test_invalid_scheme_or_missing_hostname(self, url, expected_fragment):
        with pytest.raises(SAMLMetadataURLError, match=expected_fragment):
            validate_saml_metadata_url(url)

    @ddt.data(
        'https://127.0.0.1/metadata',       # IPv4 loopback
        'https://[::1]/metadata',            # IPv6 loopback
        'https://169.254.169.254/latest',    # AWS metadata endpoint
        'https://169.254.0.1/metadata',      # other link-local
        'https://[fe80::1]/metadata',        # IPv6 link-local
        'https://240.0.0.1/metadata',        # reserved (Class E)
    )
    def test_always_blocked_regardless_of_setting(self, url):
        for allow_private in (False, True):
            with override_settings(SAML_METADATA_URL_ALLOW_PRIVATE_IPS=allow_private):
                with pytest.raises(SAMLMetadataURLError):
                    validate_saml_metadata_url(url)

    @ddt.data(
        'https://10.0.0.1/metadata',
        'https://172.16.0.1/metadata',
        'https://192.168.1.1/metadata',
        'https://[fc00::1]/metadata',        # IPv6 unique local
    )
    def test_private_ip_blocked_by_default(self, url):
        with pytest.raises(SAMLMetadataURLError):
            validate_saml_metadata_url(url)

    @ddt.data(
        'https://10.0.0.1/metadata',
        'https://172.16.0.1/metadata',
        'https://192.168.1.1/metadata',
        'https://[fc00::1]/metadata',        # IPv6 unique local
    )
    @override_settings(SAML_METADATA_URL_ALLOW_PRIVATE_IPS=True)
    def test_private_ip_allowed_when_setting_enabled(self, url):
        validate_saml_metadata_url(url)  # should not raise

    @ddt.data(
        'https://0.0.0.0/metadata',                    # unspecified
        'https://224.0.0.1/metadata',                  # multicast
        'https://100.100.100.200/metadata',            # shared address space (Alibaba Cloud metadata)
        'https://[fd00:ec2::254]/metadata',            # AWS metadata endpoint over IPv6
        'https://[::ffff:169.254.169.254]/metadata',   # IPv4-mapped AWS metadata endpoint
    )
    def test_more_ip_literals_always_blocked(self, url):
        for allow_private in (False, True):
            with override_settings(SAML_METADATA_URL_ALLOW_PRIVATE_IPS=allow_private):
                with pytest.raises(SAMLMetadataURLError):
                    validate_saml_metadata_url(url)

    @ddt.data(
        '169.254.169.254',
        '127.0.0.1',
        '::1',
        'fd00:ec2::254',
        '0.0.0.0',
        '100.100.100.200',
        '::ffff:169.254.169.254',
        'fe80::1%eth0',
    )
    def test_hostname_resolving_to_forbidden_address_blocked(self, address):
        for allow_private in (False, True):
            with override_settings(SAML_METADATA_URL_ALLOW_PRIVATE_IPS=allow_private):
                with mock_saml_metadata_dns(address):
                    with pytest.raises(SAMLMetadataURLError, match='forbidden|publicly routable'):
                        validate_saml_metadata_url('https://idp.example.com/metadata')

    @ddt.data('10.0.0.5', '192.168.1.1', 'fc00::1')
    def test_hostname_resolving_to_private_address(self, address):
        with mock_saml_metadata_dns(address):
            with pytest.raises(SAMLMetadataURLError, match='private IP address'):
                validate_saml_metadata_url('https://idp.example.com/metadata')
            with override_settings(SAML_METADATA_URL_ALLOW_PRIVATE_IPS=True):
                validate_saml_metadata_url('https://idp.example.com/metadata')  # should not raise

    def test_every_resolved_address_is_checked(self):
        with mock_saml_metadata_dns('93.184.215.14', '169.254.169.254'):
            with pytest.raises(SAMLMetadataURLError, match='forbidden'):
                validate_saml_metadata_url('https://idp.example.com/metadata')

    def test_unresolvable_hostname_blocked(self):
        with mock.patch(
            'common.djangoapps.third_party_auth.utils.getaddrinfo',
            side_effect=socket.gaierror('Name or service not known'),
        ):
            with pytest.raises(SAMLMetadataURLError, match='could not be resolved'):
                validate_saml_metadata_url('https://idp.example.com/metadata')


def _metadata_response(status_code=200, location=None):
    """Build a requests Response, optionally redirecting to `location`."""
    response = Response()
    response.status_code = status_code
    response._content = b'<EntityDescriptor/>'  # pylint: disable=protected-access
    response.raw = io.BytesIO()
    if location is not None:
        response.headers['Location'] = location
    return response


@skip_unless_lms
@mock.patch('common.djangoapps.third_party_auth.utils.requests.get')
class TestFetchSAMLMetadataResponse(TestCase):
    """Tests for fetch_saml_metadata_response."""

    def setUp(self):
        super().setUp()
        dns_patch = mock_saml_metadata_dns()
        dns_patch.start()
        self.addCleanup(dns_patch.stop)

    def test_returns_response_without_following_redirects_itself(self, mock_get):
        mock_get.return_value = _metadata_response()
        response = fetch_saml_metadata_response('https://idp.example.com/metadata')
        assert response.status_code == 200
        mock_get.assert_called_once_with(
            'https://idp.example.com/metadata', verify=True, timeout=30, allow_redirects=False,
        )

    def test_follows_valid_redirects(self, mock_get):
        mock_get.side_effect = [
            _metadata_response(302, '/v2/metadata'),
            _metadata_response(301, 'https://cdn.example.com/metadata.xml'),
            _metadata_response(),
        ]
        response = fetch_saml_metadata_response('https://idp.example.com/metadata')
        assert response.status_code == 200
        assert [call.args[0] for call in mock_get.call_args_list] == [
            'https://idp.example.com/metadata',
            'https://idp.example.com/v2/metadata',
            'https://cdn.example.com/metadata.xml',
        ]

    def test_redirect_to_unsafe_url_is_not_fetched(self, mock_get):
        for location in (
            'http://169.254.169.254/latest/meta-data/',
            'http://internal.example.com/',
            'https://169.254.169.254/latest/meta-data/',
            '//169.254.169.254/latest/meta-data/',
            'https://10.0.0.5/admin',
        ):
            mock_get.reset_mock()
            mock_get.side_effect = [_metadata_response(302, location)]
            with pytest.raises(SAMLMetadataURLError):
                fetch_saml_metadata_response('https://idp.example.com/metadata')
            assert mock_get.call_count == 1

    def test_redirect_to_hostname_resolving_to_internal_address_is_not_fetched(self, mock_get):
        mock_get.side_effect = [_metadata_response(302, 'https://imds.example.com/latest/meta-data/')]
        addresses = {'idp.example.com': '93.184.215.14', 'imds.example.com': '169.254.169.254'}

        def fake_getaddrinfo(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (addresses[host], port))]

        with mock.patch('common.djangoapps.third_party_auth.utils.getaddrinfo', side_effect=fake_getaddrinfo):
            with pytest.raises(SAMLMetadataURLError, match='forbidden'):
                fetch_saml_metadata_response('https://idp.example.com/metadata')
        assert mock_get.call_count == 1

    def test_too_many_redirects(self, mock_get):
        mock_get.side_effect = [
            _metadata_response(302, f'https://idp.example.com/{hop}') for hop in range(SAML_METADATA_MAX_REDIRECTS + 1)
        ]
        with pytest.raises(SAMLMetadataURLError, match='redirected more than'):
            fetch_saml_metadata_response('https://idp.example.com/metadata')
        assert mock_get.call_count == SAML_METADATA_MAX_REDIRECTS + 1
