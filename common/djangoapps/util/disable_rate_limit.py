"""Utilities for disabling Django Rest Framework rate limiting.

This is useful for performance tests in which we need to generate
a lot of traffic from a particular IP address.  By default,
Django Rest Framework uses the IP address to throttle traffic
for users who are not authenticated.

To disable rate limiting:

1) Decorate the Django Rest Framework APIView with `@can_disable_rate_limit`
2) In Django's admin interface, set `RateLimitConfiguration.enabled` to False.

Note: You should NEVER disable rate limiting in production.

"""


import logging
from functools import wraps

from rest_framework.views import APIView

from common.djangoapps.util.models import RateLimitConfiguration

LOGGER = logging.getLogger(__name__)


def _check_throttles_decorator(func):
    """Decorator for `APIView.check_throttles`.

    The decorated function will first check model-based config
    to see if rate limiting is disabled; if so, it skips
    the throttle check.  Otherwise, it calls the original
    function to enforce rate-limiting.

    Arguments:
        func (function): The function to decorate.

    Returns:
        The decorated function.

    """
    @wraps(func)
    def _decorated(*args, **kwargs):
        # Skip the throttle check entirely if we've disabled rate limiting.
        # Otherwise, perform the checks (as usual)
        if RateLimitConfiguration.current().enabled:
            return func(*args, **kwargs)
        else:
            msg = "Rate limiting is disabled because `RateLimitConfiguration` is not enabled."
            LOGGER.info(msg)
            return

    return _decorated


def can_disable_rate_limit(clz):
    """Class decorator that allows rate limiting to be disabled.

    Arguments:
        clz (class): The APIView subclass to decorate.

    Returns:
        class: the decorated class.

    Example Usage:
        >>> from rest_framework.views import APIView
        >>> @can_disable_rate_limit
        >>> class MyApiView(APIView):
        >>>     pass

    """
    # No-op if the class isn't a Django Rest Framework view.
    if not issubclass(clz, APIView):
        msg = (  # noqa: UP032
            "{clz} is not a Django Rest Framework APIView subclass."
        ).format(clz=clz)
        LOGGER.warning(msg)
        return clz

    # If we ARE explicitly disabling rate limiting,
    # modify the class to always allow requests.
    # Note that this overrides both rate limiting applied
    # for the particular view, as well as global rate limits
    # configured in Django settings.
    if hasattr(clz, 'check_throttles'):
        clz.check_throttles = _check_throttles_decorator(clz.check_throttles)

    return clz
