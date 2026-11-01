"""Tests for the CMS drf-spectacular hooks and schema class."""

import re
from pathlib import Path
from unittest import TestCase, mock

from django.conf import settings
from django.test import SimpleTestCase
from django.test import TestCase as DjangoTestCase
from django.urls import path as url_path
from drf_spectacular.generators import SchemaGenerator
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
from lms.lib.spectacular import SUPERSEDED_USER_PATHS, superseded_bodies
from openedx.core.apidocs import get_api_docs_settings


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
        assert set(SUPERSEDED_PATHS) == {
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


#: What each superseded user operation this service serves is expected to
#: exchange: the media types of the body it reads, and the success code with
#: the body it answers with. ``None`` is a response carrying no body at all.
#:
#: The table is spelled out here rather than taken from the tests of the
#: service that serves the replacements, so that a change made to what one of
#: the two documents says about these addresses and not to the other is a
#: failure here rather than a difference a client only finds after generating
#: from both.
SUPERSEDED_USER_EXCHANGES = {
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
    ("/api/user/v1/preferences/time_zones/", "get"): "api_user_v1_preferences_time_zones_list",
    ("/api/user/v1/preferences/{pref_key}/users/", "get"): "api_user_v1_preferences_users_list",
    ("/api/user/v1/user_prefs/", "get"): "api_user_v1_user_prefs_list",
    ("/api/user/v1/user_prefs/{id}/", "get"): "api_user_v1_user_prefs_retrieve",
    ("/api/user/v1/users/", "get"): "api_user_v1_users_list",
    ("/api/user/v1/users/{id}/", "get"): "api_user_v1_users_retrieve",
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


class SupersededUserOperationTest(DjangoTestCase):
    """
    The user addresses this service serves beside the service that replaces them.

    Both services mount that API at the same addresses and both publish an
    unauthenticated document of everything they serve, so what one of those
    documents says about an address is what the other has to say too: the same
    body on each side of the exchange, the same success code, the same
    published name, and the same mark saying a replacement exists. A client
    generated from this service's document would otherwise offer a different
    signature, and no mark, for the very same request.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with patched_settings(get_api_docs_settings()):
            cls.schema = SchemaGenerator().get_schema(request=None, public=True)

    def _operations(self):
        """Every operation of a superseded address this document carries."""
        return {
            (path, method): operation
            for path in SUPERSEDED_USER_PATHS if path in self.schema["paths"]
            for method, operation in self.schema["paths"][path].items()
            if isinstance(operation, dict) and "operationId" in operation
        }

    def test_this_service_serves_every_superseded_address(self):
        """The marking and the bodies below are claims about all of them, not about some."""
        served = [path for path in SUPERSEDED_USER_PATHS if path in self.schema["paths"]]
        assert served == list(SUPERSEDED_USER_PATHS)

    def test_every_operation_of_those_addresses_is_marked_superseded(self):
        marked = {key: operation.get("deprecated") for key, operation in self._operations().items()}
        assert marked == dict.fromkeys(SUPERSEDED_USER_OPERATION_IDS, True)

    def test_every_operation_keeps_the_name_it_is_published_under(self):
        published = {key: operation["operationId"] for key, operation in self._operations().items()}
        assert published == SUPERSEDED_USER_OPERATION_IDS

    def test_each_superseded_operation_publishes_what_it_reads_and_what_it_answers_with(self):
        published = {}
        for (address, method) in SUPERSEDED_USER_EXCHANGES:
            operation = self.schema["paths"][address][method]
            published[(address, method)] = (
                sorted((operation.get("requestBody") or {}).get("content", {})),
                {code: _named_body(response) for code, response in operation["responses"].items()},
            )
        assert published == SUPERSEDED_USER_EXCHANGES

    def test_an_image_may_be_sent_as_the_whole_body_or_as_a_form_part(self):
        """The forms are alternatives, and a body one of them needs is one the operation needs."""
        image = self.schema["paths"]["/api/user/v1/accounts/{username}/image"]["post"]["requestBody"]
        assert image["required"] is True
        assert sorted(image["content"]) == [
            "image/gif", "image/jpeg", "image/pjpeg", "image/png", "multipart/form-data",
        ]

    def test_the_described_addresses_are_ones_this_document_marks_superseded(self):
        described = {address for address, _method in superseded_bodies()}
        marked = {re.sub(r"\{[\w-]+\}", "{}", address) for address in SUPERSEDED_USER_PATHS}
        assert described <= marked, described - marked
