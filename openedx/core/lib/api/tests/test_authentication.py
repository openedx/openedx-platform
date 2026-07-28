"""
Tests for OAuth2.  This module is copied from django-rest-framework-oauth
(tests/test_authentication.py) and updated to use our subclass of BearerAuthentication.
"""

import itertools
import json
from collections import namedtuple
from datetime import timedelta

import ddt
from django.http import HttpResponse
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import path
from django.utils.http import urlencode
from django.utils.timezone import now
from oauth2_provider import models as dot_models
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.oauth_dispatch import adapters
from openedx.core.lib.api import authentication

factory = APIRequestFactory()  # pylint: disable=invalid-name


class MockView(APIView):  # pylint: disable=missing-docstring
    permission_classes = (IsAuthenticated,)

    def get(self, _request):
        return HttpResponse({'a': 1, 'b': 2, 'c': 3})

    def post(self, _request):
        return HttpResponse({'a': 1, 'b': 2, 'c': 3})

    def put(self, _request):
        return HttpResponse({'a': 1, 'b': 2, 'c': 3})


urlpatterns = [
    path('oauth2-inactive-test/',
         MockView.as_view(authentication_classes=[authentication.BearerAuthenticationAllowInactiveUser])
         ),
    path('oauth2-test/',
         MockView.as_view(authentication_classes=[authentication.BearerAuthentication])
         )
]


