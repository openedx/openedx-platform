"""Utilities for working with ID tokens."""


import json  # noqa: I001
import logging
from time import time

import jwt
from django.conf import settings
from edx_django_utils.monitoring import increment, set_custom_attribute
from edx_rbac.utils import create_role_auth_claim_for_user
from edx_toggles.toggles import SettingToggle
from jwt import PyJWK
from jwt.utils import base64url_encode
from oauth2_provider.models import Application

from common.djangoapps.student.models import UserProfile, anonymous_id_for_user

log = logging.getLogger(__name__)


def create_jwt_for_user(user, secret=None, aud=None, additional_claims=None, scopes=None):
    """
    Returns a JWT to identify the given user.

    TODO (ARCH-204) Note the returned JWT does not have an underlying access
    token associated with it and so cannot be invalidated nor refreshed. This
    interface should be revisited when addressing authentication-related cleanup
    as part of ARCH-204.

    Arguments:
        user (User): User for which to generate the JWT.
        scopes (list): Optional. Scopes that limit access to the token bearer and
            controls which optional claims are included in the token.

    Deprecated Arguments (to be removed):
        secret (string): Overrides configured JWT secret (signing) key.
        aud (string): Optional. Overrides configured JWT audience claim.
        additional_claims (dict): Optional. Additional claims to include in the token.
    """
    expires_in = settings.OAUTH_ID_TOKEN_EXPIRATION
    return _create_jwt(
        user,
        scopes=scopes,
        expires_in=expires_in,
        aud=aud,
        additional_claims=additional_claims,
        secret=secret,
        use_asymmetric_key=False,
    )


def create_jwt_token_dict(token_dict, oauth_adapter, use_asymmetric_key=None):
    """
    Returns a JWT access token dict based on the provided access token.

    Arguments:
        token_dict (dict): An access token structure as returned from an
            underlying OAuth provider. Dict includes "access_token",
            "expires_in", "token_type", and "scope".

    Deprecated Arguments (to be removed):
        oauth_adapter (DOPAdapter|DOTAdapter): An OAuth adapter that will
            provide the given token's information.
        use_asymmetric_key (Boolean): Optional. Whether the JWT should be signed
            with this app's private key. If not provided, defaults to whether
            the OAuth client is restricted.
    """
    access_token = oauth_adapter.get_access_token(token_dict['access_token'])
    client = oauth_adapter.get_client_for_token(access_token)

    jwt_expires_in = _get_jwt_access_token_expire_seconds()
    try:
        grant_type = access_token.application.authorization_grant_type
    except Exception:  # pylint: disable=broad-except
        # TODO: Remove this broad except if proven this doesn't happen.
        grant_type = 'unknown-error'
        log.exception('Unable to get grant_type from access token.')

    # .. custom_attribute_name: create_jwt_grant_type
    # .. custom_attribute_description: The grant type of the newly created JWT.
    set_custom_attribute('create_jwt_grant_type', grant_type)
    scopes = _get_updated_scopes(token_dict['scope'].split(), grant_type)

    jwt_access_token = _create_jwt(
        access_token.user,
        scopes=scopes,
        expires_in=jwt_expires_in,
        use_asymmetric_key=use_asymmetric_key,
        is_restricted=oauth_adapter.is_client_restricted(client),
        filters=oauth_adapter.get_authorization_filters(client),
        grant_type=grant_type,
    )

    jwt_token_dict = token_dict.copy()
    # Note: only "refresh_token" is not overwritten at this point.
    # At this time, the user_id scope added for grant type password is only added to the
    # JWT, and is not added for the DOT access token or refresh token, so we must override
    # here. If this inconsistency becomes an issue, then the user_id scope should be
    # added earlier with the DOT tokens, and we would no longer need to override "scope".
    jwt_token_dict.update({
        "access_token": jwt_access_token,
        "token_type": "JWT",
        "expires_in": jwt_expires_in,
        "scope": ' '.join(scopes),
    })
    return jwt_token_dict


