"""Decorators for cross-domain CSRF. """

from django.conf import settings
from django.views.decorators.csrf import ensure_csrf_cookie
from edx_django_utils.monitoring import set_custom_attribute


def ensure_csrf_cookie_cross_domain(func):
    """View decorator for sending a cross-domain CSRF cookie.

    This works like Django's `@ensure_csrf_cookie`, but
    will also set an additional CSRF cookie for use
    cross-domain.

    Arguments:
        func (function): The view function to decorate.

    """
    def _inner(*args, **kwargs):  # pylint: disable=missing-docstring
        if args:
            # Set the META `CROSS_DOMAIN_CSRF_COOKIE_USED` flag so
            # that `CsrfCrossDomainCookieMiddleware` knows to set
            # the cross-domain version of the CSRF cookie.
            try:
                request = args[0]
                request.META['CROSS_DOMAIN_CSRF_COOKIE_USED'] = True
                current_authenticator = getattr(request, 'successful_authenticator', None)

                enable_cors_csrf_detail_monitoring = getattr(settings, 'CORS_CSRF_DETAIL_MONITORING', False)

                if enable_cors_csrf_detail_monitoring:
                    if hasattr(request, "resolver_match"):
                        # .. custom_attribute_name: tmp_cors_csrf_decorator.view
                        # .. custom_attribute_description: the name of the view this request came from
                        set_custom_attribute("tmp_cors_csrf_decorator.view", request.resolver_match.view_name)

                    # .. custom_attribute_name: tmp_cors_csrf_decorator.authenticator
                    # .. custom_attribute_description: authenticator used for this view
                    set_custom_attribute("tmp_cors_csrf_decorator.authenticator", current_authenticator)

                    # .. custom_attribute_name: tmp_cors_csrf_decorator.referer
                    # .. custom_attribute_description: http_referer value obtained from the request headers
                    set_custom_attribute('tmp_cors_csrf_decorator.referer', request.META.get('HTTP_REFERER'))

                    # .. custom_attribute_name: tmp_cors_csrf_decorator.host
                    # .. custom_attribute_description: host value obtained from the request
                    set_custom_attribute('tmp_cors_csrf_decorator.host', request.get_host())
            except Exception as e:  # pylint: disable=broad-except
                # .. custom_attribute_name: tmp_cors_csrf_decorator.error
                # .. custom_attribute_description: error message if an exception occurs in ensure_csrf_cookie_cross_domain
                set_custom_attribute("tmp_cors_csrf_decorator.error", str(e))

        # Decorate the request with Django's
        # `ensure_csrf_cookie` to ensure that the usual
        # CSRF cookie gets set.
        return ensure_csrf_cookie(func)(*args, **kwargs)
    return _inner
