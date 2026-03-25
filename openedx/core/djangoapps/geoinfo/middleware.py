"""
Middleware to identify the country of origin of page requests.

Middleware adds `country_code` in session.

Usage:

# To enable the Geoinfo feature on a per-view basis, use:
decorator `django.utils.decorators.decorator_from_middleware(middleware_class)`

"""
import logging  # noqa: I001

from django.utils.deprecation import MiddlewareMixin
from python_ipware import IpWare

from .api import country_code_from_ip

log = logging.getLogger(__name__)


class CountryMiddleware(MiddlewareMixin):
    """
    Identify the country by IP address.
    """
    def process_request(self, request):
        """
        Identify the country by IP address.

        Store country code in session.
        """
        ipw = IpWare()
        new_ip_address_obj, _ = ipw.get_client_ip(meta=request.META)

        if new_ip_address_obj:
            new_ip_address = format(new_ip_address_obj)

        old_ip_address = request.session.get('ip_address', None)

        if not new_ip_address and old_ip_address:
            del request.session['ip_address']
            del request.session['country_code']
        elif new_ip_address != old_ip_address and new_ip_address_obj.is_global:
            country_code = country_code_from_ip(new_ip_address)
            request.session['country_code'] = country_code
            request.session['ip_address'] = new_ip_address
            log.debug('Country code for IP: %s is set to %s', new_ip_address, country_code)
