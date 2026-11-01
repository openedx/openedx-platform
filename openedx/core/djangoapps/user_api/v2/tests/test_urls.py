"""Tests for the version 2 User API URL surface."""

import re

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.urls import NoReverseMatch, Resolver404, get_resolver, resolve, reverse
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from rest_framework import status
from rest_framework.request import Request
from rest_framework.settings import api_settings
from rest_framework.test import APIRequestFactory, APITestCase
from rest_framework.views import APIView

from openedx.core.djangoapps.profile_images.v2.views import ProfileImageView
from openedx.core.djangoapps.user_api.v2 import urls as v2_urls
from openedx.core.djangoapps.user_api.v2.views import (
    AccountDeactivationView,
    AccountPreferenceViewSet,
    AccountViewSet,
    EmailOptInViewSet,
    IDVerificationStatusView,
    IDVerificationViewSet,
    NameChangeViewSet,
    RetirementCleanupViewSet,
    RetirementPartnerReportCleanupViewSet,
    RetirementPartnerReportViewSet,
    RetirementViewSet,
    UserPreferenceViewSet,
    UserViewSet,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms

V2_VIEW_MODULE = "openedx.core.djangoapps.user_api.v2.views"
PROFILE_IMAGE_VIEW_MODULE = "openedx.core.djangoapps.profile_images.v2.views"
V1_VIEW_MODULE = "openedx.core.djangoapps.user_api.views"
V1_ACCOUNT_VIEW_MODULE = "openedx.core.djangoapps.user_api.accounts.views"
V1_PREFERENCE_VIEW_MODULE = "openedx.core.djangoapps.user_api.preferences.views"
V2_NAMESPACE = "user_v2"

#: A route name is lower case, starts with a letter and separates words with
#: underscores; nothing else is accepted.
SNAKE_CASE_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
VERSION_IN_NAME = re.compile(r"v\d")

EXPECTED_ADDRESSES = {
    "user_v2:account_list": ({}, "/api/user/v2/accounts/"),
    "user_v2:account_detail": ({"username": "alice"}, "/api/user/v2/accounts/alice/"),
    "user_v2:email_search_list": ({}, "/api/user/v2/email_searches/"),
    "user_v2:account_deactivation": (
        {"username": "alice"},
        "/api/user/v2/accounts/alice/deactivation/",
    ),
    "user_v2:account_preference_list": (
        {"username": "alice"},
        "/api/user/v2/accounts/alice/preferences/",
    ),
    "user_v2:account_preference_detail": (
        {"username": "alice", "key": "time_zone"},
        "/api/user/v2/accounts/alice/preferences/time_zone/",
    ),
    "user_v2:email_opt_in_list": ({}, "/api/user/v2/email_opt_ins/"),
    "user_v2:account_profile_image": (
        {"username": "alice"},
        "/api/user/v2/accounts/alice/profile_image/",
    ),
    "user_v2:account_verification_status": (
        {"username": "alice"},
        "/api/user/v2/accounts/alice/verification_status/",
    ),
    "user_v2:account_verification_list": (
        {"username": "alice"},
        "/api/user/v2/accounts/alice/verifications/",
    ),
    "user_v2:name_change_list": ({}, "/api/user/v2/name_changes/"),
    "user_v2:name_change_confirmation": (
        {"username": "alice"},
        "/api/user/v2/name_changes/alice/confirmations/",
    ),
    "user_v2:user_list": ({}, "/api/user/v2/users/"),
    "user_v2:user_detail": ({"username": "alice"}, "/api/user/v2/users/alice/"),
    "user_v2:user_preference_list": ({}, "/api/user/v2/user_preferences/"),
    "user_v2:user_preference_detail": (
        {"username": "alice", "key": "time_zone"},
        "/api/user/v2/user_preferences/alice,time_zone/",
    ),
    "user_v2:time_zone_list": ({}, "/api/user/v2/time_zones/"),
    "user_v2:retirement_list": ({}, "/api/user/v2/retirements/"),
    "user_v2:retirement_detail": ({"username": "alice"}, "/api/user/v2/retirements/alice/"),
    "user_v2:retirement_erasure": (
        {"username": "alice"},
        "/api/user/v2/retirements/alice/erasures/",
    ),
    "user_v2:retirement_cleanup_list": ({}, "/api/user/v2/retirement_cleanups/"),
    "user_v2:retirement_partner_report_list": ({}, "/api/user/v2/retirement_partner_reports/"),
    "user_v2:retirement_partner_report_detail": (
        {"username": "alice"},
        "/api/user/v2/retirement_partner_reports/alice/",
    ),
    "user_v2:retirement_partner_report_cleanup_list": (
        {},
        "/api/user/v2/retirement_partner_report_cleanups/",
    ),
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


def served_methods(view_func):
    """
    Return the request methods a route mounted on a plain view serves.

    A viewset mount carries its method-to-action map; a plain view has one
    handler per method instead, and the set of those is what the route serves.
    """
    return set(view_func.view_class().allowed_methods)


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
            ("/api/user/v2/accounts/", "account_list"),
            ("/api/user/v2/accounts/alice/", "account_detail"),
            ("/api/user/v2/email_searches/", "email_search_list"),
            ("/api/user/v2/accounts/alice/deactivation/", "account_deactivation"),
            ("/api/user/v2/accounts/alice/preferences/", "account_preference_list"),
            ("/api/user/v2/accounts/alice/preferences/time_zone/", "account_preference_detail"),
            ("/api/user/v2/email_opt_ins/", "email_opt_in_list"),
            ("/api/user/v2/accounts/alice/profile_image/", "account_profile_image"),
            ("/api/user/v2/accounts/alice/verification_status/", "account_verification_status"),
            ("/api/user/v2/accounts/alice/verifications/", "account_verification_list"),
            ("/api/user/v2/name_changes/", "name_change_list"),
            ("/api/user/v2/name_changes/alice/confirmations/", "name_change_confirmation"),
            ("/api/user/v2/users/", "user_list"),
            ("/api/user/v2/users/alice/", "user_detail"),
            ("/api/user/v2/user_preferences/", "user_preference_list"),
            ("/api/user/v2/user_preferences/alice,time_zone/", "user_preference_detail"),
            ("/api/user/v2/time_zones/", "time_zone_list"),
            ("/api/user/v2/retirements/", "retirement_list"),
            ("/api/user/v2/retirements/alice/", "retirement_detail"),
            ("/api/user/v2/retirements/alice/erasures/", "retirement_erasure"),
            ("/api/user/v2/retirement_cleanups/", "retirement_cleanup_list"),
            ("/api/user/v2/retirement_partner_reports/", "retirement_partner_report_list"),
            (
                "/api/user/v2/retirement_partner_reports/alice/",
                "retirement_partner_report_detail",
            ),
            (
                "/api/user/v2/retirement_partner_report_cleanups/",
                "retirement_partner_report_cleanup_list",
            ),
        ):
            match = resolve(address)
            assert match.url_name == name, address
            assert match.view_name == f"{V2_NAMESPACE}:{name}", address

    def test_viewsets_build_action_addresses_under_this_mount(self):
        accounts = AccountViewSet(basename="account")
        assert accounts.reverse_action("list") == "/api/user/v2/accounts/"
        assert accounts.reverse_action(
            "detail", kwargs={"username": "alice"},
        ) == "/api/user/v2/accounts/alice/"
        name_changes = NameChangeViewSet(basename="name_change")
        assert name_changes.reverse_action("list") == "/api/user/v2/name_changes/"
        assert name_changes.reverse_action(
            "confirmation", kwargs={"username": "alice"},
        ) == "/api/user/v2/name_changes/alice/confirmations/"
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
            "/api/user/v2/accounts/": ("AccountViewSet", {"get": "list"}),
            "/api/user/v2/accounts/alice/": (
                "AccountViewSet", {"get": "retrieve", "patch": "partial_update"},
            ),
            "/api/user/v2/accounts/alice/verifications/": (
                "IDVerificationViewSet", {"get": "list"},
            ),
            "/api/user/v2/accounts/alice/preferences/": (
                "AccountPreferenceViewSet",
                {"get": "retrieve_all", "patch": "partial_update_all"},
            ),
            "/api/user/v2/accounts/alice/preferences/time_zone/": (
                "AccountPreferenceViewSet",
                {"get": "retrieve", "put": "update", "delete": "destroy"},
            ),
            "/api/user/v2/email_searches/": ("EmailSearchViewSet", {"post": "create"}),
            "/api/user/v2/email_opt_ins/": ("EmailOptInViewSet", {"post": "create"}),
            "/api/user/v2/name_changes/": ("NameChangeViewSet", {"post": "create"}),
            "/api/user/v2/name_changes/alice/confirmations/": (
                "NameChangeViewSet", {"post": "confirmations"},
            ),
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

    def test_a_deactivation_authenticates_the_way_the_platform_does(self):
        """
        The view serving a deactivation declares no authentication of its own.

        Disabling an account is reached with whatever credentials the platform
        accepts everywhere else, so the view carries no list of its own that a
        deployment changing that setting would have to be changed alongside.
        """
        view_class = resolve("/api/user/v2/accounts/alice/deactivation/").func.view_class
        assert view_class is AccountDeactivationView
        assert "authentication_classes" not in vars(AccountDeactivationView)
        assert AccountDeactivationView.authentication_classes is APIView.authentication_classes

    def test_the_views_reached_before_an_address_is_confirmed_replace_one_class(self):
        """
        These four are reached with the library's token class and a wider session one.

        The site's own settings pages are reached by a user who has not
        confirmed their email address, whom the session class the platform
        configures refuses, so that one class is replaced. The class reading a
        token is the one the platform's own pair is built on, named here rather
        than inherited, because a view that declares its own classes is not one
        that pair serves and is not recorded as one.
        """
        platform_token = api_settings.DEFAULT_AUTHENTICATION_CLASSES[0]
        assert issubclass(platform_token, JwtAuthentication)
        for view_class in (
            AccountViewSet, ProfileImageView, AccountPreferenceViewSet, EmailOptInViewSet,
        ):
            assert view_class.authentication_classes == (
                JwtAuthentication, SessionAuthenticationAllowInactiveUser,
            ), view_class.__name__

    def test_what_is_recorded_about_an_account_authenticates_the_way_the_platform_does(self):
        """
        The views serving what has been recorded about an account declare no authentication.

        The account itself is reached with the session class that admits a
        user who has not confirmed their address, because the site's own
        settings pages are reached that way; what has been recorded about that
        account is reached with whatever credentials the platform accepts
        everywhere else, so these views carry no list of their own that a
        deployment changing that setting would have to be changed alongside.
        """
        assert resolve(
            "/api/user/v2/accounts/alice/verifications/",
        ).func.cls is IDVerificationViewSet
        for view_class in (IDVerificationStatusView, IDVerificationViewSet):
            assert "authentication_classes" not in vars(view_class), view_class.__name__
            assert view_class.authentication_classes is APIView.authentication_classes

    def test_the_hand_routed_addresses_resolve_to_the_views_that_serve_them(self):
        """The addresses that are not router-generated carry a view and a method set too."""
        expected = {
            "/api/user/v2/accounts/alice/deactivation/": (
                V2_VIEW_MODULE, "AccountDeactivationView", {"POST", "OPTIONS"},
            ),
            "/api/user/v2/accounts/alice/profile_image/": (
                PROFILE_IMAGE_VIEW_MODULE, "ProfileImageView", {"POST", "DELETE", "OPTIONS"},
            ),
            "/api/user/v2/accounts/alice/verification_status/": (
                V2_VIEW_MODULE, "IDVerificationStatusView", {"GET", "OPTIONS"},
            ),
        }
        for address, (module, class_name, methods) in expected.items():
            match = resolve(address)
            assert view_identity(match.func) == (module, class_name), address
            assert served_methods(match.func) == methods, address
            assert match.kwargs == {"username": "alice"}, address

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
        with pytest.raises(Resolver404):
            resolve("/api/user/v2/user_preferences/al!ce,pref-lang/")

    def test_no_database_id_member_address_is_served(self):
        with pytest.raises(Resolver404):
            resolve("/api/user/v2/user_preferences/42/")

    def test_a_name_carrying_a_slash_is_not_addressed_as_a_preference(self):
        """A name is read from one segment, which the slash ends, so no member route serves it."""
        for key in ("a/b", "x/", "/x", "a//b"):
            with pytest.raises(Resolver404):
                resolve(f"/api/user/v2/accounts/ada.lovelace/preferences/{key}/")
            with pytest.raises(NoReverseMatch):
                reverse(
                    "user_v2:account_preference_detail",
                    kwargs={"username": "ada.lovelace", "key": key},
                )

    def test_every_name_the_segment_can_carry_can_be_addressed(self):
        """A preference name is free-form text, so the key half takes the whole segment."""
        for key in ("pref-lang", "visibility.bio", "visibility.", "account_privacy", "a,b"):
            address = f"/api/user/v2/accounts/ada.lovelace/preferences/{key}/"
            match = resolve(address)
            assert view_identity(match.func) == (V2_VIEW_MODULE, "AccountPreferenceViewSet"), key
            assert match.kwargs == {"username": "ada.lovelace", "key": key}, key
            assert reverse(
                "user_v2:account_preference_detail",
                kwargs={"username": "ada.lovelace", "key": key},
            ) == address, key

    def test_a_name_that_is_a_collection_of_this_version_is_read_as_a_preference_name(self):
        """The collections stand at the top of this version, so no preference name shadows one."""
        for key in ("email_opt_in", "email_opt_ins", "time_zones"):
            match = resolve(f"/api/user/v2/accounts/alice/preferences/{key}/")
            assert view_identity(match.func) == (V2_VIEW_MODULE, "AccountPreferenceViewSet"), key
            assert match.kwargs == {"username": "alice", "key": key}, key
        for address, name in (
            ("/api/user/v2/email_opt_ins/", "email_opt_in_list"),
            ("/api/user/v2/time_zones/", "time_zone_list"),
        ):
            assert resolve(address).url_name == name, address

    def test_the_preference_addresses_are_served_only_with_their_slash(self):
        """Without it no route of this version matches them."""
        for address in (
            "/api/user/v2/accounts/alice/preferences",
            "/api/user/v2/accounts/alice/preferences/time_zone",
        ):
            with pytest.raises(Resolver404):
                resolve(address)

    def test_the_name_in_a_preference_address_is_read_as_a_stored_one(self):
        """``me`` is a username here, and is routed as one rather than resolved to the caller."""
        for address, kwargs in (
            ("/api/user/v2/accounts/me/preferences/", {"username": "me"}),
            (
                "/api/user/v2/accounts/me/preferences/time_zone/",
                {"username": "me", "key": "time_zone"},
            ),
        ):
            match = resolve(address)
            assert view_identity(match.func) == (V2_VIEW_MODULE, "AccountPreferenceViewSet"), address
            assert match.kwargs == kwargs, address

    def test_the_preference_viewset_serves_both_of_its_addresses(self):
        """One class holds every action of this resource, over the two addresses it has."""
        collection = resolve("/api/user/v2/accounts/alice/preferences/")
        member = resolve("/api/user/v2/accounts/alice/preferences/time_zone/")
        assert collection.func.cls is AccountPreferenceViewSet
        assert member.func.cls is AccountPreferenceViewSet
        assert set(routed_actions(collection.func)) | set(routed_actions(member.func)) == {
            "get", "patch", "put", "delete",
        }


@skip_unless_lms
class RouteShapeTests(APITestCase):
    """Every address this version serves is named and ends in a slash."""

    def test_every_address_is_named_and_ends_in_a_slash(self):
        addresses = v2_urls.urlpatterns
        assert all(pattern.name for pattern in addresses), [str(p.pattern) for p in addresses if not p.name]
        assert sorted(pattern.name for pattern in addresses) == [
            "account_deactivation",
            "account_detail",
            "account_list",
            "account_preference_detail",
            "account_preference_list",
            "account_profile_image",
            "account_verification_list",
            "account_verification_status",
            "email_opt_in_list",
            "email_search_list",
            "name_change_confirmation",
            "name_change_list",
            "retirement_cleanup_list",
            "retirement_detail",
            "retirement_erasure",
            "retirement_list",
            "retirement_partner_report_cleanup_list",
            "retirement_partner_report_detail",
            "retirement_partner_report_list",
            "time_zone_list",
            "user_detail",
            "user_list",
            "user_preference_detail",
            "user_preference_list",
        ]
        for pattern in addresses:
            assert SNAKE_CASE_NAME.match(pattern.name), pattern.name
            assert str(pattern.pattern).endswith("/$"), pattern.name

    def test_every_pattern_is_anchored_where_the_mount_leaves_off(self):
        """
        An address is matched against what the mount leaves of the path, by searching it.

        A pattern that does not say where it starts therefore answers any
        address that merely ends in it, so every one of them says so.
        """
        for pattern in v2_urls.urlpatterns:
            assert str(pattern.pattern).startswith("^"), (pattern.name, str(pattern.pattern))

    def test_the_router_route_that_is_replaced_has_to_exist(self):
        """Dropping a route the router does not generate is refused instead of passing silently."""
        routes = v2_urls.router.urls
        assert v2_urls.REPLACED_ROUTER_ROUTE in {route.name for route in routes}
        kept = v2_urls.without_route(routes, v2_urls.REPLACED_ROUTER_ROUTE)
        assert [route.name for route in kept] == [
            "account_list",
            "account_detail",
            "account_verification_list",
            "email_search_list",
            "email_opt_in_list",
            "name_change_list",
            "name_change_confirmation",
            "user_list",
            "user_detail",
            "user_preference_list",
            "time_zone_list",
            "retirement_list",
            "retirement_detail",
            "retirement_erasure",
            "retirement_cleanup_list",
            "retirement_partner_report_list",
            "retirement_partner_report_detail",
            "retirement_partner_report_cleanup_list",
        ]
        with pytest.raises(ImproperlyConfigured):
            v2_urls.without_route(routes, "user_preference_detail_renamed")



@skip_unless_lms
class LegacyAddressTests(APITestCase):
    """Mounting version 2 leaves the version 1 surface exactly where it was."""

    def test_version_one_account_addresses_still_reach_the_version_one_views(self):
        expected = {
            "/api/user/v1/me": ("AccountViewSet", {"get": "get"}),
            "/api/user/v1/accounts": ("AccountViewSet", {"get": "list"}),
            "/api/user/v1/accounts/search_emails": ("AccountViewSet", {"post": "search_emails"}),
            "/api/user/v1/accounts/alice": (
                "AccountViewSet", {"get": "retrieve", "patch": "partial_update"},
            ),
        }
        for address, (class_name, actions) in expected.items():
            match = resolve(address)
            assert view_identity(match.func) == (V1_ACCOUNT_VIEW_MODULE, class_name), address
            assert routed_actions(match.func) == actions, address

        deactivation = resolve("/api/user/v1/accounts/alice/deactivate/")
        assert view_identity(deactivation.func) == (
            V1_ACCOUNT_VIEW_MODULE, "AccountDeactivationView",
        )

    def test_version_one_addresses_of_the_resources_this_version_adds_are_unchanged(self):
        name_changes = {
            "/api/user/v1/accounts/name_change/": {"post": "create"},
            "/api/user/v1/accounts/name_change/alice/confirm/": {"post": "confirm"},
        }
        for address, actions in name_changes.items():
            match = resolve(address)
            assert view_identity(match.func) == (V1_ACCOUNT_VIEW_MODULE, "NameChangeView"), address
            assert routed_actions(match.func) == actions, address

        image = resolve("/api/user/v1/accounts/alice/image")
        assert view_identity(image.func) == (
            "openedx.core.djangoapps.profile_images.views", "ProfileImageView",
        )
        for address, class_name in (
            ("/api/user/v1/accounts/alice/verification_status/", "IDVerificationStatusView"),
            ("/api/user/v1/accounts/alice/verifications/", "IDVerificationStatusDetailsView"),
        ):
            match = resolve(address)
            assert view_identity(match.func) == (
                "openedx.core.djangoapps.user_api.verification_api.views", class_name,
            ), address

    def test_the_profile_image_twins_in_their_own_app_are_left_where_they_are(self):
        """This version adds no second address for them, and theirs keep their names."""
        assert reverse("profile_image_upload", kwargs={"username": "alice"}) == (
            "/api/profile_images/v1/alice/upload"
        )
        assert reverse("profile_image_remove", kwargs={"username": "alice"}) == (
            "/api/profile_images/v1/alice/remove"
        )
        for name in ("profile_image_upload", "profile_image_remove"):
            match = resolve(reverse(name, kwargs={"username": "alice"}))
            assert match.func.view_class.__module__ == "openedx.core.djangoapps.profile_images.views"

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
        assert reverse("accounts_detail_api") == "/api/user/v1/accounts"
        assert reverse("accounts_search_emails_api") == "/api/user/v1/accounts/search_emails"
        assert reverse("accounts_deactivation", kwargs={"username": "alice"}) == (
            "/api/user/v1/accounts/alice/deactivate/"
        )
        assert reverse("accounts_profile_image_api", kwargs={"username": "alice"}) == (
            "/api/user/v1/accounts/alice/image"
        )
        assert reverse("request_name_change") == "/api/user/v1/accounts/name_change/"
        assert reverse("confirm_name_change", kwargs={"username": "alice"}) == (
            "/api/user/v1/accounts/name_change/alice/confirm/"
        )
        assert reverse("verification_status", kwargs={"username": "alice"}) == (
            "/api/user/v1/accounts/alice/verification_status/"
        )
        assert reverse("verification_details", kwargs={"username": "alice"}) == (
            "/api/user/v1/accounts/alice/verifications/"
        )
        assert reverse("preferences_api", kwargs={"username": "alice"}) == "/api/user/v1/preferences/alice"

    def test_version_one_preference_addresses_still_reach_the_version_one_views(self):
        expected = {
            "/api/user/v1/preferences/alice": (V1_PREFERENCE_VIEW_MODULE, "PreferencesView"),
            "/api/user/v1/preferences/alice/time_zone": (
                V1_PREFERENCE_VIEW_MODULE, "PreferencesDetailView",
            ),
            "/api/user/v1/preferences/email_opt_in/": (
                V1_VIEW_MODULE, "UpdateEmailOptInPreference",
            ),
        }
        for address, identity in expected.items():
            assert view_identity(resolve(address).func) == identity, address

    def test_version_one_preference_url_names_still_reverse(self):
        assert reverse(
            "preferences_detail_api", kwargs={"username": "alice", "preference_key": "time_zone"},
        ) == "/api/user/v1/preferences/alice/time_zone"
        assert reverse("preferences_email_opt_in") == "/api/user/v1/preferences/email_opt_in/"
        assert reverse("preferences_email_opt_in_legacy") == (
            "/user_api/v1/preferences/email_opt_in/"
        )

    def test_the_unversioned_opt_in_address_reaches_the_version_one_view_as_before(self):
        """Both of its addresses are served by one view, and this version adds no third."""
        versioned = resolve("/api/user/v1/preferences/email_opt_in/")
        unversioned = resolve("/user_api/v1/preferences/email_opt_in/")
        assert view_identity(unversioned.func) == view_identity(versioned.func)
        assert view_identity(unversioned.func) == (V1_VIEW_MODULE, "UpdateEmailOptInPreference")
        assert served_methods(unversioned.func) == served_methods(versioned.func) == {
            "POST", "OPTIONS",
        }

    def test_unversioned_twins_still_resolve(self):
        match = resolve("/user_api/v1/users/")
        assert view_identity(match.func) == (V1_VIEW_MODULE, "UserViewSet")


#: The addresses of the retirement pipeline, with the view each is mounted on
#: and the methods it serves.
RETIREMENT_ROUTES = {
    "/api/user/v2/retirements/": (RetirementViewSet, {"get": "list", "post": "create"}),
    "/api/user/v2/retirements/alice/": (
        RetirementViewSet,
        {"get": "retrieve", "patch": "partial_update", "delete": "destroy"},
    ),
    "/api/user/v2/retirements/alice/erasures/": (RetirementViewSet, {"post": "erasures"}),
    "/api/user/v2/retirement_partner_reports/": (
        RetirementPartnerReportViewSet, {"post": "create"},
    ),
    "/api/user/v2/retirement_partner_reports/alice/": (
        RetirementPartnerReportViewSet, {"put": "update"},
    ),
    "/api/user/v2/retirement_cleanups/": (RetirementCleanupViewSet, {"post": "create"}),
    "/api/user/v2/retirement_partner_report_cleanups/": (
        RetirementPartnerReportCleanupViewSet, {"post": "create"},
    ),
}

#: The addresses of the version 1 pipeline, with the view class each still
#: reaches and the actions each is still bound to.
LEGACY_RETIREMENT_ROUTES = {
    "/api/user/v1/accounts/alice/retirement_status/": (
        "AccountRetirementStatusView", {"get": "retrieve"},
    ),
    "/api/user/v1/accounts/retirement_queue/": (
        "AccountRetirementStatusView", {"get": "retirement_queue"},
    ),
    "/api/user/v1/accounts/retirements_by_status_and_date/": (
        "AccountRetirementStatusView", {"get": "retirements_by_status_and_date"},
    ),
    "/api/user/v1/accounts/update_retirement_status/": (
        "AccountRetirementStatusView", {"patch": "partial_update"},
    ),
    "/api/user/v1/accounts/retirement_cleanup/": (
        "AccountRetirementStatusView", {"post": "cleanup"},
    ),
    "/api/user/v1/accounts/retirement_partner_report/": (
        "AccountRetirementPartnerReportView",
        {"post": "retirement_partner_report", "put": "retirement_partner_status_create"},
    ),
    "/api/user/v1/accounts/retirement_partner_report_cleanup/": (
        "AccountRetirementPartnerReportView", {"post": "retirement_partner_cleanup"},
    ),
    "/api/user/v1/accounts/cancel_retirement/": (
        "CancelAccountRetirementStatusView", {"post": "cancel_retirement"},
    ),
    "/api/user/v1/accounts/retire/": ("AccountRetirementView", {"post": "post"}),
    "/api/user/v1/accounts/retire_misc/": ("LMSAccountRetirementView", {"post": "post"}),
}

#: The names the version 1 pipeline is reversed by, which out-of-tree callers
#: hold, with the address each still builds.
LEGACY_RETIREMENT_NAMES = {
    "accounts_retirement_retrieve": (
        {"username": "alice"}, "/api/user/v1/accounts/alice/retirement_status/",
    ),
    "accounts_retirement_queue": ({}, "/api/user/v1/accounts/retirement_queue/"),
    "accounts_retirements_by_status_and_date": (
        {}, "/api/user/v1/accounts/retirements_by_status_and_date/",
    ),
    "accounts_retirement_update": ({}, "/api/user/v1/accounts/update_retirement_status/"),
    "accounts_retirement_cleanup": ({}, "/api/user/v1/accounts/retirement_cleanup/"),
    "accounts_retirement_partner_report": (
        {}, "/api/user/v1/accounts/retirement_partner_report/",
    ),
    "accounts_retirement_partner_report_cleanup": (
        {}, "/api/user/v1/accounts/retirement_partner_report_cleanup/",
    ),
    "cancel_account_retirement": ({}, "/api/user/v1/accounts/cancel_retirement/"),
    "accounts_retire": ({}, "/api/user/v1/accounts/retire/"),
    "accounts_retire_misc": ({}, "/api/user/v1/accounts/retire_misc/"),
}


@skip_unless_lms
class RetirementRouteTests(APITestCase):
    """Where the retirement addresses are mounted, and what each one serves."""

    def test_each_address_is_mounted_on_the_view_that_serves_it(self):
        for address, (view, actions) in RETIREMENT_ROUTES.items():
            match = resolve(address)
            assert view_identity(match.func) == (V2_VIEW_MODULE, view.__name__), address
            assert routed_actions(match.func) == actions, address

    def test_neither_disposal_is_served_by_the_collection_whose_records_it_deletes(self):
        """Each disposal has a view of its own, because it acts on a set and under its own invariant."""
        assert {
            view_identity(resolve(address).func)
            for address in (
                "/api/user/v2/retirements/",
                "/api/user/v2/retirement_cleanups/",
                "/api/user/v2/retirement_partner_reports/",
                "/api/user/v2/retirement_partner_report_cleanups/",
            )
        } == {
            (V2_VIEW_MODULE, "RetirementViewSet"),
            (V2_VIEW_MODULE, "RetirementCleanupViewSet"),
            (V2_VIEW_MODULE, "RetirementPartnerReportViewSet"),
            (V2_VIEW_MODULE, "RetirementPartnerReportCleanupViewSet"),
        }

    def test_neither_disposal_has_a_member_address(self):
        """A collection with no member operation is given no member route by the router."""
        for address in (
            "/api/user/v2/retirement_cleanups/",
            "/api/user/v2/retirement_partner_report_cleanups/",
        ):
            with pytest.raises(Resolver404):
                resolve(f"{address}alice/")

    def test_a_name_carrying_every_character_a_username_may_hold_addresses_a_member(self):
        for username in ("a.b", "a b", "a@b", "a+b", "a-b", "a_b", "Mixed.Case+1"):
            with self.subTest(username=username):
                match = resolve(f"/api/user/v2/retirements/{username}/")
                assert match.url_name == "retirement_detail"
                assert match.kwargs == {"username": username}
                erasure = resolve(f"/api/user/v2/retirements/{username}/erasures/")
                assert erasure.url_name == "retirement_erasure"
                assert erasure.kwargs == {"username": username}

    def test_a_name_that_reads_like_a_batch_address_is_read_as_a_name(self):
        """A user may register any name, so no fixed word sits where a name goes."""
        match = resolve("/api/user/v2/retirements/cleanups/")
        assert match.url_name == "retirement_detail"
        assert match.kwargs == {"username": "cleanups"}
        assert resolve("/api/user/v2/retirement_cleanups/").url_name == "retirement_cleanup_list"

    def test_the_batch_addresses_are_not_members_of_the_collections_they_dispose_of(self):
        assert reverse("user_v2:retirement_cleanup_list") == "/api/user/v2/retirement_cleanups/"
        assert reverse("user_v2:retirement_partner_report_cleanup_list") == (
            "/api/user/v2/retirement_partner_report_cleanups/"
        )

    def test_the_viewsets_build_their_action_addresses_under_this_mount(self):
        retirements = RetirementViewSet(basename="retirement")
        assert retirements.reverse_action("list") == "/api/user/v2/retirements/"
        assert retirements.reverse_action(
            "detail", kwargs={"username": "alice"},
        ) == "/api/user/v2/retirements/alice/"
        assert retirements.reverse_action(
            "erasure", kwargs={"username": "alice"},
        ) == "/api/user/v2/retirements/alice/erasures/"
        reports = RetirementPartnerReportViewSet(basename="retirement_partner_report")
        assert reports.reverse_action("list") == "/api/user/v2/retirement_partner_reports/"
        assert reports.reverse_action(
            "detail", kwargs={"username": "alice"},
        ) == "/api/user/v2/retirement_partner_reports/alice/"

    def test_an_address_near_one_this_version_serves_is_refused_in_the_body_it_answers_with(self):
        """No route serves it, so it is answered as an address outside this prefix is."""
        with pytest.raises(Resolver404):
            resolve("/api/user/v2/retirement/")
        response = self.client.get("/api/user/v2/retirement/")
        outside = self.client.get("/api/not_a_service/v2/retirement/")
        assert response.status_code == outside.status_code == status.HTTP_404_NOT_FOUND
        assert response["Content-Type"] == outside["Content-Type"]


@skip_unless_lms
class LegacyRetirementAddressTests(APITestCase):
    """Mounting the conforming pipeline leaves the version 1 pipeline exactly where it was."""

    def test_every_version_one_address_still_reaches_the_view_and_actions_it_always_did(self):
        for address, (class_name, actions) in LEGACY_RETIREMENT_ROUTES.items():
            match = resolve(address)
            assert view_identity(match.func) == (V1_ACCOUNT_VIEW_MODULE, class_name), address
            assert routed_actions(match.func) == actions, address

    def test_every_name_the_version_one_pipeline_is_reversed_by_still_reverses(self):
        for name, (kwargs, address) in LEGACY_RETIREMENT_NAMES.items():
            assert reverse(name, kwargs=kwargs) == address, name

    def test_no_version_one_name_is_shadowed_by_a_version_two_one(self):
        """The two sets live in different namespaces, so the same word names two addresses."""
        assert reverse("accounts_retirement_queue") != reverse("user_v2:retirement_list")
        with pytest.raises(NoReverseMatch):
            reverse("retirement_list")
