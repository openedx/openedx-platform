"""Tests for the CMS drf-spectacular hooks and schema class."""

from unittest import mock

from django.test import SimpleTestCase
from django.urls import path
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import spectacular_settings
from rest_framework import serializers, viewsets
from rest_framework.response import Response
from rest_framework.settings import api_settings

from cms.lib.spectacular import CmsAutoSchema, cms_api_filter, cms_mark_migrated_paths


def _endpoint(route):
    return (route, route, "GET", None)


def _schema(*routes):
    return {"paths": {route: {"get": {"operationId": route}} for route in routes}}


class CmsApiFilterTest(SimpleTestCase):
    """The pre-processing hook keeps both the legacy and the conforming prefixes."""

    def test_keeps_contentstore_and_authoring_drops_the_rest(self):
        kept = cms_api_filter([
            _endpoint("/api/contentstore/v1/xblock/{usage_key_string}/"),
            _endpoint("/api/authoring/v1/xblocks/{usage_key_string}/"),
            _endpoint("/api/courses/v1/course-key/bulk_enable_disable_discussions"),
            _endpoint("/api/user/v1/accounts/"),
            _endpoint("/heartbeat"),
        ])
        assert [path for path, *_ in kept] == [
            "/api/contentstore/v1/xblock/{usage_key_string}/",
            "/api/authoring/v1/xblocks/{usage_key_string}/",
            "/api/courses/v1/course-key/bulk_enable_disable_discussions",
        ]


class CmsMarkMigratedPathsTest(SimpleTestCase):
    """The post-processing hook works on full paths, so both prefixes coexist in one schema."""

    def test_legacy_paths_deprecated_conforming_paths_not(self):
        result = cms_mark_migrated_paths(_schema(
            "/api/contentstore/v1/xblock/{usage_key_string}/",
            "/api/authoring/v1/xblocks/{usage_key_string}/",
            "/api/contentstore/v3/course_details/{course_id}/",
            "/api/authoring/v3/courses/{course_key}/details/",
        ), None, None, False)
        paths = result["paths"]
        assert paths["/api/contentstore/v1/xblock/{usage_key_string}/"]["get"]["deprecated"] is True
        assert paths["/api/contentstore/v3/course_details/{course_id}/"]["get"]["deprecated"] is True
        assert "deprecated" not in paths["/api/authoring/v1/xblocks/{usage_key_string}/"]["get"]
        assert "deprecated" not in paths["/api/authoring/v3/courses/{course_key}/details/"]["get"]

    def test_bff_surfaces_internal_on_both_mounts(self):
        result = cms_mark_migrated_paths(_schema(
            "/api/contentstore/v3/home/",
            "/api/authoring/v3/home/",
            "/api/authoring/v4/courses/",
        ), None, None, False)
        paths = result["paths"]
        assert paths["/api/contentstore/v3/home/"]["get"]["x-internal"] is True
        assert paths["/api/contentstore/v3/home/"]["get"]["deprecated"] is True
        assert paths["/api/authoring/v3/home/"]["get"]["x-internal"] is True
        assert "deprecated" not in paths["/api/authoring/v3/home/"]["get"]
        assert "x-internal" not in paths["/api/authoring/v4/courses/"]["get"]

    def test_paths_are_left_as_full_urls(self):
        """Paths are not trimmed, so every key resolves against a service-root server."""
        result = cms_mark_migrated_paths(_schema("/api/contentstore/v1/xblock/"), None, None, False)
        assert list(result["paths"]) == ["/api/contentstore/v1/xblock/"]


class _EmptySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    pass


class _HomeViewSet(viewsets.GenericViewSet):
    """Stand-in for a viewset served on both a legacy and a conforming mount."""

    schema = CmsAutoSchema()
    serializer_class = _EmptySerializer

    def list(self, request):
        return Response([])


class CmsAutoSchemaTest(SimpleTestCase):
    """Dual mounts get deterministic operationIds instead of registration-order numeral suffixes."""

    def test_is_the_default_schema_class(self):
        """Views without their own ``schema`` pick this up via REST_FRAMEWORK settings."""
        assert api_settings.DEFAULT_SCHEMA_CLASS is CmsAutoSchema

    def test_only_the_colliding_legacy_address_is_suffixed(self):
        patterns = [
            path("api/contentstore/v3/home/", _HomeViewSet.as_view({"get": "list"})),
            path("api/authoring/v3/home/", _HomeViewSet.as_view({"get": "list"})),
            path("api/contentstore/v4/home/courses/", _HomeViewSet.as_view({"get": "list"})),
            path("api/authoring/v4/courses/", _HomeViewSet.as_view({"get": "list"})),
        ]
        with mock.patch.object(spectacular_settings, "SCHEMA_PATH_PREFIX", r"/api/(contentstore|authoring)"):
            schema = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)
        ids = {p: op["operationId"] for p, item in schema["paths"].items() for op in item.values()}
        assert ids["/api/authoring/v3/home/"] == "v3_home_list"
        assert ids["/api/contentstore/v3/home/"] == "v3_home_list_legacy"
        # Already distinct from its conforming twin's id, so it keeps its name.
        assert ids["/api/authoring/v4/courses/"] == "v4_courses_list"
        assert ids["/api/contentstore/v4/home/courses/"] == "v4_home_courses_list"
