"""Views for enabling cross-domain requests. """


import json  # noqa: I001
import logging

from django.conf import settings
from django.http import HttpResponseNotFound
from django.views.decorators.cache import cache_page

from common.djangoapps.edxmako.shortcuts import render_to_response

from .models import XDomainProxyConfiguration

log = logging.getLogger(__name__)


XDOMAIN_PROXY_CACHE_TIMEOUT = getattr(settings, 'XDOMAIN_PROXY_CACHE_TIMEOUT', 60 * 15)


@cache_page(XDOMAIN_PROXY_CACHE_TIMEOUT)
def xdomain_proxy(request):  # pylint: disable=unused-argument
    """Serve the xdomain proxy page.

    Internet Explorer 9 does not send cookie information with CORS,
    which means we can't make cross-domain POST requests that
    require authentication (for example, from the course details
    page on the marketing site to the enrollment API
    to auto-enroll a user in an "honor" track).

    The XDomain library [https://github.com/jpillora/xdomain]
    provides an alternative to using CORS.

    The library works as follows:

    1) A static HTML file ("xdomain_proxy.html") is served from courses.edx.org.
       The file includes JavaScript and a domain whitelist.

    2) The course details page (on edx.org) creates an invisible iframe
       that loads the proxy HTML file.

    3) A JS shim library on the course details page intercepts
       AJAX requests and communicates with JavaScript on the iframed page.
       The iframed page then proxies the request to the LMS.
       Since the iframed page is served from courses.edx.org,
       this is a same-domain request, so all cookies for the domain
       are sent along with the request.

    You can enable this feature and configure the domain whitelist
    using Django admin.

    """
    config = XDomainProxyConfiguration.current()
    if not config.enabled:
        return HttpResponseNotFound()

    allowed_domains = []
    for domain in config.whitelist.split("\n"):
        if domain.strip():
            allowed_domains.append(domain.strip())

    if not allowed_domains:
        log.warning(
            "No whitelist configured for cross-domain proxy. "
            "You can configure the whitelist in Django Admin "
            "using the XDomainProxyConfiguration model."
        )
        return HttpResponseNotFound()

    context = {
        'xdomain_masters': json.dumps({
            domain: '*'
            for domain in allowed_domains
        })
    }
    return render_to_response('cors_csrf/xdomain_proxy.html', context)
