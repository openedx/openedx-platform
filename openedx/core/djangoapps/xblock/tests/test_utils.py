"""
Tests for xblock utils.
"""
import datetime

import pytest
from django.test import override_settings
from freezegun import freeze_time

from openedx.core.djangoapps.xblock.utils import (  # lint-amnesty, pylint: disable=reimported
    _get_secure_token_for_xblock_handler,
    get_secure_token_for_xblock_handler,
    validate_secure_token_for_xblock_handler,
)

REFERENCE_PARAMS = {
    "generation_user_id": "12344",
    "validation_user_id": "12344",
    "generation_block_key_str": "some key",
    "validation_block_key_str": "some key",
    "generation_secret_key": "baseline_key",
    "validation_secret_key": "baseline_key",
    "generation_xblock_handler_token_keys": None,
    "validation_xblock_handler_token_keys": None,
    # A reference time that produces a token that will expire within the next day
    # but not for a few hours.
    "reference_time": datetime.datetime(2021, 1, 28, 13, 26, 38, 787309, datetime.timezone.utc),
    "validation_time_delta_s": 0,
}


@pytest.mark.parametrize(
    "param_delta,expected_validation",
    [
        # Happy Path case with the above REFERENCE_PARAMS.
        ({}, True),
        # Ensure token still valid in 1 hour
        ({"validation_time_delta_s": 3600}, True),
        # Ensure token still valid in 1 day
        ({"validation_time_delta_s": 86400}, True),
        # Ensure token is invalid after 5 days
        ({"validation_time_delta_s": 86400 * 5}, False),
        # Ensure token is not valid 5 days before generation
        # In the case where the validating server is really skewed
        # from the generating server.
        ({"validation_time_delta_s": 86400 * -5}, False),
        # Setting reference_time to 20 seconds after start of a 2 day time period(UTC)
        # Demonstrating maximum possible validity period is just below 4 days
        # This passes because validation time is just below the cutoff point
        (
            {"reference_time": datetime.datetime(2021, 1, 27, 0, 0, 20, tzinfo=datetime.timezone.utc),
             "validation_time_delta_s": (86400 * 4) - 21
             },
            True,
        ),
        # Setting reference_time to 20 seconds after start of a 2 day time period(UTC)
        # Demonstrating maximum possible validity period is just below 4 days
        # This does not pass because validation time is just above the cutoff point
        (
            {"reference_time": datetime.datetime(2021, 1, 27, 0, 0, 20, tzinfo=datetime.timezone.utc),
             "validation_time_delta_s": (86400 * 4) - 19
             },
            False,
        ),
        # Setting reference_time to 20 seconds before end of a 2 day time period(UTC)
        # Demonstrating minimum possible validity period is just above 2 days
        # This passes because validation time is just below the cutoff point
        (
            {"reference_time": datetime.datetime(2021, 1, 28, 23, 59, 40, tzinfo=datetime.timezone.utc),
             "validation_time_delta_s": (86400 * 2) + 19
             },
            True,
        ),
        # Setting reference_time to 20 seconds before end of a 2 day time period(UTC)
        # Demonstrating minimum possible validity period is just above 2 days
        # This fails because validation time is just above the cutoff point
        (
            {"reference_time": datetime.datetime(2021, 1, 28, 23, 59, 40, tzinfo=datetime.timezone.utc),
             "validation_time_delta_s": (86400 * 2) + 21
             },
            False),
        # Different user tries to use your token.
        ({"validation_user_id": 54321}, False),
        # Access a different block.
        ({"validation_block_key_str": "some other block"}, False),
        # If key is changed, token shouldn't be valid.
        ({"validation_secret_key": "new secret key"}, False),
        # Test with token keys setting overrides that don't have anything to do with the secret key.
        ({"generation_xblock_handler_token_keys": [], "validation_xblock_handler_token_keys": []}, True),
        # Test migration from secret key to keys list. The secret_key has changed
        # but the old secret key is in the token keys list.
        (
            {
                "validation_xblock_handler_token_keys": ["baseline_key", ],
                "validation_secret_key": "new secret key",
            },
            True,
        ),
        # Test tokens generated with the old key will be valid when there is a new primary
        # token key at validation time.
        (
            {
                "generation_xblock_handler_token_keys": ["baseline_key", ],
                "validation_xblock_handler_token_keys": ["new token key", "baseline_key", ],
                "validation_secret_key": "new secret key",
            },
            True,
        ),
        # Test token generated with the new key is valid with the new key even if the secret key
        # changes.
        (
            {
                "generation_xblock_handler_token_keys": ["new token key"],
                "validation_xblock_handler_token_keys": ["new token key"],
                "validation_secret_key": "new secret key",  # Ensure that we're not matching with secret key
            },
            True,
        ),
        # Test that if the new token key is used to generate, that validating with the SECRET_KEY won't work.
        (
            {
                "generation_xblock_handler_token_keys": ["new token key"],
                "validation_xblock_handler_token_keys": [],
            },
            False,
        ),
    ],
)
def test_secure_token(param_delta: dict, expected_validation: bool):
    params = {}
    params.update(REFERENCE_PARAMS)
    params.update(param_delta)
    reference_time = params['reference_time']

    with override_settings(
        SECRET_KEY=params["generation_secret_key"],
        XBLOCK_HANDLER_TOKEN_KEYS=params["generation_xblock_handler_token_keys"],
    ):
        assert isinstance(reference_time, datetime.datetime)
        with freeze_time(reference_time):
            token = get_secure_token_for_xblock_handler(
                params["generation_user_id"], params["generation_block_key_str"]
            )

    with override_settings(
        SECRET_KEY=params["validation_secret_key"],
        XBLOCK_HANDLER_TOKEN_KEYS=params["validation_xblock_handler_token_keys"],
    ):
        assert isinstance(params["validation_time_delta_s"], int)
        new_reference_time = reference_time + datetime.timedelta(seconds=float(params["validation_time_delta_s"]))
        with freeze_time(new_reference_time):
            assert (
                validate_secure_token_for_xblock_handler(
                    params["validation_user_id"], params["validation_block_key_str"], token
                ) ==
                expected_validation
            )


def test_private_get_secure_token_for_xblock_handler():
    """
    Confirms function behaviour has not changed and is giving the same token for same inputs
    If these tokens change, this will invalidate some or all of the tokens handed out to learners
    """
    with freeze_time(datetime.datetime(2021, 1, 27, 0, 0, 0, tzinfo=datetime.timezone.utc)):
        token_now = _get_secure_token_for_xblock_handler("12345", "some_block_key", 0, "some_hashing_key")
        token_previous = _get_secure_token_for_xblock_handler("12345", "some_block_key", -1, "some_hashing_key")
        assert token_now == "de8399a51fef6aa7584a"
        assert token_previous == "f3b5d92b8ca2934009e4"
