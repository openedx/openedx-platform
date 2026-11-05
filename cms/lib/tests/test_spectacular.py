"""Tests for the CMS drf-spectacular hooks and schema class."""

import json
import re
from collections import defaultdict
from importlib import import_module
from pathlib import Path
from unittest import TestCase, mock

from django.conf import settings
from django.test import Client, SimpleTestCase
from django.test import TestCase as DjangoTestCase
from django.urls import path as url_path
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.settings import patched_settings, spectacular_settings
from rest_framework import serializers, viewsets
from rest_framework.response import Response
from rest_framework.settings import api_settings

from cms.lib.spectacular import (
    SUPERSEDED_PATHS,
    CmsAutoSchema,
    cms_api_filter,
    cms_mark_migrated_paths,
    cms_mark_superseded_paths,
)
from openedx.core.apidocs import get_api_docs_settings
from openedx.core.djangoapps.user_api.schema_components import PATCHED_USER_RETIREMENT_STATUS


def _endpoint(path):
    """Return an endpoint tuple shaped as the pre-processing hook receives it."""
    return (path, path, "GET", object())


def _schema(*routes):
    return {"paths": {route: {"get": {"operationId": route}} for route in routes}}


class CmsApiFilterTest(TestCase):
    """Which mounts reach the generated schema."""

    @staticmethod
    def _admitted(path):
        """Return whether the pre-processing hook keeps path."""
        return bool(cms_api_filter([_endpoint(path)]))

    def test_the_authoring_mount_is_admitted(self):
        assert self._admitted("/api/authoring/v1/courses/course-v1:a+b+c/youtube_transcript_checks/")

    def test_the_contentstore_mount_is_still_admitted(self):
        assert self._admitted("/api/contentstore/v0/youtube_transcripts/course-v1:a+b+c/check")
        assert self._admitted("/api/contentstore/v4/home/courses/")

    def test_the_hand_listed_discussions_path_is_still_admitted(self):
        assert self._admitted("/api/courses/course-v1:a+b+c/bulk_enable_disable_discussions")

    def test_an_unrelated_mount_is_refused(self):
        assert not self._admitted("/api/user/v1/accounts/")
        assert not self._admitted("/api/authoring/courses/")
        assert not self._admitted("/authoring/v1/courses/")

    def test_the_filter_preserves_the_endpoint_tuples_it_keeps(self):
        kept = _endpoint("/api/authoring/v1/courses/")
        dropped = _endpoint("/api/user/v1/accounts/")
        assert cms_api_filter([kept, dropped]) == [kept]


class CmsMarkSupersededPathsTest(TestCase):
    """Which operations the post-processing hook marks deprecated."""

    def _run(self, schema):
        return cms_mark_superseded_paths(schema, generator=None, request=None, public=True)

    def test_every_operation_of_a_superseded_path_is_marked(self):
        schema = {"paths": {path: {"get": {}} for path in SUPERSEDED_PATHS}}
        result = self._run(schema)
        for path in SUPERSEDED_PATHS:
            assert result["paths"][path]["get"]["deprecated"] is True

    def test_both_spellings_of_each_superseded_path_are_listed(self):
        assert {path for path in SUPERSEDED_PATHS if path.startswith("/api/contentstore/")} == {
            "/api/contentstore/v0/youtube_transcripts/{course_id}/chec",
            "/api/contentstore/v0/youtube_transcripts/{course_id}/check",
            "/api/contentstore/v0/youtube_transcripts/{course_id}/uploa",
            "/api/contentstore/v0/youtube_transcripts/{course_id}/upload",
        }

    def test_the_conforming_paths_are_left_unmarked(self):
        conforming = "/api/authoring/v1/courses/{course_key}/youtube_transcript_checks/"
        result = self._run({"paths": {conforming: {"get": {"operationId": "x"}}}})
        assert result["paths"][conforming]["get"] == {"operationId": "x"}

    def test_an_unrelated_contentstore_path_is_left_unmarked(self):
        other = "/api/contentstore/v0/advanced_settings/{course_id}"
        result = self._run({"paths": {other: {"get": {}}}})
        assert "deprecated" not in result["paths"][other]["get"]

    def test_path_level_keys_are_not_treated_as_operations(self):
        path = SUPERSEDED_PATHS[0]
        result = self._run({"paths": {path: {"parameters": [], "get": {}}}})
        assert result["paths"][path]["parameters"] == []
        assert result["paths"][path]["get"]["deprecated"] is True

    def test_a_schema_without_the_superseded_paths_is_returned_unchanged(self):
        schema = {"paths": {}}
        assert self._run(schema) == {"paths": {}}


