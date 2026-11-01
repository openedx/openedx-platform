"""Tests for the LMS drf-spectacular hooks and schema class."""

import json
import re
from collections import defaultdict
from functools import cache
from importlib import import_module
from types import SimpleNamespace
from unittest import mock

import jsonschema
from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase
from django.test.client import FakePayload
from django.urls import path, resolve
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import patched_settings, spectacular_settings
from rest_framework import serializers, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.settings import api_settings

from common.djangoapps.student.models_api import do_name_change_request
from common.djangoapps.student.tests.factories import TEST_PASSWORD, UserFactory
from lms.lib.spectacular import LmsAutoSchema, lms_api_filter, lms_mark_legacy_paths_deprecated
from openedx.core.apidocs import get_api_docs_settings
from openedx.core.djangoapps.profile_images.tests.helpers import make_image_file
from openedx.core.djangoapps.user_api.accounts.image_helpers import (
    get_profile_image_names,
    get_profile_image_storage,
)
from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.core.djangoapps.user_api.schema import (
    RENAMED_OPERATION_IDS,
    SUPERSEDED_USER_PATHS,
    mark_superseded_user_paths,
    superseded_bodies,
)

ENROLLMENT_VIEWS = "openedx.core.djangoapps.enrollments.views"
USER_API_V1_VIEWS = "openedx.core.djangoapps.user_api.views"
USER_API_V2_VIEWS = "openedx.core.djangoapps.user_api.v2.views"
PROFILE_IMAGE_V1_VIEWS = "openedx.core.djangoapps.profile_images.views"
PROFILE_IMAGE_V2_VIEWS = "openedx.core.djangoapps.profile_images.v2.views"
USER_AUTHN_VIEWS = "openedx.core.djangoapps.user_authn.views.register"
USER_MOUNT = "api/user/"
COURSE_VIEWS = "lms.djangoapps.course_api.views"


def _endpoint(route):
    return (route, route, "GET", None)


def _served_endpoint(route, view_module=COURSE_VIEWS):
    """An endpoint tuple as the generator hands it to the hook, served from ``view_module``."""
    callback = SimpleNamespace(cls=type("_View", (), {"__module__": view_module}))
    return (route, route, "GET", callback)


def _schema(*routes):
    return {"paths": {route: {"get": {"operationId": route}} for route in routes}}


class LmsApiFilterTest(SimpleTestCase):
    """The pre-processing hook keeps enrollment endpoints and drops unrelated ones."""

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


