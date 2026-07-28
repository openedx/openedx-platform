"""
Wrapper to use pwnedpassword Service
"""

import hashlib
import logging

import requests
from django.conf import settings
from requests.exceptions import ReadTimeout
from rest_framework.status import HTTP_408_REQUEST_TIMEOUT

from openedx.core.djangoapps.user_authn.config.waffle import ENABLE_PWNED_PASSWORD_API

log = logging.getLogger(__name__)

SHA_LENGTH = 40
HEX_BASE = 16


def convert_password_tuple(value):
    """
    a conversion function used to convert a string to a tuple
    """
    signature, count = value.split(":")
    return (signature, int(count))


class PwnedPasswordsAPI:
    """
    WrapperClass on pwned password service
    to fetch similar password signatures
    along with their count
    """
    API_URL = "https://api.pwnedpasswords.com"

    @staticmethod
    def range(password):
        """
        Returns a dict containing hashed password signatures along with their count.
        API URL takes first 5 characters of a SHA-1 password hash (not case-sensitive).
        API response contains suffix of every hash beginning with the specified prefix,
        followed by a count of how many times it appears in their data set.

        **Argument(s):
            password: a sha-1-hashed string against which pwnedservice is invoked

        **Returns:
            {
                "7ecd77ecd7": 341,
                "7ecd77ecd77ecd7": 12,
            }
        """
        is_encrypted = PwnedPasswordsAPI.is_sha1(password)
        if not is_encrypted:
            password = hashlib.sha1(password.encode('utf-8')).hexdigest().upper()

        range_url = PwnedPasswordsAPI.API_URL + '/range/{}'.format(password[:5])  # noqa: UP032

        if ENABLE_PWNED_PASSWORD_API.is_enabled():
            try:
                timeout = getattr(settings, 'PASSWORD_POLICY_COMPLIANCE_API_TIMEOUT', 5)
                response = requests.get(range_url, timeout=timeout)
                entries = dict(map(convert_password_tuple, response.text.split("\r\n")))
                return entries

            except ReadTimeout:
                log.warning('Request timed out for {}'.format(password))  # noqa: UP032
                return HTTP_408_REQUEST_TIMEOUT

            except Exception as exc:  # pylint: disable=W0703
                log.exception(f"Unable to range the password: {exc}")

    @staticmethod
    def is_sha1(maybe_sha):
        """
        Validates whether the provided string is sha1 encrypted or not
        """
        if len(maybe_sha) != SHA_LENGTH:
            return False

        try:
            sha_int = int(maybe_sha, HEX_BASE)  # noqa: F841
        except ValueError:
            return False

        return True
