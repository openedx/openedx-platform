"""
Contract tests shared by the course video usage and video archive APIs.
"""
import functools
import json
from collections import defaultdict
from types import SimpleNamespace

import ddt
import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase
from django.urls import get_resolver, resolve, reverse
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import patched_settings
from opaque_keys.edx.keys import CourseKey
from rest_framework.exceptions import NotFound

from cms.djangoapps.contentstore.rest_api.v2.serializers.video_usages import VideoUsageLocationSerializer
from cms.djangoapps.contentstore.rest_api.v2.views.tests.helpers import SCHEMA_SETTINGS, generate_cms_schema
from cms.djangoapps.contentstore.rest_api.v2.views.video_archives import CourseVideoArchiveView
from cms.djangoapps.contentstore.rest_api.v2.views.video_usages import CourseVideoUsageViewSet
from cms.djangoapps.contentstore.tests.utils import CourseTestCase, routed_actions
from cms.djangoapps.contentstore.views.permissions import HasStudioReadAccess
from cms.lib.spectacular import SUPERSEDED_PATHS, cms_mark_superseded_paths
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

# Keys the course_key path converter accepts that carry only a content version,
# with no organization, course or run.
VERSION_ONLY_COURSE_KEYS = (
    "course-v1:version@" + "a" * 24,
    "course-v1:branch@published-branch+version@" + "a" * 24,
    "ccx-v1:version@" + "a" * 24 + "+ccx@1",
    "library-v1:version@" + "a" * 24,
)


@functools.lru_cache
def published_document():
    return generate_cms_schema()


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

        assert names == list(self.ROUTE_NAMES)
        assert resolver.app_name == "authoring_v2"
        assert not [name for name in names if "v1" in name or "v2" in name]

@ddt.ddt
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

    # The role checks cannot evaluate a key that carries only a content version,
    # so such a key is answered as a missing course before they run.
    @ddt.data(*VERSION_ONLY_COURSE_KEYS)
    def test_a_key_naming_only_a_version_is_not_found(self, key):
        with pytest.raises(NotFound) as raised:
            self.allows(self.user, course_key=CourseKey.from_string(key))

        assert raised.value.detail == "The requested course does not exist."

    def test_a_key_naming_a_course_and_a_version_is_evaluated(self):
        key = CourseKey.from_string(f"{self.course.id}+version@{'a' * 24}")

        assert self.allows(self.user, course_key=key) is True
        assert self.allows(UserFactory.create(), course_key=key) is False


