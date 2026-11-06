"""Tests for the v2 Studio documentation links endpoint."""

import configparser
import tempfile
from contextlib import contextmanager
from pathlib import Path
from time import time
from unittest.mock import patch

import pytest
from django.conf import settings
from django.db import connection
from django.db.models import signals as model_signals
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import Resolver404, resolve, reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from help_tokens.core import HelpUrlExpert
from rest_framework import serializers
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from cms.djangoapps.contentstore.rest_api.v1.views.help_urls import HelpUrlsView as LegacyHelpUrlsView
from cms.djangoapps.contentstore.rest_api.v2.serializers.help_urls import HelpUrlsSerializer
from cms.djangoapps.contentstore.rest_api.v2.views.help_urls import HelpUrlsView
from cms.djangoapps.contentstore.rest_api.v2.views.tests.helpers import (
    PASSWORD,
    SignalRecorder,
    generate_cms_schema,
    logged_in_client,
    writes,
)
from cms.djangoapps.contentstore.utils import get_help_urls
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory

URL_NAME = "authoring_v2:help_url_list"
LEGACY_URL_NAME = "cms.djangoapps.contentstore:v1:help_urls"
HELP_URLS_PATH = "/api/authoring/v2/help_urls/"
LEGACY_HELP_URLS_PATH = "/api/contentstore/v1/help_urls"
VIEW_MODULE = "cms.djangoapps.contentstore.rest_api.v2.views.help_urls"
WRITE_METHODS = ("post", "put", "patch", "delete")

# Documentation books without a version segment, so each language and book
# override is visible in the links it produces.
KNOWN_BOOKS = {
    "course_author": "https://docs.example.com/en/authors",
    "learner": "https://docs.example.com/en/learners",
}
# Locale mapping for the temporary help tokens file: ``fr`` has its own
# directory and any other language falls back to ``es``.
KNOWN_LOCALES = {"default": "es", "fr": "fr"}

# Envelope members that legitimately differ per request.
VOLATILE_FIELDS = {"instance"}

# What the error envelope adds to the legacy ``{"detail": ...}`` body.
ERROR_ENVELOPE_DIFFERENCES = (
    ("type", "the envelope names the error type"),
    ("title", "the envelope titles the error type"),
    ("status", "the envelope repeats the HTTP status"),
)


def _ini_tokens(ini_file=None):
    """Return the ``[pages]`` tokens of a help tokens file, in file order."""
    config = configparser.ConfigParser()
    config.read(ini_file or settings.HELP_TOKENS_INI_FILE)
    return list(config["pages"])


@contextmanager
def _help_tokens_file(tokens, locales=None):
    """Serve the links from a temporary help tokens file mapping each token to ``known/<token>.html``."""
    with tempfile.TemporaryDirectory() as directory:
        ini_file = Path(directory) / "help_tokens.ini"
        pages = "".join(f"{token} = course_author:known/{token}.html\n" for token in tokens)
        locale_lines = "".join(f"{code} = {name}\n" for code, name in (locales or KNOWN_LOCALES).items())
        ini_file.write_text(f"[pages]\n{pages}\n[locales]\n{locale_lines}")
        # The library keeps one process-wide expert bound to the file it was
        # first created with; drop it so the overridden file is the one read.
        with override_settings(HELP_TOKENS_INI_FILE=ini_file), patch.object(HelpUrlExpert, "THE_ONE", None):
            yield


def _normalize(obj, path=""):
    """Drop volatile fields and collect a ``{json_path: value}`` map for diffing."""
    flat = {}
    if isinstance(obj, dict):
        for key in sorted(obj):
            if key in VOLATILE_FIELDS:
                continue
            flat.update(_normalize(obj[key], f"{path}.{key}" if path else key))
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            flat.update(_normalize(item, f"{path}[{index}]"))
    else:
        flat[path] = obj
    return flat


def _covers(declared, path):
    return path == declared or path.startswith((declared + ".", declared + "["))


