"""
Tests for OAuth token exchange views
"""

# pylint: disable=no-member

import json
import unittest
from datetime import datetime, timedelta

import ddt
import httpretty
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from freezegun import freeze_time
from oauth2_provider.models import Application
from rest_framework.test import APIClient
from social_django.models import Partial

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth.tests.utils import (
    ThirdPartyOAuthTestMixinFacebook,
    ThirdPartyOAuthTestMixinGoogle,
)
from openedx.core.djangoapps.oauth_dispatch import jwt as jwt_api
from openedx.core.djangoapps.oauth_dispatch.adapters import DOTAdapter
from openedx.core.djangoapps.oauth_dispatch.tests import factories as dot_factories
from openedx.core.djangolib.testing.utils import skip_unless_lms

from .mixins import DOTAdapterMixin
from .utils import TPA_FEATURE_ENABLED, TPA_FEATURES_KEY, AccessTokenExchangeTestMixin


@ddt.ddt
class AccessTokenExchangeViewTest(AccessTokenExchangeTestMixin):
    """
    Mixin that defines test cases for AccessTokenExchangeView

    Warning: This class was originally created to support multiple libraries,
        but we currently only support django-oauth-toolkit (DOT). At this point,
        the variety of mixins can be quite confusing and are no longer providing
        any benefit, other than the potential for reintroducing another library
        in the future.
    """
    def setUp(self):
        super().setUp()
        self.url = reverse("exchange_access_token", kwargs={"backend": self.BACKEND})
        self.csrf_client = APIClient(enforce_csrf_checks=True)

    def tearDown(self):
        super().tearDown()
        Partial.objects.all().delete()

    def _assert_error(self, data, expected_error, expected_error_description, error_code=None):
        response = self.csrf_client.post(self.url, data)
        assert response.status_code == (error_code if error_code else 400)
        assert response['Content-Type'] == 'application/json'
        expected_data = {"error": expected_error, "error_description": expected_error_description}
        if error_code:
            expected_data['error_code'] = error_code
        assert json.loads(response.content.decode('utf-8')) == expected_data

    def _assert_success(self, data, expected_scopes, expected_logged_in_user=None):
        response = self.csrf_client.post(self.url, data)
        if expected_logged_in_user:
            # Ensure that safe sessions isn't preventing an expected login
            assert expected_logged_in_user == response.wsgi_request.user
        assert response.status_code == 200
        assert response['Content-Type'] == 'application/json'
        content = json.loads(response.content.decode('utf-8'))
        assert set(content.keys()) == self.get_token_response_keys()
        assert content['token_type'] == 'Bearer'
        assert timedelta(seconds=int(content['expires_in'])) <= timedelta(days=30)
        actual_scopes = content["scope"]
        if actual_scopes:
            actual_scopes = actual_scopes.split(' ')
        else:
            actual_scopes = []
        assert set(actual_scopes) == set(expected_scopes)
        token = self.oauth2_adapter.get_access_token(token_string=content["access_token"])
        assert token.user == self.user
        assert self.oauth2_adapter.get_client_for_token(token) == self.oauth_client
        assert set(self.oauth2_adapter.get_token_scope_names(token)) == set(expected_scopes)

    def test_get_method(self):
        response = self.client.get(self.url, self.data)
        assert response.status_code == 405
        assert json.loads(response.content.decode('utf-8')) == {'detail': 'Method "GET" not allowed.'}

    def test_invalid_provider(self):
        url = reverse("exchange_access_token", kwargs={"backend": "invalid"})
        response = self.client.post(url, self.data)
        assert response.status_code == 404

    def test_logged_in_user_without_csrf_error(self):
        """
        Test that a logged in user succeeds without a CSRF permission denied.

        Note: The logged in user does not match the user of the token, but that is not
            being treated as an error.
        """
        self.csrf_client.login(username='test', password='secret')
        self._setup_provider_response(success=True)
        self._assert_success(self.data, expected_scopes=[], expected_logged_in_user=self.user)

    def test_disabled_user(self):
        """
        Test if response status code is correct in case of disabled user.
        """
        self.user.set_unusable_password()
        self.user.save()
        self._setup_provider_response(success=True)
        self._assert_error(self.data, "account_disabled", "user account is disabled", 403)


@unittest.skipUnless(TPA_FEATURE_ENABLED, TPA_FEATURES_KEY + " not enabled")
@httpretty.activate
class DOTAccessTokenExchangeViewTestFacebook(
        DOTAdapterMixin,
        AccessTokenExchangeViewTest,
        ThirdPartyOAuthTestMixinFacebook,
        TestCase,
):
    """
    Rerun AccessTokenExchangeViewTestFacebook tests against DOT backend
    """
    pass  # pylint: disable=unnecessary-pass


# This is necessary because cms does not implement third party auth
@unittest.skipUnless(TPA_FEATURE_ENABLED, TPA_FEATURES_KEY + " not enabled")
@httpretty.activate
class DOTAccessTokenExchangeViewTestGoogle(
        DOTAdapterMixin,
        AccessTokenExchangeViewTest,
        ThirdPartyOAuthTestMixinGoogle,
        TestCase,
):
    """
    Tests for AccessTokenExchangeView used with Google using
    django-oauth-toolkit backend.
    """
    pass  # pylint: disable=unnecessary-pass


