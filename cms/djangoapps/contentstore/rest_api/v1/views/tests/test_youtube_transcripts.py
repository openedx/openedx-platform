"""Tests for the v1 YouTube transcript endpoints."""

import json
from unittest.mock import Mock, patch

import ddt
from django.urls import Resolver404, resolve, reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from edxval.api import create_video
from opaque_keys.edx.keys import UsageKey
from organizations.tests.factories import OrganizationFactory
from rest_framework import status
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.rest_api.v0.views.transcripts import (
    YoutubeTranscriptCheckView,
    YoutubeTranscriptUploadView,
)
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from common.djangoapps.student.tests.factories import GlobalStaffFactory, InstructorFactory, UserFactory
from openedx.core.djangoapps.content_libraries import api as lib_api
from openedx.core.djangoapps.video_config.transcripts_utils import Transcript
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.factories import CourseFactory
from xmodule.video_block import VideoBlock

SRT_TRANSCRIPT_CONTENT = """0
00:00:10,500 --> 00:00:13,000
Elephant's Dream

1
00:00:15,000 --> 00:00:18,000
At the left we can see...

"""

SJSON_TRANSCRIPT_CONTENT = Transcript.convert(
    SRT_TRANSCRIPT_CONTENT,
    Transcript.SRT,
    Transcript.SJSON,
)

DOWNLOAD_PATH = "cms.djangoapps.contentstore.views.transcripts_ajax.download_youtube_subs"
GET_ITEM_PATH = "cms.djangoapps.contentstore.views.transcripts_ajax._get_item"

CHECK_URL_NAME = "authoring_v1:youtube_transcript_check_list"
IMPORT_URL_NAME = "authoring_v1:youtube_transcript_import_list"
LEGACY_CHECK_URL_NAME = "cms.djangoapps.contentstore:v0:cms_api_youtube_transcripts_check"
LEGACY_IMPORT_URL_NAME = "cms.djangoapps.contentstore:v0:cms_api_youtube_transcripts_upload"

YOUTUBE_ID = "test_yt_id"


class BaseYoutubeTranscriptTest(CourseTestCase):
    """Fixtures shared by every YouTube transcript endpoint test."""

    def setUp(self):
        super().setUp()
        self.api_client = APIClient()
        self.password = "password"

        self.video_usage_key = self._create_video_block(self.course)
        self.item = modulestore().get_item(self.video_usage_key)
        self._set_fields_from_xml(self.item, '<video youtube="1.0:hI10vDNYz4M" />')
        modulestore().update_item(self.item, self.user.id)
        self.item = modulestore().get_item(self.video_usage_key)

        create_video({
            "edx_video_id": "123-456-789",
            "status": "upload",
            "client_video_id": "Test Video",
            "duration": 0,
            "encoded_videos": [],
            "courses": [str(self.course.id)],
        })

        self.other_course = CourseFactory.create(org="edX", course="OtherX", run="Other_Run")

        self.student = UserFactory.create(username="student", password=self.password)
        self.global_staff = GlobalStaffFactory(username="global-staff", password=self.password)
        self.course_instructor = InstructorFactory(
            username="instructor", password=self.password, course_key=self.course.id,
        )
        self.other_course_instructor = InstructorFactory(
            username="other-course-instructor",
            password=self.password,
            course_key=self.other_course.id,
        )

    def _create_video_block(self, course, category="video"):
        """Create a block of ``category`` under ``course`` and return its usage key."""
        response = self.client.ajax_post("/xblock/", {
            "parent_locator": str(course.location),
            "category": category,
            "type": category,
        })
        assert response.status_code == status.HTTP_200_OK
        return UsageKey.from_string(json.loads(response.content.decode("utf-8"))["locator"])

    @staticmethod
    def _set_fields_from_xml(item, xml):
        """Apply the field values described by ``xml`` to ``item``."""
        for key, value in VideoBlock.parse_video_xml(xml).items():
            setattr(item, key, value)

    def check_url(self, course_key=None):
        """Return the conforming check address for ``course_key``."""
        return reverse(CHECK_URL_NAME, kwargs={"course_key": str(course_key or self.course.id)})

    def import_url(self, course_key=None):
        """Return the conforming import address for ``course_key``."""
        return reverse(IMPORT_URL_NAME, kwargs={"course_key": str(course_key or self.course.id)})

    def payload(self, locator=None, youtube_id=YOUTUBE_ID, videos=None):
        """Return a request payload naming ``locator`` and its YouTube source."""
        body = {"locator": str(locator if locator is not None else self.video_usage_key)}
        if videos is not None:
            body["videos"] = videos
        else:
            body["videos"] = [{"type": "youtube", "video": youtube_id, "mode": "youtube"}]
        return body

    def get_check(self, user=None, course_key=None, payload=None):
        """Call the check endpoint as ``user``."""
        if user is not None:
            self.api_client.force_authenticate(user=user)
        body = self.payload() if payload is None else payload
        return self.api_client.get(self.check_url(course_key), {"data": json.dumps(body)})

    def post_import(self, user=None, course_key=None, payload=None):
        """Call the import endpoint as ``user``."""
        if user is not None:
            self.api_client.force_authenticate(user=user)
        body = self.payload() if payload is None else payload
        return self.api_client.post(self.import_url(course_key), body, format="json")


