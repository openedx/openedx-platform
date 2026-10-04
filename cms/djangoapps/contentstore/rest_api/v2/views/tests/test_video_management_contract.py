"""
Contract tests shared by the course video usage and video archive APIs.
"""
import functools
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import ddt
import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase
from django.urls import Resolver404, get_resolver, resolve, reverse
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import patched_settings
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework.exceptions import NotFound

import cms.envs
from cms.djangoapps.contentstore.rest_api.v2.serializers.video_usages import VideoUsageLocationSerializer
from cms.djangoapps.contentstore.rest_api.v2.views.video_archives import CourseVideoArchiveView
from cms.djangoapps.contentstore.rest_api.v2.views.video_usages import CourseVideoUsageViewSet
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.views.permissions import HasFullCourseKey, HasStudioReadAccess
from cms.lib.spectacular import SUPERSEDED_PATH_PREFIXES, cms_api_filter, cms_mark_migrated_paths
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.factories import CourseFactory

USAGE_PATH = "/api/authoring/v2/courses/{course_key}/video_usages/{edx_video_id}/"
ARCHIVE_PATH = "/api/authoring/v2/courses/{course_key}/video_archives/"
LEGACY_USAGE_PATH = "/api/contentstore/v1/videos/{course_id}/{edx_video_id}/usage"
LEGACY_DOWNLOAD_PATH = "/api/contentstore/v1/videos/{course_id}/download"

ENUM_POSTPROCESSING_HOOK = "drf_spectacular.hooks.postprocess_schema_enums"
MIGRATED_PATHS_HOOK = "cms.lib.spectacular.cms_mark_migrated_paths"

PRODUCTION_SETTINGS = "cms.envs.production"
DEVSTACK_SETTINGS = "cms.envs.devstack"
SETTINGS_DIRECTORY = Path(cms.envs.__file__).resolve().parent
REPO_ROOT = SETTINGS_DIRECTORY.parents[1]
MOCK_CONFIG = SETTINGS_DIRECTORY / "mock.yml"

# The schema settings a document generated in this process has to be given for
# its addresses to come out the way the deployment publishes them.
GENERATION_SETTING_KEYS = ("PREPROCESSING_HOOKS", "POSTPROCESSING_HOOKS", "SCHEMA_PATH_PREFIX")
SETTINGS_MARKER = "schema settings: "

# Keys the course_key path converter accepts that carry only a content version,
# with no organization, course or run.
VERSION_ONLY_COURSE_KEYS = (
    "course-v1:version@" + "a" * 24,
    "course-v1:branch@published-branch+version@" + "a" * 24,
    "ccx-v1:version@" + "a" * 24 + "+ccx@1",
    "library-v1:version@" + "a" * 24,
)


@functools.lru_cache
def schema_settings(module):
    """
    Return ``SPECTACULAR_SETTINGS`` of the deployment settings module ``module``.

    Deployment settings share their mutable defaults with the settings the test
    process runs under and would alter them on import, so they are read in a
    separate process.
    """
    script = (
        "import importlib, json, sys\n"
        "values = importlib.import_module(sys.argv[1]).SPECTACULAR_SETTINGS\n"
        "print(sys.argv[2] + json.dumps(values))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, module, SETTINGS_MARKER],
        capture_output=True,
        check=True,
        cwd=REPO_ROOT,
        env={**os.environ, "CMS_CFG": str(MOCK_CONFIG), "SERVICE_VARIANT": "cms"},
        text=True,
    )
    reported = next(line for line in completed.stdout.splitlines() if line.startswith(SETTINGS_MARKER))
    return json.loads(reported[len(SETTINGS_MARKER):])


def generate_document(postprocessing_hooks=None, generator=None):
    """Generate the Studio document with the production schema settings."""
    configured = schema_settings(PRODUCTION_SETTINGS)
    overrides = {key: configured[key] for key in GENERATION_SETTING_KEYS}
    if postprocessing_hooks is not None:
        overrides["POSTPROCESSING_HOOKS"] = postprocessing_hooks
    with patched_settings(overrides):
        return (generator or SchemaGenerator()).get_schema(request=None, public=True)


@functools.lru_cache
def production_document():
    return generate_document()


def operations(document):
    """Yield (path, method, operation) for every operation of ``document``."""
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if isinstance(operation, dict) and "operationId" in operation:
                yield path, method, operation


