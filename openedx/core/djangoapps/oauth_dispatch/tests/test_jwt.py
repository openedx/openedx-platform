""" Tests for OAuth Dispatch's jwt module. """
from datetime import timedelta
from unittest.mock import patch

import ddt
from django.test import TestCase
from django.test.utils import override_settings
from django.utils.timezone import now

from openedx.core.djangoapps.oauth_dispatch import jwt as jwt_api
from openedx.core.djangoapps.oauth_dispatch.adapters import DOTAdapter
from openedx.core.djangoapps.oauth_dispatch.models import RestrictedApplication
from openedx.core.djangoapps.oauth_dispatch.tests.mixins import AccessTokenMixin
from common.djangoapps.student.tests.factories import UserFactory
from common.test.utils import assert_dict_contains_subset


@ddt.ddt
class TestCreateJWTs(AccessTokenMixin, TestCase):
    """ Tests for oauth_dispatch's jwt creation functionality. """
    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.default_scopes = ['email', 'profile']
        self.default_scopes_password_grant_type = ['email', 'profile', 'user_id']

    def _create_client(self, oauth_adapter, client_restricted, grant_type=None):
        """
        Creates and returns an OAuth client using the given oauth_adapter.
        Configures the client as a RestrictedApplication if client_restricted is
        True.
        """
        client = oauth_adapter.create_public_client(
            name='public app',
            user=self.user,
            redirect_uri='',
            client_id='public-client-id',
            grant_type=grant_type or '',
        )
        if client_restricted:
            RestrictedApplication.objects.create(application=client)
        return client

    def _get_token_dict(self, client_restricted, oauth_adapter, grant_type=None):
        """ Creates and returns an (opaque) access token dict """
        client = self._create_client(oauth_adapter, client_restricted, grant_type=grant_type)
        expires_in = 60 * 60
        expires = now() + timedelta(seconds=expires_in)
        token_dict = dict(
            access_token=oauth_adapter.create_access_token_for_test('token', client, self.user, expires),
            expires_in=expires_in,
            scope=' '.join(self.default_scopes)
        )
        return token_dict

    def _create_jwt_for_token(
        self, oauth_adapter, use_asymmetric_key, client_restricted=False,
    ):
        """ Creates and returns the jwt returned by jwt_api.create_jwt_from_token. """
        token_dict = self._get_token_dict(client_restricted, oauth_adapter)
        return jwt_api.create_jwt_from_token(token_dict, oauth_adapter, use_asymmetric_key=use_asymmetric_key)

    def _assert_jwt_is_valid(self, jwt_token, should_be_asymmetric_key):
        """ Asserts the given jwt_token is valid and meets expectations. """
        self.assert_valid_jwt_access_token(
            jwt_token, self.user, self.default_scopes, should_be_asymmetric_key=should_be_asymmetric_key,
        )

    def test_create_jwt_for_token(self):
        oauth_adapter = DOTAdapter()
        jwt_token = self._create_jwt_for_token(oauth_adapter, use_asymmetric_key=False)
        self._assert_jwt_is_valid(jwt_token, should_be_asymmetric_key=False)

    def test_dot_create_jwt_for_token_with_asymmetric(self):
        jwt_token = self._create_jwt_for_token(DOTAdapter(), use_asymmetric_key=True)
        self._assert_jwt_is_valid(jwt_token, should_be_asymmetric_key=True)

    @override_settings(JWT_AUTH_FORCE_CREATE_ASYMMETRIC=True)
    def test_dot_create_jwt_for_token_forced_asymmetric(self):
        jwt_token = self._create_jwt_for_token(DOTAdapter(), use_asymmetric_key=False)
        self._assert_jwt_is_valid(jwt_token, should_be_asymmetric_key=True)

    def test_kid_not_in_jwt_header_with_symmetric_key_and_kid_disabled(self):
        jwt_token = self._create_jwt_for_token(DOTAdapter(), use_asymmetric_key=False)
        header = jwt_api.jwt.get_unverified_header(jwt_token)
        assert 'kid' not in header
        self._assert_jwt_is_valid(jwt_token, should_be_asymmetric_key=False)

    def test_kid_not_in_jwt_header_with_asymmetric_key_and_kid_disabled(self):
        jwt_token = self._create_jwt_for_token(DOTAdapter(), use_asymmetric_key=True)
        header = jwt_api.jwt.get_unverified_header(jwt_token)
        assert 'kid' not in header
        self._assert_jwt_is_valid(jwt_token, should_be_asymmetric_key=True)

    @override_settings(JWT_AUTH_ADD_KID_HEADER=True)
    def test_kid_not_in_jwt_header_with_symmetric_key_and_kid_enabled(self):
        jwt_token = self._create_jwt_for_token(DOTAdapter(), use_asymmetric_key=False)
        header = jwt_api.jwt.get_unverified_header(jwt_token)
        assert 'kid' not in header
        self._assert_jwt_is_valid(jwt_token, should_be_asymmetric_key=False)

    @override_settings(JWT_AUTH_ADD_KID_HEADER=True)
    def test_kid_in_jwt_header_with_asymmetric_key_and_kid_enabled(self):
        jwt_token = self._create_jwt_for_token(DOTAdapter(), use_asymmetric_key=True)
        header = jwt_api.jwt.get_unverified_header(jwt_token)
        assert 'kid' in header
        assert header['kid'] == 'BTZ9HA6K'
        self._assert_jwt_is_valid(jwt_token, should_be_asymmetric_key=True)

    def test_create_jwt_for_token_default_expire_seconds(self):
        oauth_adapter = DOTAdapter()
        jwt_token = self._create_jwt_for_token(oauth_adapter, use_asymmetric_key=False)
        expected_expires_in = 60 * 60
        self.assert_valid_jwt_access_token(
            jwt_token, self.user, self.default_scopes, expires_in=expected_expires_in,
        )

    def test_create_jwt_for_token_overridden_expire_seconds(self):
        oauth_adapter = DOTAdapter()
        expected_expires_in = 60
        with override_settings(JWT_ACCESS_TOKEN_EXPIRE_SECONDS=expected_expires_in):
            jwt_token = self._create_jwt_for_token(oauth_adapter, use_asymmetric_key=False)
        self.assert_valid_jwt_access_token(
            jwt_token, self.user, self.default_scopes, expires_in=expected_expires_in,
        )

    def test_create_jwt_token_dict_for_default_expire_seconds(self):
        oauth_adapter = DOTAdapter()
        token_dict = self._get_token_dict(client_restricted=False, oauth_adapter=oauth_adapter)
        jwt_token_dict = jwt_api.create_jwt_token_dict(token_dict, oauth_adapter, use_asymmetric_key=False)
        expected_expires_in = 60 * 60
        self.assert_valid_jwt_access_token(
            jwt_token_dict["access_token"], self.user, self.default_scopes, expires_in=expected_expires_in,
        )
        assert jwt_token_dict["token_type"] == "JWT"
        assert jwt_token_dict["expires_in"] == expected_expires_in
        assert jwt_token_dict["scope"] == token_dict["scope"]

    def test_create_jwt_token_dict_for_overridden_expire_seconds(self):
        oauth_adapter = DOTAdapter()
        expected_expires_in = 60
        with override_settings(JWT_ACCESS_TOKEN_EXPIRE_SECONDS=expected_expires_in):
            token_dict = self._get_token_dict(client_restricted=False, oauth_adapter=oauth_adapter)
            jwt_token_dict = jwt_api.create_jwt_token_dict(token_dict, oauth_adapter, use_asymmetric_key=False)
        self.assert_valid_jwt_access_token(
            jwt_token_dict["access_token"], self.user, self.default_scopes, expires_in=expected_expires_in,
        )
        assert jwt_token_dict["token_type"] == "JWT"
        assert jwt_token_dict["expires_in"] == expected_expires_in
        assert jwt_token_dict["scope"] == token_dict["scope"]

    @ddt.data((True, False))
    def test_create_jwt_for_client_restricted(self, client_restricted):
        jwt_token = self._create_jwt_for_token(
            DOTAdapter(),
            use_asymmetric_key=None,
            client_restricted=client_restricted,
        )
        self._assert_jwt_is_valid(jwt_token, should_be_asymmetric_key=client_restricted)

    @patch('openedx.core.djangoapps.oauth_dispatch.jwt.create_role_auth_claim_for_user')
    @ddt.data(True, False)
    def test_create_jwt_for_user(self, user_email_verified, mock_create_roles):
        mock_create_roles.return_value = ['superuser', 'enterprise-admin']
        self.user.is_active = user_email_verified
        self.user.save()

        aud = 'test_aud'
        secret = 'test_secret'
        additional_claims = {'claim1_key': 'claim1_val'}
        jwt_token = jwt_api.create_jwt_for_user(self.user, secret=secret, aud=aud, additional_claims=additional_claims)
        token_payload = self.assert_valid_jwt_access_token(
            jwt_token, self.user, self.default_scopes, aud=aud, secret=secret,
        )
        assert_dict_contains_subset(self, additional_claims, token_payload)
        assert user_email_verified == token_payload['email_verified']
        assert token_payload['roles'] == mock_create_roles.return_value

    def test_scopes(self):
        """
        Ensure the requested scopes are used.
        """
        scopes = [
            'user_id',
        ]
        aud = 'test_aud'
        secret = 'test_secret'

        jwt = jwt_api.create_jwt_for_user(self.user, secret=secret, aud=aud)
        jwt_scopes = jwt_api.create_jwt_for_user(self.user, secret=secret, aud=aud, scopes=scopes)

        jwt_payload = self.assert_valid_jwt_access_token(
            jwt, self.user, self.default_scopes, aud=aud, secret=secret,
        )
        jwt_scopes_payload = self.assert_valid_jwt_access_token(
            jwt_scopes, self.user, scopes, aud=aud, secret=secret,
        )
        assert jwt_payload['scopes'] == self.default_scopes
        assert jwt_scopes_payload['scopes'] == scopes
        assert jwt_scopes_payload['user_id'] == self.user.id

    def test_password_grant_type(self):
        oauth_adapter = DOTAdapter()
        token_dict = self._get_token_dict(client_restricted=False, oauth_adapter=oauth_adapter, grant_type='password')
        jwt_token_dict = jwt_api.create_jwt_token_dict(token_dict, oauth_adapter, use_asymmetric_key=False)

        self.assert_valid_jwt_access_token(
            jwt_token_dict["access_token"], self.user, self.default_scopes_password_grant_type,
            grant_type='password',
        )

    def test_None_grant_type(self):
        oauth_adapter = DOTAdapter()
        token_dict = self._get_token_dict(client_restricted=False, oauth_adapter=oauth_adapter, grant_type=None)
        jwt_token_dict = jwt_api.create_jwt_token_dict(token_dict, oauth_adapter, use_asymmetric_key=False)

        self.assert_valid_jwt_access_token(
            jwt_token_dict["access_token"], self.user, self.default_scopes,
            grant_type='',
        )

    def test_random_str_grant_type(self):
        oauth_adapter = DOTAdapter()
        token_dict = self._get_token_dict(client_restricted=False, oauth_adapter=oauth_adapter, grant_type='test rand')
        jwt_token_dict = jwt_api.create_jwt_token_dict(token_dict, oauth_adapter, use_asymmetric_key=False)

        self.assert_valid_jwt_access_token(
            jwt_token_dict["access_token"], self.user, self.default_scopes,
            grant_type='test rand',
        )
