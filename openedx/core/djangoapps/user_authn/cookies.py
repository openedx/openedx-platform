"""
Utility functions for setting "logged in" cookies used by subdomains.
"""


import json
import logging
import time
from urllib.parse import urljoin

from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.dispatch import Signal
from django.urls import NoReverseMatch, reverse
from django.utils.http import http_date, parse_http_date
from edx_rest_framework_extensions.auth.jwt import cookies as jwt_cookies
from edx_rest_framework_extensions.auth.jwt.constants import JWT_DELIMITER
from oauth2_provider.models import Application
from common.djangoapps.student.models import UserProfile

from openedx.core.djangoapps.oauth_dispatch.adapters import DOTAdapter
from openedx.core.djangoapps.oauth_dispatch.api import create_dot_access_token
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_from_token
from openedx.core.djangoapps.user_api.accounts.utils import retrieve_last_sitewide_block_completed
from openedx.core.djangoapps.user_authn.exceptions import AuthFailedError
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from common.djangoapps.util.json_request import JsonResponse
from openedx.core.djangoapps.user_api.accounts.image_helpers import get_profile_image_urls_for_user


log = logging.getLogger(__name__)

# providing_args=['user', 'response']
CREATE_LOGON_COOKIE = Signal()


JWT_COOKIE_NAMES = (
    # Header and payload sections of a JSON Web Token containing user
    # information and used as an access token.
    jwt_cookies.jwt_cookie_header_payload_name(),

    # Signature section of a JSON Web Token.
    jwt_cookies.jwt_cookie_signature_name(),
)

# TODO (ARCH-245): Remove the following deprecated cookies.
DEPRECATED_LOGGED_IN_COOKIE_NAMES = (
    # Set to 'true' if the user is logged in.
    settings.EDXMKTG_LOGGED_IN_COOKIE_NAME,

    # JSON-encoded dictionary with user information.
    settings.EDXMKTG_USER_INFO_COOKIE_NAME,
)

ALL_LOGGED_IN_COOKIE_NAMES = JWT_COOKIE_NAMES + DEPRECATED_LOGGED_IN_COOKIE_NAMES


def are_logged_in_cookies_set(request):
    """ Check whether the request has logged in cookies set. """
    if settings.FEATURES.get('DISABLE_SET_JWT_COOKIES_FOR_TESTS', False):
        cookies_that_should_exist = DEPRECATED_LOGGED_IN_COOKIE_NAMES
    else:
        cookies_that_should_exist = ALL_LOGGED_IN_COOKIE_NAMES

    return all(
        cookie_name in request.COOKIES
        for cookie_name in cookies_that_should_exist
    ) and request.COOKIES[settings.EDXMKTG_LOGGED_IN_COOKIE_NAME]


def delete_logged_in_cookies(response):
    """
    Delete cookies indicating that the user is logged in (except for session cookie.)
    Arguments:
        response (HttpResponse): The response sent to the client.
    Returns:
        HttpResponse
    """
    for cookie_name in ALL_LOGGED_IN_COOKIE_NAMES:
        response.delete_cookie(
            cookie_name,
            path='/',
            domain=settings.SHARED_COOKIE_DOMAIN
        )

    return response


def standard_cookie_settings(request):
    """ Returns the common cookie settings (e.g. expiration time). """

    cookie_settings = {
        'domain': settings.SHARED_COOKIE_DOMAIN,
        'path': '/',
        'httponly': None,
    }

    _set_expires_in_cookie_settings(cookie_settings, request.session.get_expiry_age())

    # In production, TLS should be enabled so that this cookie is encrypted
    # when we send it.  We also need to set "secure" to True so that the browser
    # will transmit it only over secure connections.
    #
    # In non-production environments (acceptance tests, devstack, and sandboxes),
    # we still want to set this cookie.  However, we do NOT want to set it to "secure"
    # because the browser won't send it back to us.  This can cause an infinite redirect
    # loop in the third-party auth flow, which calls `are_logged_in_cookies_set` to determine
    # whether it needs to set the cookie or continue to the next pipeline stage.
    cookie_settings['secure'] = request.is_secure()

    return cookie_settings


