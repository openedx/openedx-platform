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

from lms.lib.spectacular import (
    RENAMED_OPERATION_IDS,
    SUPERSEDED_USER_PATHS,
    LmsAutoSchema,
    lms_api_filter,
    lms_mark_legacy_paths_deprecated,
    superseded_bodies,
)
from openedx.core.apidocs import get_api_docs_settings

ENROLLMENT_VIEWS = "openedx.core.djangoapps.enrollments.views"
USER_API_V1_VIEWS = "openedx.core.djangoapps.user_api.views"
USER_API_V2_VIEWS = "openedx.core.djangoapps.user_api.v2.views"
PROFILE_IMAGE_V1_VIEWS = "openedx.core.djangoapps.profile_images.views"
PROFILE_IMAGE_V2_VIEWS = "openedx.core.djangoapps.profile_images.v2.views"
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
            _endpoint("/api/user/v1/accounts", USER_API_V1_VIEWS),
            _endpoint("/api/user/v1/accounts/{username}", USER_API_V1_VIEWS),
            _endpoint("/api/user/v1/me", USER_API_V1_VIEWS),
            _endpoint("/api/user/v1/accounts/{username}/image", USER_API_V1_VIEWS),
            _endpoint("/api/user/v1/accounts/name_change/", USER_API_V1_VIEWS),
            # Served by this version from the app that owns the resource.
            _endpoint("/api/user/v2/accounts/{username}/profile_image/", PROFILE_IMAGE_V2_VIEWS),
            # Neither served nor superseded by this version.
            _endpoint("/api/user/v2/account/registration/", USER_AUTHN_VIEWS),
            _endpoint("/api/profile_images/v1/{username}/upload", PROFILE_IMAGE_V1_VIEWS),
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
            "/api/user/v1/accounts",
            "/api/user/v1/accounts/{username}",
            "/api/user/v1/me",
            "/api/user/v1/accounts/{username}/image",
            "/api/user/v1/accounts/name_change/",
            "/api/user/v2/accounts/{username}/profile_image/",
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
            "/api/user/v1/accounts/{username}",
            "/api/user/v1/accounts/{username}/deactivate/",
            "/api/user/v1/me",
            "/api/user/v1/accounts/{username}/image",
            "/api/user/v1/accounts/name_change/",
            "/api/user/v1/accounts/name_change/{username}/confirm/",
            "/api/user/v1/accounts/{username}/verification_status/",
            "/api/user/v1/accounts/{username}/verifications/",
            "/api/user/v1/preferences/{username}",
            "/api/profile_images/v1/{username}/upload",
            "/api/user/v2/users/",
            "/api/user/v2/accounts/{username}/profile_image/",
            "/user_api/v1/users/",
            "/user_api/v1/accounts/{username}",
        ), None, None, False)
        paths = result["paths"]
        for marked in (
            "/api/user/v1/users/",
            "/api/user/v1/users/{id}/",
            "/api/user/v1/preferences/time_zones/",
            "/api/user/v1/accounts",
            "/api/user/v1/accounts/{username}",
            "/api/user/v1/accounts/{username}/deactivate/",
            "/api/user/v1/me",
            "/api/user/v1/accounts/{username}/image",
            "/api/user/v1/accounts/name_change/",
            "/api/user/v1/accounts/name_change/{username}/confirm/",
            "/api/user/v1/accounts/{username}/verification_status/",
            "/api/user/v1/accounts/{username}/verifications/",
        ):
            assert paths[marked]["get"]["deprecated"] is True, marked
        for untouched in (
            "/api/user/v1/preferences/{username}",
            "/api/profile_images/v1/{username}/upload",
            "/api/user/v2/users/",
            "/api/user/v2/accounts/{username}/profile_image/",
            "/user_api/v1/users/",
            "/user_api/v1/accounts/{username}",
        ):
            assert "deprecated" not in paths[untouched]["get"], untouched

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
        # The published collection answers with a list of accounts, which is
        # what the generator derives a list operation id from.
        path("api/user/v1/accounts", view.as_view({"get": "list"})),
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


def _derived_operations(schema_class, patches):
    """The names ``schema_class`` produces with nothing pinned, which is what the generator derives."""
    with mock.patch.dict("lms.lib.spectacular.RENAMED_OPERATION_IDS", {}, clear=True):
        return _named_operations(schema_class, patches)


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

    def test_every_pinned_name_replaces_one_a_document_derives(self):
        """An entry keyed on an id no document derives is never reached, and pins nothing."""
        derived = {
            (address, "GET", operation_id)
            for patches in ({}, get_api_docs_settings())
            for address, (operation_id, _tags) in _derived_operations(LmsAutoSchema, patches).items()
        }
        assert set(RENAMED_OPERATION_IDS) - derived == set()

    def test_a_user_address_is_grouped_by_area_where_the_derived_tag_would_name_a_version(self):
        """In the document that trims the area off, the derived tag is the version, and ids are untouched."""
        assert _named_operations(LmsAutoSchema, {}) == {
            "/api/user/v1/accounts": ("v1_accounts_list", ("user",)),
            "/api/user/v1/accounts/{username}": ("v1_accounts_retrieve", ("user",)),
            "/api/user/v1/preferences/{username}": ("v1_preferences_retrieve", ("user",)),
            "/api/user/v1/preferences/{username}/{preference_key}": ("v1_preferences_retrieve_2", ("user",)),
            "/api/user/v1/users/": ("v1_users_list", ("user",)),
            "/api/user/v2/users/": ("v2_users_list", ("user",)),
            "/api/enrollment/v1/enrollments/": ("v1_enrollments_list", ("v1",)),
            "/user_api/v1/preferences/{username}": ("user_api_v1_preferences_retrieve", ("user_api",)),
        }


