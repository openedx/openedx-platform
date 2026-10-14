"""Tests for the version 2 User API URL surface."""

import re

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.urls import get_resolver, resolve, reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, APITestCase

from common.djangoapps.student.tests.factories import AdminFactory
from openedx.core.djangoapps.user_api.v2 import urls as v2_urls
from openedx.core.djangoapps.user_api.v2.views import UserPreferenceViewSet, UserViewSet
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"

V2_VIEW_MODULE = "openedx.core.djangoapps.user_api.v2.views"
V1_VIEW_MODULE = "openedx.core.djangoapps.user_api.views"
V2_NAMESPACE = "user_v2"

#: A route name is lower case, starts with a letter and separates words with
#: underscores; nothing else is accepted.
SNAKE_CASE_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
VERSION_IN_NAME = re.compile(r"v\d")

EXPECTED_ADDRESSES = {
    "user_v2:user_list": ({}, "/api/user/v2/users/"),
    "user_v2:user_detail": ({"username": "alice"}, "/api/user/v2/users/alice/"),
    "user_v2:user_preference_list": ({}, "/api/user/v2/user_preferences/"),
    "user_v2:user_preference_detail": (
        {"username": "alice", "key": "time_zone"},
        "/api/user/v2/user_preferences/alice,time_zone/",
    ),
    "user_v2:time_zone_list": ({}, "/api/user/v2/time_zones/"),
}


def registered_route_names():
    """
    Return the route names the mounted URLconf registers under this namespace.

    These are the names ``reverse()`` accepts; they are read from the resolver
    rather than from this module so that a route added or renamed in the
    URLconf is seen here.
    """
    _prefix, namespace_resolver = get_resolver().namespace_dict[V2_NAMESPACE]
    return {key for key in namespace_resolver.reverse_dict if isinstance(key, str)}


def resolved_route_names():
    """Return the route names ``resolve()`` reports for the published addresses."""
    return {resolve(address).url_name for _kwargs, address in EXPECTED_ADDRESSES.values()}


def routed_actions(view_func):
    """
    Return the method-to-action map a viewset mount was registered with.

    DRF adds a ``head`` entry to a mount's ``actions`` the first time it serves
    a GET, so the raw attribute depends on what the test suite requested first.
    """
    return {method: action for method, action in view_func.actions.items() if method != "head"}


def view_identity(view_func):
    """
    Return the module and class name of the view a route is mounted on.

    A viewset mount carries the class name on the wrapper itself; Django's
    plain class-based views record the class separately instead.
    """
    view_class = getattr(view_func, "view_class", None)
    name = view_class.__name__ if view_class is not None else view_func.__name__
    return (view_func.__module__, name)


