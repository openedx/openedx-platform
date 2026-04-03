""" Common Authentication Handlers used across projects. """

import logging

import django.utils.timezone
from edx_django_utils.monitoring import set_custom_attribute
from oauth2_provider import models as dot_models
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

OAUTH2_TOKEN_ERROR = 'token_error'
OAUTH2_TOKEN_ERROR_EXPIRED = 'token_expired'
OAUTH2_TOKEN_ERROR_MALFORMED = 'token_malformed'
OAUTH2_TOKEN_ERROR_NONEXISTENT = 'token_nonexistent'
OAUTH2_TOKEN_ERROR_NOT_PROVIDED = 'token_not_provided'
OAUTH2_USER_NOT_ACTIVE_ERROR = 'user_not_active'
OAUTH2_USER_DISABLED_ERROR = 'user_is_disabled'


logger = logging.getLogger(__name__)


class BearerAuthentication(BaseAuthentication):
    """
    BearerAuthentication backend using either `django-oauth2-provider` or 'django-oauth-toolkit'
    """

    www_authenticate_realm = 'api'

    # currently, active users are users that confirm their email.
    # a subclass could override `allow_inactive_users` to enable access without email confirmation,
    # like in the case of mobile users.
    allow_inactive_users = False

    def authenticate(self, request):
        """
        Returns tuple (user, token) if access token authentication  succeeds,
        returns None if the user did not try to authenticate using an access
        token, or raises an AuthenticationFailed (HTTP 401) if authentication
        fails.
        """

        set_custom_attribute("BearerAuthentication", "Failed")  # default value
        auth = get_authorization_header(request).split()

        if len(auth) == 1:  # lint-amnesty, pylint: disable=no-else-raise
            raise AuthenticationFailed({
                'error_code': OAUTH2_TOKEN_ERROR_NOT_PROVIDED,
                'developer_message': 'Invalid token header. No credentials provided.'})
        elif len(auth) > 2:
            raise AuthenticationFailed({
                'error_code': OAUTH2_TOKEN_ERROR_MALFORMED,
                'developer_message': 'Invalid token header. Token string should not contain spaces.'})

        if auth and auth[0].lower() == b'bearer':
            access_token = auth[1].decode('utf8')
        else:
            set_custom_attribute("BearerAuthentication", "None")
            return None

        user, token = self.authenticate_credentials(access_token)

        set_custom_attribute("BearerAuthentication", "Success")

        return user, token

    def authenticate_credentials(self, access_token):
        """
        Authenticate the request, given the access token.

        Overrides base class implementation to discard failure if user is
        inactive.
        """

        try:
            token = self.get_access_token(access_token)
        except AuthenticationFailed as exc:
            raise AuthenticationFailed({  # lint-amnesty, pylint: disable=raise-missing-from
                'error_code': OAUTH2_TOKEN_ERROR,
                'developer_message': exc.detail
            })

        if not token:  # lint-amnesty, pylint: disable=no-else-raise
            raise AuthenticationFailed({
                'error_code': OAUTH2_TOKEN_ERROR_NONEXISTENT,
                'developer_message': 'The provided access token does not match any valid tokens.'
            })
        elif token.expires < django.utils.timezone.now():
            raise AuthenticationFailed({
                'error_code': OAUTH2_TOKEN_ERROR_EXPIRED,
                'developer_message': 'The provided access token has expired and is no longer valid.',
            })
        else:
            user = token.user
            has_application = dot_models.Application.objects.filter(user_id=user.id)
            if not user.has_usable_password() and not has_application:
                msg = 'User disabled by admin: %s' % user.get_username()
                raise AuthenticationFailed({
                    'error_code': OAUTH2_USER_DISABLED_ERROR,
                    'developer_message': msg})

            # Check to make sure the users have activated their account (by confirming their email)
            if not self.allow_inactive_users and not user.is_active:  # lint-amnesty, pylint: disable=no-else-raise
                set_custom_attribute("BearerAuthentication_user_active", False)
                msg = 'User inactive or deleted: %s' % user.get_username()
                raise AuthenticationFailed({
                    'error_code': OAUTH2_USER_NOT_ACTIVE_ERROR,
                    'developer_message': msg})
            else:
                set_custom_attribute("BearerAuthentication_user_active", True)

            return user, token

    def get_access_token(self, access_token):
        """
        Return a valid access token stored by django-oauth-toolkit (DOT), or
        None if no matching token is found.
        """
        token_query = dot_models.AccessToken.objects.select_related('user')
        return token_query.filter(token=access_token).first()

    def authenticate_header(self, request):
        """
        Return a string to be used as the value of the `WWW-Authenticate`
        header in a `401 Unauthenticated` response
        """
        return 'Bearer realm="%s"' % self.www_authenticate_realm


class BearerAuthenticationAllowInactiveUser(BearerAuthentication):
    """
    Currently, is_active field on the user is coupled
    with whether or not the user has verified ownership of their claimed email address.
    Once is_active is decoupled from verified_email, we will no longer need this
    class override.

    This class can be used for an OAuth2-accessible endpoint that allows users to access
    that endpoint without having their email verified.  For example, this is used
    for mobile endpoints.
    """

    allow_inactive_users = True


class OAuth2Authentication(BearerAuthentication):
    """
    Creating temperary class cause things outside of edx-platform need OAuth2Authentication.
    This will be removed when repos outside edx-platform import BearerAuthentiction instead.
    """


class OAuth2AuthenticationAllowInactiveUser(BearerAuthenticationAllowInactiveUser):
    """
    Creating temperary class cause things outside of edx-platform need OAuth2Authentication.
    This will be removed when repos outside edx-platform import BearerAuthentiction instead.
    """
