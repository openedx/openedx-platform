"""Addresses of other apps that share the prefix version 2 of the User API is mounted on."""

from django.test import SimpleTestCase
from django.urls import resolve, reverse

from openedx.core.djangolib.testing.utils import skip_unless_lms

AUTHN_LOGIN_VIEWS = "openedx.core.djangoapps.user_authn.views.login"
AUTHN_REGISTER_VIEWS = "openedx.core.djangoapps.user_authn.views.register"


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