def record_assigned_operation_ids(result, generator, request, public):  # pylint: disable=unused-argument
    """
    Post-processing hook: keep every operation's id on the generator, unchanged.

    Post-processing runs before the generator renames colliding ids with numeric
    suffixes, so the ids kept here are the ones each path was given.
    """
    generator.assigned_operation_ids = [
        (operation["operationId"], path, method) for path, method, operation in operations(result)
    ]
    return result


RECORDING_HOOK = f"{record_assigned_operation_ids.__module__}.{record_assigned_operation_ids.__name__}"


class VideoRoutesTest(TestCase):
    """Route names: the legacy ones stay as they are; the new ones are unique."""

    COURSE_KEY = "course-v1:edX+DemoX+Demo_Course"

    # The routes of these two resources, in order. Other resources share the
    # namespace, so only these are pinned.
    ROUTE_NAMES = (
        "course_video_usage_detail",
        "course_video_archive_list",
        "course_video_usage_list_unmatched",
        "course_video_usage_detail_unmatched",
        "course_video_archive_list_unmatched",
        "course_video_archive_detail_unmatched",
    )

    # Addresses outside the two collections, some named like them, that another
    # resource may serve. Neither fallback may claim them.
    ADDRESSES_OUTSIDE_THE_COLLECTIONS = (
        f"/api/authoring/v2/courses/{COURSE_KEY}/",
        f"/api/authoring/v2/courses/{COURSE_KEY}/textbooks/",
        "/api/authoring/v2/courses/Org/Course/Run/textbooks/",
        f"/api/authoring/v2/courses/{COURSE_KEY}/textbooks/video_usages_report/",
        f"/api/authoring/v2/courses/{COURSE_KEY}/video_usage/abc-123/",
        f"/api/authoring/v2/courses/{COURSE_KEY}/all_video_archives/",
        "/api/authoring/v2/video_usages/",
    )

    def test_the_shared_legacy_name_without_a_video_id_reverses_to_the_download_route(self):
        address = reverse("cms.djangoapps.contentstore:v1:video_usage", kwargs={"course_id": self.COURSE_KEY})

        assert address == f"/api/contentstore/v1/videos/{self.COURSE_KEY}/download"
        assert resolve(address).func.view_class.__name__ == "VideoDownloadView"

    def test_the_shared_legacy_name_with_a_video_id_reverses_to_the_usage_route(self):
        address = reverse(
            "cms.djangoapps.contentstore:v1:video_usage",
            kwargs={"course_id": self.COURSE_KEY, "edx_video_id": "abc-123"},
        )

        assert address == f"/api/contentstore/v1/videos/{self.COURSE_KEY}/abc-123/usage"
        assert resolve(address).func.view_class.__name__ == "VideoUsageView"

    def test_the_new_route_names_are_unique_and_carry_no_version(self):
        __, resolver = get_resolver().namespace_dict["authoring_v2"]

        names = [pattern.name for pattern in resolver.url_patterns if pattern.name in self.ROUTE_NAMES]

        # Each fallback route has to come after the route it backs, or it would
        # answer valid course keys too.
        assert names == list(self.ROUTE_NAMES)
        assert resolver.app_name == "authoring_v2"
        assert not [name for name in names if "v1" in name or "v2" in name]

    def test_the_fallbacks_claim_nothing_outside_the_two_collections(self):
        fallbacks = {f"authoring_v2:{name}" for name in self.ROUTE_NAMES if name.endswith("_unmatched")}
        claimed = []
        for address in self.ADDRESSES_OUTSIDE_THE_COLLECTIONS:
            try:
                match = resolve(address)
            except Resolver404:
                continue
            if match.view_name in fallbacks:
                claimed.append(address)

        assert not claimed, claimed