@skip_unless_lms
class TestLoginWithAccessTokenView(TestCase):
    """
    Tests for LoginWithAccessTokenView
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.oauth2_client = Application.objects.create(client_type=Application.CLIENT_CONFIDENTIAL)

    def _get_response(self, access_token, token_type='Bearer'):
        url = reverse("login_with_access_token")
        return self.client.post(url, HTTP_AUTHORIZATION=f"{token_type} {access_token}".encode('utf-8'))  # noqa: UP012

    def _verify_response(self, access_token, expected_status_code, token_type='Bearer', expected_cookie_name=None):
        """
        Calls the login_with_access_token endpoint and verifies the response given the expected values.
        """
        response = self._get_response(access_token, token_type)
        assert response.status_code == expected_status_code
        if expected_cookie_name:
            assert expected_cookie_name in response.cookies

    def _create_dot_access_token(self, grant_type='Client credentials', skip_authorization=False):
        """
        Create dot based access token
        """
        dot_application = dot_factories.ApplicationFactory(
            user=self.user,
            authorization_grant_type=grant_type,
            skip_authorization=skip_authorization,
        )
        return dot_factories.AccessTokenFactory(user=self.user, application=dot_application)

    def test_failure_with_invalid_token(self):
        self._verify_response("invalid_token", expected_status_code=401)
        assert '_auth_user_id' not in self.client.session

    def test_success_with_dot_password_grant_supported(self):
        access_token = self._create_dot_access_token(grant_type='password')

        self._verify_response(access_token, expected_status_code=204, expected_cookie_name='sessionid')
        assert int(self.client.session['_auth_user_id']) == self.user.id

    def test_failure_with_dot_client_credentials_unsupported(self):
        access_token = self._create_dot_access_token()
        self._verify_response(access_token, expected_status_code=401)

    def test_dot_client_credentials_supported_if_authorization_skipped(self):
        access_token = self._create_dot_access_token(skip_authorization=True)
        self._verify_response(access_token, expected_status_code=204, expected_cookie_name='sessionid')

    def _create_jwt_token(self, grant_type='password', scope='email profile', use_asymmetric_key=True):
        """
        Create jwt token
        """
        access_token = self._create_dot_access_token(grant_type)
        oauth_adapter = DOTAdapter()
        token_dict = {
            'access_token': access_token,
            'scope': scope,
        }
        jwt_token = jwt_api.create_jwt_from_token(token_dict, oauth_adapter, use_asymmetric_key=use_asymmetric_key)
        return jwt_token

    def test_failure_with_invalid_jwt(self):
        self._verify_response("invalid_token", token_type="JWT", expected_status_code=401)

        assert '_auth_user_id' not in self.client.session

    def test_failure_with_valid_jwt_but_not_password_grant(self):

        jwt_token = self._create_jwt_token(grant_type='Client credentials')
        self._verify_response(jwt_token, token_type="JWT", expected_status_code=401)

        assert '_auth_user_id' not in self.client.session

    def test_failure_with_expired_jwt_with_password_grant(self):
        with override_settings(JWT_ACCESS_TOKEN_EXPIRE_SECONDS=1):
            jwt_token = self._create_jwt_token()

        # freeze_time will pretend 10 seconds have passed!
        with freeze_time(lambda: datetime.utcnow() + timedelta(seconds=10)):
            self._verify_response(jwt_token, token_type="JWT", expected_status_code=401)

        assert '_auth_user_id' not in self.client.session

    def test_failure_with_valid_symmetric_jwt(self):

        jwt_token = self._create_jwt_token(use_asymmetric_key=False)
        response = self._get_response(jwt_token, token_type="JWT")
        error_msg = {
            'error_code': 'non_asymmetric_token',
            'developer_message': 'Only asymmetric jwt are supported.'
        }
        self.assertDictEqual(error_msg, json.loads(response.content))  # noqa: PT009
        assert '_auth_user_id' not in self.client.session
        assert response.status_code == 401

    def test_failure_with_valid_asymmetric_jwt_and_disabled_user(self):
        jwt_token = self._create_jwt_token()
        self.user.set_unusable_password()
        self.user.save()

        response = self._get_response(jwt_token, token_type="JWT")
        error_msg = {
            'error_code': 'account_disabled',
            'developer_message': 'User account is disabled.'
        }
        self.assertDictEqual(error_msg, json.loads(response.content))  # noqa: PT009
        assert '_auth_user_id' not in self.client.session
        assert response.status_code == 401

    def test_success_with_valid_asymmetric_jwt(self):

        jwt_token = self._create_jwt_token()
        self._verify_response(jwt_token, token_type="JWT",
                              expected_status_code=204, expected_cookie_name='sessionid')

        assert int(self.client.session['_auth_user_id']) == self.user.id