class CmsMarkMigratedPathsTest(SimpleTestCase):
    """The post-processing hook works on full paths, so both prefixes coexist in one schema."""

    def test_legacy_paths_deprecated_conforming_paths_not(self):
        result = cms_mark_migrated_paths(_schema(
            "/api/contentstore/v1/xblock/{usage_key_string}/",
            "/api/authoring/v1/xblocks/{usage_key_string}/",
            "/api/contentstore/v3/course_details/{course_id}/",
            "/api/authoring/v3/courses/{course_key}/details/",
        ), None, None, False)
        paths = result["paths"]
        assert paths["/api/contentstore/v1/xblock/{usage_key_string}/"]["get"]["deprecated"] is True
        assert paths["/api/contentstore/v3/course_details/{course_id}/"]["get"]["deprecated"] is True
        assert "deprecated" not in paths["/api/authoring/v1/xblocks/{usage_key_string}/"]["get"]
        assert "deprecated" not in paths["/api/authoring/v3/courses/{course_key}/details/"]["get"]

    def test_bff_surfaces_internal_on_both_mounts(self):
        result = cms_mark_migrated_paths(_schema(
            "/api/contentstore/v3/home/",
            "/api/authoring/v3/home/",
            "/api/authoring/v4/courses/",
        ), None, None, False)
        paths = result["paths"]
        assert paths["/api/contentstore/v3/home/"]["get"]["x-internal"] is True
        assert paths["/api/contentstore/v3/home/"]["get"]["deprecated"] is True
        assert paths["/api/authoring/v3/home/"]["get"]["x-internal"] is True
        assert "deprecated" not in paths["/api/authoring/v3/home/"]["get"]
        assert "x-internal" not in paths["/api/authoring/v4/courses/"]["get"]

    def test_paths_are_left_as_full_urls(self):
        """Paths are not trimmed, so every key resolves against a service-root server."""
        result = cms_mark_migrated_paths(_schema("/api/contentstore/v1/xblock/"), None, None, False)
        assert list(result["paths"]) == ["/api/contentstore/v1/xblock/"]


class SpectacularSettingsTest(SimpleTestCase):
    """
    The service settings the hooks depend on.

    They are defined once in cms/envs/common.py, which every environment
    inherits, so the test settings carry the same values.
    """

    # devstack.py and production.py only add environment-specific keys.
    SETTINGS_MODULES = ("cms/envs/devstack.py", "cms/envs/production.py")

    def _source(self, module):
        return (Path(__file__).resolve().parents[3] / module).read_text()

    def test_both_hooks_are_configured(self):
        assert settings.SPECTACULAR_SETTINGS["PREPROCESSING_HOOKS"] == ["cms.lib.spectacular.cms_api_filter"]
        hooks = settings.SPECTACULAR_SETTINGS["POSTPROCESSING_HOOKS"]
        assert "cms.lib.spectacular.cms_mark_superseded_paths" in hooks
        assert "cms.lib.spectacular.cms_mark_migrated_paths" in hooks

    def test_no_marking_hook_runs_beside_the_two_this_module_defines(self):
        assert settings.SPECTACULAR_SETTINGS["POSTPROCESSING_HOOKS"] == [
            "drf_spectacular.hooks.postprocess_schema_enums",
            "cms.lib.spectacular.cms_mark_superseded_paths",
            "cms.lib.spectacular.cms_mark_migrated_paths",
            "openedx.core.apidocs.untag_deprecated_operations",
        ]

    def test_the_default_enum_hook_is_kept_alongside_the_new_one(self):
        """Setting the key replaces drf-spectacular's default list, so it is restated."""
        assert "drf_spectacular.hooks.postprocess_schema_enums" in settings.SPECTACULAR_SETTINGS["POSTPROCESSING_HOOKS"]

    def test_no_prefix_is_trimmed_so_paths_are_emitted_in_full(self):
        assert "SCHEMA_PATH_PREFIX_TRIM" not in settings.SPECTACULAR_SETTINGS

    def test_the_tag_prefix_covers_both_mounts(self):
        assert settings.SPECTACULAR_SETTINGS["SCHEMA_PATH_PREFIX"] == r"/api/(contentstore|authoring)"

    def test_no_server_advertises_a_single_mount_as_its_root(self):
        for module in self.SETTINGS_MODULES:
            assert "CMS-contentstore" not in self._source(module), module

    def test_environments_do_not_replace_the_common_settings(self):
        """Replacing the dict drops every hook common.py adds unless it is copied in."""
        for module in self.SETTINGS_MODULES:
            source = self._source(module)
            assert "SPECTACULAR_SETTINGS = {" not in source, module
            # A hook is set by its quoted key; a comment that names one is fine.
            assert not re.search(r"_HOOKS['\"]", source), module