class HasStudioReadAccessTest(CourseTestCase):
    """The permission grants exactly the callers with Studio read access to the course in the path."""

    def allows(self, user, course_key=None, kwargs=None):
        request = RequestFactory().get("/")
        request.user = user
        view = SimpleNamespace(kwargs={"course_key": course_key or self.course.id} if kwargs is None else kwargs)
        return HasStudioReadAccess().has_permission(request, view)

    def user_with_role(self, role_class, course_key=None):
        user = UserFactory.create()
        role_class(course_key or self.course.id).add_users(user)
        return user

    def test_global_staff_is_allowed(self):
        assert self.allows(self.user) is True

    def test_course_staff_is_allowed(self):
        assert self.allows(self.user_with_role(CourseStaffRole)) is True

    def test_course_instructor_is_allowed(self):
        assert self.allows(self.user_with_role(CourseInstructorRole)) is True

    def test_user_without_a_role_is_refused(self):
        assert self.allows(UserFactory.create()) is False

    def test_anonymous_is_refused(self):
        assert self.allows(AnonymousUser()) is False

    def test_limited_staff_is_refused(self):
        assert self.allows(self.user_with_role(CourseLimitedStaffRole)) is False

    def test_staff_of_another_course_is_refused(self):
        other_course = CourseFactory.create()

        assert self.allows(self.user_with_role(CourseStaffRole, course_key=other_course.id)) is False

    def test_ccx_course_is_refused_to_global_staff(self):
        assert self.allows(self.user, course_key=CourseKey.from_string("ccx-v1:edX+DemoX+Demo_Course+ccx@1")) is False

    def test_a_view_without_a_course_key_is_refused(self):
        assert self.allows(self.user, kwargs={}) is False

    # The role checks behind this class cannot evaluate a key that carries only
    # a content version, which is why the views list HasFullCourseKey first.
    def test_a_key_without_an_organization_cannot_be_evaluated(self):
        with pytest.raises(InvalidKeyError):
            self.allows(self.user, course_key=CourseKey.from_string(VERSION_ONLY_COURSE_KEYS[0]))


@ddt.ddt
class HasFullCourseKeyTest(TestCase):
    """The permission answers 404 for a course key with no organization, course or run, and passes any other."""

    def check(self, kwargs):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        return HasFullCourseKey().has_permission(request, SimpleNamespace(kwargs=kwargs))

    @ddt.data(*VERSION_ONLY_COURSE_KEYS)
    def test_a_key_carrying_only_a_version_is_not_found(self, key):
        with pytest.raises(NotFound):
            self.check({"course_key": CourseKey.from_string(key)})

    @ddt.data(
        "course-v1:edX+DemoX+Demo_Course",
        "course-v1:edX+DemoX+Demo_Course+version@" + "a" * 24,
        "ccx-v1:edX+DemoX+Demo_Course+ccx@1",
        "library-v1:edX+DemoLib",
    )
    def test_a_key_naming_an_organization_passes(self, key):
        assert self.check({"course_key": CourseKey.from_string(key)}) is True

    def test_a_view_without_a_course_key_passes(self):
        assert self.check({}) is True


class CmsSchemaHookTest(TestCase):
    """The schema hooks admit the new addresses and deprecate exactly the two superseded ones."""

    def test_filter_admits_the_authoring_prefix(self):
        admitted = cms_api_filter([
            ("/api/authoring/v2/courses/course-v1:a+b+c/video_archives/", None, "post", None),
            ("/api/contentstore/v1/videos/course-v1:a+b+c/download", None, "put", None),
            ("/api/authoring/v2", None, "get", None),
            ("/login", None, "get", None),
        ])

        assert [entry[0] for entry in admitted] == [
            "/api/authoring/v2/courses/course-v1:a+b+c/video_archives/",
            "/api/contentstore/v1/videos/course-v1:a+b+c/download",
        ]

    def test_filter_refuses_the_versioned_paths_of_other_apis(self):
        admitted = cms_api_filter([
            ("/api/xblock/v2/xblocks/lb:Org:lib:html:abc/", None, "get", None),
            ("/api/content_tagging/v1/taxonomies/", None, "get", None),
            ("/api/libraries/v2/", None, "get", None),
            ("/api/courses/v1/courses/", None, "get", None),
            ("/api/authoring_tools/v1/", None, "get", None),
            ("/api/v1/authoring/", None, "get", None),
        ])

        assert not admitted, admitted

    def test_filter_admits_the_course_discussions_switch_and_nothing_else_under_courses(self):
        admitted = cms_api_filter([
            ("/api/courses/{course_key_string}/bulk_enable_disable_discussions", None, "put", None),
            ("/api/courses/{course_key_string}/updates", None, "get", None),
            ("/api/discussions/v0/bulk_enable_disable_discussions", None, "get", None),
        ])

        assert [entry[0] for entry in admitted] == [
            "/api/courses/{course_key_string}/bulk_enable_disable_discussions",
        ]

    def test_the_superseded_paths_include_the_two_video_operations(self):
        assert [
            prefix for prefix in SUPERSEDED_PATH_PREFIXES if prefix in (LEGACY_USAGE_PATH, LEGACY_DOWNLOAD_PATH)
        ] == [LEGACY_USAGE_PATH, LEGACY_DOWNLOAD_PATH]

    # Operations of other resources that nothing supersedes, next to the two
    # superseded ones or elsewhere in Studio, and the new addresses. Marking any
    # of them would tell clients to leave an operation that has no successor.
    # Resources superseded in their own right are not listed: whether they are
    # marked is decided with their successors, not here.
    OTHER_RESOURCE_PATHS = (
        "/api/contentstore/v1/videos/{course_id}",
        "/api/contentstore/v0/videos/encodings/{course_id}",
        "/api/contentstore/v0/videos/features",
        "/api/contentstore/v0/videos/images/{course_id}/{edx_video_id}",
        "/api/contentstore/v2/home/courses",
        "/api/contentstore/v2/downstreams/{usage_key_string}",
        USAGE_PATH,
        ARCHIVE_PATH,
    )

    def marked_schema(self):
        """Run the post-processing hook over the superseded paths and those of other resources."""
        paths = {
            LEGACY_USAGE_PATH: {"get": {}},
            LEGACY_DOWNLOAD_PATH: {"put": {}},
        }
        paths.update({path: {"get": {}, "post": {}} for path in self.OTHER_RESOURCE_PATHS})
        return cms_mark_migrated_paths({"paths": paths}, None, None, True)

    def test_post_processing_marks_the_two_superseded_operations_deprecated(self):
        paths = self.marked_schema()["paths"]

        assert paths[LEGACY_USAGE_PATH] == {"get": {"deprecated": True}}
        assert paths[LEGACY_DOWNLOAD_PATH] == {"put": {"deprecated": True}}

    def test_post_processing_leaves_other_resources_alone(self):
        paths = self.marked_schema()["paths"]

        assert {path: paths[path] for path in self.OTHER_RESOURCE_PATHS} == {
            path: {"get": {}, "post": {}} for path in self.OTHER_RESOURCE_PATHS
        }

    def test_post_processing_leaves_other_members_of_a_path_alone(self):
        result = cms_mark_migrated_paths(
            {"paths": {LEGACY_DOWNLOAD_PATH: {"put": {}, "parameters": []}}}, None, None, True,
        )

        assert result["paths"][LEGACY_DOWNLOAD_PATH] == {"put": {"deprecated": True}, "parameters": []}