@skip_unless_lms
class UrlLiteralTests(APITestCase):
    """Every name reverses to the address the API publishes."""

    def test_reverse_literals(self):
        for name, (kwargs, expected) in EXPECTED_ADDRESSES.items():
            assert reverse(name, kwargs=kwargs) == expected, name

    def test_names_are_snake_case_and_carry_no_version(self):
        registered = registered_route_names()
        assert registered == {name.split(":")[1] for name in EXPECTED_ADDRESSES}
        for name in registered:
            assert SNAKE_CASE_NAME.match(name), name
            assert not VERSION_IN_NAME.search(name), name

    def test_resolved_names_are_the_names_that_reverse(self):
        resolved = resolved_route_names()
        assert resolved == registered_route_names()
        for address, name in (
            ("/api/user/v2/users/", "user_list"),
            ("/api/user/v2/users/alice/", "user_detail"),
            ("/api/user/v2/user_preferences/", "user_preference_list"),
            ("/api/user/v2/user_preferences/alice,time_zone/", "user_preference_detail"),
            ("/api/user/v2/time_zones/", "time_zone_list"),
        ):
            match = resolve(address)
            assert match.url_name == name, address
            assert match.view_name == f"{V2_NAMESPACE}:{name}", address

    def test_viewsets_build_action_addresses_under_this_mount(self):
        assert UserViewSet(basename="user").reverse_action("list") == "/api/user/v2/users/"
        assert UserViewSet(basename="user").reverse_action(
            "detail", kwargs={"username": "alice"},
        ) == "/api/user/v2/users/alice/"
        preferences = UserPreferenceViewSet(basename="user_preference")
        assert preferences.reverse_action("list") == "/api/user/v2/user_preferences/"
        assert preferences.reverse_action(
            "detail", kwargs={"username": "alice", "key": "time_zone"},
        ) == "/api/user/v2/user_preferences/alice,time_zone/"

    def test_action_addresses_follow_the_namespace_the_request_arrived_on(self):
        request = Request(APIRequestFactory().get("/api/user/v2/users/"))
        request.resolver_match = resolve("/api/user/v2/users/")
        view = UserViewSet(basename="user", request=request)
        assert view.reverse_action("list") == "http://testserver/api/user/v2/users/"

    def test_addresses_resolve_to_the_version_two_viewsets(self):
        expected = {
            "/api/user/v2/users/": ("UserViewSet", {"get": "list"}),
            "/api/user/v2/users/alice/": ("UserViewSet", {"get": "retrieve"}),
            "/api/user/v2/user_preferences/": ("UserPreferenceViewSet", {"get": "list"}),
            "/api/user/v2/user_preferences/alice,time_zone/": (
                "UserPreferenceViewSet", {"get": "retrieve"},
            ),
            "/api/user/v2/time_zones/": ("CountryTimeZoneViewSet", {"get": "list"}),
        }
        for address, (class_name, actions) in expected.items():
            match = resolve(address)
            assert view_identity(match.func) == (V2_VIEW_MODULE, class_name), address
            assert routed_actions(match.func) == actions, address

    def test_username_with_a_dot_resolves_to_the_member_route(self):
        match = resolve("/api/user/v2/users/ada.lovelace/")
        assert view_identity(match.func) == (V2_VIEW_MODULE, "UserViewSet")
        assert match.kwargs == {"username": "ada.lovelace"}

    def test_preference_member_address_splits_into_username_and_key(self):
        match = resolve("/api/user/v2/user_preferences/ada.lovelace,pref-lang/")
        assert match.kwargs == {"username": "ada.lovelace", "key": "pref-lang"}

    def test_a_key_containing_a_comma_belongs_to_the_key(self):
        """A preference name is free-form text, and the username half cannot contain a comma."""
        match = resolve("/api/user/v2/user_preferences/ada.lovelace,a,b/")
        assert match.kwargs == {"username": "ada.lovelace", "key": "a,b"}
        assert reverse(
            "user_v2:user_preference_detail", kwargs={"username": "ada.lovelace", "key": "a,b"},
        ) == "/api/user/v2/user_preferences/ada.lovelace,a,b/"

    def test_a_username_outside_the_allowed_pattern_does_not_reach_the_viewset(self):
        match = resolve("/api/user/v2/user_preferences/al!ce,pref-lang/")
        assert view_identity(match.func) == (V2_VIEW_MODULE, "UnknownAddressView")

    def test_no_database_id_member_address_is_served(self):
        match = resolve("/api/user/v2/user_preferences/42/")
        assert view_identity(match.func) == (V2_VIEW_MODULE, "UnknownAddressView")


@skip_unless_lms
class RouteShapeTests(APITestCase):
    """
    Every address this version serves is named and ends in a slash.

    The mount also carries one pattern that is neither: the fallback matched
    after all of them, which answers an address no route serves. It addresses
    no resource and reverses to nothing, which is why it has no name, and
    pinning it here keeps a second wildcard from being added quietly beside
    it.
    """

    def test_every_address_is_named_and_ends_in_a_slash(self):
        addresses = [pattern for pattern in v2_urls.urlpatterns if pattern.name]
        assert sorted(pattern.name for pattern in addresses) == [
            "time_zone_list",
            "user_detail",
            "user_list",
            "user_preference_detail",
            "user_preference_list",
        ]
        for pattern in addresses:
            assert SNAKE_CASE_NAME.match(pattern.name), pattern.name
            assert str(pattern.pattern).endswith("/$"), pattern.name

    def test_the_router_route_that_is_replaced_has_to_exist(self):
        """Dropping a route the router does not generate is refused instead of passing silently."""
        routes = v2_urls.router.urls
        assert v2_urls.REPLACED_ROUTER_ROUTE in {route.name for route in routes}
        kept = v2_urls.without_route(routes, v2_urls.REPLACED_ROUTER_ROUTE)
        assert [route.name for route in kept] == [
            "user_list",
            "user_detail",
            "user_preference_list",
            "time_zone_list",
        ]
        with pytest.raises(ImproperlyConfigured):
            v2_urls.without_route(routes, "user_preference_detail_renamed")

    def test_the_one_unnamed_pattern_is_the_fallback_and_it_is_matched_last(self):
        unnamed = [pattern for pattern in v2_urls.urlpatterns if not pattern.name]
        assert len(unnamed) == 1
        assert unnamed[0] is v2_urls.urlpatterns[-1]
        assert view_identity(unnamed[0].callback) == (V2_VIEW_MODULE, "UnknownAddressView")