class _EmptySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    pass


class _HomeViewSet(viewsets.GenericViewSet):
    """Stand-in for a viewset served on both a legacy and a conforming mount."""

    schema = CmsAutoSchema()
    serializer_class = _EmptySerializer

    def list(self, request):
        return Response([])


class CmsAutoSchemaTest(SimpleTestCase):
    """Dual mounts get deterministic operationIds instead of registration-order numeral suffixes."""

    def test_is_the_default_schema_class(self):
        """Views without their own ``schema`` pick this up via REST_FRAMEWORK settings."""
        assert api_settings.DEFAULT_SCHEMA_CLASS is CmsAutoSchema

    def test_every_operation_is_described_by_this_class_alone(self):
        assert CmsAutoSchema.__bases__ == (AutoSchema,)

    def test_only_the_colliding_legacy_address_is_suffixed(self):
        patterns = [
            url_path("api/contentstore/v3/home/", _HomeViewSet.as_view({"get": "list"})),
            url_path("api/authoring/v3/home/", _HomeViewSet.as_view({"get": "list"})),
            url_path("api/contentstore/v4/home/courses/", _HomeViewSet.as_view({"get": "list"})),
            url_path("api/authoring/v4/courses/", _HomeViewSet.as_view({"get": "list"})),
        ]
        with mock.patch.object(spectacular_settings, "SCHEMA_PATH_PREFIX", r"/api/(contentstore|authoring)"):
            schema = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)
        ids = {p: op["operationId"] for p, item in schema["paths"].items() for op in item.values()}
        assert ids["/api/authoring/v3/home/"] == "v3_home_list"
        assert ids["/api/contentstore/v3/home/"] == "v3_home_list_legacy"
        # Already distinct from its conforming twin's id, so it keeps its name.
        assert ids["/api/authoring/v4/courses/"] == "v4_courses_list"
        assert ids["/api/contentstore/v4/home/courses/"] == "v4_home_courses_list"


