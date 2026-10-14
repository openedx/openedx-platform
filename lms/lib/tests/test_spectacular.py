"""Tests for the LMS drf-spectacular hooks and schema class."""

import re
from collections import defaultdict
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.urls import path
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import patched_settings, spectacular_settings
from rest_framework import serializers, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.settings import api_settings

from lms.lib.spectacular import LmsAutoSchema, lms_api_filter, lms_mark_legacy_paths_deprecated
from openedx.core.apidocs import get_api_docs_settings

ENROLLMENT_VIEWS = "openedx.core.djangoapps.enrollments.views"
USER_API_V1_VIEWS = "openedx.core.djangoapps.user_api.views"
USER_API_V2_VIEWS = "openedx.core.djangoapps.user_api.v2.views"
USER_AUTHN_VIEWS = "openedx.core.djangoapps.user_authn.views.register"
COURSE_VIEWS = "lms.djangoapps.course_api.views"


def _endpoint(route, view_module=COURSE_VIEWS):
    """An endpoint tuple as the generator hands it to the hook, served from ``view_module``."""
    callback = SimpleNamespace(cls=type("_View", (), {"__module__": view_module}))
    return (route, route, "GET", callback)


def _schema(*routes):
    return {"paths": {route: {"get": {"operationId": route}} for route in routes}}


class LmsApiFilterTest(SimpleTestCase):
    """The hook keeps the enrollment mounts, this version of the user mounts, and what they supersede."""

    def test_keeps_enrollment_this_version_and_the_addresses_it_supersedes(self):
        kept = lms_api_filter([
            _endpoint("/api/enrollment/v2/enrollments/", ENROLLMENT_VIEWS),
            _endpoint("/api/enrollment/v1/enrollment", ENROLLMENT_VIEWS),
            _endpoint("/api/user/v2/users/", USER_API_V2_VIEWS),
            _endpoint("/api/user/v2/user_preferences/{username},{key}/", USER_API_V2_VIEWS),
            # Superseded, and spelled with the parameter name the generator has
            # not renamed to ``{id}`` yet.
            _endpoint("/api/user/v1/users/{pk}/", USER_API_V1_VIEWS),
            _endpoint("/api/user/v1/preferences/time_zones/", USER_API_V1_VIEWS),
            # Neither served nor superseded by this version.
            _endpoint("/api/user/v2/account/registration/", USER_AUTHN_VIEWS),
            _endpoint("/api/user/v1/accounts", USER_API_V1_VIEWS),
            _endpoint("/api/user/v1/me", USER_API_V1_VIEWS),
            _endpoint("/api/user/v1/preferences/{username}", USER_API_V1_VIEWS),
            _endpoint("/api/courses/v1/courses/", COURSE_VIEWS),
            _endpoint("/api/user_tours/v1/tours/"),
            _endpoint("/user_api/v1/users/", USER_API_V1_VIEWS),
        ])
        assert [path for path, *_ in kept] == [
            "/api/enrollment/v2/enrollments/",
            "/api/enrollment/v1/enrollment",
            "/api/user/v2/users/",
            "/api/user/v2/user_preferences/{username},{key}/",
            "/api/user/v1/users/{pk}/",
            "/api/user/v1/preferences/time_zones/",
        ]

    def test_under_the_version_prefix_the_view_package_decides(self):
        """Other apps are mounted under the same prefix, so the path alone cannot admit an address."""
        route = "/api/user/v2/account/registration/"
        admitted = {
            view_module: [path for path, *_ in lms_api_filter([_endpoint(route, view_module)])]
            for view_module in (USER_AUTHN_VIEWS, USER_API_V2_VIEWS)
        }
        assert admitted == {USER_AUTHN_VIEWS: [], USER_API_V2_VIEWS: [route]}


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

    def test_only_the_listed_user_addresses_are_deprecated(self):
        result = lms_mark_legacy_paths_deprecated(_schema(
            "/api/user/v1/users/",
            "/api/user/v1/users/{id}/",
            "/api/user/v1/preferences/time_zones/",
            "/api/user/v1/accounts",
            "/api/user/v1/preferences/{username}",
            "/api/user/v2/users/",
            "/user_api/v1/users/",
        ), None, None, False)
        paths = result["paths"]
        assert paths["/api/user/v1/users/"]["get"]["deprecated"] is True
        assert paths["/api/user/v1/users/{id}/"]["get"]["deprecated"] is True
        assert paths["/api/user/v1/preferences/time_zones/"]["get"]["deprecated"] is True
        assert "deprecated" not in paths["/api/user/v1/accounts"]["get"]
        assert "deprecated" not in paths["/api/user/v1/preferences/{username}"]["get"]
        assert "deprecated" not in paths["/api/user/v2/users/"]["get"]
        assert "deprecated" not in paths["/user_api/v1/users/"]["get"]

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


class _UserApiViewSet(viewsets.GenericViewSet):
    """Stand-in for a user view read both as a collection and as a member."""

    serializer_class = _EmptySerializer
    permission_classes = (AllowAny,)

    def list(self, request):
        return Response([])

    def retrieve(self, request, **kwargs):
        return Response({})


