"""Tests for the registration, sign-in and password reset addresses of version 2 of the User API."""

import contextlib
import json
import re
from types import SimpleNamespace
from unittest import mock

import jsonschema
import pytest
from crum.signals import current_user_setter
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.signals import request_finished, request_started
from django.db import connection, transaction
from django.db.models.signals import post_init, post_save, pre_init, pre_save
from django.dispatch import Signal
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import resolve, reverse
from django.utils.http import int_to_base36
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import patched_settings
from edx_django_utils.monitoring.signals import (
    monitoring_support_process_exception,
    monitoring_support_process_request,
    monitoring_support_process_response,
)
from openedx_events.learning.signals import STUDENT_REGISTRATION_COMPLETED
from openedx_events.tooling import OpenEdxPublicSignal
from openedx_filters import PipelineStep
from openedx_filters.learning.filters import StudentLoginRequested, StudentRegistrationRequested
from rest_framework.permissions import AllowAny
from rest_framework.settings import api_settings

from common.djangoapps.student.models import Registration
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth.models import OAuth2ProviderConfig
from openedx.core.apidocs import get_api_docs_settings
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.user_api.v2 import authn_views
from openedx.core.djangoapps.user_api.v2.tests.test_schema import as_json_schema
from openedx.core.djangoapps.user_authn.views import login, password_reset, register
from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.envs.common import REGISTRATION_EXTRA_FIELDS as STOCK_REGISTRATION_EXTRA_FIELDS

User = get_user_model()

PASSWORD = "Password1234"
REGISTRATION_PASSWORD = "Password1234!x"
JSON = "application/json"
FORM = "application/x-www-form-urlencoded"
MULTIPART = "multipart/form-data"

AUTHN_VIEW_MODULE = "openedx.core.djangoapps.user_api.v2.authn_views"

REGISTRATIONS = "/api/user/v2/registrations/"
PASSWORD_RESETS = "/api/user/v2/password_resets/"
TOKEN_VALIDATIONS = "/api/user/v2/password_reset_token_validations/"
REGISTRATION_VALIDATIONS = "/api/user/v2/registration_validations/"
LOGIN_SESSIONS = "/api/user/v2/login_sessions/"

V1_REGISTRATION = "/api/user/v1/account/registration/"
V2_REGISTRATION = "/api/user/v2/account/registration/"
V1_PASSWORD_RESET = "/api/user/v1/account/password_reset/"
V1_TOKEN_VALIDATION = "/api/user/v1/account/password_reset/token/validate/"
V1_REGISTRATION_VALIDATION = "/api/user/v1/validation/registration"
V1_LOGIN_SESSION = "/api/user/v1/account/login_session/"
V2_LOGIN_SESSION = "/api/user/v2/account/login_session/"
LOGIN_AJAX = "/login_ajax"
CREATE_ACCOUNT = "/create_account"

#: Each address this version serves for the authentication app, with its
#: route name, the view of that app it is answered by, and the arguments its
#: route hands the view.
CONFORMING_ROUTES = {
    REGISTRATIONS: ("user_v2:registration_list", register.RegistrationView, {}),
    PASSWORD_RESETS: ("user_v2:password_reset_list", password_reset.PasswordResetView, {}),
    TOKEN_VALIDATIONS: (
        "user_v2:password_reset_token_validation_list",
        password_reset.PasswordResetTokenValidation,
        {},
    ),
    REGISTRATION_VALIDATIONS: (
        "user_v2:registration_validation_list",
        register.RegistrationValidationView,
        {},
    ),
    LOGIN_SESSIONS: ("user_v2:login_session_list", login.LoginSessionView, {"api_version": "v2"}),
}

#: Every name the authentication app's addresses are reversed by, with the
#: arguments, the address and the view it reaches. None of them moves.
LEGACY_ROUTES = (
    ("user_api_registration", {}, V1_REGISTRATION, register.RegistrationView),
    ("user_api_registration_legacy", {}, "/user_api/v1/account/registration/", register.RegistrationView),
    ("user_api_registration_v2", {}, V2_REGISTRATION, register.RegistrationView),
    ("user_api_registration_v2_legacy", {}, "/user_api/v2/account/registration/", register.RegistrationView),
    ("create_account", {}, CREATE_ACCOUNT, register.RegistrationView),
    ("registration_validation", {}, V1_REGISTRATION_VALIDATION, register.RegistrationValidationView),
    ("user_api_login_session", {"api_version": "v1"}, V1_LOGIN_SESSION, login.LoginSessionView),
    ("user_api_login_session", {"api_version": "v2"}, V2_LOGIN_SESSION, login.LoginSessionView),
    (
        "user_api_login_session_legacy",
        {"api_version": "v1"},
        "/user_api/v1/account/login_session/",
        login.LoginSessionView,
    ),
    (
        "user_api_login_session_legacy",
        {"api_version": "v2"},
        "/user_api/v2/account/login_session/",
        login.LoginSessionView,
    ),
    ("user_api_password_reset", {}, V1_PASSWORD_RESET, password_reset.PasswordResetView),
    (
        "user_api_password_reset_legacy",
        {},
        "/user_api/v1/account/password_reset/",
        password_reset.PasswordResetView,
    ),
    (
        "user_api_password_reset_token_validate",
        {},
        V1_TOKEN_VALIDATION,
        password_reset.PasswordResetTokenValidation,
    ),
    (
        "user_api_password_reset_token_validate_legacy",
        {},
        "/user_api/v1/account/password_reset/token/validate/",
        password_reset.PasswordResetTokenValidation,
    ),
)

#: A real cache, so the limits on how often these operations may be sent take
#: effect; the stock test settings use one that stores nothing.
COUNTING_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "user_api_v2_authn_addresses",
    },
}

#: What the only intended difference between an address and the one it
#: replaces is: the description each view's OPTIONS answer carries.
OPTIONS_DIFFERENCES = [
    ("description", "each view describes itself, and the view at each address is a different class"),
]

#: The cookies a registration or a sign-in sets in the stock test settings.
SIGNED_IN_COOKIES = {"csrftoken", "edx-user-info", "edxloggedin", "openedx-language-preference", "sessionid"}

TOO_MANY_LOGINS = "Too many failed login attempts. Try again later."
MISSING_LOGIN_FIELDS = "There was an error receiving your login information. Please email us."


class RefuseRegistrationStep(PipelineStep):
    """A deployment's own step refusing every registration."""

    def run_filter(self, form_data):  # pylint: disable=arguments-differ
        raise StudentRegistrationRequested.PreventRegistration(
            "Registration is closed.", status_code=403, error_code="registration-closed",
        )


class RefuseLoginStep(PipelineStep):
    """A deployment's own step refusing every sign-in."""

    def run_filter(self, user):  # pylint: disable=arguments-differ
        raise StudentLoginRequested.PreventLogin(
            "Sign-in is closed.", redirect_to="/closed", error_code="sign-in-closed", context={"reason": "closed"},
        )


REFUSE_REGISTRATION = {
    "org.openedx.learning.student.registration.requested.v1": {
        "pipeline": [f"{__name__}.RefuseRegistrationStep"],
        "fail_silently": False,
    },
}
REFUSE_LOGIN = {
    "org.openedx.learning.student.login.requested.v1": {
        "pipeline": [f"{__name__}.RefuseLoginStep"],
        "fail_silently": False,
    },
}


def registration_form(name):
    """Return a registration the stock settings accept, for an account called ``name``."""
    return {
        "email": f"{name}@example.com",
        "name": name.replace("_", " ").title(),
        "username": name,
        "password": REGISTRATION_PASSWORD,
        "honor_code": "true",
        "country": "PK",
    }


def reset_token(user):
    """Return the token a password reset email carries for ``user``."""
    return f"{int_to_base36(user.id)}-{default_token_generator.make_token(user)}"


def signed_in(user, enforce_csrf_checks=False):
    """Return a client holding a session for ``user``."""
    client = Client(enforce_csrf_checks=enforce_csrf_checks)
    assert client.login(username=user.username, password=PASSWORD)
    return client


def cookies_set(response):
    """Return the names of the cookies a response sets."""
    return set(response.cookies)


def flatten(body, volatile=(), path=""):
    """
    Return a body as a map of member paths to values, members sorted.

    A member whose value is chosen per request -- a generated username, an
    account's id -- is kept under its path with only its type, so two
    responses are compared on every member they carry and on every value that
    does not depend on which account a request created.
    """
    flat = {}
    if isinstance(body, dict):
        for key in sorted(body):
            flat.update(flatten(body[key], volatile, f"{path}.{key}" if path else key))
    elif isinstance(body, list):
        if path in volatile:
            return {path: ("list", len(body), sorted({type(item).__name__ for item in body}))}
        for index, item in enumerate(body):
            flat.update(flatten(item, volatile, f"{path}[{index}]"))
    else:
        flat[path] = type(body).__name__ if path in volatile else body
    return flat


def differences(legacy, conforming, intentional=(), volatile=()):
    """
    Return the member paths two bodies differ on, and those of them not declared intentional.

    Every declared difference has to occur, or the list of them says more than
    the two addresses really differ by.
    """
    old, new = flatten(legacy, volatile), flatten(conforming, volatile)
    changed = {key for key in set(old) | set(new) if old.get(key, "<absent>") != new.get(key, "<absent>")}
    declared = {path for path, _reason in intentional}
    unexpected = {
        key for key in changed
        if not any(key == path or key.startswith((f"{path}.", f"{path}[")) for path in declared)
    }
    for path, reason in intentional:
        assert any(key == path or key.startswith((f"{path}.", f"{path}[")) for key in changed), (
            f"declared difference did not occur: {path} ({reason})"
        )
    return changed, unexpected


def assert_same_answer(legacy, conforming, status, volatile=(), cookies=None):
    """
    Assert two addresses answered one request the same way.

    The status, the media type, the cookies set and every member of the body
    are compared; members chosen per request are compared by type.
    """
    assert legacy.status_code == status, legacy.content
    assert conforming.status_code == status, conforming.content
    assert conforming["Content-Type"] == legacy["Content-Type"]
    assert cookies_set(conforming) == cookies_set(legacy)
    if cookies is not None:
        assert cookies_set(conforming) == cookies
    if legacy["Content-Type"].startswith(JSON):
        _changed, unexpected = differences(json.loads(legacy.content), json.loads(conforming.content), (), volatile)
        assert not unexpected, (sorted(unexpected), legacy.content, conforming.content)
    else:
        assert conforming.content == legacy.content


def assert_byte_equal(legacy, conforming, status=200):
    """Assert two addresses answered with the same bytes, media type and cookies."""
    assert legacy.status_code == status, legacy.content
    assert conforming.status_code == status, conforming.content
    assert conforming["Content-Type"] == legacy["Content-Type"]
    assert cookies_set(conforming) == cookies_set(legacy)
    assert conforming.content == legacy.content


