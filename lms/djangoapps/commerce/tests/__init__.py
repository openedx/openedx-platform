""" Commerce app tests package. """


import os
from unittest import mock
from urllib.parse import urljoin

import ddt
import httpretty
import requests  # noqa: F401
from django.conf import settings
from django.test import TestCase
from edx_rest_api_client.auth import JwtAuth
from freezegun import freeze_time  # noqa: F401

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.commerce.utils import (
    DeprecatedRestApiClient,
    get_ecommerce_api_base_url,
    get_ecommerce_api_client,
    user_agent,
)
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user  # noqa: F401

__version__ = '5.6.1'
URL = 'http://example.com/api/v2'
SIGNING_KEY = 'edx'
USERNAME = 'edx'
FULL_NAME = 'édx äpp'
EMAIL = 'edx@example.com'
TRACKING_CONTEXT = {'foo': 'bar'}
ACCESS_TOKEN = 'abc123'
JWT = 'abc.123.doremi'
JSON = 'application/json'
TEST_PUBLIC_URL_ROOT = 'http://www.example.com'
TEST_API_URL = 'http://www-internal.example.com/api'
TEST_BASKET_ID = 7
TEST_ORDER_NUMBER = '100004'
TEST_PAYMENT_DATA = {
    'payment_processor_name': 'test-processor',
    'payment_form_data': {},
    'payment_page_url': 'http://example.com/pay',
}


@ddt.ddt
class DeprecatedRestApiClientTests(TestCase):
    """
    Tests for the edX Rest API client.
    """

    @ddt.unpack
    @ddt.data(
        ({'url': URL, 'signing_key': SIGNING_KEY, 'username': USERNAME,
          'full_name': FULL_NAME, 'email': EMAIL}, JwtAuth),
        ({'url': URL, 'signing_key': SIGNING_KEY, 'username': USERNAME, 'full_name': None, 'email': EMAIL}, JwtAuth),
        ({'url': URL, 'signing_key': SIGNING_KEY, 'username': USERNAME,
          'full_name': FULL_NAME, 'email': None}, JwtAuth),
        ({'url': URL, 'signing_key': SIGNING_KEY, 'username': USERNAME, 'full_name': None, 'email': None}, JwtAuth),
        ({'url': URL, 'signing_key': SIGNING_KEY, 'username': USERNAME}, JwtAuth),
        ({'url': URL, 'signing_key': None, 'username': USERNAME}, type(None)),
        ({'url': URL, 'signing_key': SIGNING_KEY, 'username': None}, type(None)),
        ({'url': URL, 'signing_key': None, 'username': None, 'oauth_access_token': None}, type(None))
    )
    def test_valid_configuration(self, kwargs, auth_type):
        """
        The constructor should return successfully if all arguments are valid.
        We also check that the auth type of the api is what we expect.
        """
        api = DeprecatedRestApiClient(**kwargs)
        self.assertIsInstance(api._store['session'].auth, auth_type)  # pylint: disable=protected-access  # noqa: PT009

    @ddt.data(
        {'url': None, 'signing_key': SIGNING_KEY, 'username': USERNAME},
        {'url': None, 'signing_key': None, 'username': None, 'oauth_access_token': None},
    )
    def test_invalid_configuration(self, kwargs):
        """
        If the constructor arguments are invalid, an InvalidConfigurationError should be raised.
        """
        self.assertRaises(ValueError, DeprecatedRestApiClient, **kwargs)  # noqa: PT027

    @mock.patch('edx_rest_api_client.auth.JwtAuth.__init__', return_value=None)
    def test_tracking_context(self, mock_auth):
        """
        Ensure the tracking context is included with API requests if specified.
        """
        DeprecatedRestApiClient(URL, SIGNING_KEY, USERNAME, FULL_NAME, EMAIL, tracking_context=TRACKING_CONTEXT)
        self.assertIn(TRACKING_CONTEXT, mock_auth.call_args[1].values())  # noqa: PT009

    def test_oauth2(self):
        """
        Ensure OAuth2 authentication is used when an access token is supplied to the constructor.
        """

        with mock.patch('openedx.core.djangoapps.commerce.utils.BearerAuth.__init__', return_value=None) as mock_auth:
            DeprecatedRestApiClient(URL, oauth_access_token=ACCESS_TOKEN)
            mock_auth.assert_called_with(ACCESS_TOKEN)

    def test_supplied_jwt(self):
        """Ensure JWT authentication is used when a JWT is supplied to the constructor."""
        with mock.patch('edx_rest_api_client.auth.SuppliedJwtAuth.__init__', return_value=None) as mock_auth:
            DeprecatedRestApiClient(URL, jwt=JWT)
            mock_auth.assert_called_with(JWT)

    def test_user_agent(self):
        """Make sure our custom User-Agent is getting built correctly."""
        with mock.patch('socket.gethostbyname', return_value='test_hostname'):
            default_user_agent = user_agent()
            self.assertIn('python-requests', default_user_agent)  # noqa: PT009
            self.assertIn(f'edx-rest-api-client/{__version__}', default_user_agent)  # noqa: PT009
            self.assertIn('test_hostname', default_user_agent)  # noqa: PT009

        with mock.patch('socket.gethostbyname') as mock_gethostbyname:
            mock_gethostbyname.side_effect = ValueError()
            default_user_agent = user_agent()
            self.assertIn('unknown_client_name', default_user_agent)  # noqa: PT009

        with mock.patch.dict(os.environ, {'EDX_REST_API_CLIENT_NAME': "awesome_app"}):
            uagent = user_agent()
            self.assertIn('awesome_app', uagent)  # noqa: PT009

        self.assertEqual(user_agent(), DeprecatedRestApiClient.user_agent())  # noqa: PT009


class DeprecatedRestApiClientTest(TestCase):
    """
    Tests to ensure the client is initialized properly.
    """
    SCOPES = [
        'user_id',
        'email',
        'profile'
    ]

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.base_url = get_ecommerce_api_base_url()

    @httpretty.activate
    def test_client_unicode(self):
        """
        The client should handle json responses properly when they contain unicode character data.

        Regression test for ECOM-1606.
        """
        expected_content = '{"result": "Préparatoire"}'
        httpretty.register_uri(
            httpretty.GET,
            f"{settings.ECOMMERCE_API_URL.strip('/')}/baskets/1/order/",
            status=200, body=expected_content,
            adding_headers={'Content-Type': JSON},
        )
        api_url = urljoin(f"{self.base_url}/", "baskets/1/order/")
        actual_object = get_ecommerce_api_client(self.user).get(api_url).json()
        assert actual_object == {'result': 'Préparatoire'}
