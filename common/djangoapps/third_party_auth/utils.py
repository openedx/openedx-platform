"""
Utility functions for third_party_auth
"""

import datetime
import ipaddress
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import dateutil.parser
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.utils.timezone import now
from lxml import etree
from onelogin.saml2.utils import OneLogin_Saml2_Utils
from social_core.pipeline.social_auth import associate_by_email

from common.djangoapps.student.models import email_exists_or_retired, username_exists_or_retired
from common.djangoapps.third_party_auth.models import OAuth2ProviderConfig, SAMLProviderData
from openedx.core.djangolib.markup import Text

from . import provider

SAML_XML_NS = 'urn:oasis:names:tc:SAML:2.0:metadata'  # The SAML Metadata XML namespace


class MetadataParseError(Exception):
    """ An error occurred while parsing the SAML metadata from an IdP """
    pass  # pylint: disable=unnecessary-pass


class SAMLMetadataURLError(Exception):
    """ A SAML metadata URL failed security validation """
    pass  # pylint: disable=unnecessary-pass


def validate_saml_metadata_url(url):
    """
    Validate that a SAML metadata URL is safe to fetch.

    Enforces HTTPS and blocks requests to loopback, link-local, and reserved IP
    addresses. Link-local specifically covers cloud instance metadata endpoints
    (169.254.0.0/16, e.g. the AWS metadata service at 169.254.169.254).
    Reserved addresses (e.g. 240.0.0.0/4) are IETF-assigned ranges that are
    never routable on real networks.

    Private IP ranges (RFC 1918: 10.x, 172.16.x, 192.168.x) are also blocked by
    default, since most Open edX deployments fetch SAML metadata from public IdPs.
    Operators running in a private network where the SAML IdP has a private IP can
    opt out by setting SAML_METADATA_URL_ALLOW_PRIVATE_IPS = True in Django settings.

    Limitation: IP address checks only apply to literal IPs in the URL. Hostname-
    based URLs are not validated against the IP blocklists. Operators are encouraged
    to complement this with network-level egress filtering that blocks outbound
    connections from the Open edX server to link-local (169.254.0.0/16) and RFC
    1918 private address ranges.

    Raises SAMLMetadataURLError if the URL fails validation.
    """
    parsed = urlparse(url)

    if parsed.scheme != 'https':
        raise SAMLMetadataURLError(
            f"SAML metadata URL must use HTTPS, got scheme: {parsed.scheme!r}"
        )

    hostname = parsed.hostname
    if not hostname:
        raise SAMLMetadataURLError("SAML metadata URL has no hostname")

    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # hostname is a domain name, not a numeric IP literal — pass through.
        return

    # Loopback, link-local, and reserved ranges are never legitimate SAML IdP
    # addresses regardless of deployment topology.
    if addr.is_loopback or addr.is_link_local or addr.is_reserved:
        raise SAMLMetadataURLError(
            f"SAML metadata URL hostname is a forbidden IP address: {addr}"
        )

    # Private ranges are blocked by default but can be allowed via Django settings
    # for deployments where the SAML IdP lives on the same private network.
    if addr.is_private and not settings.SAML_METADATA_URL_ALLOW_PRIVATE_IPS:
        raise SAMLMetadataURLError(
            f"SAML metadata URL hostname is a private IP address: {addr}. "
            "Set SAML_METADATA_URL_ALLOW_PRIVATE_IPS = True in Django settings to allow this."
        )