def _set_expires_in_cookie_settings(cookie_settings, expires_in):
    """
    Updates the max_age and expires fields of the given cookie_settings,
    based on the value of expires_in.
    """
    expires_time = time.time() + expires_in
    expires = http_date(expires_time)

    cookie_settings.update({
        'max_age': expires_in,
        'expires': expires,
    })


def set_logged_in_cookies(request, response, user):
    """
    Set cookies at the time of user login. See ALL_LOGGED_IN_COOKIE_NAMES to see
    which cookies are set.

    Arguments:
        request (HttpRequest): The request to the view, used to calculate
            the cookie's expiration date based on the session expiration date.
        response (HttpResponse): The response on which the cookie will be set.
        user (User): The currently logged in user.

    Returns:
        HttpResponse

    """
    # Note: The user may not yet be set on the request object by this time,
    # especially during third party authentication.  So use the user object
    # that is passed in when needed.

    if user.is_authenticated and not user.is_anonymous:

        # JWT cookies expire at the same time as other login-related cookies
        # so that cookie-based login determination remains consistent.
        cookie_settings = standard_cookie_settings(request)

        _set_deprecated_logged_in_cookie(response, cookie_settings)
        _set_deprecated_user_info_cookie(response, request, user, cookie_settings)
        _create_and_set_jwt_cookies(response, request, cookie_settings, user=user)
        CREATE_LOGON_COOKIE.send(sender=None, user=user, response=response)

    return response


def get_response_with_refreshed_jwt_cookies(request, user):
    """
    Generates the response and resets the JWT related cookies in the response for the given user.
    """
    cookie_settings = standard_cookie_settings(request)
    response = JsonResponse({})
    _create_and_set_jwt_cookies(response, request, cookie_settings, user=user)

    current_time = time.time()
    expires_date = cookie_settings.get('expires', None)
    expires_epoch = parse_http_date(expires_date) if expires_date else 0
    response.content = json.dumps(
        {
            'success': True,
            'user_id': user.id,
            'response_epoch_seconds': current_time,
            'response_http_date': http_date(current_time),
            'expires': expires_date if expires_date else 'not-found',
            'expires_epoch_seconds': expires_epoch,
        }
    )
    return response


def _set_deprecated_user_info_cookie(response, request, user, cookie_settings):
    """
    Sets the user info cookie on the response.

    The user info cookie has the following format:
    {
        "version": 1,
        "username": "test-user",
        "header_urls": {
            "account_settings": "https://example.com/account/settings",
            "resume_block":
                "https://example.com//courses/org.0/course_0/Run_0/jump_to/i4x://org.0/course_0/vertical/vertical_4"
            "learner_profile": "https://example.com/u/test-user",
            "logout": "https://example.com/logout"
        }
    }
    """
    user_info = _get_user_info_cookie_data(request, user)
    response.set_cookie(
        settings.EDXMKTG_USER_INFO_COOKIE_NAME,
        json.dumps(user_info),
        **cookie_settings
    )


def _set_deprecated_logged_in_cookie(response, cookie_settings):
    """ Sets the logged in cookie on the response. """

    # Backwards compatibility: set the cookie indicating that the user
    # is logged in.  This is just a boolean value, so it's not very useful.
    # In the future, we should be able to replace this with the "user info"
    # cookie set below.
    response.set_cookie(
        settings.EDXMKTG_LOGGED_IN_COOKIE_NAME,
        'true',
        **cookie_settings
    )

    return response


def _convert_to_absolute_uris(request, urls_obj):
    """ Convert relative URL paths to absolute URIs """
    for url_name, url_path in urls_obj.items():
        urls_obj[url_name] = request.build_absolute_uri(url_path)

    return urls_obj