class CmsSchemaHookTest(TestCase):
    """The post-processing hook deprecates exactly the two superseded operations."""

    # Listed as exact paths rather than prefixes: a prefix would also reach
    # the other operations under /api/contentstore/v1/videos/.
    def test_the_superseded_paths_include_the_two_video_operations(self):
        assert {LEGACY_USAGE_PATH, LEGACY_DOWNLOAD_PATH} <= set(SUPERSEDED_PATHS)

    # Operations of other resources that nothing supersedes, next to the two
    # superseded ones or elsewhere in Studio, and the new addresses. Marking any
    # of them would tell clients to leave an operation that has no successor.
    # Resources superseded in their own right are not listed: whether they are
    # marked is decided with their successors, not here.
    OTHER_RESOURCE_PATHS = (
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
        return cms_mark_superseded_paths({"paths": paths}, None, None, True)

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
        result = cms_mark_superseded_paths(
            {"paths": {LEGACY_DOWNLOAD_PATH: {"put": {}, "parameters": []}}}, None, None, True,
        )

        assert result["paths"][LEGACY_DOWNLOAD_PATH] == {"put": {"deprecated": True}, "parameters": []}


@ddt.ddt
class CmsSchemaDocumentTest(TestCase):
    """The published Studio document."""

    def test_both_new_operations_are_published_at_their_full_address(self):
        document = published_document()

        assert [method for method in document["paths"][USAGE_PATH] if method != "parameters"] == ["get"]
        assert [method for method in document["paths"][ARCHIVE_PATH] if method != "parameters"] == ["post"]

    def test_no_other_address_of_these_resources_is_published(self):
        published = {
            path
            for path in published_document()["paths"]
            if path.startswith("/api/authoring/") and {"video_usages", "video_archives"} & set(path.split("/"))
        }

        assert published == {USAGE_PATH, ARCHIVE_PATH}

    # The usage address is a viewset mount, whose handlers come from the action
    # map it was registered with rather than from the class, so that map is
    # pinned too. The archive address is a plain view: its class is the whole
    # contract.
    def test_the_published_addresses_resolve_to_the_new_views(self):
        usage = resolve(USAGE_PATH.format(course_key="course-v1:a+b+c", edx_video_id="v1"))
        archive = resolve(ARCHIVE_PATH.format(course_key="course-v1:a+b+c"))

        assert (usage.func.cls, archive.func.cls) == (CourseVideoUsageViewSet, CourseVideoArchiveView)
        assert routed_actions(usage.func) == {"get": "retrieve"}

    def test_operation_ids(self):
        document = published_document()

        assert document["paths"][USAGE_PATH]["get"]["operationId"] == "v2_courses_video_usages_retrieve"
        assert document["paths"][ARCHIVE_PATH]["post"]["operationId"] == "v2_courses_video_archives_create"
        assert document["paths"][LEGACY_USAGE_PATH]["get"]["operationId"] == "v1_videos_usage_retrieve"
        assert document["paths"][LEGACY_DOWNLOAD_PATH]["put"]["operationId"] == "v1_videos_download_update"

    # Ids are derived from the path after its API prefix, so an authoring path
    # can be given the id of a contentstore one. The generator then renames one
    # of the two with a numeric suffix, possibly the existing operation, so the
    # ids are checked as first assigned.
    def test_the_new_operation_ids_are_given_to_no_other_path(self):
        generator = SchemaGenerator()
        hooks = [*settings.SPECTACULAR_SETTINGS["POSTPROCESSING_HOOKS"], RECORDING_HOOK]
        with patched_settings({**SCHEMA_SETTINGS, "POSTPROCESSING_HOOKS": hooks}):
            generator.get_schema(request=None, public=True)
        holders = defaultdict(list)
        for operation_id, path, method in generator.assigned_operation_ids:
            holders[operation_id].append((path, method))

        assert holders["v2_courses_video_usages_retrieve"] == [(USAGE_PATH, "get")]
        assert holders["v2_courses_video_archives_create"] == [(ARCHIVE_PATH, "post")]

    # Published operations nothing supersedes: the neighbours of the two
    # superseded ones, other Studio resources, and the new addresses. Resources
    # superseded in their own right are not listed.
    UNMARKED_OPERATIONS = (
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
        document = published_document()

        assert self.marks(document, LEGACY_USAGE_PATH, "get") == (True, None)
        assert self.marks(document, LEGACY_DOWNLOAD_PATH, "put") == (True, None)

    def test_other_operations_are_left_unmarked(self):
        document = published_document()

        assert {key: self.marks(document, *key) for key in self.UNMARKED_OPERATIONS} == {
            key: (None, None) for key in self.UNMARKED_OPERATIONS
        }

    # The document declares no security schemes for the platform's default
    # authentication, so the refusal text is where a client learns what to send.
    @ddt.data(
        (USAGE_PATH, "get", (
            "The request carries no accepted credentials. Send a JSON Web Token in the Authorization "
            "header as 'JWT <token>' (the OAuth2 access token endpoint issues one when the token request "
            "sets token_type=jwt), or the session cookie of a signed-in Studio user. OAuth2 access "
            "tokens sent as 'Bearer <token>' are not accepted."
        )),
        (ARCHIVE_PATH, "post", (
            "The request carries no accepted credentials. Send a JSON Web Token in the Authorization "
            "header as 'JWT <token>' (the OAuth2 access token endpoint issues one when the token request "
            "sets token_type=jwt), or the session cookie of a signed-in Studio user, together with the "
            "CSRF token in the X-CSRFToken header. OAuth2 access tokens sent as 'Bearer <token>' are not "
            "accepted."
        )),
    )
    @ddt.unpack
    def test_the_accepted_credentials_are_named(self, path, method, description):
        operation = published_document()["paths"][path][method]

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
        operation = published_document()["paths"][path][method]

        assert operation["responses"][code]["description"] == description

    def test_both_new_operations_are_tagged_for_the_sdk(self):
        document = published_document()

        assert document["paths"][USAGE_PATH]["get"]["tags"] == ["openedx-platform-sdk"]
        assert document["paths"][ARCHIVE_PATH]["post"]["tags"] == ["openedx-platform-sdk"]

    def component(self, document, reference):
        return document["components"]["schemas"][reference["$ref"].rsplit("/", 1)[-1]]

    def test_the_usage_response_is_declared(self):
        document = published_document()
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
        document = published_document()
        request_body = document["paths"][ARCHIVE_PATH]["post"]["requestBody"]

        body = self.component(document, request_body["content"]["application/json"]["schema"])
        entry = self.component(document, body["properties"]["files"]["items"])
        assert list(request_body["content"]) == ["application/json"]
        assert request_body["required"] is True
        assert body["required"] == ["files"]
        assert sorted(entry["required"]) == ["name", "url"]

    def test_the_archive_response_is_a_binary_zip(self):
        response = published_document()["paths"][ARCHIVE_PATH]["post"]["responses"]["200"]

        assert response["content"] == {"application/zip": {"schema": {"type": "string", "format": "binary"}}}
        assert "<course key>_videos.zip" in response["description"]

    @ddt.data(
        (USAGE_PATH, "get", ["401", "403", "404"]),
        (ARCHIVE_PATH, "post", ["400", "401", "403", "404", "406", "415", "429"]),
    )
    @ddt.unpack
    def test_every_error_response_is_the_error_envelope(self, path, method, codes):
        responses = published_document()["paths"][path][method]["responses"]

        assert sorted(code for code in responses if code != "200") == codes
        for code in codes:
            assert responses[code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }


class StudioApiDocsTest(TestCase):
    """
    The service-wide documentation site, which documents every endpoint of Studio.

    It applies none of the Authoring API's filtering or prefix stripping, so it
    carries both new operations at their full addresses.
    """

    ARCHIVE_DOCS_PATH = "/api/authoring/v2/courses/{course_key}/video_archives/"
    USAGE_DOCS_PATH = "/api/authoring/v2/courses/{course_key}/video_usages/{edx_video_id}/"

    def document(self):
        response = self.client.get(reverse("apidocs-data", kwargs={"format": "json"}))
        assert response.status_code == 200
        return json.loads(response.content)

    def test_both_operations_are_documented_at_their_full_addresses(self):
        paths = self.document()["paths"]

        assert list(paths[self.ARCHIVE_DOCS_PATH]) == ["post"]
        assert list(paths[self.USAGE_DOCS_PATH]) == ["get"]

    # A documentation generator that infers the response body from the view's
    # serializer would describe the archive as JSON. The view declares the
    # response itself, so the only representation offered is the archive.
    def test_the_archive_response_is_offered_only_as_a_zip(self):
        operation = self.document()["paths"][self.ARCHIVE_DOCS_PATH]["post"]

        assert operation["responses"]["200"]["content"] == {
            "application/zip": {"schema": {"type": "string", "format": "binary"}}
        }
        assert list(operation["requestBody"]["content"]) == ["application/json"]

    # This document runs the same post-processing hooks as the Authoring API's.
    def test_the_superseded_operations_are_deprecated_there_too(self):
        paths = self.document()["paths"]

        assert paths[LEGACY_USAGE_PATH]["get"]["deprecated"] is True
        assert paths[LEGACY_DOWNLOAD_PATH]["put"]["deprecated"] is True