def create_jwt_from_token(token_dict, oauth_adapter, use_asymmetric_key=None):
    """
    Returns a JWT created from the provided access token dict.

    Note: if you need the token dict, and not just the JWT, use
        create_jwt_token_dict instead. See its docs for more details.
    """
    jwt_token_dict = create_jwt_token_dict(token_dict, oauth_adapter, use_asymmetric_key)
    return jwt_token_dict["access_token"]


def _get_jwt_access_token_expire_seconds():
    """
    Returns the number of seconds before a JWT access token expires.

    .. setting_name: JWT_ACCESS_TOKEN_EXPIRE_SECONDS
    .. setting_default: 60 * 60
    .. setting_description: The number of seconds a JWT access token remains valid. We use this
        custom setting for JWT formatted access tokens, rather than the django-oauth-toolkit setting
        ACCESS_TOKEN_EXPIRE_SECONDS, because the JWT is non-revocable and we want it to be shorter
        lived than the legacy Bearer (opaque) access tokens, and thus to have a smaller default.
    .. setting_warning: For security purposes, 1 hour (the default) is the maximum recommended setting
        value. For tighter security, you can use a shorter amount of time.
    """
    return getattr(settings, 'JWT_ACCESS_TOKEN_EXPIRE_SECONDS', 60 * 60)


def _create_jwt(
    user,
    scopes=None,
    expires_in=None,
    is_restricted=False,
    filters=None,
    aud=None,
    additional_claims=None,
    use_asymmetric_key=None,
    secret=None,
    grant_type=None,
):
    """
    Returns an encoded JWT (string).

    Arguments:
        user (User): User for which to generate the JWT.
        scopes (list): Optional. Scopes that limit access to the token bearer and
            controls which optional claims are included in the token.
            Defaults to ['email', 'profile'].
        expires_in (int): Optional. Overrides time to token expiry, specified in seconds.
        filters (list): Optional. Filters to include in the JWT.
        is_restricted (Boolean): Whether the client to whom the JWT is issued is restricted.
        grant_type (str): grant type of the new JWT token.

    Deprecated Arguments (to be removed):
        aud (string): Optional. Overrides configured JWT audience claim.
        additional_claims (dict): Optional. Additional claims to include in the token.
        use_asymmetric_key (Boolean): Optional. Whether the JWT should be signed
            with this app's private key. If not provided, defaults to whether
            the OAuth client is restricted.
        secret (string): Overrides configured JWT secret (signing) key.
    """
    use_asymmetric_key = _get_use_asymmetric_key_value(is_restricted, use_asymmetric_key)
    # Enable monitoring of key type used. Use increment in case there are multiple calls in a transaction.
    if use_asymmetric_key:
        increment('create_asymmetric_jwt_count')
    else:
        increment('create_symmetric_jwt_count')

    # Scopes `email` and `profile` are included for legacy compatibility.
    scopes = scopes or ['email', 'profile']
    iat, exp = _compute_time_fields(expires_in)

    payload = {
        # TODO (ARCH-204) Consider getting rid of the 'aud' claim since we don't use it.
        'aud': aud if aud else settings.JWT_AUTH['JWT_AUDIENCE'],
        'exp': exp,
        'grant_type': grant_type or '',
        'iat': iat,
        'iss': settings.JWT_AUTH['JWT_ISSUER'],
        'preferred_username': user.username,
        'scopes': scopes,
        'version': settings.JWT_AUTH['JWT_SUPPORTED_VERSION'],
        'sub': anonymous_id_for_user(user, None),
        'filters': filters or [],
        'is_restricted': is_restricted,
        'email_verified': user.is_active,
    }
    payload.update(additional_claims or {})
    _update_from_additional_handlers(payload, user, scopes)
    role_claims = create_role_auth_claim_for_user(user)
    if role_claims:
        payload['roles'] = role_claims
    return _encode_and_sign(payload, use_asymmetric_key, secret)