class LmsApiFilterUserAddressTest(SimpleTestCase):
    """The hook keeps the current user mounts and the addresses they supersede, beside enrollment."""

    def test_keeps_enrollment_this_version_and_the_addresses_it_supersedes(self):
        kept = lms_api_filter([
            _served_endpoint("/api/enrollment/v2/enrollments/", ENROLLMENT_VIEWS),
            _served_endpoint("/api/enrollment/v1/enrollment", ENROLLMENT_VIEWS),
            _served_endpoint("/api/user/v2/users/", USER_API_V2_VIEWS),
            _served_endpoint("/api/user/v2/user_preferences/{username},{key}/", USER_API_V2_VIEWS),
            # Superseded, and spelled with the parameter name the generator has
            # not renamed to ``{id}`` yet.
            _served_endpoint("/api/user/v1/users/{pk}/", USER_API_V1_VIEWS),
            _served_endpoint("/api/user/v1/preferences/time_zones/", USER_API_V1_VIEWS),
            _served_endpoint("/api/user/v1/accounts", USER_API_V1_VIEWS),
            _served_endpoint("/api/user/v1/accounts/{username}", USER_API_V1_VIEWS),
            _served_endpoint("/api/user/v1/me", USER_API_V1_VIEWS),
            _served_endpoint("/api/user/v1/accounts/{username}/image", USER_API_V1_VIEWS),
            _served_endpoint("/api/user/v1/accounts/name_change/", USER_API_V1_VIEWS),
            _served_endpoint("/api/user/v1/preferences/{username}", USER_API_V1_VIEWS),
            _served_endpoint(
                "/api/user/v1/preferences/{username}/{preference_key}", USER_API_V1_VIEWS,
            ),
            _served_endpoint("/api/user/v1/preferences/email_opt_in/", USER_API_V1_VIEWS),
            _served_endpoint("/api/user/v2/account/registration/", USER_AUTHN_VIEWS),
            # Served by this version from the app that owns the resource.
            _served_endpoint("/api/user/v2/accounts/{username}/profile_image/", PROFILE_IMAGE_V2_VIEWS),
            # Neither served nor superseded by this version.
            _served_endpoint("/user_api/v2/account/registration/", USER_AUTHN_VIEWS),
            _served_endpoint("/api/profile_images/v1/{username}/upload", PROFILE_IMAGE_V1_VIEWS),
            _served_endpoint("/api/courses/v1/courses/", COURSE_VIEWS),
            _served_endpoint("/api/user_tours/v1/tours/"),
            _served_endpoint("/user_api/v1/users/", USER_API_V1_VIEWS),
            _served_endpoint("/user_api/v1/preferences/email_opt_in/", USER_API_V1_VIEWS),
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
            "/api/user/v1/preferences/{username}",
            "/api/user/v1/preferences/{username}/{preference_key}",
            "/api/user/v1/preferences/email_opt_in/",
            "/api/user/v2/account/registration/",
            "/api/user/v2/accounts/{username}/profile_image/",
        ]

    def test_under_the_version_prefix_the_view_package_decides(self):
        """Other apps are mounted under the same prefix, so the path alone cannot admit an address."""
        route = "/api/user/v2/login_sessions/"
        admitted = {
            view_module: [path for path, *_ in lms_api_filter([_served_endpoint(route, view_module)])]
            for view_module in (USER_AUTHN_VIEWS, USER_API_V2_VIEWS)
        }
        assert admitted == {USER_AUTHN_VIEWS: [], USER_API_V2_VIEWS: [route]}

    def test_keeps_the_authentication_addresses_this_version_supersedes_and_none_beside_them(self):
        """Each is admitted by its exact published key, whatever app serves it."""
        superseded = [
            "/api/user/v1/account/registration/",
            "/api/user/v2/account/registration/",
            "/api/user/v1/account/password_reset/",
            "/api/user/v1/account/password_reset/token/validate/",
            "/api/user/v1/validation/registration",
        ]
        neighbours = [
            # Superseded as well, but it takes its version as a parameter.
            "/api/user/{api_version}/account/login_session/",
            # The same app's addresses that keep resolving and are not superseded here.
            "/user_api/v1/account/registration/",
            "/user_api/v2/account/registration/",
            "/user_api/{api_version}/account/login_session/",
            "/user_api/v1/account/password_reset/",
            "/user_api/v1/account/password_reset/token/validate/",
            "/create_account",
            "/login_ajax",
            # Spellings of the keys above that no route publishes.
            "/api/user/v1/account/login_session/",
            "/api/user/v1/validation/registration/",
            # The addresses replacing them, which only their own views admit.
            "/api/user/v2/registrations/",
            "/api/user/v2/login_sessions/",
        ]
        kept = lms_api_filter([_served_endpoint(route, USER_AUTHN_VIEWS) for route in superseded + neighbours])
        assert [path for path, *_ in kept] == superseded


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


class MarkSupersededUserPathsTest(SimpleTestCase):
    """Only the addresses the shared list names are marked, wherever a service publishes them."""

    def test_only_the_listed_user_addresses_are_deprecated(self):
        result = mark_superseded_user_paths(_schema(
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
            "/api/user/v1/preferences/{username}/{preference_key}",
            "/api/user/v1/preferences/email_opt_in/",
            "/api/profile_images/v1/{username}/upload",
            "/api/user/v2/users/",
            "/api/user/v2/accounts/{username}/profile_image/",
            "/api/user/v2/accounts/{username}/preferences/",
            "/user_api/v1/users/",
            "/user_api/v1/accounts/{username}",
            "/user_api/v1/preferences/email_opt_in/",
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
            "/api/user/v1/preferences/{username}",
            "/api/user/v1/preferences/{username}/{preference_key}",
            "/api/user/v1/preferences/email_opt_in/",
        ):
            assert paths[marked]["get"]["deprecated"] is True, marked
        for untouched in (
            "/api/profile_images/v1/{username}/upload",
            "/api/user/v2/users/",
            "/api/user/v2/accounts/{username}/profile_image/",
            "/api/user/v2/accounts/{username}/preferences/",
            "/user_api/v1/users/",
            "/user_api/v1/accounts/{username}",
            "/user_api/v1/preferences/email_opt_in/",
        ):
            assert "deprecated" not in paths[untouched]["get"], untouched

    def test_the_authentication_addresses_are_deprecated_and_their_neighbours_are_not(self):
        marked = (
            "/api/user/v1/account/registration/",
            "/api/user/v2/account/registration/",
            "/api/user/v1/account/password_reset/",
            "/api/user/v1/account/password_reset/token/validate/",
            "/api/user/v1/validation/registration",
            "/api/user/{api_version}/account/login_session/",
        )
        untouched = (
            "/user_api/v1/account/registration/",
            "/user_api/v2/account/registration/",
            "/user_api/{api_version}/account/login_session/",
            "/user_api/v1/account/password_reset/",
            "/user_api/v1/account/password_reset/token/validate/",
            "/create_account",
            "/login_ajax",
            "/api/user/v1/account/login_session/",
            "/api/user/v2/account/login_session/",
            "/api/user/v1/validation/registration/",
            "/api/user/v2/registrations/",
            "/api/user/v2/registration_validations/",
            "/api/user/v2/login_sessions/",
            "/api/user/v2/password_resets/",
            "/api/user/v2/password_reset_token_validations/",
            "/api/user/v2/users/",
            "/api/user/v2/accounts/{username}/",
        )
        paths = mark_superseded_user_paths(_schema(*marked, *untouched), None, None, False)["paths"]
        assert {path: paths[path]["get"].get("deprecated") for path in marked} == dict.fromkeys(marked, True)
        assert {path: paths[path]["get"].get("deprecated") for path in untouched} == dict.fromkeys(untouched)


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
    """Dual mounts get deterministic operationIds instead of numeral suffixes."""

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
        # Two reads of the retirement queue that answer with an array, which
        # is what the generator derives a list operation id from, and which
        # the document that trims nothing off the front has published under
        # the name derived for a single record.
        path("api/user/v1/accounts/retirement_queue/", view.as_view({"get": "list"})),
        path(
            "api/user/v1/accounts/retirements_by_status_and_date/",
            view.as_view({"get": "list"}),
        ),
        path("api/user/v2/accounts/<str:username>/preferences/", view.as_view({"get": "retrieve"})),
        path(
            "api/user/v2/accounts/<str:username>/preferences/<str:key>/",
            view.as_view({"get": "retrieve"}),
        ),
        path("api/user/v2/users/", view.as_view({"get": "list"})),
        # A stored preference is addressed by its owner and its name together,
        # and the comma between them is a token of the address like any other,
        # so the generator leaves it in the name it derives.
        path(
            "api/user/v2/user_preferences/<str:username>,<str:key>/",
            view.as_view({"get": "retrieve"}),
        ),
        path("api/enrollment/v1/enrollments/", view.as_view({"get": "list"})),
        # Outside ``/api/``, so a document that derives its prefix from the
        # routes it covers derives the one the service-wide document has.
        path("user_api/v1/preferences/<str:username>", view.as_view({"get": "retrieve"})),
    ]


def _derived_operations(schema_class, patches):
    """
    The names ``schema_class`` produces with nothing pinned, which is what the generator derives.

    The paths are read as they are assembled, which is before the numeral
    telling two addresses that derived one name apart is handed out: a name
    carrying that numeral was derived without it, and is looked up without it.
    """
    with mock.patch.dict("openedx.core.djangoapps.user_api.schema.RENAMED_OPERATION_IDS", {}, clear=True):
        with patched_settings({"PREPROCESSING_HOOKS": [], **patches}):
            generator = SchemaGenerator(patterns=_user_routes(schema_class))
            return _by_address(generator.parse(None, public=True))


def _named_operations(schema_class, patches):
    """The ``{address: (operationId, tags)}`` ``schema_class`` produces under ``patches``."""
    with patched_settings({"PREPROCESSING_HOOKS": [], **patches}):
        schema = SchemaGenerator(patterns=_user_routes(schema_class)).get_schema(request=None, public=True)
    return _by_address(schema["paths"])


def _by_address(paths):
    """The ``{address: (operationId, tags)}`` the paths of a document carry."""
    return {
        route: (operation["operationId"], tuple(operation["tags"]))
        for route, path_item in paths.items()
        for operation in path_item.values()
    }


class UserOperationIdTest(SimpleTestCase):
    """A user address keeps the name the generator derives unless one is pinned for it."""

    def test_the_service_wide_document_keeps_every_name_the_generator_derives(self):
        """
        Each of these is the name that document publishes, and a client imports it by name.

        An address that document has published for years keeps the name it
        derived there, numeral and all; an address of a version it has not
        published yet carries the name pinned for it.
        """
        assert _named_operations(LmsAutoSchema, get_api_docs_settings()) == {
            "/api/user/v1/accounts": ("api_user_v1_accounts_retrieve", ("api",)),
            "/api/user/v1/accounts/{username}": ("api_user_v1_accounts_retrieve_2", ("api",)),
            "/api/user/v1/preferences/{username}": ("api_user_v1_preferences_retrieve", ("api",)),
            "/api/user/v1/preferences/{username}/{preference_key}":
                ("api_user_v1_preferences_retrieve_2", ("api",)),
            "/api/user/v1/users/": ("api_user_v1_users_list", ("api",)),
            "/api/user/v1/accounts/retirement_queue/":
                ("api_user_v1_accounts_retirement_queue_retrieve", ("api",)),
            "/api/user/v1/accounts/retirements_by_status_and_date/":
                ("api_user_v1_accounts_retirements_by_status_and_date_retrieve", ("api",)),
            "/api/user/v2/accounts/{username}/preferences/":
                ("api_user_v2_accounts_preferences_retrieve", ("api",)),
            "/api/user/v2/accounts/{username}/preferences/{key}/":
                ("api_user_v2_accounts_preferences_retrieve_one", ("api",)),
            "/api/user/v2/users/": ("api_user_v2_users_list", ("api",)),
            "/api/user/v2/user_preferences/{username},{key}/":
                ("api_user_v2_user_preferences_retrieve", ("api",)),
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
            "/api/user/v1/preferences/{username}/{preference_key}": (
                "v1_preferences_retrieve_one", ("user",),
            ),
            "/api/user/v1/users/": ("v1_users_list", ("user",)),
            "/api/user/v1/accounts/retirement_queue/": ("v1_accounts_retirement_queue_list", ("user",)),
            "/api/user/v1/accounts/retirements_by_status_and_date/": (
                "v1_accounts_retirements_by_status_and_date_list", ("user",),
            ),
            "/api/user/v2/accounts/{username}/preferences/": (
                "v2_accounts_preferences_retrieve", ("user",),
            ),
            "/api/user/v2/accounts/{username}/preferences/{key}/": (
                "v2_accounts_preferences_retrieve_one", ("user",),
            ),
            "/api/user/v2/users/": ("v2_users_list", ("user",)),
            "/api/user/v2/user_preferences/{username},{key}/": (
                "v2_user_preferences_retrieve", ("user",),
            ),
            "/api/enrollment/v1/enrollments/": ("v1_enrollments_list", ("v1",)),
            "/user_api/v1/preferences/{username}": ("user_api_v1_preferences_retrieve", ("user_api",)),
        }


#: The preference document a superseded read answers with, spelled out here
#: rather than read from the module that publishes it: an edit to the shape
#: that module publishes is then an edit this test sees.
PREFERENCE_DOCUMENT_BODY = {
    "type": "object",
    "additionalProperties": {"type": "string"},
    "description": (
        "Preferences of one account, as an object of preference name to stored value. Every "
        "value is text. Which names an account carries depends on the features it uses."
    ),
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
    ("/api/user/v1/preferences/{username}", "get"): ([], {"200": PREFERENCE_DOCUMENT_BODY}),
    ("/api/user/v1/preferences/{username}", "patch"): (["application/merge-patch+json"], {"204": None}),
    ("/api/user/v1/preferences/{username}/{preference_key}", "get"): ([], {"200": {"type": "string"}}),
    ("/api/user/v1/preferences/{username}/{preference_key}", "put"): (["application/json"], {"204": None}),
    ("/api/user/v1/preferences/{username}/{preference_key}", "delete"): ([], {"204": None}),
    ("/api/user/v1/preferences/email_opt_in/", "post"): (
        ["application/x-www-form-urlencoded"], {"200": None},
    ),
}


def _named_body(response):
    """
    Return what a response carries: the component it names, or the shape itself.

    A component is named by its own name, suffixed ``[]`` where the body is an
    array of it; a body described in place comes back as that description, and
    a response with no body at all as ``None``.
    """
    content = response.get("content")
    if not content:
        return None
    body = content["application/json"]["schema"]
    if body.get("type") == "array":
        return f"{body['items']['$ref'].rsplit('/', 1)[-1]}[]"
    if "$ref" not in body:
        return body
    return body["$ref"].rsplit("/", 1)[-1]


@cache
def _user_documents():
    """
    Return the two documents this service publishes, generated once and from the user mounts only.

    Each is generated under the settings it is published with, so what it says
    about a user address is what the whole document says about it.
    """
    patterns = [
        entry for entry in import_module(settings.ROOT_URLCONF).urlpatterns
        if str(entry.pattern).startswith(USER_MOUNT)
    ]
    published = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)
    # The whole document derives its path prefix from every address the
    # service serves, which share nothing past the root.
    with patched_settings({**get_api_docs_settings(), "SCHEMA_PATH_PREFIX": "/"}):
        service_wide = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)
    return {"published": published, "service-wide": service_wide}