def _diff(legacy, new, intentional):
    """Return every differing path, and those not covered by ``intentional``."""
    before, after = _normalize(legacy), _normalize(new)
    changed = {k for k in set(before) | set(after) if before.get(k, "<absent>") != after.get(k, "<absent>")}
    unexpected = {k for k in changed if not any(_covers(declared, k) for declared, _ in intentional)}
    return changed, unexpected


class HelpUrlsAccessTest(TestCase):
    """Who may read the documentation links, and how each refusal is shaped."""

    @classmethod
    def setUpTestData(cls):
        cls.user = UserFactory(password=PASSWORD)
        cls.staff = UserFactory(is_staff=True, password=PASSWORD)
        cls.inactive = UserFactory(is_active=False, password=PASSWORD)

    def url(self):
        return reverse(URL_NAME)

    def test_signed_in_callers_are_served(self):
        for caller in (self.user, self.staff, self.inactive):
            client = logged_in_client(caller)
            for method in ("get", "head"):
                assert getattr(client, method)(self.url()).status_code == 200, (caller.username, method)
            assert client.get(self.url()).json() == get_help_urls(), caller.username

    def test_an_inactive_caller_is_served_on_both_routes(self):
        client = logged_in_client(self.inactive)
        assert client.get(self.url()).status_code == 200
        assert client.get(reverse(LEGACY_URL_NAME)).status_code == 200

    def test_anonymous_is_refused_with_a_challenge(self):
        response = APIClient().get(self.url())
        data = assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
        assert data["detail"] == "Authentication credentials were not provided."
        assert response["WWW-Authenticate"] == 'JWT realm="api"'
        assert APIClient().head(self.url()).status_code == 401

    def test_anonymous_writes_are_refused_before_the_method_is_checked(self):
        for method in WRITE_METHODS:
            response = getattr(APIClient(), method)(self.url())
            assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
            assert getattr(APIClient(), method)(reverse(LEGACY_URL_NAME)).status_code == 401, method

    def test_a_jwt_caller_is_served(self):
        response = APIClient().get(self.url(), HTTP_AUTHORIZATION=f"JWT {create_jwt_for_user(self.user)}")
        assert response.status_code == 200
        assert response.json() == get_help_urls()

    def test_an_expired_jwt_is_refused(self):
        token = create_jwt_for_user(self.user, additional_claims={"exp": int(time()) - 60})
        response = APIClient().get(self.url(), HTTP_AUTHORIZATION=f"JWT {token}")
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
        assert response["WWW-Authenticate"] == 'JWT realm="api"'
        assert APIClient().get(reverse(LEGACY_URL_NAME), HTTP_AUTHORIZATION=f"JWT {token}").status_code == 401

    def test_a_malformed_jwt_is_refused(self):
        response = APIClient().get(self.url(), HTTP_AUTHORIZATION="JWT not-a-token")
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    def test_a_bearer_token_is_not_accepted(self):
        token = AccessTokenFactory(user=self.user, application=ApplicationFactory()).token
        response = APIClient().get(self.url(), HTTP_AUTHORIZATION=f"Bearer {token}")
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    def test_the_legacy_route_still_accepts_a_bearer_token(self):
        token = AccessTokenFactory(user=self.user, application=ApplicationFactory()).token
        response = APIClient().get(reverse(LEGACY_URL_NAME), HTTP_AUTHORIZATION=f"Bearer {token}")
        assert response.status_code == 200

    def test_writes_are_not_allowed(self):
        for caller in (self.user, self.staff, self.inactive):
            client = logged_in_client(caller)
            for method in WRITE_METHODS:
                response = getattr(client, method)(self.url())
                data = assert_error_envelope(response, expected_status=405, expected_type_slug="internal")
                assert data["detail"] == f'Method "{method.upper()}" not allowed.'
                assert response["Content-Type"] == "application/json"
                assert response["Allow"] == "GET, HEAD, OPTIONS"

    def test_an_unsatisfiable_accept_header_is_answered_in_json(self):
        response = logged_in_client(self.user).get(self.url(), HTTP_ACCEPT="application/xml")
        data = assert_error_envelope(response, expected_status=406, expected_type_slug="internal")
        assert data["detail"] == "Could not satisfy the request Accept header."
        assert response["Content-Type"] == "application/json"