# .. toggle_name: JWT_AUTH_FORCE_CREATE_ASYMMETRIC
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: When True, forces the LMS to only create JWTs signed with the asymmetric
#   key. This is a temporary rollout toggle for DEPR of symmetric JWTs.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2023-04-10
# .. toggle_target_removal_date: 2023-07-31
# .. toggle_tickets: https://github.com/openedx/public-engineering/issues/83
JWT_AUTH_FORCE_CREATE_ASYMMETRIC = SettingToggle(
    'JWT_AUTH_FORCE_CREATE_ASYMMETRIC', default=False, module_name=__name__
)


def _get_use_asymmetric_key_value(is_restricted, use_asymmetric_key):
    """
    Returns the value to use for use_asymmetric_key.
    """
    if JWT_AUTH_FORCE_CREATE_ASYMMETRIC.is_enabled():
        return True

    return use_asymmetric_key or is_restricted


def _compute_time_fields(expires_in):
    """
    Returns (iat, exp) tuple to be used as time-related values in a token.
    """
    now = int(time())
    expires_in = expires_in or settings.JWT_AUTH['JWT_EXPIRATION']
    return now, now + expires_in


def _update_from_additional_handlers(payload, user, scopes):
    """
    Updates the given token payload with data from additional handlers, as
    requested by the given scopes.
    """
    _claim_handlers = {
        'user_id': _attach_user_id_claim,
        'email': _attach_email_claim,
        'profile': _attach_profile_claim,
    }
    for scope in scopes:
        handler = _claim_handlers.get(scope)
        if handler:
            handler(payload, user)


def _attach_user_id_claim(payload, user):
    """Add the user_id claim details to the JWT payload."""
    payload['user_id'] = user.id


def _attach_email_claim(payload, user):
    """Add the email claim details to the JWT payload."""
    payload['email'] = user.email


def _attach_profile_claim(payload, user):
    """Add the profile claim details to the JWT payload."""
    try:
        # Some users (e.g., service users) may not have user profiles.
        name = UserProfile.objects.get(user=user).name
    except UserProfile.DoesNotExist:
        name = None

    payload.update({
        'name': name,
        'family_name': user.last_name,
        'given_name': user.first_name,
        'administrator': user.is_staff,
        'superuser': user.is_superuser,
    })

# .. toggle_name: JWT_AUTH_ADD_KID_HEADER
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: When True, add KID header to JWT using asymmetrical key.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2024-03-20
# .. toggle_target_removal_date: 2024-04-20
# .. toggle_tickets:
# https://2u-internal.atlassian.net/browse/AUTH-195?atlOrigin=eyJpIjoiODMzODBiODMwMjU5NGRiZTkyOTIzYThhZjZiNWE0MzMiLCJwIjoiaiJ9
JWT_AUTH_ADD_KID_HEADER = SettingToggle(
    'JWT_AUTH_ADD_KID_HEADER', default=False, module_name=__name__
)


def _encode_and_sign(payload, use_asymmetric_key, secret):
    """Encode and sign the provided payload."""

    if use_asymmetric_key:
        key = json.loads(settings.JWT_AUTH['JWT_PRIVATE_SIGNING_JWK'])
        algorithm = settings.JWT_AUTH['JWT_SIGNING_ALGORITHM']
    else:
        secret = secret if secret else settings.JWT_AUTH['JWT_SECRET_KEY']
        key = {'k': base64url_encode(secret.encode('utf-8')), 'kty': 'oct'}
        algorithm = settings.JWT_AUTH['JWT_ALGORITHM']

    jwk = PyJWK(key, algorithm)
    if JWT_AUTH_ADD_KID_HEADER.is_enabled() and jwk.key_id:
        return jwt.encode(payload, jwk.key, algorithm=algorithm, headers={'kid': jwk.key_id})

    return jwt.encode(payload, jwk.key, algorithm=algorithm)


def _get_updated_scopes(scopes, grant_type):
    """
    Default scopes should only contain non-privileged data.
    Do not be misled by the fact that `email` and `profile` are default scopes.
    They were included for legacy compatibility, even though they contain privileged
    data. The scope `user_id` must be added for requests with grant_type password.
    """
    scopes = scopes or ['email', 'profile']

    if grant_type == Application.GRANT_PASSWORD and 'user_id' not in scopes:
        scopes.append('user_id')
    return scopes
