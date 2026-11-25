"""
Deprecated. Kept so deployments that still list CountryMiddleware in MIDDLEWARE keep starting.
"""
import logging
import warnings

from django.core.exceptions import MiddlewareNotUsed

log = logging.getLogger(__name__)

_MIDDLEWARE_PATH = "openedx.core.djangoapps.geoinfo.middleware.CountryMiddleware"


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
        log.warning("%s is a deprecated no-op; remove it from MIDDLEWARE.", _MIDDLEWARE_PATH)
        raise MiddlewareNotUsed()
