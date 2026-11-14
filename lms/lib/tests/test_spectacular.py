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
    """The pre-processing hook keeps enrollment, cohorts v2 and the superseded cohorts v1 routes."""

    def test_keeps_enrollment_and_the_cohort_routes_only(self):
        """Cohorts v2, and cohorts v1 routes with a successor, are published; nothing else under /api/cohorts/ is."""
        kept = lms_api_filter([
            _endpoint("/api/enrollment/v1/enrollment"),
            _endpoint("/api/enrollment/v2/enrollments/"),
            _endpoint("/api/cohorts/v2/courses/{course_key}/cohorts/"),
            _endpoint("/api/cohorts/v2/courses/{course_key}/cohorts/{cohort_id}/users/{username}/"),
            _endpoint("/api/cohorts/v2/courses/{course_key}/cohort_settings/"),
            _endpoint("/api/cohorts/v2/courses/{course_id}/group_configurations"),
            _endpoint("/api/cohorts/v1/settings/{course_key_string}"),
            _endpoint("/api/cohorts/v1/courses/{course_key_string}/cohorts/{cohort_id}"),
            _endpoint("/api/cohorts/v1/courses/{course_key_string}/cohorts/{cohort_id}/users/{username}"),
            _endpoint("/api/cohorts/v1/courses/{course_key_string}/users"),
            _endpoint("/api/user/v1/accounts/"),
        ])
        assert [path for path, *_ in kept] == [
            "/api/enrollment/v1/enrollment",
            "/api/enrollment/v2/enrollments/",
            "/api/cohorts/v2/courses/{course_key}/cohorts/",
            "/api/cohorts/v2/courses/{course_key}/cohorts/{cohort_id}/users/{username}/",
            "/api/cohorts/v2/courses/{course_key}/cohort_settings/",
            "/api/cohorts/v1/settings/{course_key_string}",
            "/api/cohorts/v1/courses/{course_key_string}/cohorts/{cohort_id}",
            "/api/cohorts/v1/courses/{course_key_string}/cohorts/{cohort_id}/users/{username}",
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

    def test_superseded_cohorts_v1_routes_are_deprecated(self):
        """Every published cohorts v1 operation is deprecated; its v2 successors are not."""
        result = lms_mark_legacy_paths_deprecated(_schema(
            "/api/cohorts/v1/settings/{course_key_string}",
            "/api/cohorts/v1/courses/{course_key_string}/cohorts/{cohort_id}",
            "/api/cohorts/v1/courses/{course_key_string}/cohorts/{cohort_id}/users/{username}",
            "/api/cohorts/v1/courses/{course_key_string}/users",
            "/api/cohorts/v2/courses/{course_key}/cohorts/",
            "/api/cohorts/v2/courses/{course_key}/cohorts/{cohort_id}/users/",
            "/api/cohorts/v2/courses/{course_key}/cohort_settings/",
        ), None, None, False)
        deprecated = sorted(
            path for path, item in result["paths"].items() if item["get"].get("deprecated") is True
        )
        assert deprecated == [
            "/api/cohorts/v1/courses/{course_key_string}/cohorts/{cohort_id}",
            "/api/cohorts/v1/courses/{course_key_string}/cohorts/{cohort_id}/users/{username}",
            "/api/cohorts/v1/settings/{course_key_string}",
        ]

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
