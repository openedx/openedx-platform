""" Commerce API Service. """


import datetime
import json
import os
import socket

import requests
import slumber
from django.conf import settings
from edx_django_utils.cache import TieredCache
from edx_django_utils.monitoring import set_custom_attribute
from edx_rest_api_client.auth import JwtAuth, SuppliedJwtAuth
from eventtracking import tracker
from requests.auth import AuthBase

from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

# When caching tokens, use this value to err on expiring tokens a little early so they are
# sure to be valid at the time they are used.
ACCESS_TOKEN_EXPIRED_THRESHOLD_SECONDS = 5

# How long should we wait to connect to the auth service.
# https://requests.readthedocs.io/en/master/user/advanced/#timeouts
REQUEST_CONNECT_TIMEOUT = 3.05
__version__ = '5.6.1'
REQUEST_READ_TIMEOUT = 5

ECOMMERCE_DATE_FORMAT = '%Y-%m-%dT%H:%M:%SZ'


class BearerAuth(AuthBase):
    """
    Attaches Bearer Authentication to the given Request object.
    """

    def __init__(self, token):
        """
        Instantiate the auth class.
        """
        self.token = token

    def __call__(self, r):
        """
        Update the request headers.
        """
        r.headers['Authorization'] = f'Bearer {self.token}'
        return r


def user_agent():
    """
    Return a User-Agent that identifies this client.

    Example:
        python-requests/2.9.1 edx-rest-api-client/1.7.2 ecommerce

    The last item in the list will be the application name, taken from the
    OS environment variable EDX_REST_API_CLIENT_NAME. If that environment
    variable is not set, it will default to the hostname.
    """
    client_name = 'unknown_client_name'
    try:
        client_name = os.environ.get("EDX_REST_API_CLIENT_NAME") or socket.gethostbyname(socket.gethostname())
    except:  # pylint: disable=bare-except
        pass  # using 'unknown_client_name' is good enough.  no need to log.
    return "{} edx-rest-api-client/{} {}".format(
        requests.utils.default_user_agent(),  # e.g. "python-requests/2.9.1"
        __version__,  # version of this client
        client_name
    )


USER_AGENT = user_agent()


def _get_oauth_url(url):
    """
    Returns the complete url for the oauth2 endpoint.

    Args:
        url (str): base url of the LMS oauth endpoint, which can optionally include some or all of the path
            ``/oauth2/access_token``. Common example settings that would work for ``url`` would include:
                LMS_BASE_URL = 'http://edx.devstack.lms:18000'
                BACKEND_SERVICE_EDX_OAUTH2_PROVIDER_URL = 'http://edx.devstack.lms:18000/oauth2'

    """
    stripped_url = url.rstrip('/')
    if stripped_url.endswith('/access_token'):
        return url

    if stripped_url.endswith('/oauth2'):
        return stripped_url + '/access_token'

    return stripped_url + '/oauth2/access_token'


def get_oauth_access_token(url, client_id, client_secret, token_type='jwt', grant_type='client_credentials',
                           refresh_token=None,
                           timeout=(REQUEST_CONNECT_TIMEOUT, REQUEST_READ_TIMEOUT)):
    """
    Retrieves OAuth 2.0 access token using the given grant type.

    Args:
        url (str): Oauth2 access token endpoint, optionally including part of the path.
        client_id (str): client ID
        client_secret (str): client secret
    Kwargs:
        token_type (str): Type of token to return. Options include bearer and jwt.
        grant_type (str): One of 'client_credentials' or 'refresh_token'
        refresh_token (str): The previous access token (for grant_type=refresh_token)

    Raises:
        requests.RequestException if there is a problem retrieving the access token.

    Returns:
        tuple: Tuple containing (access token string, expiration datetime).

    """
    now = datetime.datetime.utcnow()
    data = {
        'grant_type': grant_type,
        'client_id': client_id,
        'client_secret': client_secret,
        'token_type': token_type,
    }
    if refresh_token:
        data['refresh_token'] = refresh_token
    else:
        assert grant_type != 'refresh_token', "refresh_token parameter required"

    response = requests.post(
        _get_oauth_url(url),
        data=data,
        headers={
            'User-Agent': USER_AGENT,
        },
        timeout=timeout
    )

    response.raise_for_status()  # Raise an exception for bad status codes.
    try:
        data = response.json()
        access_token = data['access_token']
        expires_in = data['expires_in']
    except (KeyError, json.decoder.JSONDecodeError) as json_error:
        raise requests.RequestException(response=response) from json_error

    expires_at = now + datetime.timedelta(seconds=expires_in)

    return access_token, expires_at


