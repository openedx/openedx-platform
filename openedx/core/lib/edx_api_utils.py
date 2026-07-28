"""Helper functions to get data from APIs"""


import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union  # noqa: UP035
from urllib.parse import urljoin

from django.core.cache import cache

from openedx.core.lib.cache_utils import zpickle, zunpickle

if TYPE_CHECKING:
    from requests import session

log = logging.getLogger(__name__)


def get_fields(fields, response):
    """Extracts desired fields from the API response"""
    results = {}
    for field in fields:
        results[field] = response.get(field)
    return results


def get_api_data(
    api_config: Any,
    resource: str,
    api_client: "session",
    base_api_url: str,
    resource_id: Optional[Union[int, str]] = None,  # noqa: UP007, UP045
    querystring: Optional[Dict[Any, Any]] = None,  # noqa: UP006, UP045
    cache_key: Optional[str] = None,  # noqa: UP045
    many: bool = True,
    traverse_pagination: bool = True,
    fields: Optional[List[Any]] = None,  # noqa: UP006, UP045
    long_term_cache: bool = False,
    raise_on_error: bool = False,
) -> Union[List[Any], Dict[Any, Any]]:  # noqa: UP006, UP007
    """
    GET data from an edX REST API endpoint using the API client.

    DRY utility for handling caching and pagination.

    Arguments:
        api_config (ConfigurationModel): The configuration model governing interaction with the API.
        resource (str): Name of the API resource being requested.
        api_client (requests.Session): API client (either raw requests.Session or OAuthAPIClient) to use for
            requesting data.
        base_api_url (str): base API url, used to construct the full API URL across with resource and
            resource_id (if any).

    Keyword Arguments:
        resource_id (int or str): Identifies a specific resource to be retrieved.
        querystring (dict): Optional query string parameters.
        cache_key (str): Where to cache retrieved data. The cache will be ignored if this is omitted
            (neither inspected nor updated).
        many (bool): Whether the resource requested is a collection of objects, or a single object.
            If false, an empty dict will be returned in cases of failure rather than the default empty list.
        traverse_pagination (bool): Whether to traverse pagination or return paginated response..
        fields (list):  Return only specific fields from the response
        long_term_cache (bool): Whether to use the long term cache ttl or the standard cache ttl
        raise_on_error (bool): Reraise errors back to the caller, instead if returning empty results.

    Returns:
        Data returned by the API. When hitting a list endpoint, extracts "results" (list of dict)
        returned by DRF-powered APIs. By default, returns an empty result if the called API
        returns an error.
    """
    no_data = [] if many else {}

    if not api_config.enabled:
        log.warning("%s configuration is disabled.", api_config.API_NAME)
        return no_data

    if cache_key:
        cache_key = (
            f"{cache_key}.{resource_id}" if resource_id is not None else cache_key
        )
        cache_key += ".zpickled"

        cached = cache.get(cache_key)
        if cached:
            try:
                cached_response = zunpickle(cached)
            except Exception:  # pylint: disable=broad-except
                # Data is corrupt in some way.
                log.warning("Data for cache is corrupt for cache key %s", cache_key)
                cache.delete(cache_key)
            else:
                if fields:
                    cached_response = get_fields(fields, cached_response)

                return cached_response

    try:
        querystring = querystring if querystring else {}
        api_url = urljoin(
            f"{base_api_url}/",
            f"{resource}/{str(resource_id) + '/' if resource_id is not None else ''}",
        )
        response = api_client.get(api_url, params=querystring)
        response.raise_for_status()
        response = response.json()

        if resource_id is None and traverse_pagination:
            results = _traverse_pagination(
                response, api_client, api_url, querystring, no_data
            )
        else:
            results = response

    except:  # pylint: disable=bare-except
        log.exception("Failed to retrieve data from the %s API.", api_config.API_NAME)
        if raise_on_error:
            raise
        return no_data

    if cache_key:
        zdata = zpickle(results)
        cache_ttl = api_config.cache_ttl
        if long_term_cache:
            cache_ttl = api_config.long_term_cache_ttl
        cache.set(cache_key, zdata, cache_ttl)

    if fields:
        results = get_fields(fields, results)

    return results


def _traverse_pagination(response, api_client, api_url, querystring, no_data):
    """
    Traverse a paginated API response.

    Extracts and concatenates "results" (list of dict) returned by DRF-powered APIs.
    """
    results = response.get("results", no_data)

    page = 1
    next_page = response.get("next")
    while next_page:
        page += 1
        querystring["page"] = page
        response = api_client.get(api_url, params=querystring)
        response.raise_for_status()
        response = response.json()
        results += response.get("results", no_data)
        next_page = response.get("next")

    return results