@ddt.ddt
class CmsSchemaDocumentTest(TestCase):
    """The published Studio document, generated with the production schema settings."""

    def test_both_new_operations_are_published_at_their_full_address(self):
        document = production_document()

        assert [method for method in document["paths"][USAGE_PATH] if method != "parameters"] == ["get"]
        assert [method for method in document["paths"][ARCHIVE_PATH] if method != "parameters"] == ["post"]

    def test_only_the_studio_apis_are_published(self):
        paths = list(production_document()["paths"])

        assert {path.split("/")[2] for path in paths} == {"contentstore", "authoring", "courses"}
        assert [path for path in paths if path.startswith("/api/courses/")] == [
            "/api/courses/{course_key_string}/bulk_enable_disable_discussions",
        ]

    def test_no_other_address_of_these_resources_is_published(self):
        published = {
            path
            for path in production_document()["paths"]
            if path.startswith("/api/authoring/") and {"video_usages", "video_archives"} & set(path.split("/"))
        }

        assert published == {USAGE_PATH, ARCHIVE_PATH}

    def test_the_published_addresses_resolve_to_the_new_views(self):
        usage = resolve(USAGE_PATH.format(course_key="course-v1:a+b+c", edx_video_id="v1"))
        archive = resolve(ARCHIVE_PATH.format(course_key="course-v1:a+b+c"))

        assert (usage.func.cls, archive.func.cls) == (CourseVideoUsageViewSet, CourseVideoArchiveView)

    def test_operation_ids(self):
        document = production_document()

        assert document["paths"][USAGE_PATH]["get"]["operationId"] == "v2_courses_video_usages_retrieve"
        assert document["paths"][ARCHIVE_PATH]["post"]["operationId"] == "v2_courses_video_archives_create"
        assert document["paths"][LEGACY_USAGE_PATH]["get"]["operationId"] == "v1_videos_usage_retrieve"
        assert document["paths"][LEGACY_DOWNLOAD_PATH]["put"]["operationId"] == "v1_videos_download_update"

    def test_no_authoring_operation_is_given_the_id_of_another(self):
        # Ids are derived from the path after its API prefix, so an authoring
        # path can be given the id of a contentstore one. The generator then
        # renames one of the two with a numeric suffix, possibly the existing
        # operation, so a collision is checked on the ids as first assigned.
        generator = SchemaGenerator()
        hooks = [*schema_settings(PRODUCTION_SETTINGS)["POSTPROCESSING_HOOKS"], RECORDING_HOOK]
        generate_document(postprocessing_hooks=hooks, generator=generator)
        holders = defaultdict(list)
        for operation_id, path, method in generator.assigned_operation_ids:
            holders[operation_id].append((path, method))

        shared = {
            operation_id: places
            for operation_id, places in holders.items()
            if len(places) > 1 and any(path.startswith("/api/authoring/") for path, __ in places)
        }
        assert shared == {}
        assert sorted((path, method) for __, path, method in generator.assigned_operation_ids) == sorted(
            (path, method) for path, method, __ in operations(production_document())
        )

    # Published operations nothing supersedes: the neighbours of the two
    # superseded ones, other Studio resources, and the new addresses. Resources
    # superseded in their own right are not listed.
    UNMARKED_OPERATIONS = (
        ("/api/contentstore/v1/videos/{course_id}", "get"),
        ("/api/contentstore/v0/videos/encodings/{course_id}", "get"),
        ("/api/contentstore/v0/videos/features", "get"),
        ("/api/contentstore/v0/videos/images/{course_id}/{edx_video_id}", "post"),
        ("/api/contentstore/v2/home/courses", "get"),
        ("/api/contentstore/v2/downstreams/{usage_key_string}", "get"),
        (USAGE_PATH, "get"),
        (ARCHIVE_PATH, "post"),
    )

    @staticmethod
    def marks(document, path, method):
        operation = document["paths"][path][method]
        return operation.get("deprecated"), operation.get("x-internal")

    def test_the_two_legacy_operations_are_deprecated(self):
        document = production_document()

        assert self.marks(document, LEGACY_USAGE_PATH, "get") == (True, None)
        assert self.marks(document, LEGACY_DOWNLOAD_PATH, "put") == (True, None)

    def test_other_operations_are_left_unmarked(self):
        document = production_document()

        assert {key: self.marks(document, *key) for key in self.UNMARKED_OPERATIONS} == {
            key: (None, None) for key in self.UNMARKED_OPERATIONS
        }

    # The document declares no security schemes for the platform's default
    # authentication, so the refusal text is where a client learns what to send.
    @ddt.data(
        (USAGE_PATH, "get", (
            "The request carries no accepted credentials. Send a JSON Web Token in the Authorization "
            "header as 'JWT <token>' (the OAuth2 access token endpoint issues one when the token request "
            "sets token_type=jwt), or the session cookie of a signed-in Studio user whose account is "
            "active. OAuth2 access tokens sent as 'Bearer <token>' are not accepted."
        )),
        (ARCHIVE_PATH, "post", (
            "The request carries no accepted credentials. Send a JSON Web Token in the Authorization "
            "header as 'JWT <token>' (the OAuth2 access token endpoint issues one when the token request "
            "sets token_type=jwt), or the session cookie of a signed-in Studio user whose account is "
            "active, together with the CSRF token in the X-CSRFToken header. OAuth2 access tokens sent "
            "as 'Bearer <token>' are not accepted."
        )),
    )
    @ddt.unpack
    def test_the_accepted_credentials_are_named(self, path, method, description):
        operation = production_document()["paths"][path][method]

        assert operation["responses"]["401"]["description"] == description

    @ddt.data(
        (USAGE_PATH, "get", "403", "The requester does not have Studio access to the course."),
        (ARCHIVE_PATH, "post", "403", (
            "The requester does not have Studio access to the course, or a request made with a session "
            "did not send the CSRF token."
        )),
        (ARCHIVE_PATH, "post", "406", (
            "The Accept header does not admit application/json. Errors are sent as JSON, so a request "
            "for the archive sends 'Accept: application/zip, application/json', or no Accept header. "
            "Nothing is fetched."
        )),
    )
    @ddt.unpack
    def test_each_refusal_names_its_reasons(self, path, method, code, description):
        operation = production_document()["paths"][path][method]

        assert operation["responses"][code]["description"] == description

    def test_both_new_operations_are_tagged_for_the_sdk(self):
        document = production_document()

        assert document["paths"][USAGE_PATH]["get"]["tags"] == ["openedx-platform-sdk"]
        assert document["paths"][ARCHIVE_PATH]["post"]["tags"] == ["openedx-platform-sdk"]

    def component(self, document, reference):
        return document["components"]["schemas"][reference["$ref"].rsplit("/", 1)[-1]]

    def test_the_usage_response_is_declared(self):
        document = production_document()
        response = document["paths"][USAGE_PATH]["get"]["responses"]["200"]

        body = self.component(document, response["content"]["application/json"]["schema"])
        entry = self.component(document, body["properties"]["usage_locations"]["items"])
        assert body["required"] == ["usage_locations"]
        assert sorted(entry["properties"]) == ["display_location", "url"]
        fields = VideoUsageLocationSerializer().fields
        assert {name: entry["properties"][name]["description"] for name in fields} == {
            name: field.help_text for name, field in fields.items()
        }

    def test_the_archive_request_body_is_declared(self):
        document = production_document()
        request_body = document["paths"][ARCHIVE_PATH]["post"]["requestBody"]

        body = self.component(document, request_body["content"]["application/json"]["schema"])
        entry = self.component(document, body["properties"]["files"]["items"])
        assert list(request_body["content"]) == ["application/json"]
        assert request_body["required"] is True
        assert body["required"] == ["files"]
        assert sorted(entry["required"]) == ["name", "url"]

    def test_the_archive_response_is_a_binary_zip(self):
        response = production_document()["paths"][ARCHIVE_PATH]["post"]["responses"]["200"]

        assert response["content"] == {"application/zip": {"schema": {"type": "string", "format": "binary"}}}
        assert "<course key>_videos.zip" in response["description"]

    @ddt.data(
        (USAGE_PATH, "get", ["401", "403", "404"]),
        (ARCHIVE_PATH, "post", ["400", "401", "403", "404", "406", "415", "429"]),
    )
    @ddt.unpack
    def test_every_error_response_is_the_error_envelope(self, path, method, codes):
        responses = production_document()["paths"][path][method]["responses"]

        assert sorted(code for code in responses if code != "200") == codes
        for code in codes:
            assert responses[code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }

    def test_enum_components_survive_the_registered_post_processing(self):
        without_enum_hook = generate_document(postprocessing_hooks=[MIGRATED_PATHS_HOOK])

        assert not [name for name in without_enum_hook["components"]["schemas"] if name.endswith("Enum")]
        assert "ContentTypeEnum" in production_document()["components"]["schemas"]


