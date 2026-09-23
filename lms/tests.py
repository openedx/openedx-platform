"""Tests for the lms module itself."""


import logging
import mimetypes

from django.conf import settings  # pylint: disable=unused-import  # noqa: F401
from django.core.cache import cache
from django.test import TestCase, override_settings

log = logging.getLogger(__name__)


class LmsModuleTests(TestCase):
    """
    Tests for lms module itself.
    """

    def test_new_mimetypes(self):
        extensions = ['eot', 'otf', 'ttf', 'woff']
        for extension in extensions:
            mimetype, _ = mimetypes.guess_type('test.' + extension)
            assert mimetype is not None

    def test_api_docs(self):
        """
        Tests that requests to the `/api-docs/` endpoint do not raise an exception.
        """
        response = self.client.get('/api-docs/')
        assert response.status_code == 200

    def test_api_docs_schema(self):
        """
        Tests that the OpenAPI schema generates without raising an exception.

        The `/api-docs/` view above only renders the Swagger UI shell, so this
        is what actually exercises schema generation across the whole service.
        """
        response = self.client.get('/api-docs/schema/')
        assert response.status_code == 200

    @override_settings(
        OPENAPI_CACHE_TIMEOUT=60,
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_api_docs_schema_cache(self):
        """
        The schema cache is keyed on method and language, so neither an OPTIONS
        response nor another language's document is served to a later GET.

        The suite runs with OPENAPI_CACHE_TIMEOUT = 0 and a DummyCache, so the
        cached branch is only reachable with both overridden.
        """
        cache.clear()
        options = self.client.options('/api-docs/schema/')
        spanish = self.client.get('/api-docs/schema/', HTTP_ACCEPT_LANGUAGE='es-419')
        english = self.client.get('/api-docs/schema/', HTTP_ACCEPT_LANGUAGE='en')
        cached = self.client.get('/api-docs/schema/', HTTP_ACCEPT_LANGUAGE='en')
        assert options.status_code == 200
        assert english.status_code == 200
        assert english.content.startswith(b'openapi:')
        assert english.content != spanish.content
        assert english.content == cached.content
