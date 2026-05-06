"""
Tests for token handling
"""
import datetime
import unittest
from time import time

import pytest
from freezegun import freeze_time
from jwt.exceptions import ExpiredSignatureError, InvalidSignatureError, MissingRequiredClaimError

from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.core.lib.jwt import _encode_and_sign, create_jwt, unpack_and_verify, unpack_jwt

test_user_id = 121
invalid_test_user_id = 120
test_timeout = 1000
test_now = int(time())
time_snapshot = datetime.datetime.fromtimestamp(test_now, tz=datetime.UTC)
test_claims = {"foo": "bar", "baz": "quux", "meaning": 42}
expected_full_token = {
    "lms_user_id": test_user_id,
    "iat": test_now,
    "exp": test_now + test_timeout,
    "iss": "token-test-issuer",  # these lines from test_settings.py
    "version": "1.2.0",  # these lines from test_settings.py
}


@skip_unless_lms
@freeze_time(time_snapshot)
class TestSign(unittest.TestCase):
    """
    Tests for JWT creation and signing.
    """

    def test_create_jwt(self):
        token = create_jwt(test_user_id, test_timeout, {}, test_now)

        decoded = unpack_and_verify(token)
        assert decoded == expected_full_token

    def test_create_jwt_with_claims(self):
        token = create_jwt(test_user_id, test_timeout, test_claims, test_now)

        expected_token_with_claims = expected_full_token.copy()
        expected_token_with_claims.update(test_claims)

        decoded = unpack_and_verify(token)
        assert decoded == expected_token_with_claims

    def test_malformed_token(self):
        token = create_jwt(test_user_id, test_timeout, test_claims, test_now)
        token = token + "a"

        with pytest.raises(InvalidSignatureError):
            unpack_and_verify(token)


@skip_unless_lms
@freeze_time(time_snapshot)
class TestUnpack(unittest.TestCase):
    """
    Tests for JWT unpacking.
    """

    def test_unpack_jwt(self):
        token = create_jwt(test_user_id, test_timeout, {}, test_now)
        decoded = unpack_jwt(token, test_user_id, test_now)

        assert decoded == expected_full_token

    def test_unpack_jwt_with_claims(self):
        token = create_jwt(test_user_id, test_timeout, test_claims, test_now)

        expected_token_with_claims = expected_full_token.copy()
        expected_token_with_claims.update(test_claims)

        decoded = unpack_jwt(token, test_user_id, test_now)

        assert decoded == expected_token_with_claims

    def test_malformed_token(self):
        token = create_jwt(test_user_id, test_timeout, test_claims, test_now)
        token = token + "a"

        with pytest.raises(InvalidSignatureError):
            unpack_jwt(token, test_user_id, test_now)

    def test_unpack_token_with_invalid_user(self):
        token = create_jwt(invalid_test_user_id, test_timeout, {}, test_now)

        with pytest.raises(InvalidSignatureError):
            unpack_jwt(token, test_user_id, test_now)

    def test_unpack_expired_token(self):
        token = create_jwt(test_user_id, test_timeout, {}, test_now)

        with pytest.raises(ExpiredSignatureError):
            unpack_jwt(token, test_user_id, test_now + test_timeout + 1)

    def test_missing_expired_lms_user_id(self):
        payload = expected_full_token.copy()
        del payload['lms_user_id']
        token = _encode_and_sign(payload)

        with pytest.raises(MissingRequiredClaimError):
            unpack_jwt(token, test_user_id, test_now)

    def test_missing_expired_key(self):
        payload = expected_full_token.copy()
        del payload['exp']
        token = _encode_and_sign(payload)

        with pytest.raises(MissingRequiredClaimError):
            unpack_jwt(token, test_user_id, test_now)