@ddt.ddt
class TestYoutubeTranscriptChecksAuthorization(BaseYoutubeTranscriptTest):
    """Allow and deny paths of the check endpoint."""

    def test_anonymous_is_refused(self):
        response = self.api_client.get(self.check_url(), {"data": json.dumps(self.payload())})
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    def test_student_is_refused(self):
        response = self.get_check(user=self.student)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_instructor_in_another_course_is_refused(self):
        response = self.get_check(user=self.other_course_instructor)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_course_instructor_is_allowed(self):
        response = self.get_check(user=self.course_instructor)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "Success"

    def test_global_staff_is_allowed(self):
        response = self.get_check(user=self.global_staff)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "Success"


@ddt.ddt
@patch(DOWNLOAD_PATH, Mock(return_value=[["en", SJSON_TRANSCRIPT_CONTENT]]))
class TestYoutubeTranscriptImportsAuthorization(BaseYoutubeTranscriptTest):
    """Allow and deny paths of the import endpoint."""

    def test_anonymous_is_refused(self):
        response = self.api_client.post(self.import_url(), self.payload(), format="json")
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    def test_student_is_refused(self):
        response = self.post_import(user=self.student)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_instructor_in_another_course_is_refused(self):
        response = self.post_import(user=self.other_course_instructor)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_course_instructor_is_allowed(self):
        response = self.post_import(user=self.course_instructor)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "Success"

    def test_global_staff_is_allowed(self):
        response = self.post_import(user=self.global_staff)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "Success"


@ddt.ddt
@patch(DOWNLOAD_PATH, Mock(return_value=[["en", SJSON_TRANSCRIPT_CONTENT]]))
class TestYoutubeTranscriptAuthorizationLayers(BaseYoutubeTranscriptTest):
    """The per-item checks that survive behind the course-level permission class."""

    def test_check_refuses_locator_pointing_into_another_course(self):
        """A caller authorized on the URL course cannot reach a block in another course."""
        other_video = self._create_video_block(self.other_course)
        response = self.get_check(
            user=self.course_instructor, payload=self.payload(locator=other_video),
        )
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_import_refuses_locator_pointing_into_another_course(self):
        """The same cross-course refusal applies to the write endpoint."""
        other_video = self._create_video_block(self.other_course)
        response = self.post_import(
            user=self.course_instructor, payload=self.payload(locator=other_video),
        )
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    @ddt.data("student", "other_course_instructor")
    def test_check_refuses_before_the_item_is_loaded(self, actor):
        """A caller with no rights on the URL course never reaches the item lookup."""
        with patch(GET_ITEM_PATH) as mocked_get_item:
            response = self.get_check(user=getattr(self, actor))
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not mocked_get_item.called

    @ddt.data("student", "other_course_instructor")
    def test_import_refuses_before_the_item_is_loaded(self, actor):
        """The write endpoint refuses on the URL course before touching the block."""
        with patch(GET_ITEM_PATH) as mocked_get_item:
            response = self.post_import(user=getattr(self, actor))
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not mocked_get_item.called

    def test_import_refuses_a_library_block_without_edit_rights(self):
        """The content-library branch refuses a caller who cannot edit the block."""
        library = lib_api.create_library(
            org=OrganizationFactory.create(short_name="denyorg"), slug="denylib", title="Deny",
        )
        block = lib_api.create_library_block(library.key, "video", "video-denied")
        response = self.post_import(
            user=self.course_instructor, payload=self.payload(locator=block.usage_key),
        )
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_import_allows_a_library_block_with_edit_rights(self):
        """The content-library branch admits a caller who can edit the block, with a null id."""
        library = lib_api.create_library(
            org=OrganizationFactory.create(short_name="alloworg"), slug="allowlib", title="Allow",
        )
        block = lib_api.create_library_block(library.key, "video", "video-allowed")
        response = self.post_import(
            user=self.user, payload=self.payload(locator=block.usage_key),
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"edx_video_id": None, "status": "Success"}


