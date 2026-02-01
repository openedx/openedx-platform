"""
Middleware for handling CSRF checks with CORS requests


CSRF and referrer domain checks
-------------------------------

When processing HTTPS requests, the default CSRF middleware checks that the referer
domain and protocol is the same as the request's domain and protocol. This is meant
to avoid a type of attack for sites which serve their content with both HTTP and HTTPS,
with a man in the middle on the HTTP requests.

https://github.com/django/django/blob/b91c385e324f1cb94d20e2ad146372c259d51d3b/django/middleware/csrf.py#L117

This doesn't work well with CORS requests, which aren't vulnerable to this attack when
the server from which the request is coming uses HTTPS too, as it prevents the man in the
middle attack vector.

We thus do the CSRF check of requests coming from an authorized CORS host separately
in this middleware, applying the same protections as the default CSRF middleware, but
without the referrer check, when both the request and the referer use HTTPS.


CSRF cookie domains
-------------------

In addition, in order to make cross-domain AJAX calls to CSRF-protected end-points,
we need to send the CSRF token in the HTTP header of the request.

The simple way to do this would be to set the CSRF_COOKIE_DOMAIN to ".edx.org",
but unfortunately this can cause problems.  For example, suppose that
"first.edx.org" sets the cookie with domain ".edx.org", but "second.edx.org"
sets a cookie with domain "second.edx.org".  In this case, the browser
would have two different CSRF tokens set (one for each cookie domain),
which can cause non-deterministic failures depending on which cookie
is sent first.

For this reason, we add a second cookie that (a) has the domain set to ".edx.org",
but (b) does NOT have the same name as the CSRF_COOKIE_NAME.  Clients making
cross-domain requests can use this cookie instead of the subdomain-specific
CSRF cookie.

"""


import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, MiddlewareNotUsed
from django.middleware.csrf import CsrfViewMiddleware
from django.utils.deprecation import MiddlewareMixin
from edx_django_utils.monitoring import set_custom_attribute

from .helpers import is_cross_domain_request_allowed, skip_cross_domain_referer_check

log = logging.getLogger(__name__)


class CorsCSRFMiddleware(CsrfViewMiddleware, MiddlewareMixin):
    """
    Middleware for handling CSRF checks with CORS requests
    """

    def __init__(self, *args, **kwargs):
        """Disable the middleware if the feature flag is disabled. """
        self.enable_cors_csrf_detail_monitoring = getattr(settings, 'CORS_CSRF_DETAIL_MONITORING', False)

        if self.enable_cors_csrf_detail_monitoring:
            # .. custom_attribute_name: tmp_cors_csrf.is_activated
            # .. custom_attribute_description: Boolean flag to know if CorsCSRFMiddleware is activated
            set_custom_attribute('tmp_cors_csrf.is_activated', settings.FEATURES.get('ENABLE_CORS_HEADERS'))

        if not settings.FEATURES.get('ENABLE_CORS_HEADERS'):
            raise MiddlewareNotUsed()
        super().__init__(*args, **kwargs)

    def process_view(self, request, callback, callback_args, callback_kwargs):
        """Skip the usual CSRF referer check if this is an allowed cross-domain request. """

        if self.enable_cors_csrf_detail_monitoring:
            # .. custom_attribute_name: tmp_cors_csrf.referer
            # .. custom_attribute_description: http_referer value obtained from the request headers
            set_custom_attribute('tmp_cors_csrf.referer', request.META.get('HTTP_REFERER'))

            # .. custom_attribute_name: tmp_cors_csrf.host
            # .. custom_attribute_description: host value obtained from the request
            set_custom_attribute('tmp_cors_csrf.host', request.get_host())

            # .. custom_attribute_name: tmp_cors_csrf.is_allowed
            # .. custom_attribute_description: False if this cross-domain request is not allowed
            set_custom_attribute('tmp_cors_csrf.is_allowed', is_cross_domain_request_allowed(request))

        if not is_cross_domain_request_allowed(request):
            log.debug("Could not disable CSRF middleware referer check for cross-domain request.")
            return

        with skip_cross_domain_referer_check(request):
            return super().process_view(request, callback, callback_args, callback_kwargs)