def _get_user_info_cookie_data(request, user):
    """ Returns information that will populate the user info cookie. """

    # Set a cookie with user info.  This can be used by external sites
    # to customize content based on user information.  Currently,
    # we include information that's used to customize the "account"
    # links in the header of subdomain sites (such as the marketing site).
    header_urls = {'logout': reverse('logout')}

    # Unfortunately, this app is currently used by both the LMS and Studio login pages.
    # If we're in Studio, we won't be able to reverse the account/profile URLs.
    # To handle this, we don't add the URLs if we can't reverse them.
    # External sites will need to have fallback mechanisms to handle this case
    # (most likely just hiding the links).
    try:
        header_urls['account_settings'] = settings.ACCOUNT_MICROFRONTEND_URL
        header_urls['learner_profile'] = urljoin(
            configuration_helpers.get_value(
                'PROFILE_MICROFRONTEND_URL',
                settings.PROFILE_MICROFRONTEND_URL,
            ),
            f'/u/{user.username}',
        )
    except NoReverseMatch:
        pass

    # Add 'resume course' last completed block
    try:
        block_url = retrieve_last_sitewide_block_completed(user)
        if block_url:
            header_urls['resume_block'] = block_url
    except User.DoesNotExist:
        pass
    except Exception as err:  # pylint: disable=broad-except
        log.exception(
            '[PROD-2877] Error retrieving resume block for user %s with raw error %r',
            user.username, err,
        )

    header_urls = _convert_to_absolute_uris(request, header_urls)

    image_urls = {}
    try:
        image_urls = get_profile_image_urls_for_user(user)
    except UserProfile.DoesNotExist:
        pass

    image_urls = _convert_to_absolute_uris(request, image_urls)

    user_info = {
        'version': settings.EDXMKTG_USER_INFO_COOKIE_VERSION,
        'username': user.username,
        'email': user.email,
        'header_urls': header_urls,
        'user_image_urls': image_urls,
    }

    return user_info


def _create_and_set_jwt_cookies(response, request, cookie_settings, user=None):
    """ Sets a cookie containing a JWT on the response. """

    # Skip setting JWT cookies for most unit tests, since it raises errors when
    # a login oauth client cannot be found in the database in ``_get_login_oauth_client``.
    # This solution is not ideal, but see https://github.com/openedx/edx-platform/pull/19180#issue-226706355
    # for a discussion of alternative solutions that did not work or were halted.
    if settings.FEATURES.get('DISABLE_SET_JWT_COOKIES_FOR_TESTS', False):
        return

    expires_in = settings.JWT_AUTH['JWT_IN_COOKIE_EXPIRATION']
    _set_expires_in_cookie_settings(cookie_settings, expires_in)

    jwt = _create_jwt(request, user, expires_in)
    jwt_header_and_payload, jwt_signature = _parse_jwt(jwt)

    _set_jwt_cookies(
        response,
        cookie_settings,
        jwt_header_and_payload,
        jwt_signature,
    )


def _create_jwt(request, user, expires_in):
    """
    Creates and returns a jwt for the given user with the given expires_in value.
    """
    oauth_application = _get_login_oauth_client()
    access_token = create_dot_access_token(
        # Note: Scopes for JWT cookies do not require additional permissions
        request, user, oauth_application, expires_in=expires_in, scopes=['user_id', 'email', 'profile'],
    )
    return create_jwt_from_token(access_token, DOTAdapter(), use_asymmetric_key=True)


def _parse_jwt(jwt):
    """
    Parses and returns the following parts of the jwt: header_and_payload, signature
    """
    jwt_parts = jwt.split(JWT_DELIMITER)
    header_and_payload = JWT_DELIMITER.join(jwt_parts[0:2])
    signature = jwt_parts[2]
    return header_and_payload, signature


def _set_jwt_cookies(response, cookie_settings, jwt_header_and_payload, jwt_signature):
    """
    Sets the given jwt_header_and_payload, jwt_signature, and refresh token in 3 different cookies.
    The latter 2 cookies are set as httponly.
    """
    cookie_settings['httponly'] = None
    response.set_cookie(
        jwt_cookies.jwt_cookie_header_payload_name(),
        jwt_header_and_payload,
        **cookie_settings
    )

    cookie_settings['httponly'] = True
    response.set_cookie(
        jwt_cookies.jwt_cookie_signature_name(),
        jwt_signature,
        **cookie_settings
    )


def _get_login_oauth_client():
    """
    Returns the configured OAuth Client/Application used for Login.
    """
    login_client_id = settings.JWT_AUTH['JWT_LOGIN_CLIENT_ID']
    try:
        return Application.objects.get(client_id=login_client_id)
    except Application.DoesNotExist:
        raise AuthFailedError(  # lint-amnesty, pylint: disable=raise-missing-from
            f"OAuth Client for the Login service, '{login_client_id}', is not configured."
        )