#: The unversioned twin of the version 1 email choice address, as published.
OPT_IN_TWIN = "/user_api/v1/preferences/email_opt_in/"


def _opt_in_twin_operation():
    """
    Return the operation the service-wide document publishes at the email choice's unversioned twin.

    The twin is mounted outside the user mounts, so it is generated from its
    own route, under the settings that document is published with.
    """
    routes = import_module("openedx.core.djangoapps.user_api.legacy_urls").urlpatterns
    twin = [route for route in routes if getattr(route, "name", None) == "preferences_email_opt_in_legacy"]
    with patched_settings({**get_api_docs_settings(), "SCHEMA_PATH_PREFIX": "/"}):
        schema = SchemaGenerator(patterns=twin).get_schema(request=None, public=True)
    return schema["paths"][OPT_IN_TWIN]["post"]


class SupersededBodyTest(TestCase):
    """Every address published as superseded carries the bodies it exchanges."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = _user_documents()["published"]

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


#: A request each superseded address answers with success that the body the
#: replacing address reads would refuse: more addresses than a search there
#: takes, a longer name than a change there takes, and a number where a change
#: there takes only text.
ACCEPTED_ONLY_BY_THE_SUPERSEDED_ADDRESS = {
    ("/api/user/v1/accounts/search_emails", "post"): {
        "emails": [f"crowd-{index:03d}@example.com" for index in range(101)],
    },
    ("/api/user/v1/accounts/name_change/", "post"): {"name": "N" * 256},
    ("/api/user/v1/accounts/{username}", "patch"): {"bio": 5},
}

#: What a published body may say only where the handler reading it holds a
#: request to it.
BOUNDING_KEYWORDS = {"$ref", "format", "maxItems", "maxLength", "minItems", "minLength", "pattern"}


def _keywords(node):
    """Return every key used anywhere inside a JSON schema."""
    if isinstance(node, dict):
        return set(node).union(*(_keywords(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(_keywords(value) for value in node))
    return set()


class SupersededRequestBodyTest(TestCase):
    """
    A superseded address publishes no bound or type its handler does not hold a request to.

    Its replacement reads the same members with bounds of its own. A client
    generated from either document enforces what the body it reads publishes,
    so a bound borrowed from the replacement would have that client refuse,
    on its own side, a request the superseded handler answers with success.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.documents = _user_documents()

    def setUp(self):
        super().setUp()
        self.owner = UserFactory.create(username="bodyowner", password=TEST_PASSWORD)
        self.staff = UserFactory.create(username="bodystaff", password=TEST_PASSWORD, is_staff=True)

    def _published_bodies(self, address, method):
        """Yield each document's name, a media type and the body published for it at one operation."""
        for name, document in self.documents.items():
            for media_type, content in document["paths"][address][method]["requestBody"]["content"].items():
                yield name, media_type, content["schema"]

    def test_each_body_publishes_the_members_it_reads_and_nothing_that_bounds_them(self):
        published = {}
        for address, method in ACCEPTED_ONLY_BY_THE_SUPERSEDED_ADDRESS:
            for name, media_type, body in self._published_bodies(address, method):
                published[(name, address, media_type)] = body
        for key, body in published.items():
            assert _keywords(body) & BOUNDING_KEYWORDS == set(), key
        assert published[("published", "/api/user/v1/accounts/search_emails", "application/json")] == {
            "type": "object",
            "properties": {"emails": {"type": "array", "items": {}}},
            "required": ["emails"],
        }
        assert published[("service-wide", "/api/user/v1/accounts/name_change/", "multipart/form-data")] == {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        change = published[("published", "/api/user/v1/accounts/{username}", "application/merge-patch+json")]
        assert {"bio", "email", "name", "social_links", "year_of_birth"} <= set(change["properties"])
        assert set(map(json.dumps, change["properties"].values())) == {"{}"}

    def test_each_request_the_handler_accepts_is_one_its_published_body_allows(self):
        for (address, method), request in ACCEPTED_ONLY_BY_THE_SUPERSEDED_ADDRESS.items():
            for name, media_type, body in self._published_bodies(address, method):
                with self.subTest(document=name, address=address, media_type=media_type):
                    jsonschema.Draft202012Validator(body).validate(request)

    def test_each_handler_answers_those_requests_with_success(self):
        owner, staff = Client(), Client()
        assert owner.login(username=self.owner.username, password=TEST_PASSWORD)
        assert staff.login(username=self.staff.username, password=TEST_PASSWORD)
        requests = ACCEPTED_ONLY_BY_THE_SUPERSEDED_ADDRESS
        answered = {
            "search": staff.post(
                "/api/user/v1/accounts/search_emails",
                requests[("/api/user/v1/accounts/search_emails", "post")],
                content_type="application/json",
            ).status_code,
            "name change": owner.post(
                "/api/user/v1/accounts/name_change/",
                requests[("/api/user/v1/accounts/name_change/", "post")],
                content_type="application/json",
            ).status_code,
            "account change": owner.patch(
                f"/api/user/v1/accounts/{self.owner.username}",
                requests[("/api/user/v1/accounts/{username}", "patch")],
                content_type="application/merge-patch+json",
            ).status_code,
        }
        assert answered == {"search": 200, "name change": 201, "account change": 200}

    def test_the_replacing_addresses_publish_the_bounds_they_hold_a_request_to(self):
        for name, document in self.documents.items():
            components = document["components"]["schemas"]
            with self.subTest(document=name):
                assert components["EmailSearch"]["properties"]["emails"]["maxItems"] == 100
                assert components["NameChange"]["properties"]["name"]["maxLength"] == 255
                assert components["PatchedAccountUpdate"]["properties"]["bio"]["type"] == "string"
                paths = document["paths"]
                assert paths["/api/user/v2/email_searches/"]["post"]["requestBody"]["content"][
                    "application/json"
                ]["schema"] == {"$ref": "#/components/schemas/EmailSearch"}
                assert paths["/api/user/v2/name_changes/"]["post"]["requestBody"]["content"][
                    "application/json"
                ]["schema"] == {"$ref": "#/components/schemas/NameChange"}
                assert paths["/api/user/v2/accounts/{username}/"]["patch"]["requestBody"]["content"][
                    "application/merge-patch+json"
                ]["schema"] == {"$ref": "#/components/schemas/PatchedAccountUpdate"}

    def test_a_body_that_names_a_member_it_must_carry_is_published_as_required(self):
        required = {
            (name, address): document["paths"][address][method]["requestBody"].get("required", False)
            for name, document in self.documents.items()
            for address, method in ACCEPTED_ONLY_BY_THE_SUPERSEDED_ADDRESS
        }
        assert required == {
            (name, address): address != "/api/user/v1/accounts/{username}"
            for name in self.documents
            for address, _method in ACCEPTED_ONLY_BY_THE_SUPERSEDED_ADDRESS
        }


#: A value each superseded preference write stores as its text that the body
#: the replacing address reads would refuse, because that body takes only text.
PREFERENCE_VALUES_ACCEPTED_ONLY_BY_THE_SUPERSEDED_ADDRESS = {
    ("/api/user/v1/preferences/{username}", "patch"): {"num_pref": 5},
    ("/api/user/v1/preferences/{username}/{preference_key}", "put"): 7,
}


class SupersededPreferenceRequestBodyTest(TestCase):
    """
    A superseded preference write publishes values of any type, as its handler stores them.

    Both handlers store the text of whatever value they are sent, where the
    replacing addresses take text alone. A client generated from a body that
    borrowed that type would refuse, on its own side, a value the superseded
    handler stores.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.documents = _user_documents()

    def _published_bodies(self, address, method):
        """Yield each document's name, a media type and the body published for it at one operation."""
        for name, document in self.documents.items():
            for media_type, content in document["paths"][address][method]["requestBody"]["content"].items():
                yield name, media_type, content["schema"]

    def test_each_body_publishes_a_value_of_any_type(self):
        published = {
            (name, address, media_type): {key: value for key, value in body.items() if key != "description"}
            for address, method in PREFERENCE_VALUES_ACCEPTED_ONLY_BY_THE_SUPERSEDED_ADDRESS
            for name, media_type, body in self._published_bodies(address, method)
        }
        assert published == {
            **{
                (name, "/api/user/v1/preferences/{username}", "application/merge-patch+json"): {
                    "type": "object", "minProperties": 1, "additionalProperties": {},
                }
                for name in ("published", "service-wide")
            },
            **{
                (name, "/api/user/v1/preferences/{username}/{preference_key}", "application/json"): {}
                for name in ("published", "service-wide")
            },
        }

    def test_each_value_the_handler_stores_is_one_its_published_body_allows(self):
        for (address, method), request in PREFERENCE_VALUES_ACCEPTED_ONLY_BY_THE_SUPERSEDED_ADDRESS.items():
            for name, media_type, body in self._published_bodies(address, method):
                with self.subTest(document=name, address=address, media_type=media_type):
                    jsonschema.Draft202012Validator(body).validate(request)

    def test_each_handler_stores_those_values_as_their_text(self):
        owner = UserFactory.create(username="prefbodyowner", password=TEST_PASSWORD)
        client = Client()
        assert client.login(username=owner.username, password=TEST_PASSWORD)
        values = PREFERENCE_VALUES_ACCEPTED_ONLY_BY_THE_SUPERSEDED_ADDRESS
        answered = {
            "change": client.patch(
                f"/api/user/v1/preferences/{owner.username}",
                values[("/api/user/v1/preferences/{username}", "patch")],
                content_type="application/merge-patch+json",
            ).status_code,
            "write": client.put(
                f"/api/user/v1/preferences/{owner.username}/other_pref",
                json.dumps(values[("/api/user/v1/preferences/{username}/{preference_key}", "put")]),
                content_type="application/json",
            ).status_code,
        }
        assert answered == {"change": 204, "write": 204}
        assert dict(UserPreference.objects.filter(user=owner).values_list("key", "value")) == {
            "num_pref": "5", "other_pref": "7",
        }

    def test_the_replacing_addresses_publish_values_as_text(self):
        for name, document in self.documents.items():
            paths = document["paths"]
            with self.subTest(document=name):
                change = paths["/api/user/v2/accounts/{username}/preferences/"]["patch"]["requestBody"]
                assert change["content"]["application/merge-patch+json"]["schema"]["additionalProperties"] == {
                    "type": "string", "nullable": True,
                }
                value = document["components"]["schemas"]["AccountPreferenceValue"]["properties"]["value"]
                assert value["type"] == "string"

    def test_a_change_is_published_as_needing_a_body_and_a_write_as_not(self):
        required = {
            (name, address): document["paths"][address][method]["requestBody"].get("required", False)
            for name, document in self.documents.items()
            for address, method in PREFERENCE_VALUES_ACCEPTED_ONLY_BY_THE_SUPERSEDED_ADDRESS
        }
        assert required == {
            (name, address): address == "/api/user/v1/preferences/{username}"
            for name in ("published", "service-wide")
            for address, _method in PREFERENCE_VALUES_ACCEPTED_ONLY_BY_THE_SUPERSEDED_ADDRESS
        }


#: The preference operations this service serves replacements for. Studio
#: mounts the same addresses and serves no replacement, so only this service
#: marks them.
SUPERSEDED_PREFERENCE_OPERATIONS = (
    ("/api/user/v1/preferences/{username}", "get"),
    ("/api/user/v1/preferences/{username}", "patch"),
    ("/api/user/v1/preferences/{username}/{preference_key}", "delete"),
    ("/api/user/v1/preferences/{username}/{preference_key}", "get"),
    ("/api/user/v1/preferences/{username}/{preference_key}", "put"),
    ("/api/user/v1/preferences/email_opt_in/", "post"),
)


class SupersededPreferenceMarkTest(TestCase):
    """Both documents of this service mark each preference operation it serves a replacement for."""

    def test_every_preference_operation_is_marked_superseded_in_both_documents(self):
        marked = {
            (name, address, method): document["paths"][address][method].get("deprecated")
            for name, document in _user_documents().items()
            for address, method in SUPERSEDED_PREFERENCE_OPERATIONS
        }
        assert marked == {
            (name, address, method): True
            for name in ("published", "service-wide")
            for address, method in SUPERSEDED_PREFERENCE_OPERATIONS
        }


#: The exchanges of the preference and opt-in addresses this change describes.
PREFERENCE_EXCHANGES = {
    operation: exchange for operation, exchange in SUPERSEDED_EXCHANGES.items()
    if operation[0].startswith("/api/user/v1/preferences/")
}


class SupersededBodyInTheServiceWideDocumentTest(TestCase):
    """
    The same bodies describe these addresses in the document that covers the whole service.

    That document publishes every endpoint rather than this area alone, and it
    already carried these addresses before they were described, so the table
    changes what they publish there instead of adding them. Two of them
    answered with no body all along and are published with the code they
    really answer with.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = _user_documents()["service-wide"]

    def test_each_superseded_operation_publishes_the_same_exchange_there(self):
        published = {}
        for (address, method), _expected in PREFERENCE_EXCHANGES.items():
            operation = self.schema["paths"][address][method]
            request_media_types = sorted((operation.get("requestBody") or {}).get("content", {}))
            published[(address, method)] = (
                request_media_types,
                {code: _named_body(response) for code, response in operation["responses"].items()},
            )
        assert published == PREFERENCE_EXCHANGES

    def test_the_twin_of_the_opt_in_is_left_as_that_document_always_published_it(self):
        """It keeps resolving, is described by nothing here, and is not marked superseded."""
        assert resolve(OPT_IN_TWIN).url_name == "preferences_email_opt_in_legacy"
        twin = _opt_in_twin_operation()
        assert "requestBody" not in twin
        assert sorted(twin["responses"]) == ["200"]
        assert "content" not in twin["responses"]["200"]
        assert twin.get("deprecated", False) is False


#: Each address this document already published before its body was described,
#: and the success code its handler really answers. That document carried a 200
#: with no body for all four, which no handler answers; the code each is
#: published under now is driven below.
CORRECTED_SUCCESS_CODES = (
    ("post", "/api/user/v1/accounts/name_change/{username}/confirm/", "204"),
    ("post", "/api/user/v1/accounts/{username}/image", "204"),
    ("patch", "/api/user/v1/preferences/{username}", "204"),
    ("put", "/api/user/v1/preferences/{username}/{preference_key}", "204"),
)


class CorrectedSuccessCodeTest(TestCase):
    """
    Four addresses both documents already published answer a success code neither carried.

    These are not new addresses: a client generated before this change branches
    on the code the document gave them, so each is pinned here in both
    documents and each handler is driven, so that the published code can only
    change again together with the handler that answers it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.documents = _user_documents()

    def setUp(self):
        super().setUp()
        self.owner = UserFactory.create(username="codeowner", password=TEST_PASSWORD)
        self.owner.profile.year_of_birth = 1980
        self.owner.profile.save()
        self.staff = UserFactory.create(username="codestaff", password=TEST_PASSWORD, is_staff=True)
        self.storage = get_profile_image_storage()

    def tearDown(self):
        for name in get_profile_image_names(self.owner.username).values():
            self.storage.delete(name)
        super().tearDown()

    def _as(self, user):
        """A client signed in as that account."""
        client = Client()
        assert client.login(username=user.username, password=TEST_PASSWORD)
        return client

    def _answered(self):
        """Drive each of the four on its success path and return the code it answers."""
        owner, staff = self._as(self.owner), self._as(self.staff)
        do_name_change_request(self.owner, "Another Name", "a reason")
        answered = {
            ("post", "/api/user/v1/accounts/name_change/{username}/confirm/"): staff.post(
                f"/api/user/v1/accounts/name_change/{self.owner.username}/confirm/",
            ).status_code,
            ("patch", "/api/user/v1/preferences/{username}"): owner.patch(
                f"/api/user/v1/preferences/{self.owner.username}",
                data='{"pref-lang": "en"}',
                content_type=MERGE_PATCH,
            ).status_code,
            ("put", "/api/user/v1/preferences/{username}/{preference_key}"): owner.put(
                f"/api/user/v1/preferences/{self.owner.username}/account_privacy",
                data='"private"',
                content_type="application/json",
            ).status_code,
        }
        with make_image_file() as image_file:
            answered[("post", "/api/user/v1/accounts/{username}/image")] = owner.post(
                f"/api/user/v1/accounts/{self.owner.username}/image", {"file": image_file},
            ).status_code
        return answered

    def test_each_handler_answers_the_code_it_is_published_under(self):
        assert self._answered() == {
            (method, address): int(code) for method, address, code in CORRECTED_SUCCESS_CODES
        }

    def test_every_document_publishes_that_code_and_nothing_beside_it(self):
        published = {}
        for name, document in self.documents.items():
            for method, address, _code in CORRECTED_SUCCESS_CODES:
                responses = document["paths"][address][method]["responses"]
                published[(name, method, address)] = (
                    sorted(responses),
                    ["content" in response for response in responses.values()],
                )
        assert published == {
            (name, method, address): ([code], [False])
            for name in self.documents
            for method, address, code in CORRECTED_SUCCESS_CODES
        }


MERGE_PATCH = "application/merge-patch+json"

#: Each address that describes its body in place, the form it reads it in, and
#: what the handler answers a request carrying no body at all.
BODIES_SENT_AS_NOTHING = (
    ("PATCH", "/api/user/v1/preferences/{}", "/api/user/v1/preferences/bodyowner", MERGE_PATCH, 400),
    ("POST", "/api/user/v1/preferences/email_opt_in/", "/api/user/v1/preferences/email_opt_in/",
     "application/x-www-form-urlencoded", 400),
    ("PATCH", "/api/user/v2/accounts/{}/preferences/", "/api/user/v2/accounts/bodyowner/preferences/",
     MERGE_PATCH, 400),
    ("PUT", "/api/user/v1/preferences/{}/{}", "/api/user/v1/preferences/bodyowner/account_privacy",
     "application/json", 204),
)


class RequiredRequestBodyTest(TestCase):
    """
    A body no request may leave out is published as one the operation needs.

    Every address below describes its body in place rather than through a
    serializer, which leaves the generator no field to read that from, so it
    publishes no mark at all and a client makes the body optional. Each is
    driven here with no body, in both documents, so the mark one carries is
    held to what the handler answers.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.documents = _user_documents()

    def setUp(self):
        super().setUp()
        self.owner = UserFactory.create(username="bodyowner", password=TEST_PASSWORD)
        assert self.client.login(username=self.owner.username, password=TEST_PASSWORD)

    @staticmethod
    def _published_address(shape):
        """The address as a document spells it, from the shape its parameters are removed from."""
        return {
            "/api/user/v1/preferences/{}": "/api/user/v1/preferences/{username}",
            "/api/user/v1/preferences/{}/{}": "/api/user/v1/preferences/{username}/{preference_key}",
            "/api/user/v2/accounts/{}/preferences/": "/api/user/v2/accounts/{username}/preferences/",
        }.get(shape, shape)

    def _send_nothing(self, method, address, media_type):
        """Send the request with its form named and no body behind it."""
        return self.client.generic(
            method,
            address,
            data=b"",
            content_type=media_type,
            CONTENT_TYPE=media_type,
            CONTENT_LENGTH="0",
            **{"wsgi.input": FakePayload(b"")},
        )

    def test_a_handler_that_refuses_a_request_carrying_no_body_is_the_reason_for_the_mark(self):
        """What each handler answers, which is what the mark below is held to."""
        answered = {
            (method, address): self._send_nothing(method, address, media_type).status_code
            for method, _shape, address, media_type, _expected in BODIES_SENT_AS_NOTHING
        }
        assert answered == {
            (method, address): expected
            for method, _shape, address, media_type, expected in BODIES_SENT_AS_NOTHING
        }

    def test_every_document_marks_a_body_the_handler_refuses_to_work_without(self):
        marks = {}
        for name, document in self.documents.items():
            for method, shape, _address, _media_type, _answer in BODIES_SENT_AS_NOTHING:
                address = self._published_address(shape)
                operation = document["paths"][address][method.lower()]
                marks[(name, method, address)] = operation["requestBody"].get("required", "<absent>")
        assert marks == {
            (name, method, self._published_address(shape)): (True if answer == 400 else "<absent>")
            for name in self.documents
            for method, shape, _address, _media_type, answer in BODIES_SENT_AS_NOTHING
        }


class ServiceWideDocumentSettingsTest(TestCase):
    """
    The document covering the whole service keeps the settings it is generated under.

    Its title, its version and its description are given to the generator for
    the length of the generation rather than configured for the deployment,
    and a part of the document is built under a setting held for that part
    alone. What the document says about itself is read here beside that part,
    because a hold let go of too widely takes the rest with it and the
    document would describe itself as something else.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.given = get_api_docs_settings()
        cls.schema = _user_documents()["service-wide"]

    def test_the_document_says_of_itself_what_it_was_generated_with(self):
        expected = {
            "title": self.given["TITLE"],
            "version": self.given["VERSION"],
            "description": self.given["DESCRIPTION"],
        }
        if self.given.get("CONTACT"):
            expected["contact"] = self.given["CONTACT"]
        assert self.schema["info"] == expected

    def test_the_document_names_no_server_to_send_callers_to(self):
        """It is generated with none named, and a document naming one points at that host."""
        assert "servers" not in self.schema

    def test_the_body_read_whole_is_published_whole_in_that_document_too(self):
        """The body is read whole under the settings of every document, not only the service's own."""
        body = self.schema["paths"]["/api/user/v2/retirements/{username}/"]["patch"]["requestBody"]
        assert body["required"] is True
        assert body["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/RetirementUpdate",
        }
        assert self.schema["components"]["schemas"]["RetirementUpdate"]["required"] == [
            "new_state", "response",
        ]
        assert "PatchedRetirementUpdate" not in self.schema["components"]["schemas"]




class PublishedDocumentTest(TestCase):
    """What the service publishes for the user addresses: distinct ids, and one area per tag group."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = _user_documents()["published"]

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

    def test_no_operation_is_told_apart_by_a_numeral_suffix(self):
        """A numeral suffix is assigned in route order, so it is not a stable name."""
        suffixed = {
            route: operation["operationId"]
            for route, operation in self.operations()
            if re.fullmatch(r".+_\d+", operation["operationId"])
        }
        assert suffixed == {}

    def test_a_read_of_one_preference_is_named_for_the_one_preference_it_reads(self):
        """
        The read of a whole preference document and the read of one of its names are named apart.

        Both addresses derive one name, which the assembled document would
        tell apart with a numeral, so the name a client imports for the read
        of one preference would say which of the two addresses sorts first
        rather than what it answers with. Each version names that read for
        what it reads, and the read of the whole document keeps the name both
        addresses derive.
        """
        ids = {
            (route, method): operation["operationId"]
            for route, path_item in self.schema["paths"].items()
            for method, operation in path_item.items()
            if isinstance(operation, dict) and "operationId" in operation
        }
        assert ids[("/api/user/v1/preferences/{username}", "get")] == "v1_preferences_retrieve"
        assert ids[("/api/user/v1/preferences/{username}/{preference_key}", "get")] == (
            "v1_preferences_retrieve_one"
        )
        assert ids[("/api/user/v2/accounts/{username}/preferences/", "get")] == (
            "v2_accounts_preferences_retrieve"
        )
        assert ids[("/api/user/v2/accounts/{username}/preferences/{key}/", "get")] == (
            "v2_accounts_preferences_retrieve_one"
        )

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
            "openedx-platform-sdk": ["user"],
            "user": ["user"],
        }
