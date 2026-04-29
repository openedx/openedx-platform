"""Common utility for testing third party oauth2 features."""


import json
from base64 import b64encode
from unittest import skip

import httpretty
from oauth2_provider.models import Application
from onelogin.saml2.utils import OneLogin_Saml2_Utils
from social_core.backends.facebook import API_VERSION as FACEBOOK_API_VERSION
from social_core.backends.facebook import FacebookOAuth2
from social_django.models import Partial, UserSocialAuth

from common.djangoapps.student.tests.factories import UserFactory

from .testutil import AUTH_FEATURE_ENABLED, AUTH_FEATURES_KEY, ThirdPartyAuthTestMixin


@httpretty.activate
class ThirdPartyOAuthTestMixin(ThirdPartyAuthTestMixin):
    """
    Mixin with tests for third party oauth views. A TestCase that includes
    this must define the following:

    BACKEND: The name of the backend from python-social-auth
    USER_URL: The URL of the endpoint that the backend retrieves user data from
    UID_FIELD: The field in the user data that the backend uses as the user id
    """
    social_uid = "test_social_uid"
    access_token = "test_access_token"
    client_id = "test_client_id"

    CREATE_USER = True

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        if self.CREATE_USER:
            self.user = UserFactory.create(password='secret')
            UserSocialAuth.objects.create(user=self.user, provider=self.BACKEND, uid=self.social_uid)
        self.oauth_client = self._create_client()
        if self.BACKEND == 'google-oauth2':
            self.configure_google_provider(enabled=True, visible=True)
        elif self.BACKEND == 'facebook':
            self.configure_facebook_provider(enabled=True, visible=True)

    def tearDown(self):
        super().tearDown()
        Partial.objects.all().delete()

    def _create_client(self):
        """
        Create an OAuth2 client application
        """
        return Application.objects.create(
            client_id=self.client_id,
            client_type=Application.CLIENT_PUBLIC,
        )

    def _setup_provider_response(self, success=False, email='', profile_data=None):
        """
        Register a mock response for the third party user information endpoint;
        success indicates whether the response status code should be 200 or 400
        """
        if success:
            status = 200
            response = {self.UID_FIELD: self.social_uid}
            if email:
                response.update({'email': email})

            if profile_data:
                response.update(profile_data)

            body = json.dumps(response)
        else:
            status = 400
            body = json.dumps({})

        self._setup_provider_response_with_body(status, body)

    def _setup_provider_response_with_body(self, status, body):
        """
        Register a mock response for the third party user information endpoint with given status and body.
        """
        httpretty.register_uri(
            httpretty.GET,
            self.USER_URL,
            body=body,
            status=status,
            content_type="application/json",
        )


class ThirdPartyOAuthTestMixinFacebook:
    """Tests oauth with the Facebook backend"""
    BACKEND = "facebook"
    USER_URL = FacebookOAuth2.USER_DATA_URL.format(version=FACEBOOK_API_VERSION)
    # In facebook responses, the "id" field is used as the user's identifier
    UID_FIELD = "id"


class ThirdPartyOAuthTestMixinGoogle:
    """Tests oauth with the Google backend"""
    BACKEND = "google-oauth2"
    USER_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
    # In google-oauth2 responses, the "email" field is used as the user's identifier
    UID_FIELD = "email"


def read_and_pre_process_xml(file_name):
    """
    Read XML file with the name specified in the argument and pre process the xml so that it can be parsed.

    Pre Processing removes line retune characters (i.e. "\n").

    Arguments:
        file_name (str): Name of the XML file.

    Returns:
         (str): Pre Processed contents of the file.
    """
    with open(file_name) as xml_file:
        return xml_file.read().replace('\n', '')


def prepare_saml_response_from_xml(xml, relay_state='testshib'):
    """
    Pre Process XML so that it can be used as a SAML Response coming from SAML IdP.

    This method will perform the following operations on the XML in given order

    1. base64 encode XML.
    2. URL encode the base64 encoded data.

    Arguments:
        xml (string): XML data
        relay_state (string): Relay State of the SAML Response

    Returns:
         (str): Base64 and URL encoded XML.
    """
    b64encoded_xml = b64encode(xml.encode())
    return 'RelayState={relay_state}&SAMLResponse={saml_response}'.format(  # noqa: UP032
        relay_state=OneLogin_Saml2_Utils.escape_url(relay_state),
        saml_response=OneLogin_Saml2_Utils.escape_url(b64encoded_xml)
    )


def skip_unless_thirdpartyauth():
    """
    Wraps unittest.skip in consistent logic to skip certain third_party_auth tests in CMS.
    """
    if AUTH_FEATURE_ENABLED:
        return lambda func: func
    return skip("%s not enabled" % AUTH_FEATURES_KEY)  # noqa: UP031