#: Every operation of a superseded user address this service serves, with the
#: name it is published under. The names are the ones both documents have
#: always carried: a client imports a function per name, so an operation that
#: comes back under a different one has been taken away from it.
SUPERSEDED_USER_OPERATION_IDS = {
    ("/api/user/v1/accounts", "get"): "api_user_v1_accounts_retrieve",
    ("/api/user/v1/accounts/name_change/", "post"): "api_user_v1_accounts_name_change_create",
    ("/api/user/v1/accounts/name_change/{username}/confirm/", "post"): (
        "api_user_v1_accounts_name_change_confirm_create"
    ),
    ("/api/user/v1/accounts/search_emails", "post"): "api_user_v1_accounts_search_emails_create",
    ("/api/user/v1/accounts/{username}", "get"): "api_user_v1_accounts_retrieve_2",
    ("/api/user/v1/accounts/{username}", "patch"): "api_user_v1_accounts_partial_update",
    ("/api/user/v1/accounts/{username}/deactivate/", "post"): "api_user_v1_accounts_deactivate_create",
    ("/api/user/v1/accounts/{username}/image", "delete"): "api_user_v1_accounts_image_destroy",
    ("/api/user/v1/accounts/{username}/image", "post"): "api_user_v1_accounts_image_create",
    ("/api/user/v1/accounts/{username}/verification_status/", "get"): (
        "api_user_v1_accounts_verification_status_retrieve"
    ),
    ("/api/user/v1/accounts/{username}/verifications/", "get"): "api_user_v1_accounts_verifications_list",
    ("/api/user/v1/forum_roles/{name}/users/", "get"): "api_user_v1_forum_roles_users_list",
    ("/api/user/v1/me", "get"): "api_user_v1_me_retrieve",
    ("/api/user/v1/preferences/email_opt_in/", "post"): "api_user_v1_preferences_email_opt_in_create",
    ("/api/user/v1/preferences/time_zones/", "get"): "api_user_v1_preferences_time_zones_list",
    ("/api/user/v1/preferences/{pref_key}/users/", "get"): "api_user_v1_preferences_users_list",
    ("/api/user/v1/preferences/{username}", "get"): "api_user_v1_preferences_retrieve",
    ("/api/user/v1/preferences/{username}", "patch"): "api_user_v1_preferences_partial_update",
    ("/api/user/v1/preferences/{username}/{preference_key}", "delete"): "api_user_v1_preferences_destroy",
    ("/api/user/v1/preferences/{username}/{preference_key}", "get"): "api_user_v1_preferences_retrieve_2",
    ("/api/user/v1/preferences/{username}/{preference_key}", "put"): "api_user_v1_preferences_update",
    ("/api/user/v1/user_prefs/", "get"): "api_user_v1_user_prefs_list",
    ("/api/user/v1/user_prefs/{id}/", "get"): "api_user_v1_user_prefs_retrieve",
    ("/api/user/v1/users/", "get"): "api_user_v1_users_list",
    ("/api/user/v1/users/{id}/", "get"): "api_user_v1_users_retrieve",
    ("/api/user/v1/accounts/cancel_retirement/", "post"): "api_user_v1_accounts_cancel_retirement_create",
    ("/api/user/v1/accounts/retire/", "post"): "api_user_v1_accounts_retire_create",
    ("/api/user/v1/accounts/retire_misc/", "post"): "api_user_v1_accounts_retire_misc_create",
    ("/api/user/v1/accounts/retirement_cleanup/", "post"): "api_user_v1_accounts_retirement_cleanup_create",
    ("/api/user/v1/accounts/retirement_partner_report/", "post"): (
        "api_user_v1_accounts_retirement_partner_report_create"
    ),
    ("/api/user/v1/accounts/retirement_partner_report/", "put"): (
        "api_user_v1_accounts_retirement_partner_report_update"
    ),
    ("/api/user/v1/accounts/retirement_partner_report_cleanup/", "post"): (
        "api_user_v1_accounts_retirement_partner_report_cleanup_create"
    ),
    ("/api/user/v1/accounts/retirement_queue/", "get"): "api_user_v1_accounts_retirement_queue_retrieve",
    ("/api/user/v1/accounts/retirements_by_status_and_date/", "get"): (
        "api_user_v1_accounts_retirements_by_status_and_date_retrieve"
    ),
    ("/api/user/v1/accounts/update_retirement_status/", "patch"): (
        "api_user_v1_accounts_update_retirement_status_partial_update"
    ),
    ("/api/user/v1/accounts/{username}/retirement_status/", "get"): "api_user_v1_accounts_retirement_status_retrieve",
    ("/api/user/v1/account/registration/", "get"): "api_user_v1_account_registration_retrieve",
    ("/api/user/v1/account/registration/", "post"): "api_user_v1_account_registration_create",
    ("/api/user/v2/account/registration/", "get"): "api_user_v2_account_registration_retrieve",
    ("/api/user/v2/account/registration/", "post"): "api_user_v2_account_registration_create",
    ("/api/user/v1/account/password_reset/", "get"): "api_user_v1_account_password_reset_retrieve",
    ("/api/user/v1/account/password_reset/token/validate/", "post"): (
        "api_user_v1_account_password_reset_token_validate_create"
    ),
    ("/api/user/v1/validation/registration", "post"): "api_user_v1_validation_registration_create",
    ("/api/user/{api_version}/account/login_session/", "get"): "api_user_account_login_session_retrieve",
    ("/api/user/{api_version}/account/login_session/", "post"): "api_user_account_login_session_create",
}


