"""Tests for the LMS drf-spectacular hooks and schema class."""

from unittest import mock

from django.test import SimpleTestCase
from django.urls import path
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import spectacular_settings
from rest_framework import serializers, viewsets
from rest_framework.response import Response
from rest_framework.settings import api_settings

from lms.lib.spectacular import LmsAutoSchema, lms_api_filter, lms_mark_legacy_paths_deprecated


def _endpoint(route):
    return (route, route, "GET", None)


def _schema(*routes):
    return {"paths": {route: {"get": {"operationId": route}} for route in routes}}


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

    def test_keeps_grade_versions_and_superseded_grades_v1(self):
        kept = lms_api_filter([
            _endpoint("/api/grade/v2/course_grades/"),
            _endpoint("/api/grades/v1/courses/"),
            _endpoint("/api/grades/v2/courses/"),
            _endpoint("/api/grade/courses/"),
            _endpoint("/api/user/v1/accounts/"),
        ])
        assert [path for path, *_ in kept] == [
            "/api/grade/v2/course_grades/",
            "/api/grades/v1/courses/",
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

    def test_superseded_grades_v1_paths_are_deprecated(self):
        result = lms_mark_legacy_paths_deprecated(_schema(
            "/api/grades/v1/courses/",
            "/api/grades/v1/gradebook/{course_id}/bulk-update",
            "/api/grade/v2/course_grades/",
            "/api/grade/v2/courses/{course_key}/gradebook_entries/",
        ), None, None, False)
        paths = result["paths"]
        assert paths["/api/grades/v1/courses/"]["get"]["deprecated"] is True
        assert paths["/api/grades/v1/gradebook/{course_id}/bulk-update"]["get"]["deprecated"] is True
        assert "deprecated" not in paths["/api/grade/v2/course_grades/"]["get"]
        assert "deprecated" not in paths["/api/grade/v2/courses/{course_key}/gradebook_entries/"]["get"]

    def test_paths_are_left_as_full_urls(self):
        """Paths are not trimmed, so every key resolves against LMS_ROOT_URL."""
        result = lms_mark_legacy_paths_deprecated(_schema("/api/enrollment/v2/enrollments/"), None, None, False)
        assert list(result["paths"]) == ["/api/enrollment/v2/enrollments/"]


class _EmptySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    pass


class _EnrollmentsViewSet(viewsets.GenericViewSet):
    """Stand-in for a view served on both a legacy slashless and a conforming mount."""

    schema = LmsAutoSchema()
    serializer_class = _EmptySerializer

    def list(self, request):
        return Response([])

    def retrieve(self, request, pk=None):
        return Response({})


class LmsAutoSchemaTest(SimpleTestCase):
    """Dual mounts get deterministic operationIds instead of registration-order numeral suffixes."""

    def test_is_the_default_schema_class(self):
        """Views without their own ``schema`` pick this up via REST_FRAMEWORK settings."""
        assert api_settings.DEFAULT_SCHEMA_CLASS is LmsAutoSchema

    def test_only_the_colliding_legacy_address_is_suffixed(self):
        patterns = [
            path("api/enrollment/v2/enrollments", _EnrollmentsViewSet.as_view({"get": "list"})),
            path("api/enrollment/v2/enrollments/", _EnrollmentsViewSet.as_view({"get": "list"})),
            path("api/enrollment/v2/enrollment/<str:pk>", _EnrollmentsViewSet.as_view({"get": "retrieve"})),
            path("api/enrollment/v2/courses/<str:pk>/", _EnrollmentsViewSet.as_view({"get": "retrieve"})),
        ]
        with mock.patch.object(spectacular_settings, "SCHEMA_PATH_PREFIX", "/api/enrollment"):
            schema = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)
        ids = {p: op["operationId"] for p, item in schema["paths"].items() for op in item.values()}
        assert ids["/api/enrollment/v2/enrollments/"] == "v2_enrollments_list"
        assert ids["/api/enrollment/v2/enrollments"] == "v2_enrollments_list_legacy"
        assert ids["/api/enrollment/v2/courses/{id}/"] == "v2_courses_retrieve"
        # Already distinct from every conforming id, so it keeps its name.
        assert ids["/api/enrollment/v2/enrollment/{id}"] == "v2_enrollment_retrieve"