def spend(address, data, times, **extra):
    """Send ``times`` requests to ``address`` from new clients and return their statuses."""
    return [Client().post(address, data, **extra).status_code for _ in range(times)]


def limit_of(rate):
    """Return the number of requests a rate setting allows in its period."""
    return int(rate.split("/")[0])


def narrow_document():
    """Return the document the service publishes for the APIs it standardizes."""
    return SchemaGenerator().get_schema(request=None, public=True)


def service_wide_document():
    """Return the document the service publishes for browsing its whole surface."""
    with patched_settings(get_api_docs_settings()):
        return SchemaGenerator().get_schema(request=None, public=True)


@skip_unless_lms
class ConformingAddressTests(TestCase):
    """Each address reverses, resolves to its view, and leaves the older addresses where they were."""

    def test_each_name_reverses_to_its_address(self):
        reversed_addresses = {
            address: reverse(name, kwargs={}) for address, (name, _view, _kwargs) in CONFORMING_ROUTES.items()
        }
        assert reversed_addresses == {address: address for address in CONFORMING_ROUTES}

    def test_each_address_is_answered_by_a_view_extending_the_one_serving_the_older_address(self):
        for address, (name, legacy_view, route_kwargs) in CONFORMING_ROUTES.items():
            match = resolve(address)
            view = match.func.view_class
            assert match.view_name == name, address
            assert view.__module__ == AUTHN_VIEW_MODULE, address
            assert view.__name__ == legacy_view.__name__, address
            assert view is not legacy_view, address
            assert issubclass(view, legacy_view), address
            assert match.kwargs == route_kwargs, address

    def test_every_older_name_still_reverses_to_its_address_and_reaches_the_same_view(self):
        for name, kwargs, address, view in LEGACY_ROUTES:
            assert reverse(name, kwargs=kwargs) == address, name
            match = resolve(address)
            assert getattr(match.func, "view_class", None) is view, (name, address)

    def test_the_sign_in_function_keeps_its_own_address(self):
        assert reverse("login_api") == LOGIN_AJAX
        assert resolve(LOGIN_AJAX).func is login.login_user

    def test_an_address_sent_without_its_slash_is_redirected_to_it(self):
        """These addresses are served only with the slash, as every address of the service is."""
        for address in CONFORMING_ROUTES:
            response = Client().get(address.rstrip("/"), HTTP_ACCEPT=JSON)
            assert (response.status_code, response["Location"]) == (301, address), address

    def test_the_older_addresses_sent_without_their_slash_are_still_redirected(self):
        for address in (V1_REGISTRATION, V2_REGISTRATION, V1_LOGIN_SESSION, V2_LOGIN_SESSION, V1_PASSWORD_RESET):
            response = Client().get(address.rstrip("/"))
            assert response.status_code == 301, address
            assert response["Location"] == address, address

    def test_the_older_validation_address_is_still_served_only_without_a_slash(self):
        assert Client().post(f"{V1_REGISTRATION_VALIDATION}/", {}).status_code == 404
        assert Client().post(V1_REGISTRATION_VALIDATION, {}).status_code == 200


#: What each view these addresses are answered by holds of its own, read as
#: this module is imported, before any request is served. The first request a
#: view that reads signed tokens serves adds a permission to that view's class
#: in place, so a class read once such a request has run no longer shows what
#: it declares.
DECLARED_ATTRIBUTES = {
    view: dict(vars(view))
    for view in (
        authn_views.RegistrationView,
        authn_views.PasswordResetView,
        authn_views.PasswordResetTokenValidation,
        authn_views.RegistrationValidationView,
        authn_views.LoginSessionView,
        register.RegistrationView,
        register.RegistrationValidationView,
        password_reset.PasswordResetView,
        password_reset.PasswordResetTokenValidation,
        login.LoginSessionView,
    )
}


@skip_unless_lms
class ViewShapeTests(TestCase):
    """
    The views add a declaration of who may call them and the description they are published with.

    Everything else -- how a caller is identified, CSRF handling, the limits
    on how often a request may be sent, the bodies and codes -- is the view
    they extend, reached through the method that view defines.
    """

    #: What a view class carries of its own besides its methods.
    OWN_ATTRIBUTES = {"__module__", "__doc__", "permission_classes"}

    def test_each_view_declares_only_who_may_call_it(self):
        for address, (_name, legacy_view, _kwargs) in CONFORMING_ROUTES.items():
            view = resolve(address).func.view_class
            declared = DECLARED_ATTRIBUTES[view]
            own = set(declared) - {"__firstlineno__", "__static_attributes__"}
            methods = {name for name in own if name in view.http_method_names}
            assert own - methods == self.OWN_ATTRIBUTES, address
            assert declared["permission_classes"] == (AllowAny,), address
            legacy_methods = {name for name in DECLARED_ATTRIBUTES[legacy_view] if name in view.http_method_names}
            assert methods == legacy_methods, address

    #: The methods each address is served with.
    SERVED_METHODS = {
        REGISTRATIONS: {"get", "post"},
        PASSWORD_RESETS: {"get"},
        TOKEN_VALIDATIONS: {"post"},
        REGISTRATION_VALIDATIONS: {"post"},
        LOGIN_SESSIONS: {"get", "post"},
    }

    def test_each_method_hands_the_request_to_the_one_it_extends(self):
        """A method of its own would run without the decorators the extended method carries."""
        for address, (_name, legacy_view, _kwargs) in CONFORMING_ROUTES.items():
            view = resolve(address).func.view_class
            methods = {name for name in vars(view) if name in view.http_method_names}
            assert methods == self.SERVED_METHODS[address], address
            for method in methods:
                assert vars(view)[method].__wrapped__ is vars(legacy_view)[method], (address, method)

    def test_how_a_caller_is_identified_is_the_extended_views(self):
        for address, (_name, legacy_view, _kwargs) in CONFORMING_ROUTES.items():
            view = resolve(address).func.view_class
            assert view.authentication_classes is legacy_view.authentication_classes, address
            assert view.throttle_classes is legacy_view.throttle_classes, address
        assert resolve(TOKEN_VALIDATIONS).func.view_class.authentication_classes == (
            api_settings.DEFAULT_AUTHENTICATION_CLASSES
        )
        for address in (REGISTRATIONS, PASSWORD_RESETS, REGISTRATION_VALIDATIONS, LOGIN_SESSIONS):
            assert resolve(address).func.view_class.authentication_classes == [], address

    def test_the_extended_views_are_left_as_they_were(self):
        """Describing a method here changes nothing the older addresses publish or run."""
        for legacy_view in (
            register.RegistrationView,
            register.RegistrationValidationView,
            password_reset.PasswordResetView,
            password_reset.PasswordResetTokenValidation,
            login.LoginSessionView,
        ):
            assert "permission_classes" not in DECLARED_ATTRIBUTES[legacy_view], legacy_view
        for legacy_method in (
            register.RegistrationView.get,
            register.RegistrationView.post,
            register.RegistrationValidationView.post,
            password_reset.PasswordResetView.get,
            password_reset.PasswordResetTokenValidation.post,
            login.LoginSessionView.get,
        ):
            assert not hasattr(legacy_method, "kwargs"), legacy_method
        assert set(login.LoginSessionView.post.kwargs) == {"schema"}
        assert login.LoginSessionView.post.kwargs["schema"] is not (
            resolve(LOGIN_SESSIONS).func.view_class.post.kwargs["schema"]
        )