@patch(DOWNLOAD_PATH, Mock(return_value=[["en", SJSON_TRANSCRIPT_CONTENT]]))
class TestYoutubeTranscriptWriteBehaviour(BaseYoutubeTranscriptTest):
    """Which operation writes, and which does not."""

    @staticmethod
    def _writes(captured):
        """
        Return the write statements among ``captured`` queries.

        The authorization engine's cache-invalidation marker is a process-wide
        singleton row created on first use, not state this endpoint owns, so it
        is excluded.
        """
        return [
            q["sql"] for q in captured
            if q["sql"]
            and q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
            and "openedx_authz_policycachecontrol" not in q["sql"]
        ]

    def _capture(self, call):
        """Run ``call`` and return its response with the queries it issued."""
        from django.db import connection  # noqa: PLC0415
        from django.test.utils import CaptureQueriesContext  # noqa: PLC0415

        with CaptureQueriesContext(connection) as captured:
            response = call()
        return response, captured.captured_queries

    def test_check_performs_no_domain_writes(self):
        self.api_client.force_authenticate(user=self.course_instructor)
        response, queries = self._capture(
            lambda: self.api_client.get(self.check_url(), {"data": json.dumps(self.payload())}),
        )
        assert response.status_code == status.HTTP_200_OK
        assert self._writes(queries) == []

    def test_repeated_checks_issue_no_writes_at_all(self):
        """Once the shared caches are warm, the read issues no write of any kind."""
        self.api_client.force_authenticate(user=self.course_instructor)
        self.api_client.get(self.check_url(), {"data": json.dumps(self.payload())})
        response, queries = self._capture(
            lambda: self.api_client.get(self.check_url(), {"data": json.dumps(self.payload())}),
        )
        assert response.status_code == status.HTTP_200_OK
        writes = [
            q["sql"] for q in queries
            if q["sql"] and q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        assert writes == []

    def test_head_on_check_performs_no_domain_writes(self):
        self.api_client.force_authenticate(user=self.course_instructor)
        response, queries = self._capture(
            lambda: self.api_client.head(self.check_url(), {"data": json.dumps(self.payload())}),
        )
        assert response.status_code == status.HTTP_200_OK
        assert self._writes(queries) == []

    def test_import_creates_an_external_video_for_an_unlinked_block(self):
        """The write the legacy GET performed still happens, now on the POST."""
        self.item.edx_video_id = ""
        modulestore().update_item(self.item, self.user.id)

        response = self.post_import(user=self.course_instructor)

        assert response.status_code == status.HTTP_200_OK
        created_id = response.json()["edx_video_id"]
        assert created_id
        assert modulestore().get_item(self.video_usage_key).edx_video_id == created_id

    def test_import_keeps_an_existing_video_id(self):
        self.item.edx_video_id = "123-456-789"
        modulestore().update_item(self.item, self.user.id)

        response = self.post_import(user=self.course_instructor)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"edx_video_id": "123-456-789", "status": "Success"}

    def test_import_is_not_reachable_by_get(self):
        self.api_client.force_authenticate(user=self.course_instructor)
        response = self.api_client.get(self.import_url())
        assert_error_envelope(
            response, expected_status=405, expected_type_slug="method-not-allowed",
        )

    def test_check_is_not_reachable_by_post(self):
        self.api_client.force_authenticate(user=self.course_instructor)
        response = self.api_client.post(self.check_url(), self.payload(), format="json")
        assert_error_envelope(
            response, expected_status=405, expected_type_slug="method-not-allowed",
        )