def parse_metadata_xml(xml, entity_id):
    """
    Given an XML document containing SAML 2.0 metadata, parse it and return a tuple of
    (public_key, sso_url, expires_at) for the specified entityID.

    Raises MetadataParseError if anything is wrong.
    """

    if xml.tag == etree.QName(SAML_XML_NS, 'EntityDescriptor'):
        entity_desc = xml
    else:
        if xml.tag != etree.QName(SAML_XML_NS, 'EntitiesDescriptor'):
            raise MetadataParseError(Text("Expected root element to be <EntitiesDescriptor>, not {}").format(xml.tag))
        entity_desc = xml.find(
            ".//{}[@entityID='{}']".format(etree.QName(SAML_XML_NS, 'EntityDescriptor'), entity_id)
        )
        if entity_desc is None:
            raise MetadataParseError(f"Can't find EntityDescriptor for entityID {entity_id}")

    expires_at = None
    if "validUntil" in xml.attrib:
        expires_at = dateutil.parser.parse(xml.attrib["validUntil"])
    if "cacheDuration" in xml.attrib:
        cache_expires = OneLogin_Saml2_Utils.parse_duration(xml.attrib["cacheDuration"])
        cache_expires = datetime.datetime.fromtimestamp(cache_expires, tz=ZoneInfo("UTC"))
        if expires_at is None or cache_expires < expires_at:
            expires_at = cache_expires

    sso_desc = entity_desc.find(etree.QName(SAML_XML_NS, "IDPSSODescriptor"))
    if sso_desc is None:
        raise MetadataParseError("IDPSSODescriptor missing")
    if 'urn:oasis:names:tc:SAML:2.0:protocol' not in sso_desc.get("protocolSupportEnumeration"):
        raise MetadataParseError("This IdP does not support SAML 2.0")

    # Now we just need to get the public_key and sso_url
    # We want the use='signing' cert, not the 'encryption' one
    # There may be multiple signing certs returned by the server so create one record per signing cert found.
    certs = sso_desc.findall("./{}[@use='signing']//{}".format(
        etree.QName(SAML_XML_NS, "KeyDescriptor"), "{http://www.w3.org/2000/09/xmldsig#}X509Certificate"
    ))

    if not certs:
        # it's possible that there is just one keyDescription with no use attribute
        # that is a shortcut for both signing and encryption combined. So we can use that as fallback.
        certs = sso_desc.findall("./{}//{}".format(
            etree.QName(SAML_XML_NS, "KeyDescriptor"), "{http://www.w3.org/2000/09/xmldsig#}X509Certificate"
        ))
        if not certs:
            raise MetadataParseError("Public Key missing. Expected an <X509Certificate>")

    public_keys = []
    for key in certs:
        public_keys.append(key.text.replace(" ", ""))

    binding_elements = sso_desc.iterfind("./{}".format(etree.QName(SAML_XML_NS, "SingleSignOnService")))
    sso_bindings = {element.get('Binding'): element.get('Location') for element in binding_elements}
    try:
        # The only binding supported by python-saml and python-social-auth is HTTP-Redirect:
        sso_url = sso_bindings['urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect']
    except KeyError:
        raise MetadataParseError("Unable to find SSO URL with HTTP-Redirect binding.")  # pylint: disable=raise-missing-from  # noqa: B904
    return public_keys, sso_url, expires_at


def user_exists(details):
    """
    Return True if user with given details exist in the system.

    Arguments:
        details (dict): dictionary containing user infor like email, username etc.

    Returns:
        (bool): True if user with given details exists, `False` otherwise.
    """
    email = details.get('email')
    username = details.get('username')
    if email:
        return email_exists_or_retired(email)
    elif username:
        # username__iexact preserves the original case insensitivity
        return User.objects.filter(username__iexact=username).exists() or username_exists_or_retired(username)

    return False


def get_user_from_email(details):
    """
    Return user with given details exist in the system.∂i

    Arguments:
        details (dict): dictionary containing user email.

    Returns:
        User: if user with given details exists, None otherwise.
    """
    email = details.get('email')
    if email:
        return User.objects.filter(email=email).first()

    return None


def create_or_update_bulk_saml_provider_data(entity_id, public_keys, sso_url, expires_at):
    """
    Method to bulk update or create provider data entries
    """
    fetched_at = now()
    new_records_created = False
    # Create a data record for each of the public keys provided
    for key in public_keys:
        existing_data_objects = SAMLProviderData.objects.filter(public_key=key, entity_id=entity_id)
        if len(existing_data_objects) > 1:
            for obj in existing_data_objects:
                obj.sso_url = sso_url
                obj.expires_at = expires_at
                obj.fetched_at = fetched_at
            SAMLProviderData.objects.bulk_update(existing_data_objects, ['sso_url', 'expires_at', 'fetched_at'])
            return True
        else:
            _, created = SAMLProviderData.objects.update_or_create(
                public_key=key, entity_id=entity_id,
                defaults={'sso_url': sso_url, 'expires_at': expires_at, 'fetched_at': fetched_at},
            )
        if created:
            new_records_created = True

    return new_records_created


def is_saml_provider(backend, kwargs):
    """ Verify that the third party provider uses SAML """
    current_provider = provider.Registry.get_from_pipeline({'backend': backend, 'kwargs': kwargs})
    saml_providers_list = list(provider.Registry.get_enabled_by_backend_name('tpa-saml'))
    return (current_provider and
            current_provider.slug in [saml_provider.slug for saml_provider in saml_providers_list]), current_provider


def is_oauth_provider(backend_name, **kwargs):
    """
    Verify that the third party provider uses oauth
    """
    current_provider = provider.Registry.get_from_pipeline({'backend': backend_name, 'kwargs': kwargs})
    if current_provider:
        return current_provider.provider_id.startswith(OAuth2ProviderConfig.prefix)

    return False


def get_associated_user_by_email_response(backend, details, user, *args, **kwargs):
    """
    Gets the user associated by the `associate_by_email` social auth method
    """

    association_response = associate_by_email(backend, details, user, *args, **kwargs)

    if (
        association_response and
        association_response.get('user')
    ):
        # Only return the user matched by email if their email has been activated.
        # Otherwise, an illegitimate user can create an account with another user's
        # email address and the legitimate user would now login to the illegitimate
        # account.
        return (association_response, association_response['user'].is_active)

    return (None, False)
