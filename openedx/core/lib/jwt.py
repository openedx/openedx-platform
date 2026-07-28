"""
JWT Token handling and signing functions.
"""

from time import time

import jwt
from django.conf import settings
from jwt.api_jwk import PyJWK, PyJWKSet
from jwt.exceptions import ExpiredSignatureError, InvalidSignatureError, MissingRequiredClaimError


def create_jwt(lms_user_id, expires_in_seconds, additional_token_claims, now=None):
    """
    Produce an encoded JWT (string) indicating some temporary permission for the indicated user.

    What permission that is must be encoded in additional_claims.
    Arguments:
        lms_user_id (int): LMS user ID this token is being generated for
        expires_in_seconds (int): Time to token expiry, specified in seconds.
        additional_token_claims (dict): Additional claims to include in the token.
        now(int): optional now value for testing
    """
    now = now or int(time())

    payload = {
        'lms_user_id': lms_user_id,
        'exp': now + expires_in_seconds,
        'iat': now,
        'iss': settings.TOKEN_SIGNING['JWT_ISSUER'],
        'version': settings.TOKEN_SIGNING['JWT_SUPPORTED_VERSION'],
    }
    payload.update(additional_token_claims)
    return _encode_and_sign(payload)


def _encode_and_sign(payload):
    """
    Encode and sign the provided payload.

    The signing key and algorithm are pulled from settings.
    """
    private_key = PyJWK.from_json(settings.TOKEN_SIGNING['JWT_PRIVATE_SIGNING_JWK'])
    algorithm = settings.TOKEN_SIGNING['JWT_SIGNING_ALGORITHM']
    return jwt.encode(payload, key=private_key.key, algorithm=algorithm)


def unpack_jwt(token, lms_user_id, now=None):
    """
    Unpack and verify an encoded JWT.

    Validate the user and expiration.

    Arguments:
        token (string): The token to be unpacked and verified.
        lms_user_id (int): LMS user ID this token should match with.
        now (int): Optional now value for testing.

    Returns a valid, decoded json payload (string).
    """
    now = now or int(time())
    payload = unpack_and_verify(token)

    if "lms_user_id" not in payload:
        raise MissingRequiredClaimError("LMS user id is missing")
    if "exp" not in payload:
        raise MissingRequiredClaimError("Expiration is missing")
    if payload["lms_user_id"] != lms_user_id:
        raise InvalidSignatureError("User does not match")
    if payload["exp"] < now:
        raise ExpiredSignatureError("Token is expired")

    return payload


def unpack_and_verify(token):  # pylint: disable=inconsistent-return-statements
    """
    Unpack and verify the provided token.

    The signing key and algorithm are pulled from settings.
    """
    key_set = []
    key_set.extend(
        PyJWKSet.from_json(settings.TOKEN_SIGNING["JWT_PUBLIC_SIGNING_JWK_SET"]).keys
    )

    for i in range(len(key_set)):  # pylint: disable=consider-using-enumerate
        try:
            decoded = jwt.decode(
                token,
                key=key_set[i].key,
                algorithms=["RS256", "RS512"],
                options={"verify_signature": True, "verify_aud": False},
            )
            return decoded
        except Exception:  # pylint: disable=broad-exception-caught
            if i == len(key_set) - 1:
                raise