@skip_unless_lms
class RegistrationParityTests(TestCase):
    """The registration address answers every request as the version 2 address it replaces does."""

    def test_the_form_is_the_same_bytes(self):
        legacy, conforming = Client().get(V2_REGISTRATION), Client().get(REGISTRATIONS)
        assert_byte_equal(legacy, conforming)
        assert {"csrftoken", "sessionid"} <= cookies_set(conforming)
        assert json.loads(conforming.content)["submit_url"] == V2_REGISTRATION

    def test_a_configured_confirmation_field_is_offered_as_version_two_offers_it(self):
        extra = {**settings.REGISTRATION_EXTRA_FIELDS, "confirm_email": "required"}
        with override_settings(REGISTRATION_EXTRA_FIELDS=extra):
            legacy, conforming = Client().get(V2_REGISTRATION), Client().get(REGISTRATIONS)
            version_one = Client().get(V1_REGISTRATION)
        assert_byte_equal(legacy, conforming)
        offered = [field["name"] for field in json.loads(conforming.content)["fields"]]
        assert "confirm_email" in offered
        assert "confirm_email" not in [field["name"] for field in json.loads(version_one.content)["fields"]]

    def test_a_configured_confirmation_field_is_required_as_version_two_requires_it(self):
        extra = {**settings.REGISTRATION_EXTRA_FIELDS, "confirm_email": "required"}
        with override_settings(REGISTRATION_EXTRA_FIELDS=extra):
            legacy = Client().post(V2_REGISTRATION, registration_form("confirm_old"))
            conforming = Client().post(REGISTRATIONS, registration_form("confirm_new"))
            version_one = Client().post(V1_REGISTRATION, registration_form("confirm_v_one"))
        assert_same_answer(legacy, conforming, 400)
        assert json.loads(conforming.content) == {
            "confirm_email": [{"user_message": "You are missing one or more required fields"}],
            "error_code": "validation-error",
        }
        assert version_one.status_code == 200

    def test_an_account_is_created_and_the_caller_signed_in(self):
        legacy = Client().post(V2_REGISTRATION, registration_form("parity_old"))
        conforming_client = Client()
        conforming = conforming_client.post(REGISTRATIONS, registration_form("parity_new"))
        assert_same_answer(
            legacy,
            conforming,
            200,
            volatile={"authenticated_user.username", "authenticated_user.full_name", "authenticated_user.user_id"},
            cookies=SIGNED_IN_COOKIES,
        )
        created = User.objects.get(username="parity_new")
        assert json.loads(conforming.content)["authenticated_user"] == {
            "username": "parity_new", "full_name": "Parity New", "user_id": created.id,
        }
        assert int(conforming_client.session["_auth_user_id"]) == created.id

    def test_a_form_sent_as_multipart_is_read_as_well(self):
        conforming = Client().post(REGISTRATIONS, registration_form("multipart_new"))
        assert conforming.status_code == 200, conforming.content
        sent_as_form = Client().post(
            REGISTRATIONS,
            "&".join(f"{key}={value}" for key, value in registration_form("urlencoded_new").items()),
            content_type=FORM,
        )
        assert sent_as_form.status_code == 200, sent_as_form.content

    def test_a_signed_in_caller_ends_up_signed_in_as_the_account_it_registered(self):
        UserFactory.create(username="already_in", password=PASSWORD)
        legacy_client = signed_in(User.objects.get(username="already_in"))
        conforming_client = signed_in(User.objects.get(username="already_in"))
        legacy = legacy_client.post(V2_REGISTRATION, registration_form("switch_old"))
        conforming = conforming_client.post(REGISTRATIONS, registration_form("switch_new"))
        assert legacy.status_code == conforming.status_code == 200
        assert int(conforming_client.session["_auth_user_id"]) == User.objects.get(username="switch_new").id
        assert int(legacy_client.session["_auth_user_id"]) == User.objects.get(username="switch_old").id

    def test_no_csrf_token_is_asked_for(self):
        legacy = Client(enforce_csrf_checks=True).post(V2_REGISTRATION, registration_form("csrf_old"))
        conforming = Client(enforce_csrf_checks=True).post(REGISTRATIONS, registration_form("csrf_new"))
        assert legacy.status_code == conforming.status_code == 200

    def test_credentials_in_a_header_are_not_read(self):
        """No authenticator runs, so a header that would be refused anywhere else is ignored."""
        legacy = Client().post(V2_REGISTRATION, {}, HTTP_AUTHORIZATION="JWT not-a-jwt")
        conforming = Client().post(REGISTRATIONS, {}, HTTP_AUTHORIZATION="JWT not-a-jwt")
        assert_same_answer(legacy, conforming, 400)
        assert Client().get(REGISTRATIONS, HTTP_AUTHORIZATION="JWT not-a-jwt").status_code == 200

    def test_an_empty_registration_names_every_field_it_needs(self):
        """No registration address takes a request without a body, which is why each publishes it as needed."""
        legacy, conforming = Client().post(V2_REGISTRATION, {}), Client().post(REGISTRATIONS, {})
        assert_same_answer(legacy, conforming, 400)
        needed = {"email", "error_code", "honor_code", "name", "password", "terms_of_service", "username"}
        assert set(json.loads(conforming.content)) == needed
        version_one = Client().post(V1_REGISTRATION, {})
        assert version_one.status_code == 400
        assert set(json.loads(version_one.content)) == needed

    def test_a_json_body_is_read_as_empty(self):
        form = registration_form("json_new")
        legacy = Client().post(V2_REGISTRATION, json.dumps(form), content_type=JSON)
        conforming = Client().post(REGISTRATIONS, json.dumps(form), content_type=JSON)
        assert_same_answer(legacy, conforming, 400)
        assert not User.objects.filter(username="json_new").exists()

    def test_a_taken_name_is_refused_with_suggestions(self):
        Client().post(REGISTRATIONS, registration_form("taken_name"))
        legacy = Client().post(V2_REGISTRATION, registration_form("taken_name"))
        conforming = Client().post(REGISTRATIONS, registration_form("taken_name"))
        assert_same_answer(legacy, conforming, 409, volatile={"username_suggestions"})
        body = json.loads(conforming.content)
        assert body["error_code"] == "duplicate-email-username"
        assert len(body["username_suggestions"]) == 3

    def test_a_country_the_site_does_not_know_is_refused(self):
        legacy = Client().post(V2_REGISTRATION, {**registration_form("country_old"), "country": "ZZ"})
        conforming = Client().post(REGISTRATIONS, {**registration_form("country_new"), "country": "ZZ"})
        assert_same_answer(legacy, conforming, 400)
        assert json.loads(conforming.content)["error_code"] == "invalid-country"

    def test_a_site_that_requires_another_provider_refuses_a_password(self):
        with override_settings(ENABLE_REQUIRE_THIRD_PARTY_AUTH=True):
            legacy = Client().post(V2_REGISTRATION, registration_form("provider_old"))
            conforming = Client().post(REGISTRATIONS, registration_form("provider_new"))
        assert_same_answer(legacy, conforming, 403)
        assert conforming["Content-Type"].startswith("text/html")

    def test_a_site_that_creates_no_accounts_here_refuses_the_registration(self):
        with override_settings(ALLOW_PUBLIC_ACCOUNT_CREATION=False):
            legacy = Client().post(V2_REGISTRATION, registration_form("closed_old"))
            conforming = Client().post(REGISTRATIONS, registration_form("closed_new"))
        assert_same_answer(legacy, conforming, 403)
        assert conforming.content == b"Account creation not allowed."

    @override_settings(OPEN_EDX_FILTERS_CONFIG=REFUSE_REGISTRATION)
    def test_a_step_the_deployment_adds_refuses_it_at_both(self):
        legacy = Client().post(V2_REGISTRATION, registration_form("step_old"))
        conforming = Client().post(REGISTRATIONS, registration_form("step_new"))
        assert_same_answer(legacy, conforming, 403)
        assert json.loads(conforming.content) == {
            "error_message": [{"user_message": "Registration is closed."}],
            "error_code": "registration-closed",
        }

    def test_the_methods_it_does_not_serve_are_refused_alike(self):
        # This view runs outside the transaction a request is otherwise given,
        # so a refusal marks the transaction it runs in for rollback; each
        # request is given one of its own here, as a deployment gives it one.
        with transaction.atomic():
            legacy = Client().put(V2_REGISTRATION)
        with transaction.atomic():
            conforming = Client().put(REGISTRATIONS)
        assert_same_answer(legacy, conforming, 405)
        assert json.loads(conforming.content) == {"detail": 'Method "PUT" not allowed.'}

    def test_head_is_answered_as_get_is(self):
        legacy, conforming = Client().head(V2_REGISTRATION), Client().head(REGISTRATIONS)
        assert legacy.status_code == conforming.status_code == 200
        assert conforming["Content-Type"] == legacy["Content-Type"] == JSON
        assert conforming.content == legacy.content == b""

    def test_options_differ_only_in_the_description(self):
        legacy, conforming = Client().options(V2_REGISTRATION), Client().options(REGISTRATIONS)
        assert legacy.status_code == conforming.status_code == 200
        _changed, unexpected = differences(
            json.loads(legacy.content), json.loads(conforming.content), OPTIONS_DIFFERENCES,
        )
        assert not unexpected
        assert json.loads(conforming.content)["name"] == "Registration"


@skip_unless_lms
@override_settings(CACHES=COUNTING_CACHE)
class RegistrationLimitTests(TestCase):
    """The registrations counted against an address are counted at every address registering accounts."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.allowed = limit_of(settings.REGISTRATION_RATELIMIT)

    def test_registrations_sent_to_the_older_address_count_here(self):
        assert spend(V2_REGISTRATION, {}, self.allowed) == [400] * self.allowed
        refused = Client().post(REGISTRATIONS, {})
        assert refused.status_code == 403
        assert json.loads(refused.content) == {"error_code": "forbidden-request"}
        assert Client().post(REGISTRATIONS, {}, REMOTE_ADDR="10.0.0.9").status_code == 400

    def test_registrations_sent_here_count_at_every_older_address(self):
        assert spend(REGISTRATIONS, {}, self.allowed) == [400] * self.allowed
        for address in (V1_REGISTRATION, V2_REGISTRATION, CREATE_ACCOUNT):
            refused = Client().post(address, {})
            assert refused.status_code == 403, address
            assert json.loads(refused.content) == {"error_code": "forbidden-request"}, address

    def test_reading_the_form_is_not_counted(self):
        assert [Client().get(REGISTRATIONS).status_code for _ in range(self.allowed + 2)] == [200] * (
            self.allowed + 2
        )


@skip_unless_lms
class PasswordResetFormParityTests(TestCase):
    """The password reset form is the one the older address describes."""

    def test_the_form_is_the_same_bytes(self):
        legacy, conforming = Client().get(V1_PASSWORD_RESET), Client().get(PASSWORD_RESETS)
        assert_byte_equal(legacy, conforming)
        assert cookies_set(conforming) == {"csrftoken"}
        assert json.loads(conforming.content)["submit_url"] == reverse("password_change_request")

    def test_a_signed_in_caller_reads_the_same_form(self):
        UserFactory.create(username="reset_reader", password=PASSWORD)
        reader = User.objects.get(username="reset_reader")
        assert_byte_equal(signed_in(reader).get(V1_PASSWORD_RESET), signed_in(reader).get(PASSWORD_RESETS))

    def test_head_is_answered_as_get_is(self):
        legacy, conforming = Client().head(V1_PASSWORD_RESET), Client().head(PASSWORD_RESETS)
        assert legacy.status_code == conforming.status_code == 200
        assert conforming.content == legacy.content == b""

    def test_the_methods_it_does_not_serve_are_refused_alike(self):
        assert_same_answer(Client().post(V1_PASSWORD_RESET), Client().post(PASSWORD_RESETS), 405)

    def test_options_differ_only_in_the_description(self):
        legacy, conforming = Client().options(V1_PASSWORD_RESET), Client().options(PASSWORD_RESETS)
        _changed, unexpected = differences(
            json.loads(legacy.content), json.loads(conforming.content), OPTIONS_DIFFERENCES,
        )
        assert not unexpected


@skip_unless_lms
class TokenValidationParityTests(TestCase):
    """A reset token is checked here exactly as it is at the older address."""

    def setUp(self):
        super().setUp()
        self.owner = UserFactory.create(username="token_owner", password=PASSWORD)
        self.other = UserFactory.create(username="token_other", password=PASSWORD)

    def both(self, client_factory, data, content_type=None, **extra):
        """Send one check to the older address and to this one, each from a new client."""
        sent = {"content_type": content_type} if content_type else {}
        return (
            client_factory().post(V1_TOKEN_VALIDATION, data, **sent, **extra),
            client_factory().post(TOKEN_VALIDATIONS, data, **sent, **extra),
        )

    def test_a_valid_token_is_valid(self):
        legacy, conforming = self.both(Client, {"token": reset_token(self.owner)})
        assert_same_answer(legacy, conforming, 200)
        assert json.loads(conforming.content) == {"is_valid": True}

    def test_a_token_sent_as_json_is_read(self):
        legacy, conforming = self.both(Client, json.dumps({"token": reset_token(self.owner)}), JSON)
        assert_same_answer(legacy, conforming, 200)
        assert json.loads(conforming.content) == {"is_valid": True}

    def test_a_token_that_cannot_be_read_or_is_absent_is_not_valid(self):
        for data in ({"token": "zz-zz"}, {}):
            legacy, conforming = self.both(Client, data)
            assert_same_answer(legacy, conforming, 200)
            assert json.loads(conforming.content) == {"is_valid": False}, data

    def test_a_valid_token_activates_an_inactive_account(self):
        for address, username in ((V1_TOKEN_VALIDATION, "inactive_old"), (TOKEN_VALIDATIONS, "inactive_new")):
            inactive = UserFactory.create(username=username, password=PASSWORD, is_active=False)
            response = Client().post(address, {"token": reset_token(inactive)})
            assert json.loads(response.content) == {"is_valid": True}, address
            inactive.refresh_from_db()
            assert inactive.is_active, address

    def test_a_signed_in_caller_is_told_only_about_its_own_account(self):
        # Signing in changes what a token is made from, so each token is made
        # once both sessions exist.
        sessions = (signed_in(self.owner), signed_in(self.owner))
        self.owner.refresh_from_db()
        for token_of, valid in ((self.owner, True), (self.other, False)):
            data = {"token": reset_token(token_of)}
            legacy = sessions[0].post(V1_TOKEN_VALIDATION, data)
            conforming = sessions[1].post(TOKEN_VALIDATIONS, data)
            assert_same_answer(legacy, conforming, 200)
            assert json.loads(conforming.content) == {"is_valid": valid}, token_of.username

    def test_a_session_caller_sending_no_csrf_token_is_refused(self):
        legacy, conforming = self.both(
            lambda: signed_in(self.owner, enforce_csrf_checks=True), {"token": reset_token(self.owner)},
        )
        assert_same_answer(legacy, conforming, 403)
        assert json.loads(conforming.content) == {"detail": "CSRF Failed: CSRF cookie not set."}

    def test_a_caller_presenting_a_token_of_its_own_needs_no_csrf_token(self):
        jwt = create_jwt_for_user(self.owner)
        own = self.both(
            lambda: Client(enforce_csrf_checks=True),
            {"token": reset_token(self.owner)},
            HTTP_AUTHORIZATION=f"JWT {jwt}",
        )
        assert_same_answer(*own, 200)
        assert json.loads(own[1].content) == {"is_valid": True}
        other = self.both(Client, {"token": reset_token(self.other)}, HTTP_AUTHORIZATION=f"JWT {jwt}")
        assert json.loads(other[1].content) == {"is_valid": False}

    def test_credentials_that_cannot_be_read_are_refused(self):
        legacy, conforming = self.both(Client, {"token": "zz"}, HTTP_AUTHORIZATION="JWT not-a-jwt")
        assert_same_answer(legacy, conforming, 401)
        assert json.loads(conforming.content) == {"detail": "Invalid token."}

    def test_a_body_that_cannot_be_parsed_is_refused(self):
        legacy, conforming = self.both(Client, "{not json", JSON)
        assert_same_answer(legacy, conforming, 400)
        assert json.loads(conforming.content)["detail"].startswith("JSON parse error")

    def test_the_methods_it_does_not_serve_are_refused_alike(self):
        assert_same_answer(Client().get(V1_TOKEN_VALIDATION), Client().get(TOKEN_VALIDATIONS), 405)


@skip_unless_lms
@override_settings(CACHES=COUNTING_CACHE)
class TokenValidationLimitTests(TestCase):
    """The checks counted against a caller are counted at both addresses."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.allowed = limit_of(settings.RESET_PASSWORD_TOKEN_VALIDATE_API_RATELIMIT)
        self.owner = UserFactory.create(username="limited_owner", password=PASSWORD)

    def test_checks_sent_to_the_older_address_count_here(self):
        assert spend(V1_TOKEN_VALIDATION, {"token": "zz"}, self.allowed) == [200] * self.allowed
        refused = Client().post(TOKEN_VALIDATIONS, {"token": "zz"})
        assert refused.status_code == 429
        assert json.loads(refused.content)["detail"].startswith("Request was throttled.")

    def test_checks_sent_here_count_at_the_older_address(self):
        assert spend(TOKEN_VALIDATIONS, {"token": "zz"}, self.allowed) == [200] * self.allowed
        assert Client().post(V1_TOKEN_VALIDATION, {"token": "zz"}).status_code == 429

    def test_a_signed_in_caller_is_not_counted_with_the_others(self):
        spend(TOKEN_VALIDATIONS, {"token": "zz"}, self.allowed + 1)
        session = signed_in(self.owner)
        assert [session.post(TOKEN_VALIDATIONS, {"token": "zz"}).status_code for _ in range(3)] == [200] * 3