@patch(DOWNLOAD_PATH, Mock(return_value=[["en", SJSON_TRANSCRIPT_CONTENT]]))
class TestYoutubeTranscriptErrors(BaseYoutubeTranscriptTest):
    """Every error the endpoints can produce, in the standardized envelope."""

    def test_check_rejects_an_absent_data_parameter(self):
        self.api_client.force_authenticate(user=self.course_instructor)
        response = self.api_client.get(self.check_url())
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert response.json()["errors"] == {"data": ["Must be a JSON object."]}

    def test_check_rejects_a_non_object_data_parameter(self):
        self.api_client.force_authenticate(user=self.course_instructor)
        response = self.api_client.get(self.check_url(), {"data": "[1, 2]"})
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert response.json()["errors"] == {"data": ["Must be a JSON object."]}

    def test_check_rejects_a_payload_without_a_locator(self):
        response = self.get_check(
            user=self.course_instructor, payload={"videos": []},
        )
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert response.json()["errors"] == {"locator": ["This field is required."]}

    def test_import_rejects_a_payload_without_a_locator(self):
        response = self.post_import(user=self.course_instructor, payload={"videos": []})
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert response.json()["errors"] == {"locator": ["This field is required."]}

    def test_import_rejects_a_payload_without_videos(self):
        response = self.post_import(
            user=self.course_instructor, payload={"locator": str(self.video_usage_key)},
        )
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert response.json()["errors"] == {"videos": ["This field is required."]}

    def test_check_reports_an_unparseable_locator_as_a_rejected_request(self):
        response = self.get_check(
            user=self.course_instructor, payload=self.payload(locator="not-a-usage-key"),
        )
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert response.json()["errors"] == {
            "locator": ["The transcript status for this video could not be determined."],
        }

    def test_check_reports_a_missing_block_as_a_rejected_request(self):
        missing = str(self.course.id.make_usage_key("video", "no-such-block"))
        response = self.get_check(
            user=self.course_instructor, payload=self.payload(locator=missing),
        )
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")

    def test_import_reports_a_missing_block_as_a_rejected_request(self):
        missing = str(self.course.id.make_usage_key("video", "no-such-block"))
        response = self.post_import(
            user=self.course_instructor, payload=self.payload(locator=missing),
        )
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert response.json()["errors"] == {
            "locator": ["The YouTube transcript could not be imported for this video."],
        }

    def test_a_block_outside_a_course_or_library_is_reported_as_not_found(self):
        """The service translates the handler's own Django-native errors."""
        from django.http import Http404  # noqa: PLC0415
        with patch(GET_ITEM_PATH, side_effect=Http404()):
            response = self.post_import(user=self.course_instructor)
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        assert response.json()["detail"] == "No video block matches the supplied locator."

    def test_import_reports_an_unparseable_locator_as_a_rejected_request(self):
        response = self.post_import(
            user=self.course_instructor, payload=self.payload(locator="not-a-usage-key"),
        )
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")

    def test_import_reports_a_non_video_block_as_a_rejected_request(self):
        problem = self._create_video_block(self.course, category="problem")
        response = self.post_import(
            user=self.course_instructor, payload=self.payload(locator=problem),
        )
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")

    def test_import_without_a_youtube_source_is_rejected(self):
        response = self.post_import(
            user=self.course_instructor,
            payload=self.payload(videos=[{"type": "html5", "video": "v1", "mode": "mp4"}]),
        )
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")

    def test_a_youtube_download_failure_does_not_leak_its_message(self):
        from openedx.core.djangoapps.video_config.transcripts_utils import (  # noqa: PLC0415
            GetTranscriptsFromYouTubeException,
        )
        secret = "internal youtube client detail 0xDEADBEEF"
        with patch(DOWNLOAD_PATH, side_effect=GetTranscriptsFromYouTubeException(secret)):
            response = self.post_import(user=self.course_instructor)
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert secret not in response.content.decode("utf-8")

    def test_an_unknown_course_key_is_refused(self):
        response = self.get_check(
            user=self.course_instructor, course_key="course-v1:edX+NoSuch+Course",
        )
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")


