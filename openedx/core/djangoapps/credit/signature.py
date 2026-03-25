"""
Calculate digital signatures for messages sent to/from credit providers,
using a shared secret key.

The signature is calculated as follows:

    1) Encode all parameters of the request (except the signature) in a string.
    2) Encode each key/value pair as a string of the form "{key}:{value}".
    3) Concatenate key/value pairs in ascending alphabetical order by key.
    4) Calculate the HMAC-SHA256 digest of the encoded request parameters, using a 32-character shared secret key.
    5) Encode the digest in hexadecimal.

It is the responsibility of the credit provider to check the signature of messages
we send them, and it is our responsibility to check the signature of messages
we receive from the credit provider.

"""


import hashlib  # noqa: I001
import hmac
import logging

from django.conf import settings

log = logging.getLogger(__name__)


def _encode_secret(secret, provider_id):
    """
    Helper function for encoding string secrets into ascii.
    """
    try:
        secret.encode('ascii')
    except UnicodeEncodeError:
        secret = None
        log.error('Shared secret key for credit provider "%s" contains non-ASCII unicode.', provider_id)

    return secret


def get_shared_secret_key(provider_id):
    """
    Retrieve the shared secret for a particular credit provider.

    It is possible for the secret to be stored in 2 ways:
    1 - a key/value pair of provider_id and secret string
        {'cool_school': '123abc'}
    2 - a key/value pair of provider_id and secret list
        {'cool_school': ['987zyx', '123abc']}
    """

    secret = getattr(settings, "CREDIT_PROVIDER_SECRET_KEYS", {}).get(provider_id)

    # When secret is just characters
    if isinstance(secret, str):
        secret = _encode_secret(secret, provider_id)

    # When secret is a list containing multiple keys, encode all of them
    elif isinstance(secret, list):
        for index, secretvalue in enumerate(secret):
            if isinstance(secretvalue, str):
                secret[index] = _encode_secret(secretvalue, provider_id)

    return secret


def signature(params, shared_secret):
    """
    Calculate the digital signature for parameters using a shared secret.

    Arguments:
        params (dict): Parameters to sign.  Ignores the "signature" key if present.
        shared_secret (str): The shared secret string.

    Returns:
        str: The 32-character signature.

    """
    encoded_params = "".join([
        f"{key}:{params[key]}"
        for key in sorted(params.keys())
        if key != "signature"
    ])
    hasher = hmac.new(shared_secret.encode('utf-8'), encoded_params.encode('utf-8'), hashlib.sha256)
    return hasher.hexdigest()
