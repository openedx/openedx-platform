"""
Utility functions used during user authentication.
"""

import hashlib
import math
import random
import re
from urllib.parse import urlparse  # pylint: disable=import-error
from uuid import uuid4  # pylint: disable=unused-import  # noqa: F401

from django.conf import settings
from django.utils import http
from oauth2_provider.models import Application
from rest_framework.status import HTTP_408_REQUEST_TIMEOUT

from common.djangoapps.student.models import username_exists_or_retired
from openedx.core.djangoapps.password_policy.hibp import PwnedPasswordsAPI
from openedx.core.djangoapps.user_api.accounts import USERNAME_MAX_LENGTH


def _remove_unsafe_bytes_from_url(url):
    _UNSAFE_URL_BYTES_TO_REMOVE = ["\t", "\r", "\n"]
    for byte in _UNSAFE_URL_BYTES_TO_REMOVE:
        url = url.replace(byte, "")
    return url


def is_safe_login_or_logout_redirect(redirect_to, request_host, dot_client_id, require_https):
    """
    Determine if the given redirect URL/path is safe for redirection.

    Arguments:
        redirect_to (str):
            The URL in question.
        request_host (str):
            Originating hostname of the request.
            This is always considered an acceptable redirect target.
        dot_client_id (str|None):
            ID of Django OAuth Toolkit client.
            It is acceptable to redirect to any of the DOT client's redirect URIs.
            This argument is ignored if it is None.
        require_https (str):
            Whether HTTPs should be required in the redirect URL.

    Returns: bool
    """
    login_redirect_whitelist = set(getattr(settings, 'LOGIN_REDIRECT_WHITELIST', []))
    login_redirect_whitelist.add(request_host)

    redirect_to = _remove_unsafe_bytes_from_url(redirect_to)

    # Allow OAuth2 clients to redirect back to their site after logout.
    if dot_client_id:
        application = Application.objects.get(client_id=dot_client_id)
        if redirect_to in application.redirect_uris:
            login_redirect_whitelist.add(urlparse(redirect_to).netloc)

    url_has_allowed_host_and_scheme = http.url_has_allowed_host_and_scheme(
        redirect_to, allowed_hosts=login_redirect_whitelist, require_https=require_https
    )

    return url_has_allowed_host_and_scheme


def is_registration_api_v1(request):
    """
    Checks if registration api is v1
    :param request:
    :return: Bool
    """
    return 'v1' in request.get_full_path() and 'register' not in request.get_full_path()


def remove_special_characters_from_name(name: str) -> str:
    return "".join(re.findall(r"[\w-]+", name))


def generate_username_suggestions(name: str) -> list[str]:
    """
    Generate 3 available username suggestions based on the provided name.

    Args:
        name (str): The full name to generate username suggestions from.
            Must contain only ASCII characters.

    Returns:
        list[str]: A list of up to 3 available username suggestions,
            or an empty list if name contains non-ASCII characters or if no valid
            suggestions could be generated.

    Note:
        Generated usernames will be combinations of:
        - firstname + lastname
        - first initial + lastname
        - firstname + random number
    """
    # Check if name contains non-ASCII characters
    try:
        name.encode('ascii')
    except UnicodeEncodeError:
        return []

    username_suggestions = []
    max_length = USERNAME_MAX_LENGTH
    names = name.split(' ')
    if names:
        first_name = remove_special_characters_from_name(names[0].lower())
        last_name = remove_special_characters_from_name(names[-1].lower())

        if first_name != last_name and first_name:
            # username combination of first and last name
            suggestion = f'{first_name}{last_name}'[:max_length]
            if not username_exists_or_retired(suggestion):
                username_suggestions.append(suggestion)

            # username is combination of first letter of first name and last name
            suggestion = f'{first_name[0]}-{last_name}'[:max_length]
            if not username_exists_or_retired(suggestion):
                username_suggestions.append(suggestion)

        if len(first_name) >= 2:

            short_username = first_name[:max_length - 6] if max_length is not None else first_name
            short_username = short_username.replace('_', '').replace('-', '')

            int_ranges = [
                {'min': 0, 'max': 9},
                {'min': 10, 'max': 99},
                {'min': 100, 'max': 999},
                {'min': 1000, 'max': 9999},
                {'min': 10000, 'max': 99999},
            ]
            for int_range in int_ranges:
                for _ in range(10):
                    random_int = random.randint(int_range['min'], int_range['max'])
                    suggestion = f'{short_username}_{random_int}'
                    if not username_exists_or_retired(suggestion):
                        username_suggestions.append(suggestion)
                        break

                if len(username_suggestions) == 3:
                    break

    return username_suggestions


def check_pwned_password(password):
    """
        Check the Pwned Databases for vulnerable passwords.
        check_pwned_password returns password hash suffix and a dictionary containing
        suffix of every SHA-1 password hash beginning with the specified prefix,
        followed by a count of how many times it appears in their data set.
    """
    properties = {}
    password = hashlib.sha1(password.encode('utf-8')).hexdigest().upper()

    pwned_response = PwnedPasswordsAPI.range(password)
    if pwned_response is not None:
        if pwned_response == HTTP_408_REQUEST_TIMEOUT:
            properties['vulnerability'] = 'unknown'
            pwned_count = 0
        else:
            pwned_count = pwned_response.get(password[5:], 0)
            properties['vulnerability'] = 'yes' if pwned_count > 0 else 'no'

        if pwned_count > 0:
            properties['frequency'] = math.ceil(math.log10(pwned_count))

    return properties