class TestYoutubeTranscriptUrls(BaseYoutubeTranscriptTest):
    """The addresses this pass creates, and the ones it must leave alone."""

    def test_conforming_addresses_reverse_to_their_literals(self):
        course_key = "course-v1:edX+DemoX+Demo"
        assert reverse(CHECK_URL_NAME, kwargs={"course_key": course_key}) == (
            f"/api/authoring/v1/courses/{course_key}/youtube_transcript_checks/"
        )
        assert reverse(IMPORT_URL_NAME, kwargs={"course_key": course_key}) == (
            f"/api/authoring/v1/courses/{course_key}/youtube_transcript_imports/"
        )

    def test_every_live_legacy_spelling_still_resolves_to_its_view(self):
        base = "/api/contentstore/v0/youtube_transcripts/course-v1:edX+DemoX+Demo"
        for suffix, view_class in (
            ("chec", YoutubeTranscriptCheckView),
            ("check", YoutubeTranscriptCheckView),
            ("uploa", YoutubeTranscriptUploadView),
            ("upload", YoutubeTranscriptUploadView),
        ):
            match = resolve(f"{base}/{suffix}")
            assert match.func.cls is view_class

    def test_the_legacy_slashed_addresses_still_do_not_resolve(self):
        base = "/api/contentstore/v0/youtube_transcripts/course-v1:edX+DemoX+Demo"
        for suffix in ("check/", "upload/"):
            try:
                resolve(f"{base}/{suffix}")
            except Resolver404:
                continue
            raise AssertionError(f"{suffix} resolved but did not before")

    def test_the_legacy_url_names_still_reverse(self):
        course_id = "course-v1:edX+DemoX+Demo"
        assert reverse(LEGACY_CHECK_URL_NAME, kwargs={"course_id": course_id}) == (
            f"/api/contentstore/v0/youtube_transcripts/{course_id}/chec"
        )
        assert reverse(LEGACY_IMPORT_URL_NAME, kwargs={"course_id": course_id}) == (
            f"/api/contentstore/v0/youtube_transcripts/{course_id}/uploa"
        )

    def test_the_conforming_url_names_are_unique_in_their_namespace(self):
        from cms.djangoapps.contentstore.rest_api.v1 import authoring_urls  # noqa: PLC0415
        names = [pattern.name for pattern in authoring_urls.urlpatterns]
        assert sorted(names) == ["youtube_transcript_check_list", "youtube_transcript_import_list"]

    def test_a_malformed_course_key_does_not_reach_the_view(self):
        """An unparseable key fails to route, so no handler runs."""
        self.api_client.force_authenticate(user=self.course_instructor)
        with patch(GET_ITEM_PATH) as mocked_get_item:
            response = self.api_client.get(
                "/api/authoring/v1/courses/not-a-course-key/youtube_transcript_checks/",
            )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not mocked_get_item.called

    def test_a_deprecated_course_key_does_not_reach_the_view(self):
        """A pre-opaque-key course id is refused at routing, as for any conforming route."""
        self.api_client.force_authenticate(user=self.course_instructor)
        with patch(GET_ITEM_PATH) as mocked_get_item:
            response = self.api_client.get(
                "/api/authoring/v1/courses/edX+DemoX+Demo/youtube_transcript_checks/",
            )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not mocked_get_item.called

    def test_a_routing_level_404_is_answered_with_html(self):
        """
        A key the converter rejects never enters the view, so the service-wide
        handler answers it with the Studio error page rather than a JSON body.
        Pinned so a change to that handler is visible here.
        """
        self.api_client.force_authenticate(user=self.course_instructor)
        response = self.api_client.get(
            "/api/authoring/v1/courses/not-a-course-key/youtube_transcript_checks/",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response["Content-Type"].startswith("text/html")

    def test_both_surfaces_share_one_domain_implementation(self):
        """The legacy and conforming addresses reach the same handler functions."""
        from cms.djangoapps.contentstore.rest_api.v1 import youtube_transcripts_service  # noqa: PLC0415
        from cms.djangoapps.contentstore.views import transcripts_ajax  # noqa: PLC0415

        assert youtube_transcripts_service.check_transcripts is transcripts_ajax.check_transcripts
        assert youtube_transcripts_service.replace_transcripts is transcripts_ajax.replace_transcripts


@patch(DOWNLOAD_PATH, Mock(return_value=[["en", SJSON_TRANSCRIPT_CONTENT]]))
class TestYoutubeTranscriptQueryCounts(BaseYoutubeTranscriptTest):
    """The conforming addresses must not cost more queries than the legacy ones."""

    def _legacy_check(self):
        self.api_client.force_authenticate(user=self.course_instructor)
        return self.api_client.get(
            reverse(LEGACY_CHECK_URL_NAME, kwargs={"course_id": str(self.course.id)}),
            {"data": json.dumps(self.payload())},
        )

    def _count(self, call):
        """Return the number of queries ``call`` issues, asserting it succeeded."""
        from django.db import connection  # noqa: PLC0415
        from django.test.utils import CaptureQueriesContext  # noqa: PLC0415

        with CaptureQueriesContext(connection) as captured:
            response = call()
        assert response.status_code == status.HTTP_200_OK
        return len(captured.captured_queries)

    def test_check_costs_no_more_queries_than_the_legacy_address(self):
        legacy = self._count(self._legacy_check)
        conforming = self._count(lambda: self.get_check(user=self.course_instructor))
        assert conforming <= legacy, f"legacy={legacy} conforming={conforming}"


# Differences between the legacy addresses and the conforming ones that the
# design intends. The parity tests assert these are the only ones, and that
# each of them actually occurs.
INTENTIONAL_DIFFERENCES = [
    ("type", "errors move to the standardized envelope, which the legacy shapes lack"),
    ("title", "errors move to the standardized envelope, which the legacy shapes lack"),
    ("status", "the legacy error body carries the message under status; the envelope uses detail"),
    ("detail", "errors move to the standardized envelope, which the legacy shapes lack"),
    ("errors", "validation failures name the offending field"),
    ("html5_equal", "the legacy check error body repeats the whole result dict; the envelope does not"),
    ("is_youtube_mode", "the legacy check error body repeats the whole result dict; the envelope does not"),
    ("youtube_local", "the legacy check error body repeats the whole result dict; the envelope does not"),
    ("youtube_server", "the legacy check error body repeats the whole result dict; the envelope does not"),
    ("youtube_diff", "the legacy check error body repeats the whole result dict; the envelope does not"),
    ("current_item_subs", "the legacy check error body repeats the whole result dict; the envelope does not"),
    ("developer_message", "the legacy authorization body carries developer_message; the envelope does not"),
    ("error_code", "the legacy authorization body carries error_code; the envelope does not"),
]

VOLATILE_FIELDS = {"instance"}


def _normalize(obj, path=""):
    """Flatten ``obj`` into a ``{json_path: value}`` map, dropping volatile fields."""
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


def _changed_paths(legacy, new):
    """Return the json paths whose value differs between the two bodies."""
    left, right = _normalize(legacy), _normalize(new)
    return {
        key for key in set(left) | set(right)
        if left.get(key, "<absent>") != right.get(key, "<absent>")
    }


def _unexpected(changed):
    """Return the changed paths no declared intentional difference covers."""
    declared = {path for path, _ in INTENTIONAL_DIFFERENCES}
    return {
        key for key in changed
        if not any(key == d or key.startswith(f"{d}.") or key.startswith(f"{d}[") for d in declared)
    }


@patch(DOWNLOAD_PATH, Mock(return_value=[["en", SJSON_TRANSCRIPT_CONTENT]]))
class TestYoutubeTranscriptParity(BaseYoutubeTranscriptTest):
    """The conforming addresses return what the legacy ones return, bar the declared differences."""

    def setUp(self):
        super().setUp()
        self.api_client.force_authenticate(user=self.course_instructor)
        self.client.login(username=self.course_instructor.username, password=self.password)
        self.observed_differences = set()

    def legacy_check(self, payload=None):
        """Call the legacy check address with the session client."""
        return self.client.get(
            reverse(LEGACY_CHECK_URL_NAME, kwargs={"course_id": str(self.course.id)}),
            {"data": json.dumps(self.payload() if payload is None else payload)},
        )

    def legacy_import(self, payload=None):
        """Call the legacy import address with the session client."""
        return self.client.get(
            reverse(LEGACY_IMPORT_URL_NAME, kwargs={"course_id": str(self.course.id)}),
            {"data": json.dumps(self.payload() if payload is None else payload)},
        )

    def assert_bodies_match(self, legacy, new, expected_status):
        """Assert the two bodies differ only where the design says they do."""
        assert legacy.status_code == expected_status, legacy.content
        assert new.status_code == expected_status, new.content
        legacy_json = json.loads(legacy.content or b"null")
        new_json = json.loads(new.content or b"null")
        changed = _changed_paths(legacy_json, new_json)
        unexpected = _unexpected(changed)
        assert not unexpected, (
            f"undeclared differences {sorted(unexpected)}: legacy={legacy_json} new={new_json}"
        )
        self.observed_differences |= changed
        return legacy_json, new_json

    def test_check_success_body_is_identical(self):
        legacy_json, new_json = self.assert_bodies_match(
            self.legacy_check(), self.get_check(), expected_status=200,
        )
        assert new_json == legacy_json
        assert sorted(new_json) == [
            "command", "current_item_subs", "html5_equal", "html5_local", "is_youtube_mode",
            "status", "youtube_diff", "youtube_local", "youtube_server",
        ]

    def test_check_success_passes_a_string_subs_value_through(self):
        """``current_item_subs`` is a string on the wire, which the new declaration allows."""
        self.item.sub = "a-subs-name"
        modulestore().update_item(self.item, self.user.id)
        legacy_json, new_json = self.assert_bodies_match(
            self.legacy_check(), self.get_check(), expected_status=200,
        )
        assert new_json["current_item_subs"] == legacy_json["current_item_subs"]

    def test_import_success_body_is_identical(self):
        self.item.edx_video_id = "123-456-789"
        modulestore().update_item(self.item, self.user.id)
        legacy_json, new_json = self.assert_bodies_match(
            self.legacy_import(), self.post_import(), expected_status=200,
        )
        assert new_json == legacy_json == {"edx_video_id": "123-456-789", "status": "Success"}

    def test_import_success_for_a_library_block_returns_a_null_video_id(self):
        """A null id is a success value the legacy serializer would have refused."""
        library = lib_api.create_library(
            org=OrganizationFactory.create(short_name="parityorg"), slug="paritylib", title="Parity",
        )
        block = lib_api.create_library_block(library.key, "video", "video-parity")
        self.api_client.force_authenticate(user=self.user)
        response = self.post_import(payload=self.payload(locator=block.usage_key))
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"edx_video_id": None, "status": "Success"}

    def test_empty_payload_errors_differ_only_in_the_envelope(self):
        payload = {"locator": "", "videos": []}
        legacy, new = self.legacy_check(payload), self.get_check(payload=payload)
        self.assert_bodies_match(legacy, new, expected_status=400)
        assert json.loads(new.content)["type"].endswith("/validation")

    def test_bad_locator_errors_differ_only_in_the_envelope(self):
        payload = self.payload(locator="not-a-usage-key")
        legacy, new = self.legacy_check(payload), self.get_check(payload=payload)
        self.assert_bodies_match(legacy, new, expected_status=400)

    def test_non_video_block_errors_differ_only_in_the_envelope(self):
        payload = self.payload(locator=self._create_video_block(self.course, category="problem"))
        legacy, new = self.legacy_import(payload), self.post_import(payload=payload)
        self.assert_bodies_match(legacy, new, expected_status=400)

    def test_missing_youtube_id_errors_differ_only_in_the_envelope(self):
        payload = self.payload(videos=[{"type": "html5", "video": "v1", "mode": "mp4"}])
        legacy, new = self.legacy_import(payload), self.post_import(payload=payload)
        self.assert_bodies_match(legacy, new, expected_status=400)

    def test_unauthenticated_errors_differ_only_in_the_envelope(self):
        self.client.logout()
        self.api_client.force_authenticate(user=None)
        legacy = self.client.get(
            reverse(LEGACY_CHECK_URL_NAME, kwargs={"course_id": str(self.course.id)}),
            {"data": json.dumps(self.payload())},
        )
        new = self.api_client.get(self.check_url(), {"data": json.dumps(self.payload())})
        self.assert_bodies_match(legacy, new, expected_status=401)

    def test_forbidden_errors_differ_only_in_the_envelope(self):
        self.client.logout()
        self.client.login(username=self.student.username, password=self.password)
        self.api_client.force_authenticate(user=self.student)
        legacy = self.client.get(
            reverse(LEGACY_CHECK_URL_NAME, kwargs={"course_id": str(self.course.id)}),
            {"data": json.dumps(self.payload())},
        )
        new = self.api_client.get(self.check_url(), {"data": json.dumps(self.payload())})
        self.assert_bodies_match(legacy, new, expected_status=403)

    def test_no_shaping_parameter_changes_the_response(self):
        """Neither ``view`` nor ``fields`` is accepted, so neither alters the body."""
        default = self.get_check()
        for parameter in ("view", "fields"):
            response = self.api_client.get(
                self.check_url(), {"data": json.dumps(self.payload()), parameter: "minimal"},
            )
            assert response.status_code == status.HTTP_200_OK
            assert response.json() == default.json()

    def test_the_response_is_an_object_not_a_paginated_envelope(self):
        body = self.get_check().json()
        assert isinstance(body, dict)
        assert "results" not in body
        assert "count" not in body

    def test_every_declared_difference_occurs_somewhere_in_this_suite(self):
        """No entry may sit in the declared list without a case that produces it."""
        payload = {"locator": "", "videos": []}
        self.assert_bodies_match(
            self.legacy_check(payload), self.get_check(payload=payload), expected_status=400,
        )
        import_payload = self.payload(videos=[{"type": "html5", "video": "v1", "mode": "mp4"}])
        self.assert_bodies_match(
            self.legacy_import(import_payload),
            self.post_import(payload=import_payload),
            expected_status=400,
        )
        # A locator the check handler accepts but cannot resolve: the legacy body
        # repeats the whole result dict, the envelope names the field instead.
        unresolvable = self.payload(locator="not-a-usage-key")
        self.assert_bodies_match(
            self.legacy_check(unresolvable),
            self.get_check(payload=unresolvable),
            expected_status=400,
        )
        self.client.logout()
        self.api_client.force_authenticate(user=None)
        self.assert_bodies_match(
            self.client.get(
                reverse(LEGACY_CHECK_URL_NAME, kwargs={"course_id": str(self.course.id)}),
                {"data": json.dumps(self.payload())},
            ),
            self.api_client.get(self.check_url(), {"data": json.dumps(self.payload())}),
            expected_status=401,
        )
        self.client.login(username=self.student.username, password=self.password)
        self.api_client.force_authenticate(user=self.student)
        self.assert_bodies_match(
            self.client.get(
                reverse(LEGACY_CHECK_URL_NAME, kwargs={"course_id": str(self.course.id)}),
                {"data": json.dumps(self.payload())},
            ),
            self.api_client.get(self.check_url(), {"data": json.dumps(self.payload())}),
            expected_status=403,
        )
        missing = [
            path for path, _ in INTENTIONAL_DIFFERENCES
            if not any(
                key == path or key.startswith(f"{path}.") or key.startswith(f"{path}[")
                for key in self.observed_differences
            )
        ]
        assert missing == [], f"declared but never produced: {missing}"