class HelpUrlsResponseTest(TestCase):
    """What a successful request returns, what it costs, and how a failure is reported."""

    @classmethod
    def setUpTestData(cls):
        cls.user = UserFactory(password=PASSWORD)

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_the_body_is_one_object_keyed_by_token(self):
        body = self.client.get(reverse(URL_NAME)).json()
        assert isinstance(body, dict)
        assert list(body) == _ini_tokens()
        for token, url in body.items():
            assert isinstance(url, str), token
            assert url.startswith("https://"), (token, url)

    def test_the_view_makes_no_queries(self):
        request = APIRequestFactory().get(HELP_URLS_PATH)
        force_authenticate(request, user=self.user)
        with self.assertNumQueries(0):
            response = HelpUrlsView.as_view()(request).render()
        assert response.status_code == 200

    def test_an_unreadable_configuration_is_a_generic_server_error(self):
        with patch(f"{VIEW_MODULE}.get_help_urls", side_effect=KeyError("pages")):
            response = self.client.get(reverse(URL_NAME))
        data = assert_error_envelope(response, expected_status=500, expected_type_slug="internal")
        assert data["detail"] == "An unexpected error occurred. Please try again later."
        assert b"pages" not in response.content
        assert b"KeyError" not in response.content


class HelpUrlsIdempotenceTest(TestCase):
    """A read of the documentation links writes nothing and sends no signal."""

    @classmethod
    def setUpTestData(cls):
        cls.user = UserFactory(password=PASSWORD)

    def test_a_session_read_writes_nothing(self):
        client = logged_in_client(self.user)
        for method in ("get", "head"):
            with CaptureQueriesContext(connection) as queries, SignalRecorder() as signals:
                response = getattr(client, method)(reverse(URL_NAME))
            assert response.status_code == 200, method
            assert writes(queries.captured_queries) == [], method
            assert signals.unexpected() == [], method

    def test_a_jwt_read_writes_nothing(self):
        header = f"JWT {create_jwt_for_user(self.user)}"
        for method in ("get", "head"):
            with CaptureQueriesContext(connection) as queries, SignalRecorder() as signals:
                response = getattr(APIClient(), method)(reverse(URL_NAME), HTTP_AUTHORIZATION=header)
            assert response.status_code == 200, method
            assert writes(queries.captured_queries) == [], method
            assert signals.unexpected() == [], method

    def test_the_capture_sees_a_write_when_one_happens(self):
        with CaptureQueriesContext(connection) as queries, SignalRecorder() as signals:
            UserFactory(password=PASSWORD)
        assert writes(queries.captured_queries) != []
        assert any(signal is model_signals.post_save for _, signal in signals.unexpected())


