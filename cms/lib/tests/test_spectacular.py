"""Tests for the CMS drf-spectacular hooks."""

from pathlib import Path
from unittest import TestCase

from cms.lib.spectacular import SUPERSEDED_PATHS, cms_api_filter, cms_mark_superseded_paths


def _endpoint(path):
    """Return an endpoint tuple shaped as the pre-processing hook receives it."""
    return (path, path, "GET", object())


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