#: What each superseded operation the schema describes is expected to exchange:
#: the media types of the body it reads, and the success code with the body it
#: answers with. ``None`` is a response code carrying no body at all.
SUPERSEDED_EXCHANGES = {
    ("/api/user/v1/accounts", "get"): ([], {"200": "Account[]"}),
    ("/api/user/v1/accounts/{username}", "get"): ([], {"200": "Account"}),
    ("/api/user/v1/accounts/{username}", "patch"): (["application/merge-patch+json"], {"200": "Account"}),
    ("/api/user/v1/accounts/search_emails", "post"): (
        ["application/json", "application/merge-patch+json"], {"200": "EmailSearchResult[]"},
    ),
    ("/api/user/v1/accounts/{username}/deactivate/", "post"): ([], {"200": "Account"}),
    ("/api/user/v1/accounts/name_change/", "post"): (
        ["application/json", "application/x-www-form-urlencoded", "multipart/form-data"],
        {"201": None},
    ),
    ("/api/user/v1/accounts/name_change/{username}/confirm/", "post"): ([], {"204": None}),
    ("/api/user/v1/accounts/{username}/image", "post"): (
        ["image/gif", "image/jpeg", "image/pjpeg", "image/png", "multipart/form-data"],
        {"204": None},
    ),
    ("/api/user/v1/accounts/{username}/image", "delete"): ([], {"204": None}),
    ("/api/user/v1/accounts/{username}/verification_status/", "get"): ([], {"200": "VerificationStatus"}),
}


def _named_body(response):
    """Return the component a response carries, suffixed ``[]`` for an array, or ``None``."""
    content = response.get("content")
    if not content:
        return None
    body = content["application/json"]["schema"]
    if body.get("type") == "array":
        return f"{body['items']['$ref'].rsplit('/', 1)[-1]}[]"
    return body["$ref"].rsplit("/", 1)[-1]


class SupersededBodyTest(TestCase):
    """Every address published as superseded carries the bodies it exchanges."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = SchemaGenerator().get_schema(request=None, public=True)

    def test_the_described_addresses_are_ones_this_schema_marks_superseded(self):
        described = {address for address, _method in superseded_bodies()}
        marked = {re.sub(r"\{[\w-]+\}", "{}", address) for address in SUPERSEDED_USER_PATHS}
        assert described <= marked, described - marked

    def test_each_superseded_operation_publishes_what_it_reads_and_what_it_answers_with(self):
        published = {}
        for (address, method), _expected in SUPERSEDED_EXCHANGES.items():
            operation = self.schema["paths"][address][method]
            request_media_types = sorted((operation.get("requestBody") or {}).get("content", {}))
            published[(address, method)] = (
                request_media_types,
                {code: _named_body(response) for code, response in operation["responses"].items()},
            )
        assert published == SUPERSEDED_EXCHANGES

    def test_a_body_one_of_the_published_forms_requires_is_published_as_required(self):
        """The forms an image may be sent in are alternatives, and one of them is a form part."""
        image = self.schema["paths"]["/api/user/v1/accounts/{username}/image"]["post"]
        assert image["requestBody"]["required"] is True
        assert sorted(image["requestBody"]["content"]) == [
            "image/gif", "image/jpeg", "image/pjpeg", "image/png", "multipart/form-data",
        ]

    def test_no_superseded_operation_answers_with_a_body_it_does_not_describe(self):
        """A published success body with no schema is the half-documented state this avoids."""
        undescribed = [
            (address, method, code)
            for (address, method), (_request, responses) in SUPERSEDED_EXCHANGES.items()
            for code, body in responses.items()
            if body is None and "content" in self.schema["paths"][address][method]["responses"][code]
        ]
        assert undescribed == []


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

    def test_a_tag_group_holds_one_area_unless_it_marks_the_client_library(self):
        """
        The group a client library is generated from marks operations in any area.

        Every other group names an area, and gathering two of them under one
        name would move operations in every client generated from the document.
        """
        areas = defaultdict(set)
        for route, operation in self.operations():
            for tag in operation.get("tags", []):
                areas[tag].add(route.split("/")[2])
        assert {tag: sorted(members) for tag, members in areas.items()} == {
            "v1": ["enrollment"],
            "openedx-platform-sdk": ["enrollment", "user"],
            "user": ["user"],
        }