class HelpUrlsParityTest(TestCase):
    """The v2 route returns what the legacy route returns, under each documentation setting."""

    @classmethod
    def setUpTestData(cls):
        cls.user = UserFactory(password=PASSWORD)

    def setUp(self):
        super().setUp()
        self.client = logged_in_client(self.user)

    def _pair(self, client=None, params=None):
        client = client or self.client
        return client.get(reverse(LEGACY_URL_NAME), params), client.get(reverse(URL_NAME), params)

    def _assert_parity(self, legacy, new, expected_status, intentional=()):
        """Assert both routes answered ``expected_status`` and differ only where ``intentional`` says."""
        assert legacy.status_code == expected_status, legacy.content
        assert new.status_code == expected_status, new.content
        changed, unexpected = _diff(legacy.json(), new.json(), intentional)
        assert not unexpected, f"unexpected differences: {sorted(unexpected)}"
        for declared, reason in intentional:
            occurred = any(_covers(declared, k) for k in changed)
            assert occurred, f"declared difference did not occur: {declared} ({reason})"

    def _assert_identical(self, legacy, new):
        self._assert_parity(legacy, new, expected_status=200)
        assert new.content == legacy.content

    def test_the_default_body_is_byte_identical(self):
        legacy, new = self._pair()
        self._assert_identical(legacy, new)
        assert list(new.json()) == list(legacy.json()) == _ini_tokens()

    def test_a_documentation_version_override_is_served_identically(self):
        stock = self._pair()[1].json()
        with override_settings(HELP_TOKENS_VERSION="open-release-x.master"):
            legacy, new = self._pair()
        self._assert_identical(legacy, new)
        assert new.json() != stock
        for token, url in new.json().items():
            assert "/open-release-x.master/" in url, (token, url)

    def test_a_documentation_books_override_is_served_identically(self):
        stock = self._pair()[1].json()
        with override_settings(HELP_TOKENS_BOOKS=KNOWN_BOOKS):
            legacy, new = self._pair()
        self._assert_identical(legacy, new)
        assert new.json() != stock
        for token, url in new.json().items():
            assert url.startswith("https://docs.example.com/"), (token, url)

    def test_each_documentation_language_branch_is_served_identically(self):
        tokens = _ini_tokens()
        stock = self._pair()[1].json()
        for language, directory in (("fr", "fr"), ("de", "es"), (None, "en")):
            with self.subTest(language=language):
                with _help_tokens_file(tokens), override_settings(
                    HELP_TOKENS_BOOKS=KNOWN_BOOKS, HELP_TOKENS_LANGUAGE_CODE=language,
                ):
                    legacy, new = self._pair()
                self._assert_identical(legacy, new)
                assert new.json() != stock
                assert new.json() == {
                    token: f"https://docs.example.com/{directory}/authors/known/{token}.html" for token in tokens
                }

    def test_a_token_the_contract_does_not_declare_is_served_only_by_the_legacy_route(self):
        with _help_tokens_file([*_ini_tokens(), "unlisted_page"]), override_settings(
            HELP_TOKENS_BOOKS=KNOWN_BOOKS, HELP_TOKENS_LANGUAGE_CODE=None,
        ):
            legacy, new = self._pair()
        self._assert_parity(
            legacy, new, expected_status=200,
            intentional=(("unlisted_page", "the v2 body carries the declared tokens only"),),
        )
        assert legacy.json()["unlisted_page"] == "https://docs.example.com/en/authors/known/unlisted_page.html"
        assert list(new.json()) == _ini_tokens()

    def test_query_strings_are_ignored_on_both_routes(self):
        stock_legacy, stock_new = self._pair()
        for params in ({"foo": "bar"}, {"view": "minimal"}, {"fields": "home"}):
            legacy, new = self._pair(params=params)
            self._assert_identical(legacy, new)
            assert legacy.content == stock_legacy.content, params
            assert new.content == stock_new.content, params

    def test_the_unauthenticated_error_keeps_the_legacy_detail(self):
        legacy, new = self._pair(client=APIClient())
        assert legacy.json() == {"detail": "Authentication credentials were not provided."}
        assert_error_envelope(new, expected_status=401, expected_type_slug="authn")
        self._assert_parity(legacy, new, expected_status=401, intentional=ERROR_ENVELOPE_DIFFERENCES)

    def test_neither_view_makes_a_query(self):
        for view_class, path in ((LegacyHelpUrlsView, LEGACY_HELP_URLS_PATH), (HelpUrlsView, HELP_URLS_PATH)):
            request = APIRequestFactory().get(path)
            force_authenticate(request, user=self.user)
            with self.assertNumQueries(0):
                response = view_class.as_view()(request).render()
            assert response.status_code == 200, path

    def test_both_routes_cost_the_same_through_the_full_request_stack(self):
        for url_name in (LEGACY_URL_NAME, URL_NAME):
            assert self.client.get(reverse(url_name)).status_code == 200, url_name
        counts = {}
        for url_name in (LEGACY_URL_NAME, URL_NAME):
            with CaptureQueriesContext(connection) as queries:
                self.client.get(reverse(url_name))
            counts[url_name] = len(queries.captured_queries)
        assert counts[URL_NAME] == counts[LEGACY_URL_NAME], counts


