"""
Simple Python API to identify the country of origin of page requests.
"""

import geoip2.database
from django.conf import settings
from python_ipware import IpWare

_REQUEST_COUNTRY_CODE_ATTR = '_geoinfo_country_code'


def country_code_from_ip(ip_addr: str) -> str:
    """
    Return the country code associated with an IP address.
    Handles both IPv4 and IPv6 addresses.

    Args:
        ip_addr: The IP address to look up.

    Returns:
        A 2-letter country code,
        or an empty string if lookup failed.
    """
    reader = geoip2.database.Reader(settings.GEOIP_PATH)
    try:
        response = reader.country(ip_addr)
        # pylint: disable=no-member
        country_code = response.country.iso_code or ""
    except geoip2.errors.AddressNotFoundError:
        country_code = ""
    reader.close()
    return country_code


def country_code_for_request(request) -> str:
    """
    Return the country code for the client IP address of a request.

    The lookup runs at most once per request and is not stored in the session,
    so requests that don't otherwise use the session don't create or modify one.

    Returns:
        A 2-letter country code, or an empty string if the client IP is missing,
        not globally routable, or not found.
    """
    if not hasattr(request, _REQUEST_COUNTRY_CODE_ATTR):
        ip_address, _ = IpWare().get_client_ip(meta=request.META)
        country_code = ""
        if ip_address and ip_address.is_global:
            country_code = country_code_from_ip(format(ip_address))
        setattr(request, _REQUEST_COUNTRY_CODE_ATTR, country_code)
    return getattr(request, _REQUEST_COUNTRY_CODE_ATTR)