@ddt.ddt
class CmsSchemaSettingsTest(TestCase):
    """The deployment settings publish full paths, and no server adds an API prefix of its own."""

    @ddt.data(PRODUCTION_SETTINGS, DEVSTACK_SETTINGS)
    def test_paths_are_published_in_full(self, module):
        configured = schema_settings(module)

        assert "SCHEMA_PATH_PREFIX_TRIM" not in configured
        assert configured["SCHEMA_PATH_PREFIX"] == r"/api/(contentstore|authoring)"

    @ddt.data(PRODUCTION_SETTINGS, DEVSTACK_SETTINGS)
    def test_the_post_processing_hooks_keep_the_enum_hook(self, module):
        hooks = schema_settings(module)["POSTPROCESSING_HOOKS"]

        assert [hook for hook in hooks if hook in (ENUM_POSTPROCESSING_HOOK, MIGRATED_PATHS_HOOK)] == [
            ENUM_POSTPROCESSING_HOOK,
            MIGRATED_PATHS_HOOK,
        ]

    # The Public server is AUTHORING_API_URL as configured. Whether a gateway
    # behind it serves the full paths is not decided by these settings and is
    # not checked here.
    @ddt.data(PRODUCTION_SETTINGS, DEVSTACK_SETTINGS)
    def test_no_server_url_carries_an_api_prefix(self, module):
        servers = schema_settings(module)["SERVERS"]

        assert [server["description"] for server in servers] == ["Public", "Local"]
        assert [server for server in servers if "/api/" in server["url"]] == []


class StudioApiDocsTest(TestCase):
    """The Studio API documentation site, which documents each view from what the view declares."""

    ARCHIVE_DOCS_PATH = "/authoring/v2/courses/{course_key}/video_archives/"

    # The site reads a view's serializer_class as both its request and its
    # response body. The archive view answers with a binary archive, so it
    # declares none, and the site shows no body for it rather than a JSON one.
    def test_the_archive_operation_is_given_no_json_body(self):
        response = self.client.get(reverse("apidocs-data", kwargs={"format": ".json"}))

        operation = json.loads(response.content)["paths"][self.ARCHIVE_DOCS_PATH]["post"]
        assert [parameter for parameter in operation["parameters"] if parameter["in"] == "body"] == []
        assert {code: body for code, body in operation["responses"].items() if "schema" in body} == {}
