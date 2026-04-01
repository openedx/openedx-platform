"""Helper methods for CORS and CSRF checks. """


import contextlib
import logging
import urllib.parse

from django.conf import settings

log = logging.getLogger(__name__)


def is_cross_domain_request_allowed(request):
    """Check whether we should allow the cross-domain request.

    We allow a cross-domain request only if:

    1) The request is made securely and the referer has "https://" as the protocol.
    2) The referer domain has been whitelisted.

    Arguments:
        request (HttpRequest)

    Returns:
        bool

    """
    referer = request.META.get('HTTP_REFERER')
    referer_parts = urllib.parse.urlparse(referer) if referer else None

    # Use CORS_ALLOW_INSECURE *only* for development and testing environments;
    # it should never be enabled in production.
    if not getattr(settings, 'CORS_ALLOW_INSECURE', False):
        if not request.is_secure():
            log.debug(
                "Request is not secure, so we cannot send the CSRF token. "
                "For testing purposes, you can disable this check by setting "
                "`CORS_ALLOW_INSECURE` to True in the settings"
            )
            return False

        if not referer:
            log.debug("No referer provided over a secure connection, so we cannot check the protocol.")
            return False

        if not referer_parts.scheme == 'https':
            log.debug("Referer '%s' must have the scheme 'https'", str(referer))
            return False

    # Reduce the referer URL to just the scheme and authority
    # components (no path, query, or fragment).
    if referer_parts:
        origin_parts = (referer_parts.scheme, referer_parts.netloc, '', '', '', '')
        referer_origin = urllib.parse.urlunparse(origin_parts)
    else:
        referer_origin = None

    allow_all = getattr(settings, 'CORS_ORIGIN_ALLOW_ALL', False)
    origin_is_whitelisted = (
        allow_all or
        referer_origin in getattr(settings, 'CORS_ORIGIN_WHITELIST', [])
    )
    if not origin_is_whitelisted:
        log.info(
            f"Origin {referer_origin!r} was not in `CORS_ORIGIN_WHITELIST`; "
            f"full referer was {referer!r} and requested host was {request.get_host()!r}; "
            f"CORS_ORIGIN_ALLOW_ALL={allow_all}"
        )
        return False

    return True


@contextlib.contextmanager
def skip_cross_domain_referer_check(request):
    """Skip the cross-domain CSRF referer check.

    Django's CSRF middleware performs the referer check
    only when the request is made over a secure connection.
    To skip the check, we patch `request.is_secure()` to
    False.
    """
    is_secure_default = request.is_secure
    request.is_secure = lambda: False
    try:
        yield
    finally:
        request.is_secure = is_secure_default