#: The authentication app's mount, which serves its addresses under the user prefix.
AUTHN_URLS = "openedx.core.djangoapps.user_authn.urls_common"

#: The user addresses this service marks superseded.
SUPERSEDED_USER_ADDRESSES = tuple(path for path in SUPERSEDED_PATHS if path.startswith("/api/user/"))

#: The preference operations this service's own pages call. It serves no
#: replacement for them, so its document publishes them as current.
CURRENT_PREFERENCE_OPERATIONS = frozenset({
    ("/api/user/v1/preferences/{username}", "get"),
    ("/api/user/v1/preferences/{username}", "patch"),
    ("/api/user/v1/preferences/{username}/{preference_key}", "delete"),
    ("/api/user/v1/preferences/{username}/{preference_key}", "get"),
    ("/api/user/v1/preferences/{username}/{preference_key}", "put"),
    ("/api/user/v1/preferences/email_opt_in/", "post"),
})


class SupersededUserOperationTest(DjangoTestCase):
    """
    The user addresses this service serves beside the LMS, which serves their replacements.

    Both services mount that API at the same addresses and both publish an
    unauthenticated document of everything they serve. This one marks the
    addresses it lists as superseded and keeps the name each operation is
    published under.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The authentication app serves the superseded registration, sign-in
        # and password reset addresses from a mount of its own.
        patterns = [
            entry for entry in import_module(settings.ROOT_URLCONF).urlpatterns
            if str(entry.pattern).startswith("api/user/")
            or getattr(getattr(entry, "urlconf_module", None), "__name__", None) == AUTHN_URLS
        ]
        # The whole document derives its path prefix from every address the
        # service serves, which share nothing past the root.
        with patched_settings({**get_api_docs_settings(), "SCHEMA_PATH_PREFIX": "/"}):
            cls.schema = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)

    def _operations(self):
        """Every operation of the user addresses that have a replacement, as this document carries it."""
        return {
            (address, method): self.schema["paths"][address][method]
            for address, method in SUPERSEDED_USER_OPERATION_IDS
        }

    def test_this_service_serves_every_address_it_marks(self):
        """The mark is matched on the exact address, so one this document spells otherwise is never marked."""
        assert [address for address in SUPERSEDED_USER_ADDRESSES if address not in self.schema["paths"]] == []

    def test_every_operation_of_the_addresses_it_lists_is_marked_superseded(self):
        marked = {
            key: operation.get("deprecated") for key, operation in self._operations().items()
            if key not in CURRENT_PREFERENCE_OPERATIONS
        }
        assert marked == dict.fromkeys(set(SUPERSEDED_USER_OPERATION_IDS) - CURRENT_PREFERENCE_OPERATIONS, True)

    def test_the_preference_addresses_its_own_pages_call_are_published_as_current(self):
        operations = self._operations()
        marked = {key: "deprecated" in operations[key] for key in CURRENT_PREFERENCE_OPERATIONS}
        assert marked == dict.fromkeys(CURRENT_PREFERENCE_OPERATIONS, False)

    def test_every_operation_keeps_the_name_it_is_published_under(self):
        published = {key: operation["operationId"] for key, operation in self._operations().items()}
        assert published == SUPERSEDED_USER_OPERATION_IDS

    def test_signing_in_by_posting_keeps_what_it_has_always_published(self):
        """Its handler is described where it is defined, and nothing here replaces that."""
        operation = self.schema["paths"]["/api/user/{api_version}/account/login_session/"]["post"]
        assert {media_type: body["schema"] for media_type, body in operation["requestBody"]["content"].items()} == (
            dict.fromkeys(
                ["application/json", "application/x-www-form-urlencoded", "multipart/form-data"],
                {"$ref": "#/components/schemas/LoginUserRequest"},
            )
        )
        assert {
            code: response["content"]["application/json"]["schema"] for code, response in operation["responses"].items()
        } == dict.fromkeys(["200", "400", "403"], {"$ref": "#/components/schemas/LoginUserResponse"})
        assert operation["security"] == [{"csrf": []}]

    def test_no_address_replacing_a_superseded_one_is_published_here(self):
        """This service serves none of them, so its document describes only what it serves."""
        under_version_two = {path for path in self.schema["paths"] if path.startswith("/api/user/v2/")}
        # The authentication app serves a superseded registration under the same prefix.
        assert "/api/user/v2/account/registration/" in under_version_two, sorted(under_version_two)
        replacing = {path for path in under_version_two if not path.startswith("/api/user/v2/account/")}
        assert replacing == set()

    def test_the_retirement_status_model_is_published_as_the_other_service_appends_it(self):
        """
        A client generated from this document names a class after each component in it.

        The partial update of a version 1 retirement refers to the
        retirement-status model here, as it always has. The other service
        publishes that operation's real body instead and appends the model so
        that the class keeps its name, and the two describe it identically.
        """
        operation = self.schema["paths"]["/api/user/v1/accounts/update_retirement_status/"]["patch"]
        bodies = {content["schema"]["$ref"] for content in operation["requestBody"]["content"].values()}
        assert bodies == {"#/components/schemas/PatchedUserRetirementStatus"}
        assert self.schema["components"]["schemas"]["PatchedUserRetirementStatus"] == PATCHED_USER_RETIREMENT_STATUS


#: How each served document is expected to describe the CSRF token, written out
#: rather than read from the definition the service appends, so that a change
#: to that definition shows up here as a change to what callers are told.
CSRF_SCHEME = {
    "type": "apiKey",
    "name": "X-CSRFToken",
    "in": "header",
    "description": (
        "The value of the `csrftoken` cookie the sign-in, registration and password reset forms set, "
        "sent in this header along with that cookie."
    ),
}


def credentials_named(document):
    """Return each credential an operation of a document names, with the operations naming it."""
    named = defaultdict(set)
    for route, path_item in document["paths"].items():
        for method, operation in path_item.items():
            if isinstance(operation, dict):
                for alternative in operation.get("security", ()):
                    for credential in alternative:
                        named[credential].add((method, route))
    return named


class ServedCredentialSchemesTest(DjangoTestCase):
    """
    Each document this service serves describes every credential its operations name.

    A client is generated from one document, and it can only offer the caller
    a way to send a credential the document describes; an operation naming one
    the document does not describe makes the document invalid. This service serves the user API's older sign-in, which
    names the CSRF token, so the token is described here as well as by the
    other service. The documents are requested at the addresses they are served
    from, because what each carries depends on the settings it is generated
    under.
    """

    ADDRESSES = ("/authoring-api/schema/", "/api-docs/schema/")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        client = Client()
        cls.documents = {}
        for address in cls.ADDRESSES:
            response = client.get(address, {"format": "json"}, HTTP_ACCEPT="application/vnd.oai.openapi+json")
            assert response.status_code == 200, (address, response.status_code)
            cls.documents[address] = json.loads(response.content)

    def test_every_credential_an_operation_names_is_described_in_the_same_document(self):
        named = {address: credentials_named(document) for address, document in self.documents.items()}
        # The older sign-in served here names the CSRF token, and other operations the session cookie.
        assert {"cookieAuth", "csrf"} <= set(named["/api-docs/schema/"]), sorted(named["/api-docs/schema/"])
        for address, document in self.documents.items():
            described = set(document["components"].get("securitySchemes", {}))
            assert {name: sorted(named[address][name]) for name in set(named[address]) - described} == {}, address

    def test_the_csrf_token_is_described_where_the_older_sign_in_names_it(self):
        document = self.documents["/api-docs/schema/"]
        sign_in = ("post", "/api/user/{api_version}/account/login_session/")
        assert sign_in in credentials_named(document)["csrf"]
        assert document["components"].get("securitySchemes", {}).get("csrf") == CSRF_SCHEME

    def test_every_document_describing_the_csrf_token_describes_it_alike(self):
        described = {
            address: document["components"].get("securitySchemes", {}).get("csrf")
            for address, document in self.documents.items()
        }
        describing = {address: scheme for address, scheme in described.items() if scheme is not None}
        # The document whose older sign-in names the token is one of them.
        assert "/api-docs/schema/" in describing, sorted(describing)
        assert describing == dict.fromkeys(describing, CSRF_SCHEME)