class HelpUrlsSerializerTest(SimpleTestCase):
    """The serializer declares exactly the tokens of the help tokens file."""

    def test_the_fields_follow_the_help_tokens_file_in_order(self):
        fields = list(HelpUrlsSerializer().fields)
        assert fields == _ini_tokens()
        assert fields == list(get_help_urls())

    def test_every_field_is_a_read_only_url(self):
        for name, field in HelpUrlsSerializer().fields.items():
            assert isinstance(field, serializers.URLField), name
            assert field.read_only is True, name

    def test_every_field_describes_its_own_page(self):
        help_texts = [field.help_text for field in HelpUrlsSerializer().fields.values()]
        assert len(set(help_texts)) == len(help_texts)
        for text in help_texts:
            assert text.startswith("Documentation page URL for "), text


class HelpUrlsRoutingTest(TestCase):
    """Where the endpoint is served, and where the legacy endpoint still is."""

    def test_url_names_reverse_to_their_addresses(self):
        assert reverse(URL_NAME) == HELP_URLS_PATH
        assert reverse(LEGACY_URL_NAME) == LEGACY_HELP_URLS_PATH

    def test_each_address_resolves_to_its_own_view(self):
        assert resolve(HELP_URLS_PATH).func.view_class is HelpUrlsView
        assert resolve(LEGACY_HELP_URLS_PATH).func.view_class is LegacyHelpUrlsView
        assert HelpUrlsView is not LegacyHelpUrlsView

    def test_each_address_has_one_spelling(self):
        for path in (HELP_URLS_PATH.rstrip("/"), LEGACY_HELP_URLS_PATH + "/"):
            with pytest.raises(Resolver404):
                resolve(path)

    def test_a_slashless_address_redirects(self):
        client = logged_in_client(UserFactory(password=PASSWORD))
        response = client.get(HELP_URLS_PATH.rstrip("/"))
        assert response.status_code == 301
        assert response["Location"] == HELP_URLS_PATH


class HelpUrlsSchemaTest(TestCase):
    """What the published schema says about the endpoint and its legacy address."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = generate_cms_schema()
        cls.components = cls.schema["components"]["schemas"]

    def _operation(self):
        return self.schema["paths"][HELP_URLS_PATH]["get"]

    def test_the_operation_is_published_at_its_full_address(self):
        assert set(self.schema["paths"][HELP_URLS_PATH]) == {"get"}
        operation = self._operation()
        assert operation["operationId"] == "v2_help_urls_retrieve"
        assert operation["tags"] == ["openedx-platform-sdk"]
        assert "parameters" not in operation
        assert "deprecated" not in operation

    def test_the_success_body_is_one_object_of_links(self):
        success = self._operation()["responses"]["200"]["content"]["application/json"]["schema"]
        assert success == {"$ref": "#/components/schemas/HelpUrls"}
        component = self.components["HelpUrls"]
        tokens = _ini_tokens()
        assert component["type"] == "object"
        assert list(component["properties"]) == tokens
        assert sorted(component["required"]) == sorted(tokens)
        fields = HelpUrlsSerializer().fields
        for token, prop in component["properties"].items():
            assert prop == {
                "type": "string", "format": "uri", "readOnly": True, "description": fields[token].help_text,
            }, token

    def test_the_unauthenticated_error_references_the_shared_envelope(self):
        responses = self._operation()["responses"]
        assert set(responses) == {"200", "401"}
        assert responses["401"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }

    def test_only_the_legacy_help_urls_operation_is_deprecated(self):
        legacy = self.schema["paths"][LEGACY_HELP_URLS_PATH]["get"]
        assert legacy["operationId"] == "v1_help_urls_retrieve"
        assert legacy["deprecated"] is True
        for other in (
            "/api/contentstore/v1/proctored_exam_settings/{course_id}",
            "/api/authoring/v2/courses/{course_key}/textbooks/",
        ):
            operations = self.schema["paths"][other]
            assert operations, other
            for method, operation in operations.items():
                assert "deprecated" not in operation, (other, method)
