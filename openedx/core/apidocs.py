"""
Open API support.
"""

import logging

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.utils import translation
from django.utils.cache import add_never_cache_headers
from edx_django_utils.cache import get_cache_key
from rest_framework import serializers

from openedx.core.lib.cache_utils import zpickle, zunpickle

log = logging.getLogger(__name__)

# Tag that marks an operation for the generated platform SDK.
SDK_TAG = "openedx-platform-sdk"


def untag_deprecated_operations(result, generator, request, public):  # pylint: disable=unused-argument
    """
    Post-processing hook: drop the SDK tag from deprecated operations.

    A legacy address kept for its deprecation window is served by the same
    view as its replacement, so it inherits the view's SDK tag. Leaving it
    tagged would generate a client for an address callers should move off.
    Registered after the hooks that mark operations deprecated.
    """
    for path_item in result.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict) or not operation.get("deprecated"):
                continue
            tags = [tag for tag in operation.get("tags", []) if tag != SDK_TAG]
            if tags:
                operation["tags"] = tags
            else:
                operation.pop("tags", None)
    return result


def cached_schema_view():
    """
    Build the ``/api-docs`` schema view, caching the rendered document compressed.

    The document is the service-wide schema that ``SPECTACULAR_SETTINGS``
    (openedx/envs/common.py) configures, so the view needs no
    ``custom_settings``; those are for the narrower SDK-facing schemas, see
    ``SDK_SPECTACULAR_SETTINGS`` in each service's settings.

    ``cache_page`` stores the pickled ``Response``, which for this schema is over
    memcached's default 1MB item limit. ``CACHES['default']`` sets
    ``ignore_exc: True``, so that oversized ``set`` fails silently and Django
    deletes the key -- the endpoint would look cached while regenerating the
    whole schema on every request. Storing the zlib-compressed body instead
    brings it to roughly a tenth of the limit.

    The key covers the URLconf, the path, the negotiated representation and the
    active language. The URLconf because the LMS and the CMS serve this view at
    the same path and share one cache by default, so without it whichever
    service generated first would serve its document at the other's URL. The
    language because drf-spectacular renders the document under whatever
    language is active, and ``LocaleMiddleware`` sets that from the request's
    cookie or ``Accept-Language`` before this runs. Nothing else in it varies by
    requester -- ``SERVE_PUBLIC`` defaults to ``True`` and ``SPECTACULAR_SETTINGS``
    sets no ``SERVERS``, so no ``servers`` block is emitted.

    Only GET and HEAD are cached, as ``cache_page`` did: DRF answers OPTIONS on
    this view with a 200 metadata document, which would otherwise be stored and
    served to the next GET.

    ``drf_spectacular.views`` is imported inside the function rather than at
    module scope. When this module was still imported from the settings,
    importing DRF views that early froze ``api_settings`` before
    ``DEFAULT_SCHEMA_CLASS`` was set, failing schema generation with
    ``Incompatible AutoSchema used on View``. Settings no longer import app
    code, but ``SPECTACULAR_SETTINGS`` still names the hook above by dotted
    path, so drf-spectacular imports this module while resolving its own
    settings; the deferred import keeps that free of side effects.
    """
    from drf_spectacular.views import SpectacularAPIView  # pylint: disable=import-outside-toplevel

    view = SpectacularAPIView.as_view()

    def _response(content_type, body):
        """Build the response, marking it uncacheable downstream as drf-yasg did."""
        response = HttpResponse(body, content_type=content_type)
        add_never_cache_headers(response)
        return response

    def schema_view(request, *args, **kwargs):
        """Serve the schema, from the cache when a fresh copy is stored."""
        if not settings.OPENAPI_CACHE_TIMEOUT or request.method not in ('GET', 'HEAD'):
            return view(request, *args, **kwargs)

        cache_key = get_cache_key(
            resource='apidocs-schema',
            urlconf=settings.ROOT_URLCONF,
            path=request.get_full_path(),
            accept=request.META.get('HTTP_ACCEPT', ''),
            language=translation.get_language(),
        ) + '.zpickled'

        cached = cache.get(cache_key)
        if cached:
            try:
                content_type, body = zunpickle(cached)
            except Exception:  # pylint: disable=broad-except
                log.warning("Data for cache is corrupt for cache key %s", cache_key)
                cache.delete(cache_key)
            else:
                return _response(content_type, body)

        response = view(request, *args, **kwargs).render()
        if response.status_code != 200:
            return response

        content_type = response['Content-Type']
        cache.set(
            cache_key,
            zpickle((content_type, response.content)),
            settings.OPENAPI_CACHE_TIMEOUT,
        )
        return _response(content_type, response.content)

    return schema_view


def cursor_paginate_serializer(inner_serializer_class):
    """
    Create a cursor-paginated version of a serializer.

    This is hacky workaround for an edx-api-doc-tools issue described here:
    https://github.com/openedx/api-doc-tools/issues/32

    It assumes we are using cursor-style pagination and assumes a specific
    schema for the pages. It should be removed once we address the underlying issue.

    Arguments:
        inner_serializer_class (type): A subclass of ``Serializer``.

    Returns: type
        A subclass of ``Serializer`` to model the schema of a page of a cursor-paginated
        endpoint.
    """
    class PageOfInnerSerializer(serializers.Serializer):
        """
        A serializer for a page of a cursor-paginated list of ``inner_serializer_class``.
        """
        # pylint: disable=abstract-method
        previous = serializers.URLField(
            required=False,
            help_text="Link to the previous page or results, or null if this is the first.",
        )
        next = serializers.URLField(
            required=False,
            help_text="Link to the next page of results, or null if this is the last.",
        )
        results = serializers.ListField(
            child=inner_serializer_class(),
            help_text="The list of result objects on this page.",
        )

    PageOfInnerSerializer.__name__ = f'PageOf{inner_serializer_class.__name__}'
    return PageOfInnerSerializer