@skip_unless_lms
class RegistrationValidationParityTests(TestCase):
    """Candidate registration values are judged here as they are at the older address."""

    def both(self, client_factory, data, content_type=JSON):
        """Send one check to the older address and to this one, each from a new client."""
        body = json.dumps(data) if content_type == JSON and not isinstance(data, str) else data
        return (
            client_factory().post(V1_REGISTRATION_VALIDATION, body, content_type=content_type),
            client_factory().post(REGISTRATION_VALIDATIONS, body, content_type=content_type),
        )

    def test_a_taken_username_is_judged_with_suggestions(self):
        UserFactory.create(username="judged_name", password=PASSWORD)
        legacy, conforming = self.both(Client, {"username": "judged_name", "email": "free@example.com"})
        assert_same_answer(legacy, conforming, 200, volatile={"username_suggestions"})
        body = json.loads(conforming.content)
        assert body["validation_decisions"] == {
            "username": "It looks like this username is already taken", "email": "",
        }
        assert len(body["username_suggestions"]) == 3

    def test_one_field_named_is_the_only_one_judged(self):
        legacy, conforming = self.both(
            Client, {"form_field_key": "password", "password": "short", "username": "x"},
        )
        assert_same_answer(legacy, conforming, 200)
        assert set(json.loads(conforming.content)["validation_decisions"]) == {"password"}

    def test_values_sent_as_a_form_are_read(self):
        legacy = Client().post(V1_REGISTRATION_VALIDATION, {"username": "free_name"})
        conforming = Client().post(REGISTRATION_VALIDATIONS, {"username": "free_name"})
        assert_same_answer(legacy, conforming, 200)
        assert json.loads(conforming.content) == {"validation_decisions": {"username": ""}}

    def test_nothing_sent_is_nothing_judged(self):
        legacy, conforming = self.both(Client, {})
        assert_same_answer(legacy, conforming, 200)
        assert json.loads(conforming.content) == {"validation_decisions": {}}

    def test_no_csrf_token_is_asked_for_of_any_caller(self):
        UserFactory.create(username="validating_caller", password=PASSWORD)
        caller = User.objects.get(username="validating_caller")
        for client_factory in (
            lambda: Client(enforce_csrf_checks=True),
            lambda: signed_in(caller, enforce_csrf_checks=True),
        ):
            legacy, conforming = self.both(client_factory, {"username": "x"})
            assert_same_answer(legacy, conforming, 200)

    def test_a_body_that_cannot_be_parsed_is_refused(self):
        legacy, conforming = self.both(Client, "{not json")
        assert_same_answer(legacy, conforming, 400)
        assert json.loads(conforming.content)["detail"].startswith("JSON parse error")

    def test_the_methods_it_does_not_serve_are_refused_alike(self):
        legacy = Client().get(V1_REGISTRATION_VALIDATION)
        conforming = Client().get(REGISTRATION_VALIDATIONS)
        assert_same_answer(legacy, conforming, 405)


@skip_unless_lms
@override_settings(CACHES=COUNTING_CACHE)
class RegistrationValidationLimitTests(TestCase):
    """The checks counted against an address are counted at both."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.allowed = limit_of(settings.REGISTRATION_VALIDATION_RATELIMIT)

    def test_checks_sent_to_the_older_address_count_here(self):
        assert spend(V1_REGISTRATION_VALIDATION, {}, self.allowed) == [200] * self.allowed
        refused = Client().post(REGISTRATION_VALIDATIONS, {})
        assert refused.status_code == 403
        assert json.loads(refused.content) == {"detail": "You do not have permission to perform this action."}

    def test_checks_sent_here_count_at_the_older_address(self):
        assert spend(REGISTRATION_VALIDATIONS, {}, self.allowed) == [200] * self.allowed
        assert Client().post(V1_REGISTRATION_VALIDATION, {}).status_code == 403


@skip_unless_lms
class LoginParityTests(TestCase):
    """Signing in here is signing in at the version 2 address it replaces."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(username="signing_in", email="signing_in@example.com", password=PASSWORD)

    def both(self, client_factory, data, **extra):
        """Send one sign-in to the version 2 address and to this one, each from a new client."""
        return (
            client_factory().post(V2_LOGIN_SESSION, data, **extra),
            client_factory().post(LOGIN_SESSIONS, data, **extra),
        )

    def test_the_form_is_the_same_bytes_as_at_either_older_version(self):
        conforming = Client().get(LOGIN_SESSIONS)
        for address in (V1_LOGIN_SESSION, V2_LOGIN_SESSION):
            assert_byte_equal(Client().get(address), conforming)
        assert {"csrftoken", "sessionid"} <= cookies_set(conforming)

    def test_head_is_answered_as_get_is(self):
        legacy, conforming = Client().head(V2_LOGIN_SESSION), Client().head(LOGIN_SESSIONS)
        assert legacy.status_code == conforming.status_code == 200
        assert conforming.content == legacy.content == b""

    def test_options_differ_only_in_the_description(self):
        legacy, conforming = Client().options(V2_LOGIN_SESSION), Client().options(LOGIN_SESSIONS)
        _changed, unexpected = differences(
            json.loads(legacy.content), json.loads(conforming.content), OPTIONS_DIFFERENCES,
        )
        assert not unexpected

    def test_an_account_is_named_by_its_username_or_its_email(self):
        for named in ("signing_in", "signing_in@example.com"):
            legacy, conforming = self.both(Client, {"email_or_username": named, "password": PASSWORD})
            assert_same_answer(legacy, conforming, 200, cookies=SIGNED_IN_COOKIES)
            assert json.loads(conforming.content) == {"success": True, "redirect_url": None}, named

    def test_the_version_one_field_name_is_not_read(self):
        """The address answers as version 2 does, which reads the account from another field."""
        legacy, conforming = self.both(Client, {"email": "signing_in@example.com", "password": PASSWORD})
        assert_same_answer(legacy, conforming, 400)
        assert json.loads(conforming.content) == {"success": False, "value": MISSING_LOGIN_FIELDS, "email": None}
        version_one = Client().post(V1_LOGIN_SESSION, {"email": "signing_in@example.com", "password": PASSWORD})
        assert version_one.status_code == 200

    def test_a_wrong_password_is_refused(self):
        legacy, conforming = self.both(Client, {"email_or_username": "signing_in", "password": "wrong"})
        assert_same_answer(legacy, conforming, 400)
        body = json.loads(conforming.content)
        assert body["error_code"] == "incorrect-email-or-password"
        assert body["context"] == {"failure_count": 0}

    def test_an_inactive_account_is_refused(self):
        inactive = UserFactory.create(username="not_active", password=PASSWORD, is_active=False)
        Registration().register(inactive)
        legacy, conforming = self.both(Client, {"email_or_username": "not_active", "password": PASSWORD})
        assert_same_answer(legacy, conforming, 400)
        assert json.loads(conforming.content)["error_code"] == "inactive-user"

    def test_a_signed_in_caller_ends_up_signed_in_as_the_account_it_named(self):
        other = UserFactory.create(username="signed_in_first", password=PASSWORD)
        clients = (signed_in(other), signed_in(other))
        legacy = clients[0].post(V2_LOGIN_SESSION, {"email_or_username": "signing_in", "password": PASSWORD})
        conforming = clients[1].post(LOGIN_SESSIONS, {"email_or_username": "signing_in", "password": PASSWORD})
        assert legacy.status_code == conforming.status_code == 200
        assert int(clients[1].session["_auth_user_id"]) == self.user.id
        assert int(clients[0].session["_auth_user_id"]) == self.user.id

    def test_a_sign_in_without_a_csrf_token_is_refused(self):
        legacy, conforming = self.both(
            lambda: Client(enforce_csrf_checks=True), {"email_or_username": "signing_in", "password": PASSWORD},
        )
        assert_same_answer(legacy, conforming, 403)
        assert conforming["Content-Type"].startswith("text/html")

    def test_the_token_the_form_sets_lets_the_caller_sign_in(self):
        for form_address, address in ((V2_LOGIN_SESSION, V2_LOGIN_SESSION), (LOGIN_SESSIONS, LOGIN_SESSIONS)):
            client = Client(enforce_csrf_checks=True)
            client.get(form_address)
            response = client.post(
                address,
                {"email_or_username": "signing_in", "password": PASSWORD},
                HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
            )
            assert response.status_code == 200, (address, response.content)
            assert json.loads(response.content) == {"success": True, "redirect_url": None}

    def test_credentials_in_a_header_are_not_read(self):
        legacy, conforming = self.both(
            Client, {"email_or_username": "signing_in", "password": "wrong"}, HTTP_AUTHORIZATION="JWT not-a-jwt",
        )
        assert_same_answer(legacy, conforming, 400)

    def test_a_site_that_requires_another_provider_refuses_a_password(self):
        with override_settings(ENABLE_REQUIRE_THIRD_PARTY_AUTH=True):
            legacy, conforming = self.both(Client, {"email_or_username": "signing_in", "password": PASSWORD})
        assert_same_answer(legacy, conforming, 403)
        assert conforming["Content-Type"].startswith("text/html")

    def test_a_provider_account_linked_to_no_account_here_is_refused(self):
        running = {"backend": "oa2-provider", "kwargs": {"username": "elsewhere", "uid": "elsewhere"}}
        with mock.patch.object(login.pipeline, "running", return_value=True), \
                mock.patch.object(login.pipeline, "get", return_value=running), \
                mock.patch.object(
                    login.provider.Registry, "get_from_pipeline", return_value=SimpleNamespace(name="Provider"),
                ), \
                mock.patch.object(login.pipeline, "get_authenticated_user", side_effect=User.DoesNotExist):
            legacy, conforming = self.both(Client, {})
        assert_same_answer(legacy, conforming, 403)
        body = json.loads(conforming.content)
        assert body["success"] is False
        assert body["error_code"] == "third-party-auth-with-no-linked-account"

    @override_settings(OPEN_EDX_FILTERS_CONFIG=REFUSE_LOGIN)
    def test_a_step_the_deployment_adds_refuses_it_at_both(self):
        legacy, conforming = self.both(Client, {"email_or_username": "signing_in", "password": PASSWORD})
        assert_same_answer(legacy, conforming, 400)
        assert json.loads(conforming.content) == {
            "success": False,
            "value": "Sign-in is closed.",
            "redirect_url": "/closed",
            "error_code": "sign-in-closed",
            "context": {"reason": "closed"},
            "email": "signing_in@example.com",
        }

    def test_the_methods_it_does_not_serve_are_refused_alike(self):
        assert_same_answer(Client().put(V2_LOGIN_SESSION), Client().put(LOGIN_SESSIONS), 405)


