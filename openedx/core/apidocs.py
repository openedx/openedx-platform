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

# Settings for the service-wide ``/api-docs`` schema, served by drf-spectacular.
#
# These are passed as ``custom_settings`` to SpectacularAPIView rather than
# living in ``SPECTACULAR_SETTINGS``, because that global is already claimed by
# a deliberately narrow schema in each service -- the Authoring API
# (``/authoring-api/``) in CMS and the Enrollment API (``/lms-api/``) in LMS.
# Both filter the surface down via ``PREPROCESSING_HOOKS`` and trim a path
# prefix, so ``/api-docs`` must switch that filtering off explicitly to cover
# the whole service.
#
# Note this is wider than what edx-api-doc-tools produced: its
# ``ApiSchemaGenerator`` kept only paths under ``/api/`` and pinned the path
# prefix there, so ``/api-docs`` now documents every DRF endpoint in the
# service rather than just the versioned ``/api/*`` surface.
# Note: ``SERVE_*`` settings cannot be overridden through ``custom_settings``
# (drf-spectacular raises AttributeError); SpectacularAPIView takes dedicated
# constructor arguments for those instead.
API_DOCS_SETTINGS = {
    'TITLE': 'Open edX API',
    'DESCRIPTION': 'APIs for access to Open edX information',
    'VERSION': 'v1',
    # Document every endpoint, without the per-service filtering and prefix
    # trimming that SPECTACULAR_SETTINGS applies.
    'PREPROCESSING_HOOKS': [],
    'SCHEMA_PATH_PREFIX': None,
    'SCHEMA_PATH_PREFIX_TRIM': False,
    'SERVERS': [],
}


def get_api_docs_settings():
    """
    Build the ``/api-docs`` schema settings, adding contact details if available.

    The contact email is included when ``API_ACCESS_MANAGER_EMAIL`` is set.
    """
    api_docs_settings = dict(API_DOCS_SETTINGS)
    contact_email = getattr(settings, 'API_ACCESS_MANAGER_EMAIL', None)
    if contact_email:
        api_docs_settings['CONTACT'] = {'email': contact_email}
    return api_docs_settings


def cached_schema_view():
    """
    Build the ``/api-docs`` schema view, caching the rendered document compressed.

    ``cache_page`` stores the pickled ``Response``, which for this schema is over
    memcached's default 1MB item limit. ``CACHES['default']`` sets
    ``ignore_exc: True``, so that oversized ``set`` fails silently and Django
    deletes the key -- the endpoint would look cached while regenerating the
    whole schema on every request. Storing the zlib-compressed body instead
    brings it to roughly a tenth of the limit.

    The key covers the path, the negotiated representation and the active
    language. The document is not identical for every requester: drf-spectacular
    renders it under whatever language is active, and ``LocaleMiddleware`` sets
    that from the request's cookie or ``Accept-Language`` before this runs, so
    two locales produce different documents. Nothing else in it varies by user --
    ``SERVE_PUBLIC`` defaults to ``True``, ``SERVE_*`` cannot be set through
    ``custom_settings``, and ``API_DOCS_SETTINGS`` sets ``'SERVERS': []`` so no
    ``servers`` block is emitted.

    Only GET and HEAD are cached, as ``cache_page`` did: DRF answers OPTIONS on
    this view with a 200 metadata document, which would otherwise be stored and
    served to the next GET.

    ``drf_spectacular.views`` is imported inside the function rather than at
    module scope: this module is imported from the settings, and importing DRF
    views that early freezes ``api_settings`` before ``DEFAULT_SCHEMA_CLASS`` is
    set, which makes schema generation fail with ``Incompatible AutoSchema used
    on View``.
    """
    from drf_spectacular.views import SpectacularAPIView  # pylint: disable=import-outside-toplevel

    view = SpectacularAPIView.as_view(custom_settings=get_api_docs_settings())

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