class CsrfCrossDomainCookieMiddleware(MiddlewareMixin):
    """Set an additional "cross-domain" CSRF cookie.

    Usage:

        1) Decorate a view with `@ensure_csrf_cookie_cross_domain`.
        2) Set `CROSS_DOMAIN_CSRF_COOKIE_NAME` and `CROSS_DOMAIN_CSRF_COOKIE_DOMAIN`
            in settings.
        3) Add the domain to `CORS_ORIGIN_WHITELIST`
        4) Enable `FEATURES['ENABLE_CROSS_DOMAIN_CSRF_COOKIE']`

    For testing, it is often easier to relax the security checks by setting:
        * `CORS_ALLOW_INSECURE = True`
        * `CORS_ORIGIN_ALLOW_ALL = True`

    """

    def __init__(self, *args, **kwargs):
        """Disable the middleware if the feature is not enabled. """
        self.enable_cors_csrf_detail_monitoring = getattr(settings, 'CORS_CSRF_DETAIL_MONITORING', False)

        if self.enable_cors_csrf_detail_monitoring:
            # .. custom_attribute_name: tmp_csrf_cross_domain.is_activated
            # .. custom_attribute_description: Boolean flag to know if CsrfCrossDomainCookieMiddleware is activated
            set_custom_attribute(
                'tmp_csrf_cross_domain.is_activated',
                settings.FEATURES.get('ENABLE_CROSS_DOMAIN_CSRF_COOKIE')
            )

        if not settings.FEATURES.get('ENABLE_CROSS_DOMAIN_CSRF_COOKIE'):
            raise MiddlewareNotUsed()

        if not getattr(settings, 'CROSS_DOMAIN_CSRF_COOKIE_NAME', ''):
            raise ImproperlyConfigured(
                "You must set `CROSS_DOMAIN_CSRF_COOKIE_NAME` when "
                "`FEATURES['ENABLE_CROSS_DOMAIN_CSRF_COOKIE']` is True."
            )

        if not getattr(settings, 'CROSS_DOMAIN_CSRF_COOKIE_DOMAIN', ''):
            raise ImproperlyConfigured(
                "You must set `CROSS_DOMAIN_CSRF_COOKIE_DOMAIN` when "
                "`FEATURES['ENABLE_CROSS_DOMAIN_CSRF_COOKIE']` is True."
            )

        if self.enable_cors_csrf_detail_monitoring:
            # .. custom_attribute_name: tmp_csrf_cross_domain.is_properly_config
            # .. custom_attribute_description: True if CsrfCrossDomainCookieMiddleware is activated
            #   and properly configured
            set_custom_attribute('tmp_csrf_cross_domain.is_properly_config', True)
        super().__init__(*args, **kwargs)

    def process_response(self, request, response):
        """Set the cross-domain CSRF cookie. """

        if self.enable_cors_csrf_detail_monitoring:
            # .. custom_attribute_name: tmp_csrf_cross_domain.referer
            # .. custom_attribute_description: http_referer value obtained from the request headers
            set_custom_attribute('tmp_csrf_cross_domain.referer', request.META.get('HTTP_REFERER'))

            # .. custom_attribute_name: tmp_csrf_cross_domain.host
            # .. custom_attribute_description: host value obtained from the request
            set_custom_attribute('tmp_csrf_cross_domain.host', request.get_host())

        # Check whether this is a secure request from a domain on our whitelist.
        if not is_cross_domain_request_allowed(request):
            log.debug("Could not set cross-domain CSRF cookie.")

            if self.enable_cors_csrf_detail_monitoring:
                # .. custom_attribute_name: tmp_csrf_cross_domain.is_allowed
                # .. custom_attribute_description: False if this cross-domain request is not allowed
                set_custom_attribute('tmp_csrf_cross_domain.is_allowed', False)
            return response

        # Send the cross-domain CSRF cookie if this is a view decorated with
        # `@ensure_cross_domain_csrf_cookie` and the same-domain CSRF cookie
        # value is available.
        #
        # Because CSRF_COOKIE can be set either by an inbound CSRF token or
        # by the middleware generating a new one or echoing the old one for
        # the response, this might result in sending the cookie more often
        # than the CSRF value actually changes, but as of Django 4.0 we no
        # longer have a good way of finding out when the csrf middleware has
        # updated the value.
        should_set_cookie = (
            request.META.get('CROSS_DOMAIN_CSRF_COOKIE_USED', False) and
            request.META.get('CSRF_COOKIE') is not None
        )

        if self.enable_cors_csrf_detail_monitoring:
            # .. custom_attribute_name: tmp_csrf_cross_domain.should_set_cookie
            # .. custom_attribute_description: True if CROSS_DOMAIN_CSRF_COOKIE_USED is true
            #   and there is a csrf cookie
            set_custom_attribute('tmp_csrf_cross_domain.should_set_cookie', should_set_cookie)

        if should_set_cookie:
            # This is very similar to the code in Django's CSRF middleware
            # implementation, with two exceptions:
            # 1) We change the cookie name and domain so it can be used cross-domain.
            # 2) We always set "secure" to True, so that the CSRF token must be
            # sent over a secure connection.
            response.set_cookie(
                settings.CROSS_DOMAIN_CSRF_COOKIE_NAME,
                request.META['CSRF_COOKIE'],
                max_age=settings.CSRF_COOKIE_AGE,
                domain=settings.CROSS_DOMAIN_CSRF_COOKIE_DOMAIN,
                path=settings.CSRF_COOKIE_PATH,
                secure=True
            )
            log.debug(
                "Set cross-domain CSRF cookie '%s' for domain '%s'",
                settings.CROSS_DOMAIN_CSRF_COOKIE_NAME,
                settings.CROSS_DOMAIN_CSRF_COOKIE_DOMAIN
            )

            if self.enable_cors_csrf_detail_monitoring:
                # .. custom_attribute_name: tmp_csrf_cross_domain.cookie_name
                # .. custom_attribute_description: csrf cookie name configured
                set_custom_attribute('tmp_csrf_cross_domain.cookie_name', settings.CROSS_DOMAIN_CSRF_COOKIE_NAME)

                # .. custom_attribute_name: tmp_csrf_cross_domain.cookie_domain
                # .. custom_attribute_description: csrf cookie domain configured
                set_custom_attribute('tmp_csrf_cross_domain.cookie_domain', settings.CROSS_DOMAIN_CSRF_COOKIE_DOMAIN)

                if hasattr(request, "resolver_match"):
                    # .. custom_attribute_name: tmp_csrf_cross_domain.view
                    # .. custom_attribute_description: the name of the view this request came from
                    set_custom_attribute("tmp_csrf_cross_domain.view", request.resolver_match.view_name)

        return response
