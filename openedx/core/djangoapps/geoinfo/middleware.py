"""
Deprecated. Kept so deployments that still list CountryMiddleware in MIDDLEWARE keep starting.
"""
import warnings

from django.core.exceptions import MiddlewareNotUsed


class CountryMiddleware:
    """
    Deprecated no-op. The country code is now looked up per request by
    geoinfo.api.country_code_for_request; remove this from MIDDLEWARE.
    """

    def __init__(self, get_response):
        warnings.warn(
            "CountryMiddleware is deprecated and does nothing; remove it from MIDDLEWARE.",
            DeprecationWarning,
            stacklevel=2,
        )
        raise MiddlewareNotUsed()
