"""Helper functions for working with Credentials."""

import logging
from typing import Dict, List  # noqa: UP035
from urllib.parse import urljoin

import requests
from django.contrib.auth import get_user_model
from edx_rest_api_client.auth import SuppliedJwtAuth

from openedx.core.djangoapps.credentials.models import CredentialsApiConfig
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.lib.edx_api_utils import get_api_data

log = logging.getLogger(__name__)
User = get_user_model()


def get_credentials_records_url(program_uuid=None):
    """
    Returns a URL for a given records page (or general records list if given no UUID).
    May return None if this feature is disabled.

    Arguments:
        program_uuid (str): Optional program uuid to link for a program records URL
    """
    base_url = CredentialsApiConfig.current().public_records_url
    if base_url is None:
        return None

    if program_uuid:
        # Credentials expects Program UUIDs without dashes so we remove them here
        stripped_program_uuid = program_uuid.replace("-", "")
        return urljoin(base_url, f"programs/{stripped_program_uuid}")

    return base_url


def get_credentials_api_client(user) -> requests.Session:
    """
    Returns an authenticated Credentials API client.

    Arguments:
        user (User): The user to authenticate as when requesting credentials.
    """
    scopes = ["email", "profile", "user_id"]
    jwt = create_jwt_for_user(user, scopes=scopes)

    client = requests.Session()
    client.auth = SuppliedJwtAuth(jwt)
    return client


def get_credentials_api_base_url(org=None):
    """
    Returns a credentials API base URL.

    Arguments:
        org (str): Optional organization to look up the site config for, rather than the current request
    """
    if org is None:
        url = CredentialsApiConfig.current().internal_api_url  # by current request
    else:
        url = CredentialsApiConfig.get_internal_api_url_for_org(org)  # by org

    return url


def get_credentials(
    user: User,
    program_uuid: str = None,
    credential_type: str = None,
    raise_on_error: bool = False,
) -> List[Dict]:  # noqa: UP006
    """
    Given a user, get credentials earned from the credentials service.

    Arguments:
        user (User): The user to authenticate as when requesting credentials.

    Keyword Arguments:
        program_uuid (str): UUID of the program whose credential to retrieve.
        credential_type (str): Which type of credentials to return (course-run or program)
        raise_on_error (bool): Reraise errors back to the caller, instead if returning empty results.

    Returns:
        list of dict, representing credentials returned by the Credentials
        service.
    """
    credential_configuration = CredentialsApiConfig.current()

    querystring = {
        "username": user.username,
        "status": "awarded",
        "only_visible": "True",
    }

    if program_uuid:
        querystring["program_uuid"] = program_uuid

    if credential_type:
        querystring["type"] = credential_type

    # Bypass caching for staff users, who may be generating credentials and
    # want to see them displayed immediately.
    use_cache = credential_configuration.is_cache_enabled and not user.is_staff
    cache_key = f"{credential_configuration.CACHE_KEY}.{user.username}" if use_cache else None
    if cache_key and program_uuid:
        cache_key = f"{cache_key}.{program_uuid}"

    api_client = get_credentials_api_client(user)
    base_api_url = get_credentials_api_base_url()

    return get_api_data(
        credential_configuration,
        "credentials",
        api_client=api_client,
        base_api_url=base_api_url,
        querystring=querystring,
        cache_key=cache_key,
        raise_on_error=raise_on_error,
    )
