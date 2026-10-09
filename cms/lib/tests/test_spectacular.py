"""Tests for the CMS drf-spectacular hooks and schema class."""

from pathlib import Path
from unittest import TestCase, mock

from django.test import SimpleTestCase
from django.urls import path as url_path
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import spectacular_settings
from rest_framework import serializers, viewsets
from rest_framework.response import Response
from rest_framework.settings import api_settings

from cms.lib.spectacular import (
    SUPERSEDED_PATHS,
    CmsAutoSchema,
    cms_api_filter,
    cms_mark_migrated_paths,
    cms_mark_superseded_paths,
)


def _endpoint(path):
    """Return an endpoint tuple shaped as the pre-processing hook receives it."""
    return (path, path, "GET", object())


def _schema(*routes):
    return {"paths": {route: {"get": {"operationId": route}} for route in routes}}


class CmsApiFilterTest(TestCase):
    """Which mounts reach the generated schema."""

    @staticmethod
    def _admitted(path):
        """Return whether the pre-processing hook keeps path."""
        return bool(cms_api_filter([_endpoint(path)]))

    def test_the_authoring_mount_is_admitted(self):
        assert self._admitted("/api/authoring/v1/courses/course-v1:a+b+c/youtube_transcript_checks/")

    def test_the_contentstore_mount_is_still_admitted(self):
        assert self._admitted("/api/contentstore/v0/youtube_transcripts/course-v1:a+b+c/check")
        assert self._admitted("/api/contentstore/v4/home/courses/")

    def test_the_hand_listed_discussions_path_is_still_admitted(self):
        assert self._admitted("/api/courses/course-v1:a+b+c/bulk_enable_disable_discussions")

    def test_an_unrelated_mount_is_refused(self):
        assert not self._admitted("/api/user/v1/accounts/")
        assert not self._admitted("/api/authoring/courses/")
        assert not self._admitted("/authoring/v1/courses/")

    def test_the_filter_preserves_the_endpoint_tuples_it_keeps(self):
        kept = _endpoint("/api/authoring/v1/courses/")
        dropped = _endpoint("/api/user/v1/accounts/")
        assert cms_api_filter([kept, dropped]) == [kept]


class CmsMarkSupersededPathsTest(TestCase):
    """Which operations the post-processing hook marks deprecated."""

    def _run(self, schema):
        return cms_mark_superseded_paths(schema, generator=None, request=None, public=True)

    def test_every_operation_of_a_superseded_path_is_marked(self):
        schema = {"paths": {path: {"get": {}} for path in SUPERSEDED_PATHS}}
        result = self._run(schema)
        for path in SUPERSEDED_PATHS:
            assert result["paths"][path]["get"]["deprecated"] is True

    def test_both_spellings_of_each_superseded_path_are_listed(self):
        assert set(SUPERSEDED_PATHS) == {
            "/api/contentstore/v0/youtube_transcripts/{course_id}/chec",
            "/api/contentstore/v0/youtube_transcripts/{course_id}/check",
            "/api/contentstore/v0/youtube_transcripts/{course_id}/uploa",
            "/api/contentstore/v0/youtube_transcripts/{course_id}/upload",
        }

    def test_the_conforming_paths_are_left_unmarked(self):
        conforming = "/api/authoring/v1/courses/{course_key}/youtube_transcript_checks/"
        result = self._run({"paths": {conforming: {"get": {"operationId": "x"}}}})
        assert result["paths"][conforming]["get"] == {"operationId": "x"}

    def test_an_unrelated_contentstore_path_is_left_unmarked(self):
        other = "/api/contentstore/v0/advanced_settings/{course_id}"
        result = self._run({"paths": {other: {"get": {}}}})
        assert "deprecated" not in result["paths"][other]["get"]

    def test_path_level_keys_are_not_treated_as_operations(self):
        path = SUPERSEDED_PATHS[0]
        result = self._run({"paths": {path: {"parameters": [], "get": {}}}})
        assert result["paths"][path]["parameters"] == []
        assert result["paths"][path]["get"]["deprecated"] is True

    def test_a_schema_without_the_superseded_paths_is_returned_unchanged(self):
        schema = {"paths": {}}
        assert self._run(schema) == {"paths": {}}


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


class SpectacularSettingsTest(TestCase):
    """
    The service settings the hooks depend on.

    Read from the environment modules that define them, because the test
    settings module does not configure the schema at all.
    """

    SETTINGS_MODULES = ("cms/envs/devstack.py", "cms/envs/production.py")

    def _source(self, module):
        return (Path(__file__).resolve().parents[3] / module).read_text()

    def test_both_hooks_are_configured_in_every_environment(self):
        for module in self.SETTINGS_MODULES:
            source = self._source(module)
            assert "'PREPROCESSING_HOOKS': ['cms.lib.spectacular.cms_api_filter']" in source, module
            assert "'cms.lib.spectacular.cms_mark_superseded_paths'," in source, module
            assert "'cms.lib.spectacular.cms_mark_migrated_paths'," in source, module

    def test_the_default_enum_hook_is_kept_alongside_the_new_one(self):
        """Setting the key replaces drf-spectacular's default list, so it is restated."""
        for module in self.SETTINGS_MODULES:
            source = self._source(module)
            assert "'drf_spectacular.hooks.postprocess_schema_enums'," in source, module

    def test_no_prefix_is_trimmed_so_paths_are_emitted_in_full(self):
        for module in self.SETTINGS_MODULES:
            assert "SCHEMA_PATH_PREFIX_TRIM" not in self._source(module), module

    def test_the_tag_prefix_covers_both_mounts(self):
        for module in self.SETTINGS_MODULES:
            source = self._source(module)
            assert "'SCHEMA_PATH_PREFIX': r'/api/(contentstore|authoring)'," in source, module

    def test_no_server_advertises_a_single_mount_as_its_root(self):
        for module in self.SETTINGS_MODULES:
            assert "CMS-contentstore" not in self._source(module), module


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
            url_path("api/contentstore/v3/home/", _HomeViewSet.as_view({"get": "list"})),
            url_path("api/authoring/v3/home/", _HomeViewSet.as_view({"get": "list"})),
            url_path("api/contentstore/v4/home/courses/", _HomeViewSet.as_view({"get": "list"})),
            url_path("api/authoring/v4/courses/", _HomeViewSet.as_view({"get": "list"})),
        ]
        with mock.patch.object(spectacular_settings, "SCHEMA_PATH_PREFIX", r"/api/(contentstore|authoring)"):
            schema = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)
        ids = {p: op["operationId"] for p, item in schema["paths"].items() for op in item.values()}
        assert ids["/api/authoring/v3/home/"] == "v3_home_list"
        assert ids["/api/contentstore/v3/home/"] == "v3_home_list_legacy"
        # Already distinct from its conforming twin's id, so it keeps its name.
        assert ids["/api/authoring/v4/courses/"] == "v4_courses_list"
        assert ids["/api/contentstore/v4/home/courses/"] == "v4_home_courses_list"
