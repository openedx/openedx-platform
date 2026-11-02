""" Common utilities for tests in the user_authn app. """


from datetime import datetime, timedelta
from enum import Enum
from unittest.mock import patch

import ddt
from zoneinfo import ZoneInfo
from django.conf import settings
from oauth2_provider import models as dot_models
from rest_framework import status

from openedx.core.djangoapps.oauth_dispatch.adapters.dot import DOTAdapter
from openedx.core.djangoapps.oauth_dispatch.jwt import _create_jwt
from common.djangoapps.student.tests.factories import UserFactory


class AuthType(Enum):
    session = 1
    oauth = 2
    jwt = 3
    jwt_restricted = 4

JWT_AUTH_TYPES = [AuthType.jwt, AuthType.jwt_restricted]


def setup_login_oauth_client():
    """
    Sets up a test OAuth client for the login service.
    """
    login_service_user = UserFactory.create()
    DOTAdapter().create_public_client(
        name='login-service',
        user=login_service_user,
        redirect_uri='',
        client_id=settings.JWT_AUTH['JWT_LOGIN_CLIENT_ID'],
    )


def utcnow():
    """
    Helper function to return the current UTC time localized to the UTC timezone.
    """
    return datetime.now(ZoneInfo("UTC"))


@ddt.ddt
class AuthAndScopesTestMixin:
    """
    Mixin class to test authentication and oauth scopes for an API.
    Test classes that use this Mixin need to define:
        default_scopes - default list of scopes to include in created JWTs.
        get_url(self, username) - method that returns the URL to call given
            a username.
        assert_success_response_for_student(resp) - method that verifies the
            data returned in a successful response when accessing the URL for
            self.student.
    """
    default_scopes = None
    user_password = 'Password1234'

    def setUp(self):
        super().setUp()
        self.student = UserFactory.create(password=self.user_password)
        self.other_student = UserFactory.create(password=self.user_password)
        self.global_staff = UserFactory.create(password=self.user_password, is_staff=True)

    def get_response(self, auth_type, requesting_user=None, requested_user=None, url=None, token=None):
        """
        Calls the url using the given auth_type.
        Arguments:
            - requesting_user is the user that is making the call to the url. Defaults to self.student.
            - requested_user is user that is passed to the url. Defaults to self.student.
            - url defaults to the response from calling self.get_url with requested_user.username.
            - token defaults to the default creation of the token given the value of auth_type.
        """
        requesting_user = requesting_user or self.student
        requested_user = requested_user or self.student

        auth_header = None
        if auth_type == AuthType.session:
            self.client.login(username=requesting_user.username, password=self.user_password)
        elif auth_type == AuthType.oauth:
            if not token:
                token = self._create_oauth_token(requesting_user)
            auth_header = f"Bearer {token}"
        else:
            assert auth_type in JWT_AUTH_TYPES
            if not token:
                token = self._create_jwt_token(requesting_user, auth_type)
            auth_header = f"JWT {token}"

        extra = dict(HTTP_AUTHORIZATION=auth_header) if auth_header else {}
        return self.client.get(
            url if url else self.get_url(requested_user.username),
            **extra
        )

    def _create_oauth_token(self, user):
        """ Creates and returns an OAuth token for the given user. """
        dot_app_user = UserFactory.create(password=self.user_password)
        dot_app = dot_models.Application.objects.create(
            name='test app',
            user=dot_app_user,
            client_type='confidential',
            authorization_grant_type='authorization-code',
            redirect_uris='http://localhost:8079/complete/edxorg/'
        )
        return dot_models.AccessToken.objects.create(
            user=user,
            application=dot_app,
            expires=utcnow() + timedelta(weeks=1),
            scope='read write',
            token='test_token',
        )

    def _create_jwt_token(self, user, auth_type, scopes=None, include_org_filter=True, include_me_filter=False):
        """ Creates and returns a JWT token for the given user with the given parameters. """
        filters = []
        if include_org_filter:
            filters += [f'content_org:{self.course.id.org}']
        if include_me_filter:
            filters += ['user:me']

        if scopes is None:
            scopes = self.default_scopes

        return _create_jwt(
            user,
            scopes=scopes,
            is_restricted=(auth_type == AuthType.jwt_restricted),
            filters=filters,
        )

    def _assert_in_log(self, text, mock_log_method):
        assert mock_log_method.called
        assert text in mock_log_method.call_args_list[0][0][0]

    def test_anonymous_user(self):
        resp = self.client.get(self.get_url(self.student.username))
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    @ddt.data(*JWT_AUTH_TYPES)
    def test_self_user(self, auth_type):
        resp = self.get_response(auth_type)
        assert resp.status_code == status.HTTP_200_OK
        self.assert_success_response_for_student(resp)

    @ddt.data(*list(AuthType))
    def test_staff_user(self, auth_type):
        resp = self.get_response(auth_type, requesting_user=self.global_staff)
        assert resp.status_code == status.HTTP_200_OK
        self.assert_success_response_for_student(resp)

    @ddt.data(*list(AuthType))
    def test_inactive_user(self, auth_type):
        self.student.is_active = False
        self.student.save()
        resp = self.get_response(auth_type)
        assert resp.status_code == status.HTTP_200_OK

    @patch('edx_rest_framework_extensions.permissions.log')
    @ddt.data(*list(AuthType))
    def test_another_user(self, auth_type, mock_log):
        """
        Returns 403 for OAuth, Session, and JWT auth with IsUserInUrl.
        Returns 200 for jwt_restricted and user:me filter unset.
        """
        resp = self.get_response(auth_type, requesting_user=self.other_student)

        # Restricted JWT tokens without the user:me filter have access to other users
        expected_jwt_access_granted = auth_type == AuthType.jwt_restricted

        assert resp.status_code == (status.HTTP_200_OK if expected_jwt_access_granted else status.HTTP_403_FORBIDDEN)
        if not expected_jwt_access_granted:
            self._assert_in_log("IsUserInUrl", mock_log.info)

    @patch('edx_rest_framework_extensions.permissions.log')
    @ddt.data(*JWT_AUTH_TYPES)
    def test_jwt_no_scopes(self, auth_type, mock_log):
        """ Returns 403 when scopes are enforced with JwtHasScope. """
        jwt_token = self._create_jwt_token(self.student, auth_type, scopes=[])
        resp = self.get_response(AuthType.jwt, token=jwt_token)

        is_enforced = auth_type == AuthType.jwt_restricted
        assert resp.status_code == (status.HTTP_403_FORBIDDEN if is_enforced else status.HTTP_200_OK)

        if is_enforced:
            self._assert_in_log("JwtHasScope", mock_log.warning)

    @patch('edx_rest_framework_extensions.permissions.log')
    @ddt.data(*JWT_AUTH_TYPES)
    def test_jwt_no_filter(self, auth_type, mock_log):
        """ Returns 403 when scopes are enforced with JwtHasContentOrgFilterForRequestedCourse. """
        jwt_token = self._create_jwt_token(self.student, auth_type, include_org_filter=False)
        resp = self.get_response(AuthType.jwt, token=jwt_token)

        is_enforced = auth_type == AuthType.jwt_restricted
        assert resp.status_code == (status.HTTP_403_FORBIDDEN if is_enforced else status.HTTP_200_OK)

        if is_enforced:
            self._assert_in_log("JwtHasContentOrgFilterForRequestedCourse", mock_log.warning)

    @ddt.data(*JWT_AUTH_TYPES)
    def test_jwt_on_behalf_of_user(self, auth_type):
        jwt_token = self._create_jwt_token(self.student, auth_type, include_me_filter=True)

        resp = self.get_response(AuthType.jwt, token=jwt_token)
        assert resp.status_code == status.HTTP_200_OK

    @patch('edx_rest_framework_extensions.permissions.log')
    @ddt.data(*JWT_AUTH_TYPES)
    def test_jwt_on_behalf_of_other_user(self, auth_type, mock_log):
        """ Returns 403 when scopes are enforced with JwtHasUserFilterForRequestedUser. """
        jwt_token = self._create_jwt_token(self.other_student, auth_type, include_me_filter=True)
        resp = self.get_response(AuthType.jwt, token=jwt_token)

        assert resp.status_code == status.HTTP_403_FORBIDDEN

        if auth_type == AuthType.jwt_restricted:
            self._assert_in_log("JwtHasUserFilterForRequestedUser", mock_log.warning)
        else:
            self._assert_in_log("IsUserInUrl", mock_log.info)

    def test_valid_oauth_token(self):
        resp = self.get_response(AuthType.oauth)
        assert resp.status_code == status.HTTP_200_OK

    def test_invalid_oauth_token(self):
        resp = self.get_response(AuthType.oauth, token="fooooooooooToken")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_expired_oauth_token(self):
        token = self._create_oauth_token(self.student)
        token.expires = utcnow() - timedelta(weeks=1)
        token.save()
        resp = self.get_response(AuthType.oauth, token=token)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED
