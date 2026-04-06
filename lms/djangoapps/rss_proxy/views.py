"""
Views for the rss_proxy djangoapp.
"""


import requests
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseNotFound

from lms.djangoapps.rss_proxy.models import WhitelistedRssUrl

CACHE_KEY_RSS = "rss_proxy.{url}"


def proxy(request):
    """
    Proxy requests for the given RSS url if it has been whitelisted.
    """

    url = request.GET.get('url')
    if url and WhitelistedRssUrl.objects.filter(url=url).exists():
        # Check cache for RSS if the given url is whitelisted
        cache_key = CACHE_KEY_RSS.format(url=url)
        status_code = 200
        rss = cache.get(cache_key, '')
        print(cache_key)
        print('Cached rss: %s' % rss)  # noqa: UP031
        if not rss:
            # Go get the RSS from the URL if it was not cached
            resp = requests.get(url)
            status_code = resp.status_code
            if status_code == 200:
                # Cache RSS
                rss = resp.content
                cache.set(cache_key, rss, settings.RSS_PROXY_CACHE_TIMEOUT)
        return HttpResponse(rss, status=status_code, content_type='application/xml')

    return HttpResponseNotFound()