def get_and_cache_oauth_access_token(url, client_id, client_secret, token_type='jwt', grant_type='client_credentials',
                                     refresh_token=None,
                                     timeout=(REQUEST_CONNECT_TIMEOUT, REQUEST_READ_TIMEOUT)):
    """
    Retrieves a possibly cached OAuth 2.0 access token using the given grant type.

    See ``get_oauth_access_token`` for usage details.

    First retrieves the access token from the cache and ensures it has not expired. If
    the access token either wasn't found in the cache, or was expired, retrieves a new
    access token and caches it for the lifetime of the token.

    Note: Consider tokens to be expired ACCESS_TOKEN_EXPIRED_THRESHOLD_SECONDS early
    to ensure the token won't expire while it is in use.

    Returns:
        tuple: Tuple containing (access token string, expiration datetime).

    """
    oauth_url = _get_oauth_url(url)
    cache_key = 'edx_rest_api_client.access_token.{}.{}.{}.{}'.format(
        token_type,
        grant_type,
        client_id,
        oauth_url,
    )
    cached_response = TieredCache.get_cached_response(cache_key)

    # Attempt to get an unexpired cached access token
    if cached_response.is_found:
        _, expiration = cached_response.value
        # Double-check the token hasn't already expired as a safety net.
        adjusted_expiration = expiration - datetime.timedelta(seconds=ACCESS_TOKEN_EXPIRED_THRESHOLD_SECONDS)
        if datetime.datetime.utcnow() < adjusted_expiration:
            return cached_response.value

    # Get a new access token if no unexpired access token was found in the cache.
    oauth_access_token_response = get_oauth_access_token(
        oauth_url,
        client_id,
        client_secret,
        grant_type=grant_type,
        refresh_token=refresh_token,
        timeout=timeout,
    )

    # Cache the new access token with an expiration matching the lifetime of the token.
    _, expiration = oauth_access_token_response
    expires_in = (expiration - datetime.datetime.utcnow()).seconds - ACCESS_TOKEN_EXPIRED_THRESHOLD_SECONDS
    TieredCache.set_all_tiers(cache_key, oauth_access_token_response, expires_in)

    return oauth_access_token_response


def create_tracking_context(user):
    """ Assembles attributes from user and request objects to be sent along
    in E-Commerce API calls for tracking purposes. """
    context_tracker = tracker.get_tracker().resolve_context()

    return {
        'lms_user_id': user.id,
        'lms_ip': context_tracker.get('ip'),
    }


def is_commerce_service_configured():
    """
    Return a Boolean indicating whether or not configuration is present to use the external commerce service.
    """
    ecommerce_api_url = configuration_helpers.get_value('ECOMMERCE_API_URL', settings.ECOMMERCE_API_URL)
    return bool(ecommerce_api_url)


def get_ecommerce_api_base_url():
    """
    Returns an E-Commerce API base URL.
    """
    return configuration_helpers.get_value('ECOMMERCE_API_URL', settings.ECOMMERCE_API_URL)


def ecommerce_api_client(user, session=None):
    """
    Returns an E-Commerce API client setup with authentication for the specified user.

    DEPRECATED: To be replaced with get_ecommerce_api_client.
    """
    claims = {'tracking_context': create_tracking_context(user)}
    scopes = [
        'user_id',
        'email',
        'profile'
    ]
    jwt = create_jwt_for_user(user, additional_claims=claims, scopes=scopes)

    return DeprecatedRestApiClient(
        configuration_helpers.get_value('ECOMMERCE_API_URL', settings.ECOMMERCE_API_URL),
        jwt=jwt,
        session=session
    )


def get_ecommerce_api_client(user):
    """
    Returns an E-Commerce API client setup with authentication for the specified user.
    """
    claims = {'tracking_context': create_tracking_context(user)}
    scopes = [
        'user_id',
        'email',
        'profile'
    ]
    jwt = create_jwt_for_user(user, additional_claims=claims, scopes=scopes)

    client = requests.Session()
    client.auth = SuppliedJwtAuth(jwt)

    return client


class DeprecatedRestApiClient(slumber.API):
    """
    API client for edX REST API.

    (deprecated)  See docs/decisions/0002-oauth-api-client-replacement.rst.
    """

    @classmethod
    def user_agent(cls):
        return USER_AGENT

    @classmethod
    def get_oauth_access_token(cls, url, client_id, client_secret, token_type='bearer',
                               timeout=(REQUEST_CONNECT_TIMEOUT, REQUEST_READ_TIMEOUT)):
        """
        To help transition to OAuthAPIClient, use DeprecatedRestApiClient.
        get_and_cache_jwt_oauth_access_token instead'

        'of DeprecatedRestApiClient.get_oauth_access_token to share cached jwt token used by OAuthAPIClient.'

        """
        return get_oauth_access_token(url, client_id, client_secret, token_type=token_type, timeout=timeout)

    @classmethod
    def get_and_cache_jwt_oauth_access_token(cls, url, client_id, client_secret,
                                             timeout=(REQUEST_CONNECT_TIMEOUT, REQUEST_READ_TIMEOUT)):
        return get_and_cache_oauth_access_token(url, client_id, client_secret, token_type="jwt", timeout=timeout)

    def __init__(self, url, signing_key=None, username=None, full_name=None, email=None,
                 timeout=5, issuer=None, expires_in=30, tracking_context=None, oauth_access_token=None,
                 session=None, jwt=None, **kwargs):
        """
        DeprecatedRestApiClient is deprecated. Use OAuthAPIClient instead.

        Instantiate a new client. You can pass extra kwargs to Slumber like
        'append_slash'.

        Raises:
            ValueError: If a URL is not provided.

        """
        set_custom_attribute('api_client', 'DeprecatedRestApiClient')
        if not url:
            raise ValueError('An API url must be supplied!')

        if jwt:
            auth = SuppliedJwtAuth(jwt)
        elif oauth_access_token:
            auth = BearerAuth(oauth_access_token)
        elif signing_key and username:
            auth = JwtAuth(username, full_name, email, signing_key,
                           issuer=issuer, expires_in=expires_in, tracking_context=tracking_context)
        else:
            auth = None

        session = session or requests.Session()
        session.headers['User-Agent'] = self.user_agent()

        session.timeout = timeout
        super().__init__(
            url,
            session=session,
            auth=auth,
            **kwargs
        )
