"""Tests for the cms module itself."""

from django.test import TestCase


class CmsModuleTests(TestCase):
    """
    Tests for cms module itself.
    """

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
