"""Tests for the CMS drf-spectacular hooks."""

from django.test import SimpleTestCase

from cms.lib.spectacular import cms_api_filter, cms_mark_migrated_paths


def _endpoint(path):
    return (path, path, "GET", None)


def _schema(*paths):
    return {"paths": {path: {"get": {"operationId": path}} for path in paths}}


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