@ddt.ddt  # pylint: disable=missing-docstring
@override_settings(ROOT_URLCONF=__name__)
class OAuth2AllowInActiveUsersTests(TestCase):  # pylint: disable=missing-class-docstring
    OAUTH2_BASE_TESTING_URL = '/oauth2-inactive-test/'

    def setUp(self):
        super().setUp()
        self.dot_adapter = adapters.DOTAdapter()
        self.csrf_client = APIClient(enforce_csrf_checks=True)
        self.username = 'john'
        self.email = 'lennon@thebeatles.com'
        self.password = 'password'
        self.user = UserFactory.create(username=self.username, email=self.email, password=self.password)

        self.dot_oauth2_client = self.dot_adapter.create_public_client(
            name='example',
            user=self.user,
            client_id='dot-client-id',
            redirect_uri='https://example.edx/redirect',
        )
        self.dot_access_token = dot_models.AccessToken.objects.create(
            user=self.user,
            token='dot-access-token',
            application=self.dot_oauth2_client,
            expires=now() + timedelta(days=30),
        )
        self.dot_refresh_token = dot_models.RefreshToken.objects.create(
            user=self.user,
            token='dot-refresh-token',
            application=self.dot_oauth2_client,
        )

        # This is the a change we've made from the django-rest-framework-oauth version
        # of these tests.
        self.user.is_active = False
        self.user.save()

    def _create_authorization_header(self, token=None):
        if token is None:
            token = self.dot_access_token.token
        return f"Bearer {token}"

    def get_with_bearer_token(self, target_url, params=None, token=None):
        """
        Make a GET request to the specified URL with an OAuth2 bearer token.  If
        no token is provided, a valid token will be used.  Query parameters can
        also be passed in if desired.
        """
        auth = self._create_authorization_header(token)
        return self.csrf_client.get(target_url, params, HTTP_AUTHORIZATION=auth)

    def post_with_bearer_token(self, target_url, token=None):
        """
        Make a POST request to the specified URL with an OAuth2 bearer token.  If
        no token is provided, a valid token will be used.
        """
        auth = self._create_authorization_header(token)
        return self.csrf_client.post(target_url, HTTP_AUTHORIZATION=auth)

    def check_error_codes(self, response, status_code, error_code):
        """
        Ensure that the response has the appropriate HTTP status, and provides
        the expected error_code in the JSON response body.
        """
        response_dict = json.loads(response.content.decode('utf-8'))
        assert response.status_code == status_code
        assert response_dict['error_code'] == error_code

    @ddt.data(None, {})
    def test_get_form_with_wrong_authorization_header_token_type_failing(self, params):
        """Ensure that a wrong token type lead to the correct HTTP error status code"""
        response = self.csrf_client.get(
            self.OAUTH2_BASE_TESTING_URL,
            params,
            HTTP_AUTHORIZATION='Wrong token-type-obviously'
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # If no Authorization header is provided that contains a bearer token,
        # authorization passes to the next registered authorization class, or
        # (in this case) to standard DRF fallback code, so no error_code is
        # provided (yet).
        assert 'error_code' not in json.loads(response.content.decode('utf-8'))

    def test_get_form_passing_auth_with_dot(self):
        response = self.get_with_bearer_token(self.OAUTH2_BASE_TESTING_URL, token=self.dot_access_token.token)
        assert response.status_code == status.HTTP_200_OK

    def test_get_form_failing_auth_url_transport(self):
        """Ensure GETing form over OAuth with correct client credentials in query fails when DEBUG is False"""
        query = urlencode({'access_token': self.dot_access_token.token})
        response = self.csrf_client.get(self.OAUTH2_BASE_TESTING_URL + '?%s' % query)  # noqa: UP031
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        # This case is handled directly by DRF so no error_code is provided (yet).
        assert 'error_code' not in json.loads(response.content.decode('utf-8'))

    def test_post_form_passing_auth(self):
        """Ensure POSTing form over OAuth with correct credentials passes and does not require CSRF"""
        response = self.post_with_bearer_token(self.OAUTH2_BASE_TESTING_URL)
        assert response.status_code == status.HTTP_200_OK

    def test_post_form_token_removed_failing_auth(self):
        """Ensure POSTing when there is no OAuth access token in db fails"""
        self.dot_access_token.delete()
        response = self.post_with_bearer_token(self.OAUTH2_BASE_TESTING_URL)
        self.check_error_codes(
            response,
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=authentication.OAUTH2_TOKEN_ERROR_NONEXISTENT
        )

    def test_post_form_with_refresh_token_failing_auth(self):
        """Ensure POSTing with refresh token instead of access token fails"""
        response = self.post_with_bearer_token(self.OAUTH2_BASE_TESTING_URL, token=self.dot_refresh_token.token)
        self.check_error_codes(
            response,
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=authentication.OAUTH2_TOKEN_ERROR_NONEXISTENT
        )

    def test_post_form_with_expired_access_token_failing_auth(self):
        """Ensure POSTing with expired access token fails with a 'token_expired' error"""
        self.dot_access_token.expires = now() - timedelta(seconds=10)  # 10 seconds late
        self.dot_access_token.save()
        response = self.post_with_bearer_token(self.OAUTH2_BASE_TESTING_URL)
        self.check_error_codes(
            response,
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=authentication.OAUTH2_TOKEN_ERROR_EXPIRED
        )

    TokenErrorDDT = namedtuple('TokenErrorDDT', ['token', 'error_code'])

    @ddt.data(
        *itertools.product(
            [None, {}],
            [
                TokenErrorDDT('wrong format', authentication.OAUTH2_TOKEN_ERROR_MALFORMED),
                TokenErrorDDT('wrong-token', authentication.OAUTH2_TOKEN_ERROR_NONEXISTENT),
                TokenErrorDDT('', authentication.OAUTH2_TOKEN_ERROR_NOT_PROVIDED),
            ]
        )
    )
    @ddt.unpack
    def test_response_for_get_request_with_bad_auth_token(self, http_params, token_error):
        response = self.get_with_bearer_token(self.OAUTH2_BASE_TESTING_URL, http_params, token=token_error.token)
        self.check_error_codes(
            response,
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=token_error.error_code
        )

    @ddt.data(
        TokenErrorDDT('notatoken', authentication.OAUTH2_TOKEN_ERROR_NONEXISTENT),
        TokenErrorDDT('malformed token', authentication.OAUTH2_TOKEN_ERROR_MALFORMED),
        TokenErrorDDT('', authentication.OAUTH2_TOKEN_ERROR_NOT_PROVIDED),
    )
    def test_response_for_post_request_with_bad_auth_token(self, token_error):
        response = self.post_with_bearer_token(self.OAUTH2_BASE_TESTING_URL, token=token_error.token)
        self.check_error_codes(response, status_code=status.HTTP_401_UNAUTHORIZED, error_code=token_error.error_code)


class BearerAuthenticationTests(OAuth2AllowInActiveUsersTests):  # pylint: disable=missing-class-docstring, test-inherits-tests

    OAUTH2_BASE_TESTING_URL = '/oauth2-test/'

    def setUp(self):
        super().setUp()
        # Since this is testing back to previous version, user should be set to true
        self.user.is_active = True
        self.user.save()


class OAuthDenyDisabledUsers(OAuth2AllowInActiveUsersTests):  # pylint: disable=test-inherits-tests
    """
     To test OAuth on disabled user.
    """
    OAUTH2_BASE_TESTING_URL = '/oauth2-test/'

    def setUp(self):
        super().setUp()
        # User is active but has have disabled status
        self.user.is_active = True
        self.user.set_unusable_password()
        self.user.save()

    def test_get_form_passing_auth_with_dot(self):
        """
         Asserts response with disabled user with DOT App
        """
        response = self.get_with_bearer_token(self.OAUTH2_BASE_TESTING_URL, token=self.dot_access_token.token)
        assert response.status_code == status.HTTP_200_OK

    def test_post_form_passing_auth(self):
        """
         Asserts response with disabled user with DOT App
        """
        response = self.post_with_bearer_token(self.OAUTH2_BASE_TESTING_URL)
        assert response.status_code == status.HTTP_200_OK

    def test_get_form_without_oauth_app(self):
        """
         Asserts response with disabled user and without DOT APP
        """
        dot_models.Application.objects.filter(user_id=self.user.id).delete()
        response = self.get_with_bearer_token(self.OAUTH2_BASE_TESTING_URL, token=self.dot_access_token.token)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_post_form_without_oauth_app(self):
        """
         Asserts response with disabled user and without DOT APP
        """
        dot_models.Application.objects.filter(user_id=self.user.id).delete()
        response = self.post_with_bearer_token(self.OAUTH2_BASE_TESTING_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