@skip_unless_lms
@override_settings(CACHES=COUNTING_CACHE)
class LoginLimitTests(TestCase):
    """The failed sign-ins counted against a caller are counted wherever it signs in."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.allowed = limit_of(settings.LOGISTRATION_RATELIMIT_RATE)
        self.user = UserFactory.create(username="counted", email="counted@example.com", password=PASSWORD)

    def assert_refused_for_a_while(self, response):
        """The refusal is asserted on its code and message alone."""
        assert response.status_code == 400
        body = json.loads(response.content)
        assert body["success"] is False
        assert body["value"] == TOO_MANY_LOGINS

    def test_failures_at_the_older_address_count_here(self):
        failures = spend(V2_LOGIN_SESSION, {"email_or_username": "counted", "password": "wrong"}, self.allowed)
        assert failures == [400] * self.allowed
        self.assert_refused_for_a_while(
            Client().post(LOGIN_SESSIONS, {"email_or_username": "counted", "password": PASSWORD}),
        )

    def test_failures_here_count_at_every_older_address(self):
        failures = spend(LOGIN_SESSIONS, {"email_or_username": "counted", "password": "wrong"}, self.allowed)
        assert failures == [400] * self.allowed
        for address in (V1_LOGIN_SESSION, LOGIN_AJAX):
            self.assert_refused_for_a_while(
                Client().post(address, {"email": "counted@example.com", "password": PASSWORD}),
            )


#: A statement that changes rows, told by its first keyword whatever follows
#: it, so ``INSERT OR IGNORE INTO`` and ``INSERT IGNORE INTO`` (what a bulk
#: create that ignores conflicts sends) are writes as plain ``INSERT INTO`` is.
WRITE_STATEMENT = re.compile(
    r"""\s*(?P<statement>INSERT|UPDATE|DELETE|REPLACE)\b
        (?:\s+(?:OR\s+\w+|IGNORE|LOW_PRIORITY|DELAYED|HIGH_PRIORITY|QUICK|ONLY|INTO|FROM)\b)*
        \s*[`"]?(?P<table>\w+)?""",
    re.IGNORECASE | re.VERBOSE,
)


def written_tables(captured):
    """
    Return the (statement, table) of every write a captured block sent to the database.

    A write whose table cannot be read is still returned, with the whole
    statement in place of the table, so it is never left out.
    """
    writes = []
    for query in captured:
        match = WRITE_STATEMENT.match(query["sql"])
        if match:
            writes.append((match.group("statement").upper(), match.group("table") or query["sql"]))
    return writes


@skip_unless_lms
def test_a_write_is_told_by_its_first_keyword_whatever_follows_it():
    captured = [{"sql": sql} for sql in (
        'INSERT INTO "django_site" ("domain", "name") VALUES (\'a\', \'a\')',
        'INSERT OR IGNORE INTO "django_site" ("domain", "name") VALUES (\'b\', \'b\')',
        "INSERT IGNORE INTO `django_site` (`domain`, `name`) VALUES ('c', 'c')",
        "REPLACE INTO `django_site` (`domain`, `name`) VALUES ('d', 'd')",
        'UPDATE "django_session" SET "expire_date" = 1',
        'UPDATE OR REPLACE "django_session" SET "expire_date" = 1',
        'DELETE FROM "django_session" WHERE 1',
        "DELETE LOW_PRIORITY QUICK IGNORE FROM `django_session` WHERE 1",
        "  insert into auth_user (username) values ('e')",
        "DELETE FROM quick_links WHERE 1",
        'SELECT "id" FROM "django_site" FOR UPDATE',
        'SAVEPOINT "s1"',
        'RELEASE SAVEPOINT "s1"',
        "INSERT (",
    )]
    assert written_tables(captured) == [
        ("INSERT", "django_site"),
        ("INSERT", "django_site"),
        ("INSERT", "django_site"),
        ("REPLACE", "django_site"),
        ("UPDATE", "django_session"),
        ("UPDATE", "django_session"),
        ("DELETE", "django_session"),
        ("DELETE", "django_session"),
        ("INSERT", "auth_user"),
        ("DELETE", "quick_links"),
        ("INSERT", "INSERT ("),
    ]


#: What every request sends whatever it reads: the request starting and
#: finishing, the monitoring and current-user middleware around it, and a
#: model instance being built.
SIGNALS_OF_EVERY_REQUEST = (
    request_started,
    request_finished,
    monitoring_support_process_request,
    monitoring_support_process_response,
    monitoring_support_process_exception,
    current_user_setter,
    pre_init,
    post_init,
)


@contextlib.contextmanager
def signals_sent():
    """
    Record each Django signal and Open edX event sent while this block runs.

    What every request sends is left out, and so is the saving of the
    caller's session, which the capture of writes already accounts for. An
    event is recorded as it is sent, whether or not events are turned on, and
    an event that is turned on is recorded again by the signal it is sent as.
    """
    sent = []

    def recording(send):
        """Return a replacement for one of the ways a signal or an event is sent."""
        def spy(signal, *args, **kwargs):
            sender = kwargs.get("sender", args[0] if args else None)
            saves_the_session = signal in (pre_save, post_save) and sender is Session
            if signal not in SIGNALS_OF_EVERY_REQUEST and not saves_the_session:
                sent.append((send.__name__, getattr(signal, "event_type", signal), sender))
            return send(signal, *args, **kwargs)
        return spy

    with mock.patch.object(Signal, "send", recording(Signal.send)), \
            mock.patch.object(Signal, "send_robust", recording(Signal.send_robust)), \
            mock.patch.object(OpenEdxPublicSignal, "send_event", recording(OpenEdxPublicSignal.send_event)):
        yield sent


@skip_unless_lms
def test_each_way_of_sending_is_recorded_even_with_events_turned_off():
    signal = Signal()
    with mock.patch.object(STUDENT_REGISTRATION_COMPLETED, "_allow_events", False), signals_sent() as sent:
        signal.send(sender=User)
        signal.send_robust(User)
        STUDENT_REGISTRATION_COMPLETED.send_event()
    assert sent == [
        ("send", signal, User),
        ("send_robust", signal, User),
        ("send_event", "org.openedx.learning.student.registration.completed.v1", None),
    ]


class _FormReadTests:
    """Reading a form writes nothing but the caller's session, and sends nothing, at either address."""

    #: The tables a read may write to in this configuration, by the address it reads.
    EXPECTED_WRITES = {}

    PAIRS = (
        (V2_REGISTRATION, REGISTRATIONS),
        (V1_PASSWORD_RESET, PASSWORD_RESETS),
        (V2_LOGIN_SESSION, LOGIN_SESSIONS),
    )

    def captured(self, method, address):
        """Return the writes one request to ``address`` sends to the database."""
        with CaptureQueriesContext(connection) as queries:
            response = getattr(Client(), method)(address)
        assert response.status_code == 200, (method, address)
        return written_tables(queries.captured_queries)

    def signalled(self, method, address):
        """Return the signals and events one request to ``address`` sends."""
        with signals_sent() as sent:
            response = getattr(Client(), method)(address)
        assert response.status_code == 200, (method, address)
        return sent

    def test_a_read_writes_only_the_session_and_the_same_way_at_both_addresses(self):
        for method in ("get", "head"):
            for legacy, conforming in self.PAIRS:
                legacy_writes, conforming_writes = self.captured(method, legacy), self.captured(method, conforming)
                assert conforming_writes == legacy_writes, (method, conforming)
                assert {table for _statement, table in conforming_writes} == self.EXPECTED_WRITES[conforming], (
                    method, conforming,
                )

    def test_a_read_sends_no_signal_or_event_at_either_address(self):
        for method in ("get", "head"):
            for legacy, conforming in self.PAIRS:
                legacy_sent, conforming_sent = self.signalled(method, legacy), self.signalled(method, conforming)
                assert conforming_sent == legacy_sent, (method, conforming)
                assert not conforming_sent, (method, conforming)


@skip_unless_lms
@override_settings(ENABLE_THIRD_PARTY_AUTH=True)
class FormReadWithThirdPartyAuthTests(_FormReadTests, TestCase):
    """With sign-in through other providers on, the forms that offer it note it in the session."""

    EXPECTED_WRITES = {
        REGISTRATIONS: {"django_session"},
        PASSWORD_RESETS: set(),
        LOGIN_SESSIONS: {"django_session"},
    }


@skip_unless_lms
@override_settings(ENABLE_THIRD_PARTY_AUTH=False)
class FormReadWithoutThirdPartyAuthTests(_FormReadTests, TestCase):
    """With it off, reading a form writes nothing at all."""

    EXPECTED_WRITES = {REGISTRATIONS: set(), PASSWORD_RESETS: set(), LOGIN_SESSIONS: set()}


#: What a sign-in through another provider that is under way holds: the
#: provider's backend and what the provider has said about the person.
RUNNING_PIPELINE = {
    "backend": "google-oauth2",
    "kwargs": {
        "username": "elsewhere",
        "details": {"fullname": "Else Where", "email": "elsewhere@example.com"},
    },
}


class _FormReadDuringThirdPartySignIn(_FormReadTests):
    """
    Reading a form while a sign-in through another provider is under way writes nothing and sends nothing.

    The sign-in under way and its provider are handed to the forms in place
    of the ones the session would point to, so the session is not read for
    them and nothing is written to it.
    """

    EXPECTED_WRITES = {REGISTRATIONS: set(), PASSWORD_RESETS: set(), LOGIN_SESSIONS: set()}

    #: How the registration form shows the fields a provider can fill in: type and default.
    FIELDS_SHOWN = {}

    def provider(self):
        """Return the provider the sign-in under way runs through, as this site knows it."""
        return OAuth2ProviderConfig(name="Provider", backend_name="google-oauth2", sync_learner_profile_data=True)

    def setUp(self):
        super().setUp()
        reading = mock.patch.object(login.pipeline, "get", return_value=RUNNING_PIPELINE)
        looking_up = mock.patch.object(login.provider.Registry, "get_from_pipeline", return_value=self.provider())
        reading.start()
        self.addCleanup(reading.stop)
        self.found = looking_up.start()
        self.addCleanup(looking_up.stop)

    def test_each_form_is_built_for_the_sign_in_under_way(self):
        for legacy, conforming in ((V2_REGISTRATION, REGISTRATIONS), (V2_LOGIN_SESSION, LOGIN_SESSIONS)):
            self.found.reset_mock()
            assert_byte_equal(Client().get(legacy), Client().get(conforming))
            assert self.found.call_args_list == [mock.call(RUNNING_PIPELINE)] * 2, conforming
        fields = json.loads(Client().get(REGISTRATIONS).content)["fields"]
        shown = {field["name"]: (field["type"], field["defaultValue"]) for field in fields}
        assert {name: shown.get(name) for name in self.FIELDS_SHOWN} == self.FIELDS_SHOWN


@skip_unless_lms
@override_settings(ENABLE_THIRD_PARTY_AUTH=True)
class FormReadDuringThirdPartySignInTests(_FormReadDuringThirdPartySignIn, TestCase):
    """The provider is known and fills the profile in, so the form hides what it filled in."""

    FIELDS_SHOWN = {
        "username": ("hidden", "elsewhere"),
        "name": ("hidden", "Else Where"),
        "email": ("hidden", "elsewhere@example.com"),
        "password": ("hidden", ""),
        "social_auth_provider": ("hidden", "Provider"),
    }


@skip_unless_lms
@override_settings(ENABLE_THIRD_PARTY_AUTH=True)
class FormReadDuringThirdPartySignInWithoutProfileFillingTests(_FormReadDuringThirdPartySignIn, TestCase):
    """The provider is known and does not fill the profile in, so the form offers what it said, to change."""

    FIELDS_SHOWN = {
        "username": ("text", "elsewhere"),
        "name": ("text", "Else Where"),
        "email": ("email", "elsewhere@example.com"),
        "password": ("hidden", ""),
        "social_auth_provider": ("hidden", "Provider"),
    }

    def provider(self):
        return OAuth2ProviderConfig(name="Provider", backend_name="google-oauth2", sync_learner_profile_data=False)


@skip_unless_lms
@override_settings(ENABLE_THIRD_PARTY_AUTH=True)
class FormReadDuringThirdPartySignInThroughAnUnknownProviderTests(_FormReadDuringThirdPartySignIn, TestCase):
    """The provider is not one this site knows, so the form is the stock one."""

    FIELDS_SHOWN = {
        "username": ("text", ""),
        "name": ("text", ""),
        "email": ("email", ""),
        "password": ("password", ""),
        "social_auth_provider": None,
    }

    def provider(self):
        return None


def query_count(send):
    """Return the response ``send`` produces and the number of queries it took."""
    with CaptureQueriesContext(connection) as queries:
        response = send()
    return response, len(queries.captured_queries)


@pytest.mark.django_db
@skip_unless_lms
@pytest.mark.parametrize(("legacy", "conforming"), [
    (V2_REGISTRATION, REGISTRATIONS),
    (V1_PASSWORD_RESET, PASSWORD_RESETS),
    (V2_LOGIN_SESSION, LOGIN_SESSIONS),
])
def test_reading_a_form_takes_as_many_queries_at_either_address(django_assert_num_queries, legacy, conforming):
    Client().get(legacy)
    Client().get(conforming)
    _response, expected = query_count(lambda: Client().get(legacy))
    with django_assert_num_queries(expected):
        assert Client().get(conforming).status_code == 200


@pytest.mark.django_db
@skip_unless_lms
def test_a_registration_takes_as_many_queries_at_either_address(django_assert_num_queries):
    Client().post(V2_REGISTRATION, registration_form("warm_old"))
    Client().post(REGISTRATIONS, registration_form("warm_new"))
    response, expected = query_count(lambda: Client().post(V2_REGISTRATION, registration_form("count_old")))
    assert response.status_code == 200
    with django_assert_num_queries(expected):
        assert Client().post(REGISTRATIONS, registration_form("count_new")).status_code == 200


@pytest.mark.django_db
@skip_unless_lms
@pytest.mark.parametrize(("password", "status"), [(PASSWORD, 200), ("wrong", 400)])
def test_a_sign_in_takes_as_many_queries_at_either_address(django_assert_num_queries, password, status):
    UserFactory.create(username="query_counted", password=PASSWORD)
    data = {"email_or_username": "query_counted", "password": password}
    Client().post(V2_LOGIN_SESSION, data)
    Client().post(LOGIN_SESSIONS, data)
    response, expected = query_count(lambda: Client().post(V2_LOGIN_SESSION, data))
    assert response.status_code == status
    with django_assert_num_queries(expected):
        assert Client().post(LOGIN_SESSIONS, data).status_code == status


@pytest.mark.django_db
@skip_unless_lms
@pytest.mark.parametrize(("legacy", "conforming", "data"), [
    (V1_TOKEN_VALIDATION, TOKEN_VALIDATIONS, {"token": "zz-zz"}),
    (V1_REGISTRATION_VALIDATION, REGISTRATION_VALIDATIONS, {"username": "query_free", "email": "q@example.com"}),
])
def test_a_check_takes_as_many_queries_at_either_address(django_assert_num_queries, legacy, conforming, data):
    Client().post(legacy, data)
    Client().post(conforming, data)
    response, expected = query_count(lambda: Client().post(legacy, data))
    assert response.status_code == 200
    with django_assert_num_queries(expected):
        assert Client().post(conforming, data).status_code == 200


#: Each operation this version adds for the authentication app, with the name
#: it is published under in the document trimming the area off each path and
#: in the one trimming nothing, and the codes it is published with.
CONFORMING_OPERATIONS = {
    (REGISTRATIONS, "get"): ("v2_registrations_retrieve", "api_user_v2_registrations_retrieve", ["200", "406"]),
    (REGISTRATIONS, "post"): (
        "v2_registrations_create", "api_user_v2_registrations_create", ["200", "400", "403", "406", "409", "415"],
    ),
    (PASSWORD_RESETS, "get"): (
        "v2_password_resets_retrieve", "api_user_v2_password_resets_retrieve", ["200", "406"],
    ),
    (TOKEN_VALIDATIONS, "post"): (
        "v2_password_reset_token_validations_create",
        "api_user_v2_password_reset_token_validations_create",
        ["200", "400", "401", "403", "406", "415", "429"],
    ),
    (REGISTRATION_VALIDATIONS, "post"): (
        "v2_registration_validations_create",
        "api_user_v2_registration_validations_create",
        ["200", "400", "403", "406", "415"],
    ),
    (LOGIN_SESSIONS, "get"): ("v2_login_sessions_retrieve", "api_user_v2_login_sessions_retrieve", ["200", "406"]),
    (LOGIN_SESSIONS, "post"): (
        "v2_login_sessions_create", "api_user_v2_login_sessions_create", ["200", "400", "403", "406", "415"],
    ),
}

#: The media types each body is published in, and whether it is published as one the operation needs.
CONFORMING_REQUESTS = {
    (REGISTRATIONS, "post"): ([FORM, MULTIPART], True),
    (TOKEN_VALIDATIONS, "post"): ([JSON, FORM, MULTIPART], False),
    (REGISTRATION_VALIDATIONS, "post"): ([JSON, FORM, MULTIPART], False),
    (LOGIN_SESSIONS, "post"): ([FORM, MULTIPART], True),
}


def published_operations(document, keys):
    """Return the operation each (path, method) in ``keys`` is published as."""
    return {key: document["paths"][key[0]][key[1]] for key in keys}


def schema_nodes(node):
    """Yield every mapping inside a published node."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from schema_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from schema_nodes(value)


@skip_unless_lms
class ConformingDocumentTests(TestCase):
    """What the two documents of this service publish about each address."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.documents = {"narrow": narrow_document(), "service-wide": service_wide_document()}

    def operations(self, which):
        return published_operations(self.documents[which], CONFORMING_OPERATIONS)

    def test_each_operation_is_published_under_its_name_in_both_documents(self):
        for which, position in (("narrow", 0), ("service-wide", 1)):
            published = {key: operation["operationId"] for key, operation in self.operations(which).items()}
            assert published == {key: names[position] for key, names in CONFORMING_OPERATIONS.items()}, which

    def test_each_operation_is_grouped_with_its_area_and_none_is_marked_for_the_client_library(self):
        expected = {"narrow": ["user"], "service-wide": ["api"]}
        for which, tags in expected.items():
            published = {key: operation["tags"] for key, operation in self.operations(which).items()}
            assert published == dict.fromkeys(CONFORMING_OPERATIONS, tags), which

    def test_no_operation_is_marked_superseded(self):
        for which in self.documents:
            marked = {key: operation.get("deprecated", False) for key, operation in self.operations(which).items()}
            assert marked == dict.fromkeys(CONFORMING_OPERATIONS, False), which

    def test_each_operation_publishes_the_codes_it_answers_with(self):
        for which in self.documents:
            published = {key: sorted(operation["responses"]) for key, operation in self.operations(which).items()}
            assert published == {key: codes for key, (_n, _s, codes) in CONFORMING_OPERATIONS.items()}, which

    def test_each_body_is_published_in_the_forms_it_is_read_in(self):
        for which in self.documents:
            published = {
                key: (sorted(operation["requestBody"]["content"]), operation["requestBody"].get("required", False))
                for key, operation in self.operations(which).items()
                if key in CONFORMING_REQUESTS
            }
            expected = {key: (sorted(types), needed) for key, (types, needed) in CONFORMING_REQUESTS.items()}
            assert published == expected, which
            readers = {key for key, operation in self.operations(which).items() if "requestBody" in operation}
            assert readers == set(CONFORMING_REQUESTS), which

    def test_signing_in_publishes_the_fields_version_two_reads(self):
        for which in self.documents:
            body = self.documents[which]["paths"][LOGIN_SESSIONS]["post"]["requestBody"]["content"]
            for media_type in (FORM, MULTIPART):
                shape = body[media_type]["schema"]
                assert sorted(shape["properties"]) == ["email_or_username", "password"], which
                assert shape["required"] == ["email_or_username", "password"], which

    def test_signing_in_publishes_the_security_the_older_sign_in_publishes(self):
        """
        A client generated from either document treats the two sign-in addresses alike.

        Both need the CSRF token, which the older one publishes as a scheme of
        its own; a document declaring that scheme says how the token is sent.
        No other address here needs anything.
        """
        older = self.documents["service-wide"]["paths"][LOGIN_SESSION_KEY]["post"]["security"]
        assert older == [{"csrf": []}]
        expected = {**dict.fromkeys(CONFORMING_OPERATIONS, [{}]), (LOGIN_SESSIONS, "post"): older}
        for which in self.documents:
            security = {key: operation.get("security") for key, operation in self.operations(which).items()}
            assert security == expected, which

    def test_no_operation_publishes_a_choice_list(self):
        for which in self.documents:
            for key, operation in self.operations(which).items():
                assert not any("enum" in node for node in schema_nodes(operation)), (which, key)

    def test_the_older_sign_in_still_publishes_what_it_always_has(self):
        """Describing the sign-in here leaves the description of the older address untouched."""
        operation = self.documents["service-wide"]["paths"]["/api/user/{api_version}/account/login_session/"]["post"]
        for media_type in operation["requestBody"]["content"].values():
            assert media_type["schema"] == {"$ref": "#/components/schemas/LoginUserRequest"}
        assert {code: response["content"][JSON]["schema"] for code, response in operation["responses"].items()} == (
            dict.fromkeys(["200", "400", "403"], {"$ref": "#/components/schemas/LoginUserResponse"})
        )
        assert operation["security"] == [{"csrf": []}]


LOGIN_SESSION_KEY = "/api/user/{api_version}/account/login_session/"

#: Each operation the addresses above supersede, with the operation replacing
#: it and the name it is published under in the document trimming the area off
#: each path, where that document publishes it, and in the one trimming nothing.
SUPERSEDED_OPERATIONS = {
    (V1_REGISTRATION, "get"): (
        (REGISTRATIONS, "get"), "v1_account_registration_retrieve", "api_user_v1_account_registration_retrieve",
    ),
    (V1_REGISTRATION, "post"): (
        (REGISTRATIONS, "post"), "v1_account_registration_create", "api_user_v1_account_registration_create",
    ),
    (V2_REGISTRATION, "get"): (
        (REGISTRATIONS, "get"), "v2_account_registration_retrieve", "api_user_v2_account_registration_retrieve",
    ),
    (V2_REGISTRATION, "post"): (
        (REGISTRATIONS, "post"), "v2_account_registration_create", "api_user_v2_account_registration_create",
    ),
    (V1_PASSWORD_RESET, "get"): (
        (PASSWORD_RESETS, "get"),
        "v1_account_password_reset_retrieve",
        "api_user_v1_account_password_reset_retrieve",
    ),
    (V1_TOKEN_VALIDATION, "post"): (
        (TOKEN_VALIDATIONS, "post"),
        "v1_account_password_reset_token_validate_create",
        "api_user_v1_account_password_reset_token_validate_create",
    ),
    (V1_REGISTRATION_VALIDATION, "post"): (
        (REGISTRATION_VALIDATIONS, "post"),
        "v1_validation_registration_create",
        "api_user_v1_validation_registration_create",
    ),
    (LOGIN_SESSION_KEY, "get"): (
        (LOGIN_SESSIONS, "get"), None, "api_user_account_login_session_retrieve",
    ),
    (LOGIN_SESSION_KEY, "post"): (
        (LOGIN_SESSIONS, "post"), None, "api_user_account_login_session_create",
    ),
}

#: The one superseded operation whose handler is described where it is
#: defined; it keeps that description rather than taking its replacement's.
SIGN_IN_BY_POSTING = (LOGIN_SESSION_KEY, "post")

#: What each document publishes of the operations above. The narrow document
#: admits no address that takes its version as a parameter.
PUBLISHED_SUPERSEDED_OPERATIONS = {
    "narrow": {key for key in SUPERSEDED_OPERATIONS if key[0] != LOGIN_SESSION_KEY},
    "service-wide": set(SUPERSEDED_OPERATIONS),
}


@skip_unless_lms
class SupersededDocumentTests(TestCase):
    """What the two documents of this service publish about each address these replace."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.documents = {"narrow": narrow_document(), "service-wide": service_wide_document()}

    def operations(self, which):
        return published_operations(self.documents[which], PUBLISHED_SUPERSEDED_OPERATIONS[which])

    def test_each_operation_is_marked_superseded_in_both_documents(self):
        for which in self.documents:
            marked = {key: operation.get("deprecated") for key, operation in self.operations(which).items()}
            assert marked == dict.fromkeys(PUBLISHED_SUPERSEDED_OPERATIONS[which], True), which

    def test_each_operation_keeps_the_name_it_is_published_under(self):
        for which, position in (("narrow", 1), ("service-wide", 2)):
            published = {key: operation["operationId"] for key, operation in self.operations(which).items()}
            assert published == {
                key: names[position] for key, names in SUPERSEDED_OPERATIONS.items()
                if key in PUBLISHED_SUPERSEDED_OPERATIONS[which]
            }, which

    def test_each_operation_is_grouped_with_its_area(self):
        narrow = {key: operation["tags"] for key, operation in self.operations("narrow").items()}
        assert narrow == dict.fromkeys(PUBLISHED_SUPERSEDED_OPERATIONS["narrow"], ["user"])
        service_wide = {key: operation["tags"] for key, operation in self.operations("service-wide").items()}
        assert service_wide == dict.fromkeys(SUPERSEDED_OPERATIONS, ["api"])

    def test_each_operation_publishes_what_the_one_replacing_it_publishes(self):
        for which in self.documents:
            for key, ((path, method), _narrow, _service_wide) in SUPERSEDED_OPERATIONS.items():
                if key == SIGN_IN_BY_POSTING or key not in PUBLISHED_SUPERSEDED_OPERATIONS[which]:
                    continue
                superseded = self.documents[which]["paths"][key[0]][key[1]]
                replacing = self.documents[which]["paths"][path][method]
                assert superseded.get("requestBody") == replacing.get("requestBody"), (which, key)
                assert superseded["responses"] == replacing["responses"], (which, key)

    def test_signing_in_by_posting_keeps_what_it_has_always_published(self):
        document = self.documents["service-wide"]
        operation = document["paths"][LOGIN_SESSION_KEY]["post"]
        assert {media_type: body["schema"] for media_type, body in operation["requestBody"]["content"].items()} == (
            dict.fromkeys([JSON, FORM, MULTIPART], {"$ref": "#/components/schemas/LoginUserRequest"})
        )
        assert {code: response["content"][JSON]["schema"] for code, response in operation["responses"].items()} == (
            dict.fromkeys(["200", "400", "403"], {"$ref": "#/components/schemas/LoginUserResponse"})
        )
        assert operation["security"] == [{"csrf": []}]
        assert {"LoginUserRequest", "LoginUserResponse"} <= set(document["components"]["schemas"])

    def test_the_narrow_document_leaves_out_the_address_that_takes_its_version_as_a_parameter(self):
        """Signing in by posting there is described by its handler, and that description is not what it does."""
        narrow = self.documents["narrow"]
        assert LOGIN_SESSION_KEY not in narrow["paths"]
        assert {"LoginUserRequest", "LoginUserResponse"} & set(narrow["components"]["schemas"]) == set()

    def test_only_the_token_check_publishes_an_unauthenticated_or_throttled_refusal(self):
        token_checks = {(TOKEN_VALIDATIONS, "post"), (V1_TOKEN_VALIDATION, "post")}
        for which, document in self.documents.items():
            keys = set(CONFORMING_OPERATIONS) | PUBLISHED_SUPERSEDED_OPERATIONS[which]
            for key, operation in published_operations(document, keys).items():
                published = set(operation["responses"]) & {"401", "429"}
                assert published == ({"401", "429"} if key in token_checks else set()), (which, key)


def published_response_schema(document, path, method, status):
    """Return what a body answered at ``path`` with ``status`` must satisfy, components included."""
    node = document["paths"][path][method]["responses"][str(status)]["content"][JSON]["schema"]
    return as_json_schema({**node, "components": document["components"]})


def undeclared_members(body, node, where="$"):
    """Return the members of ``body`` an object of ``node`` that lists its members does not declare."""
    if "anyOf" in node:
        if body is None:
            return []
        node = node["anyOf"][0]
    found = []
    if isinstance(body, dict) and "properties" in node:
        extra = node.get("additionalProperties")
        if extra is None:
            found += [f"{where}.{key}" for key in body if key not in node["properties"]]
        for key, value in body.items():
            member = node["properties"].get(key, extra if isinstance(extra, dict) else None)
            if member:
                found += undeclared_members(value, member, f"{where}.{key}")
    elif isinstance(body, list) and "items" in node:
        for index, item in enumerate(body):
            found += undeclared_members(item, node["items"], f"{where}[{index}]")
    return found


@skip_unless_lms
class PublishedShapeTests(TestCase):
    """
    Every body these addresses really answer with is the one the document publishes for it.

    Each response below is sent to the running address, then held against the
    shape published for that operation and code: every member published as
    always present is there, each value has the published type, and an object
    that lists its members carries no other.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.document = service_wide_document()

    def assert_published(self, path, method, response):
        """Assert ``response`` is a body the document publishes for that operation and code."""
        assert response["Content-Type"].startswith(JSON), (path, response.status_code)
        body = json.loads(response.content)
        schema = published_response_schema(self.document, path, method, response.status_code)
        jsonschema.validate(body, schema)
        assert undeclared_members(body, schema) == [], (path, method, response.status_code)

    def test_the_forms(self):
        extra = {**settings.REGISTRATION_EXTRA_FIELDS, "confirm_email": "required", "country": "required"}
        with override_settings(REGISTRATION_EXTRA_FIELDS=extra):
            self.assert_published(REGISTRATIONS, "get", Client().get(REGISTRATIONS))
        self.assert_published(REGISTRATIONS, "get", Client().get(REGISTRATIONS))
        self.assert_published(PASSWORD_RESETS, "get", Client().get(PASSWORD_RESETS))
        self.assert_published(LOGIN_SESSIONS, "get", Client().get(LOGIN_SESSIONS))

    def test_a_registration_and_its_refusals(self):
        self.assert_published(REGISTRATIONS, "post", Client().post(REGISTRATIONS, registration_form("shape_new")))
        self.assert_published(REGISTRATIONS, "post", Client().post(REGISTRATIONS, registration_form("shape_new")))
        self.assert_published(REGISTRATIONS, "post", Client().post(REGISTRATIONS, {}))
        self.assert_published(
            REGISTRATIONS, "post", Client().post(REGISTRATIONS, {**registration_form("shape_x"), "country": "ZZ"}),
        )
        with override_settings(OPEN_EDX_FILTERS_CONFIG=REFUSE_REGISTRATION):
            self.assert_published(REGISTRATIONS, "post", Client().post(REGISTRATIONS, registration_form("shape_y")))
        with override_settings(CACHES=COUNTING_CACHE):
            cache.clear()
            spend(REGISTRATIONS, {}, limit_of(settings.REGISTRATION_RATELIMIT))
            self.assert_published(REGISTRATIONS, "post", Client().post(REGISTRATIONS, {}))
            cache.clear()

    def test_a_token_check_and_its_refusals(self):
        owner = UserFactory.create(username="shape_owner", password=PASSWORD)
        valid = Client().post(TOKEN_VALIDATIONS, {"token": reset_token(owner)})
        self.assert_published(TOKEN_VALIDATIONS, "post", valid)
        self.assert_published(TOKEN_VALIDATIONS, "post", Client().post(TOKEN_VALIDATIONS, "{x", content_type=JSON))
        self.assert_published(
            TOKEN_VALIDATIONS, "post", Client().post(TOKEN_VALIDATIONS, {}, HTTP_AUTHORIZATION="JWT not-a-jwt"),
        )
        self.assert_published(
            TOKEN_VALIDATIONS, "post", signed_in(owner, enforce_csrf_checks=True).post(TOKEN_VALIDATIONS, {}),
        )
        with override_settings(CACHES=COUNTING_CACHE):
            cache.clear()
            spend(TOKEN_VALIDATIONS, {}, limit_of(settings.RESET_PASSWORD_TOKEN_VALIDATE_API_RATELIMIT))
            self.assert_published(TOKEN_VALIDATIONS, "post", Client().post(TOKEN_VALIDATIONS, {}))
            cache.clear()

    def test_a_validation_and_its_refusals(self):
        UserFactory.create(username="shape_taken", password=PASSWORD)
        for data in ({"username": "shape_taken", "email": "e@example.com"}, {"name": "Shape Name"}, {}):
            self.assert_published(
                REGISTRATION_VALIDATIONS,
                "post",
                Client().post(REGISTRATION_VALIDATIONS, json.dumps(data), content_type=JSON),
            )
        self.assert_published(
            REGISTRATION_VALIDATIONS, "post", Client().post(REGISTRATION_VALIDATIONS, "{x", content_type=JSON),
        )
        with override_settings(CACHES=COUNTING_CACHE):
            cache.clear()
            spend(REGISTRATION_VALIDATIONS, {}, limit_of(settings.REGISTRATION_VALIDATION_RATELIMIT))
            self.assert_published(REGISTRATION_VALIDATIONS, "post", Client().post(REGISTRATION_VALIDATIONS, {}))
            cache.clear()

    def test_a_sign_in_and_its_refusals(self):
        UserFactory.create(username="shape_signer", password=PASSWORD)
        inactive = UserFactory.create(username="shape_inactive", password=PASSWORD, is_active=False)
        Registration().register(inactive)
        for data in (
            {"email_or_username": "shape_signer", "password": PASSWORD},
            {"email_or_username": "shape_signer", "password": "wrong"},
            {"email_or_username": "shape_inactive", "password": PASSWORD},
            {"password": PASSWORD},
        ):
            self.assert_published(LOGIN_SESSIONS, "post", Client().post(LOGIN_SESSIONS, data))
        with override_settings(OPEN_EDX_FILTERS_CONFIG=REFUSE_LOGIN):
            self.assert_published(
                LOGIN_SESSIONS,
                "post",
                Client().post(LOGIN_SESSIONS, {"email_or_username": "shape_signer", "password": PASSWORD}),
            )
        running = {"backend": "oa2-provider", "kwargs": {"username": "elsewhere", "uid": "elsewhere"}}
        with mock.patch.object(login.pipeline, "running", return_value=True), \
                mock.patch.object(login.pipeline, "get", return_value=running), \
                mock.patch.object(
                    login.provider.Registry, "get_from_pipeline", return_value=SimpleNamespace(name="Provider"),
                ), \
                mock.patch.object(login.pipeline, "get_authenticated_user", side_effect=User.DoesNotExist):
            self.assert_published(LOGIN_SESSIONS, "post", Client().post(LOGIN_SESSIONS, {}))


class UnlabelledExtensionForm(forms.Form):
    """A field a site adds to registration with a name and nothing else: no label, no starting value."""

    nickname = forms.CharField()


#: Forms a site can name to add fields to registration, each with the field
#: it adds and the label that field is described with.
EXTENSION_FORMS = {
    "openedx.core.djangoapps.user_api.tests.test_helpers.TestCaseForm": ("favorite_movie", "Fav Flick"),
    f"{UnlabelledExtensionForm.__module__}.UnlabelledExtensionForm": ("nickname", None),
}


@skip_unless_lms
class ExtensionFormShapeTests(TestCase):
    """
    A registration form carrying the fields a site adds is a body both documents publish.

    Such a field holds what the field of the site's form holds, and a field
    of a Django form has no label and no starting value unless it is given
    them, so the form describes it with neither.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.documents = {"narrow": narrow_document(), "service-wide": service_wide_document()}

    def test_a_field_the_site_adds_is_described_as_published(self):
        for form, (name, label) in EXTENSION_FORMS.items():
            # Reading the form appends the added fields to the configured order in place.
            order = list(settings.REGISTRATION_FIELD_ORDER)
            with override_settings(PROFILE_EXTENSION_FORM=form, REGISTRATION_FIELD_ORDER=order):
                for address in (REGISTRATIONS, V1_REGISTRATION, V2_REGISTRATION):
                    response = Client().get(address)
                    body = json.loads(response.content)
                    added = [
                        (field["label"], field["placeholder"], field["defaultValue"])
                        for field in body["fields"] if field["name"] == name
                    ]
                    assert added == [(label, None, None)], (form, address)
                    for which, document in self.documents.items():
                        schema = published_response_schema(document, address, "get", response.status_code)
                        jsonschema.validate(body, schema)
                        assert undeclared_members(body, schema) == [], (which, form, address)


#: Where an operation is requested, for one published under a key that is not an address.
REQUESTED_AT = {LOGIN_SESSION_KEY: V2_LOGIN_SESSION}

#: Each operation that reads a body, at the addresses replacing others and at the ones they replace.
BODY_READERS = set(CONFORMING_REQUESTS) | {
    superseded for superseded, (replacing, _narrow, _service_wide) in SUPERSEDED_OPERATIONS.items()
    if replacing in CONFORMING_REQUESTS
}

#: The operations each document is held to here. Signing in by posting at the
#: older address keeps the description its handler carries.
REFUSING_OPERATIONS = {
    which: set(CONFORMING_OPERATIONS) | (published - {SIGN_IN_BY_POSTING})
    for which, published in PUBLISHED_SUPERSEDED_OPERATIONS.items()
}


@skip_unless_lms
class RefusedRepresentationTests(TestCase):
    """
    Each operation publishes the refusal of an answer it cannot give and of a body it cannot read.

    Both are settled as the request enters, before the handler runs, so every
    operation refuses an ``Accept`` header naming nothing it answers in, and
    every operation reading a body refuses one in a media type it cannot
    read. Each refusal is sent to the running address and held against the
    shape both documents publish for that operation and code.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.documents = {"narrow": narrow_document(), "service-wide": service_wide_document()}

    def test_each_operation_publishes_the_refusals_it_answers_with(self):
        for which, keys in REFUSING_OPERATIONS.items():
            for key in sorted(keys):
                path, method = key
                address = REQUESTED_AT.get(path, path)
                requests = [(406, {"HTTP_ACCEPT": "text/html"})]
                if key in BODY_READERS:
                    requests.append((415, {"data": "x", "content_type": "text/plain"}))
                for status, extra in requests:
                    # Registration runs outside the transaction a request is
                    # otherwise given, so a refusal there marks the one it runs
                    # in for rollback; each request is given one of its own.
                    with transaction.atomic():
                        response = getattr(Client(), method)(address, **extra)
                    assert response.status_code == status, (which, key)
                    assert response["Content-Type"].startswith(JSON), (which, key, status)
                    body = json.loads(response.content)
                    schema = published_response_schema(self.documents[which], path, method, status)
                    jsonschema.validate(body, schema)
                    assert undeclared_members(body, schema) == [], (which, key, status)

    def test_the_fields_published_as_required_are_the_ones_the_stock_form_requires(self):
        """An empty registration is refused for each of them, and for the terms taken from `honor_code`."""
        with override_settings(REGISTRATION_EXTRA_FIELDS=dict(STOCK_REGISTRATION_EXTRA_FIELDS)):
            form = json.loads(Client().get(REGISTRATIONS).content)
            refused = set(json.loads(Client().post(REGISTRATIONS, {}).content)) - {"error_code"}
        required = sorted(field["name"] for field in form["fields"] if field["required"])
        assert refused == set(required) | {"terms_of_service"}
        for which, document in self.documents.items():
            for path in (REGISTRATIONS, V1_REGISTRATION, V2_REGISTRATION):
                content = document["paths"][path]["post"]["requestBody"]["content"]
                published = {media_type: sorted(body["schema"]["required"]) for media_type, body in content.items()}
                assert published == dict.fromkeys([FORM, MULTIPART], required), (which, path)
