"""Addresses of other apps that share the prefix version 2 of the User API is mounted on."""

from django.test import SimpleTestCase
from django.urls import resolve, reverse

from openedx.core.djangolib.testing.utils import skip_unless_lms

AUTHN_LOGIN_VIEWS = "openedx.core.djangoapps.user_authn.views.login"
AUTHN_REGISTER_VIEWS = "openedx.core.djangoapps.user_authn.views.register"
V2_VIEWS = "openedx.core.djangoapps.user_api.v2.views"


@skip_unless_lms
class SharedPrefixTests(SimpleTestCase):
    """
    The authentication app keeps the two addresses it serves under this prefix.

    The mount for this version is matched before the include that carries the
    rest of the user surface and before the authentication app's own patterns,
    so it is first in line for every address that begins with
    ``/api/user/v2/``. Those two addresses reach the authentication app only
    because no pattern here matches them first.
    """

    def assert_resolves_to(self, address, module, class_name, url_name, kwargs=None):
        """Assert an address reaches the named view through the named route."""
        match = resolve(address)
        view_class = match.func.view_class
        assert (view_class.__module__, view_class.__name__) == (module, class_name), address
        assert match.url_name == url_name, address
        assert match.kwargs == (kwargs or {}), address

    def test_registration_still_reaches_the_authentication_app(self):
        self.assert_resolves_to(
            "/api/user/v2/account/registration/",
            AUTHN_REGISTER_VIEWS,
            "RegistrationView",
            "user_api_registration_v2",
        )

    def test_login_session_still_reaches_the_authentication_app(self):
        self.assert_resolves_to(
            "/api/user/v2/account/login_session/",
            AUTHN_LOGIN_VIEWS,
            "LoginSessionView",
            "user_api_login_session",
            {"api_version": "v2"},
        )

    def test_the_version_one_addresses_beside_them_are_unaffected(self):
        self.assert_resolves_to(
            "/api/user/v1/account/registration/",
            AUTHN_REGISTER_VIEWS,
            "RegistrationView",
            "user_api_registration",
        )
        self.assert_resolves_to(
            "/api/user/v1/account/login_session/",
            AUTHN_LOGIN_VIEWS,
            "LoginSessionView",
            "user_api_login_session",
            {"api_version": "v1"},
        )

    def test_both_shared_prefix_addresses_still_reverse_to_themselves(self):
        assert reverse("user_api_registration_v2") == "/api/user/v2/account/registration/"
        assert reverse(
            "user_api_login_session", kwargs={"api_version": "v2"},
        ) == "/api/user/v2/account/login_session/"


@skip_unless_lms
class ClaimedPrefixTests(SimpleTestCase):
    """
    Every address under this prefix that no route serves is answered by this version.

    The mount matches the whole prefix, so an address under it that none of
    the routes and neither of the two addresses the authentication app keeps
    matches is answered here rather than left to anything mounted later. That
    is what makes a mistyped address come back as JSON, and it is also what
    keeps an address registered under this prefix by anything mounted after
    this version from being reached at all.
    """

    def assert_refused_here(self, address):
        """Assert an address under the prefix is answered by this version's refusal."""
        match = resolve(address)
        view_class = match.func.view_class
        assert (view_class.__module__, view_class.__name__) == (V2_VIEWS, "UnknownAddressView"), address

    def test_a_mistyped_address_is_answered_by_this_version(self):
        self.assert_refused_here("/api/user/v2/acounts/")

    def test_an_address_below_a_route_of_this_version_is_answered_by_this_version(self):
        self.assert_refused_here("/api/user/v2/accounts/alice/not-a-property/")

    def test_a_third_address_of_the_other_apps_shape_is_answered_here(self):
        """Only the two addresses spelled out for that app are left to it; a third is not."""
        self.assert_refused_here("/api/user/v2/account/password_reset/")

    def test_an_address_a_later_mount_would_serve_is_answered_here_instead(self):
        """
        The prefix is claimed whole, which is the constraint an operator needs.

        Anything mounted after this version that registers an address under
        the prefix is unreachable, so the shape of such an address is pinned
        here rather than left to be discovered from a 404 in a deployment.
        """
        self.assert_refused_here("/api/user/v2/some_plugin/thing/")
        self.assert_refused_here("/api/user/v2/badges/")