def _user_routes(schema_class):
    """User, enrollment and non-``/api/`` addresses, inspected by ``schema_class``."""
    view = type("_InspectedViewSet", (_UserApiViewSet,), {"schema": schema_class()})
    return [
        path("api/user/v1/accounts", view.as_view({"get": "retrieve"})),
        path("api/user/v1/accounts/<str:username>", view.as_view({"get": "retrieve"})),
        path("api/user/v1/preferences/<str:username>", view.as_view({"get": "retrieve"})),
        path(
            "api/user/v1/preferences/<str:username>/<str:preference_key>",
            view.as_view({"get": "retrieve"}),
        ),
        path("api/user/v1/users/", view.as_view({"get": "list"})),
        path("api/user/v2/users/", view.as_view({"get": "list"})),
        path("api/enrollment/v1/enrollments/", view.as_view({"get": "list"})),
        # Outside ``/api/``, so a document that derives its prefix from the
        # routes it covers derives the one the service-wide document has.
        path("user_api/v1/preferences/<str:username>", view.as_view({"get": "retrieve"})),
    ]


def _named_operations(schema_class, patches):
    """The ``{address: (operationId, tags)}`` ``schema_class`` produces under ``patches``."""
    with patched_settings({"PREPROCESSING_HOOKS": [], **patches}):
        schema = SchemaGenerator(patterns=_user_routes(schema_class)).get_schema(request=None, public=True)
    return {
        route: (operation["operationId"], tuple(operation["tags"]))
        for route, path_item in schema["paths"].items()
        for operation in path_item.values()
    }


class UserOperationIdTest(SimpleTestCase):
    """User addresses keep the names the generator derives; only their tag group is chosen here."""

    def test_the_service_wide_document_keeps_every_name_the_generator_derives(self):
        """Each of these is the name that document already published, and a client imports it by name."""
        assert _named_operations(LmsAutoSchema, get_api_docs_settings()) == {
            "/api/user/v1/accounts": ("api_user_v1_accounts_retrieve", ("api",)),
            "/api/user/v1/accounts/{username}": ("api_user_v1_accounts_retrieve_2", ("api",)),
            "/api/user/v1/preferences/{username}": ("api_user_v1_preferences_retrieve", ("api",)),
            "/api/user/v1/preferences/{username}/{preference_key}":
                ("api_user_v1_preferences_retrieve_2", ("api",)),
            "/api/user/v1/users/": ("api_user_v1_users_list", ("api",)),
            "/api/user/v2/users/": ("api_user_v2_users_list", ("api",)),
            "/api/enrollment/v1/enrollments/": ("api_enrollment_v1_enrollments_list", ("api",)),
            "/user_api/v1/preferences/{username}": ("user_api_v1_preferences_retrieve", ("user_api",)),
        }

    def test_a_user_address_is_grouped_by_area_where_the_derived_tag_would_name_a_version(self):
        """In the document that trims the area off, the derived tag is the version, and ids are untouched."""
        assert _named_operations(LmsAutoSchema, {}) == {
            "/api/user/v1/accounts": ("v1_accounts_retrieve", ("user",)),
            "/api/user/v1/accounts/{username}": ("v1_accounts_retrieve_2", ("user",)),
            "/api/user/v1/preferences/{username}": ("v1_preferences_retrieve", ("user",)),
            "/api/user/v1/preferences/{username}/{preference_key}": ("v1_preferences_retrieve_2", ("user",)),
            "/api/user/v1/users/": ("v1_users_list", ("user",)),
            "/api/user/v2/users/": ("v2_users_list", ("user",)),
            "/api/enrollment/v1/enrollments/": ("v1_enrollments_list", ("v1",)),
            "/user_api/v1/preferences/{username}": ("user_api_v1_preferences_retrieve", ("user_api",)),
        }


class PublishedDocumentTest(TestCase):
    """What the service actually publishes: distinct ids, and one area per tag group."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = SchemaGenerator().get_schema(request=None, public=True)

    @classmethod
    def operations(cls):
        """Yield every (path, operation) the published document carries."""
        for route, path_item in cls.schema["paths"].items():
            for operation in path_item.values():
                if isinstance(operation, dict) and "operationId" in operation:
                    yield route, operation

    def test_every_operation_id_is_unique(self):
        ids = [operation["operationId"] for _route, operation in self.operations()]
        assert sorted(ids) == sorted(set(ids))

    def test_only_the_one_enrollment_pair_is_told_apart_by_a_numeral_suffix(self):
        """A numeral suffix is assigned in route order, so it is not a stable name."""
        suffixed = {
            route: operation["operationId"]
            for route, operation in self.operations()
            if re.fullmatch(r".+_\d+", operation["operationId"])
        }
        # Renaming this one would change an id the document already published, so it stays.
        assert suffixed == {"/api/enrollment/v1/enrollment/{course_id}": "v1_enrollment_retrieve_2"}

    def test_no_tag_group_holds_two_areas(self):
        areas = defaultdict(set)
        for route, operation in self.operations():
            for tag in operation.get("tags", []):
                areas[tag].add(route.split("/")[2])
        assert {tag: sorted(members) for tag, members in areas.items()} == {
            "v1": ["enrollment"],
            "openedx-platform-sdk": ["enrollment"],
            "user": ["user"],
        }