@skip_unless_lms
class LegacyAddressTests(APITestCase):
    """Mounting version 2 leaves the version 1 surface exactly where it was."""

    def test_version_one_addresses_still_reach_the_version_one_views(self):
        expected = {
            "/api/user/v1/users/": "UserViewSet",
            "/api/user/v1/users/42/": "UserViewSet",
            "/api/user/v1/user_prefs/": "UserPreferenceViewSet",
            "/api/user/v1/user_prefs/42/": "UserPreferenceViewSet",
            "/api/user/v1/preferences/pref-lang/users/": "PreferenceUsersListView",
            "/api/user/v1/forum_roles/Moderator/users/": "ForumRoleUsersListView",
            "/api/user/v1/preferences/time_zones/": "CountryTimeZoneListView",
        }
        for address, class_name in expected.items():
            match = resolve(address)
            assert view_identity(match.func) == (V1_VIEW_MODULE, class_name), address

    def test_version_one_url_names_still_reverse(self):
        assert reverse("accounts_api", kwargs={"username": "alice"}) == "/api/user/v1/accounts/alice"
        assert reverse("own_username_api") == "/api/user/v1/me"
        assert reverse("preferences_api", kwargs={"username": "alice"}) == "/api/user/v1/preferences/alice"

    def test_unversioned_twins_still_resolve(self):
        match = resolve("/user_api/v1/users/")
        assert view_identity(match.func) == (V1_VIEW_MODULE, "UserViewSet")


@skip_unless_lms
class MalformedAddressTests(APITestCase):
    """An address that matches no route is refused in the body shape this API answers with."""

    def setUp(self):
        super().setUp()
        self.admin = AdminFactory.create(username="url-admin", password=PASSWORD)
        assert self.client.login(username=self.admin.username, password=PASSWORD)

    def assert_refused_in_json(self, response):
        """Assert the refusal is the standard error body and not a page meant for a browser."""
        assert response["Content-Type"].startswith("application/json"), response["Content-Type"]
        assert "<html" not in response.content.decode().lower()
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found",
        )
        assert envelope["title"] == "Not Found"
        return envelope

    def test_preference_member_address_without_a_key_is_not_found(self):
        self.assert_refused_in_json(self.client.get("/api/user/v2/user_preferences/42/"))

    def test_unknown_collection_is_not_found(self):
        self.assert_refused_in_json(self.client.get("/api/user/v2/not_a_collection/"))

    def test_a_caller_that_asked_for_json_is_answered_in_json(self):
        response = self.client.get(
            "/api/user/v2/user_preferences/garbage/",
            HTTP_ACCEPT="application/json",
            content_type="application/json",
        )
        envelope = self.assert_refused_in_json(response)
        assert envelope["instance"] == "/api/user/v2/user_preferences/garbage/"

    def test_a_username_outside_the_allowed_pattern_is_not_found(self):
        self.assert_refused_in_json(self.client.get("/api/user/v2/user_preferences/al!ce,pref-lang/"))

    def test_an_anonymous_caller_is_refused_the_same_way(self):
        self.client.logout()
        self.assert_refused_in_json(self.client.get("/api/user/v2/not_a_collection/"))

    def test_a_write_to_an_unknown_address_is_not_found_rather_than_not_allowed(self):
        self.assert_refused_in_json(
            self.client.post("/api/user/v2/not_a_collection/", {}, format="json"),
        )

    def test_the_version_this_mount_shares_its_prefix_with_is_left_alone(self):
        """The authentication app's addresses under this prefix keep reaching it."""
        for address in (
            "/api/user/v2/account/registration/",
            "/api/user/v2/account/login_session/",
        ):
            match = resolve(address)
            assert match.func.view_class.__module__.startswith(
                "openedx.core.djangoapps.user_authn",
            ), address
