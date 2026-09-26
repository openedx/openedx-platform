"""Tests for the LMS drf-spectacular hooks."""

from django.test import SimpleTestCase

from lms.lib.spectacular import lms_api_filter, lms_mark_legacy_paths_deprecated


def _endpoint(path):
    return (path, path, "GET", None)


def _schema(*paths):
    return {"paths": {path: {"get": {"operationId": path}} for path in paths}}


class LmsApiFilterTest(SimpleTestCase):
    """The pre-processing hook keeps only enrollment endpoints."""

    def test_keeps_enrollment_drops_the_rest(self):
        kept = lms_api_filter([
            _endpoint("/api/enrollment/v2/enrollments/"),
            _endpoint("/api/enrollment/v1/enrollment"),
            _endpoint("/api/user/v1/accounts/"),
        ])
        assert [path for path, *_ in kept] == [
            "/api/enrollment/v2/enrollments/",
            "/api/enrollment/v1/enrollment",
        ]


class LmsMarkLegacyPathsDeprecatedTest(SimpleTestCase):
    """Slashless v2 paths are legacy and deprecated; slashed ones are conforming."""

    def test_only_slashless_v2_paths_are_deprecated(self):
        result = lms_mark_legacy_paths_deprecated(_schema(
            "/api/enrollment/v2/enrollments",
            "/api/enrollment/v2/enrollments/",
            "/api/enrollment/v2/course/{course_key}",
            "/api/enrollment/v2/courses/{course_key}/",
            "/api/enrollment/v1/enrollment",
        ), None, None, False)
        paths = result["paths"]
        assert paths["/api/enrollment/v2/enrollments"]["get"]["deprecated"] is True
        assert paths["/api/enrollment/v2/course/{course_key}"]["get"]["deprecated"] is True
        assert "deprecated" not in paths["/api/enrollment/v2/enrollments/"]["get"]
        assert "deprecated" not in paths["/api/enrollment/v2/courses/{course_key}/"]["get"]
        assert "deprecated" not in paths["/api/enrollment/v1/enrollment"]["get"]

    def test_paths_are_left_as_full_urls(self):
        """Paths are not trimmed, so every key resolves against LMS_ROOT_URL."""
        result = lms_mark_legacy_paths_deprecated(_schema("/api/enrollment/v2/enrollments/"), None, None, False)
        assert list(result["paths"]) == ["/api/enrollment/v2/enrollments/"]
