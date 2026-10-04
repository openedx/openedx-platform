"""
Unit tests for the course video archives API.
"""
import io
import json
import zipfile
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

import ddt
import pytest
import requests
from django.core.signals import got_request_exception
from django.urls import Resolver404, resolve, reverse
from edx_rest_framework_extensions.auth.jwt.tests.utils import generate_jwt
from edx_rest_framework_extensions.testing import assert_error_envelope
from edxval.api import create_profile, create_video, remove_video_for_course
from rest_framework import status
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.rest_api.v1.views.unknown_route import UnknownRouteView
from cms.djangoapps.contentstore.rest_api.v1.views.videos import VideoDownloadThrottle
from cms.djangoapps.contentstore.rest_api.v2.views.video_archives import CourseVideoArchiveView
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from xmodule.modulestore.tests.factories import CourseFactory

LIST_URL_NAME = "authoring_v2:course_video_archive_list"
LEGACY_URL_NAME = "cms.djangoapps.contentstore:v1:video_usage"

MISSING_COURSE_KEY = "course-v1:Org+Missing+Run"
CCX_COURSE_KEY = "ccx-v1:edX+DemoX+Demo_Course+ccx@1"

# Keys the course_key path converter accepts that carry only a content version,
# with no organization, course or run.
VERSION_ONLY_COURSE_KEYS = (
    "course-v1:version@" + "a" * 24,
    "course-v1:branch@published-branch+version@" + "a" * 24,
    "ccx-v1:version@" + "a" * 24 + "+ccx@1",
    "library-v1:version@" + "a" * 24,
)

FIRST_URL = "http://video-store.example.com/profile1/first.mp4"
SECOND_URL = "http://video-store.example.com/profile2/second.webm"
OTHER_COURSE_URL = "http://video-store.example.com/profile1/other-course.mp4"
SHARED_URL = "http://video-store.example.com/profile1/shared.mp4"
# An internal address an attacker might try to make the server fetch.
SSRF_URL = "http://169.254.169.254/latest/meta-data/"

FETCH = "cms.djangoapps.contentstore.video_storage_handlers.requests.get"

# The archive builder each address calls.
SERVICE_ARCHIVE_BUILDER = "cms.djangoapps.contentstore.rest_api.v2.video_management_service.create_video_zip"
LEGACY_ARCHIVE_BUILDER = "cms.djangoapps.contentstore.rest_api.v1.views.videos.create_video_zip"

# How the error handling publishes a required member missing from a list entry.
MISSING_MEMBER_ERROR = "{{'{member}': [ErrorDetail(string='This field is required.', code='required')]}}"

# The size of a zip archive with no entries: the end-of-central-directory record.
EMPTY_ARCHIVE_SIZE = 22
# The signature opening each record of a zip archive's central directory, which
# follows the last entry.
CENTRAL_DIRECTORY_SIGNATURE = b"PK\x01\x02"


def upstream_response(payload=b"video-bytes", content_type="video/mp4"):
    """A stand-in for a streamed video fetch answering with ``payload``."""
    response = MagicMock()
    response.iter_content = lambda chunk_size=None: iter([payload])
    response.headers = {"Content-Type": content_type}
    return response


def failing_upstream_response():
    """A stand-in for a video fetch that the video store answers with a server error."""
    response = upstream_response()
    response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
    return response


def read_into(received, stream):
    """Append each chunk of ``stream`` to ``received`` as it arrives."""
    for chunk in stream:
        received.append(chunk)


def archive_entries(response):
    """Return {entry name: bytes} of the streamed zip ``response``."""
    with zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content))) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


class VideoArchiveTestBase(CourseTestCase):
    """A course with two encoded videos, and a second course with one of its own."""

    # Isolates the throttle counters between tests.
    ENABLED_CACHES = ["default"]

    def setUp(self):
        super().setUp()
        create_profile("profile1")
        create_profile("profile2")
        self.create_course_video("first", self.course.id, [("profile1", FIRST_URL), ("profile2", SECOND_URL)])
        self.other_course = CourseFactory.create()
        self.create_course_video("other", self.other_course.id, [("profile1", OTHER_COURSE_URL)])

    def create_course_video(self, edx_video_id, course_key, encodings, also_in=()):
        create_video({
            "edx_video_id": edx_video_id,
            "client_video_id": f"{edx_video_id}.mp4",
            "duration": 42.0,
            "status": "file_complete",
            "courses": [str(key) for key in (course_key, *also_in)],
            "created": datetime.now(UTC),
            "encoded_videos": [
                {"profile": profile, "url": url, "file_size": 1600, "bitrate": 100}
                for profile, url in encodings
            ],
        })

    def url(self, course_key=None):
        return reverse(LIST_URL_NAME, kwargs={"course_key": course_key or self.course.id})

    def legacy_url(self, course_key=None):
        # The legacy download route shares its name with the usage route and is
        # the one reverse() picks when no video id is given.
        return reverse(LEGACY_URL_NAME, kwargs={"course_id": course_key or self.course.id})

    def client_for(self, user):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def user_with_role(self, role_class, course_key=None):
        user = UserFactory.create()
        role_class(course_key or self.course.id).add_users(user)
        return user

    def post(self, client, files, course_key=None, **extra):
        return client.post(self.url(course_key), {"files": files}, format="json", **extra)

    def put_legacy(self, client, files, course_key=None, **extra):
        return client.put(self.legacy_url(course_key), {"files": files}, format="json", **extra)


@patch(FETCH)
class VideoArchiveResponseTest(VideoArchiveTestBase):
    """The archive holds the requested videos under the requested names."""

    def test_the_archive_is_sent_as_a_zip_attachment(self, fetch):
        fetch.return_value = upstream_response()

        response = self.post(self.client_for(self.user), [{"url": FIRST_URL, "name": "Lecture 1.mp4"}])

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "application/zip"
        assert response["Content-Disposition"] == f"attachment; filename={self.course.id}_videos.zip"
        assert archive_entries(response) == {"Lecture 1.mp4": b"video-bytes"}
        fetch.assert_called_once_with(FIRST_URL, stream=True, allow_redirects=True)

    def test_a_name_without_an_extension_gets_one_from_the_content_type(self, fetch):
        fetch.return_value = upstream_response(content_type="video/mp4")

        response = self.post(self.client_for(self.user), [{"url": FIRST_URL, "name": "Lecture 1"}])

        assert list(archive_entries(response)) == ["Lecture 1.mp4"]

    def test_an_unknown_content_type_leaves_the_name_alone(self, fetch):
        fetch.return_value = upstream_response(content_type="application/x-not-a-real-type")

        response = self.post(self.client_for(self.user), [{"url": FIRST_URL, "name": "Lecture 1"}])

        assert list(archive_entries(response)) == ["Lecture 1"]

    def test_several_videos_are_archived_in_request_order(self, fetch):
        fetch.side_effect = [
            upstream_response(b"first-bytes", "video/mp4"),
            upstream_response(b"second-bytes", "video/webm"),
        ]

        response = self.post(self.client_for(self.user), [
            {"url": FIRST_URL, "name": "one.mp4"},
            {"url": SECOND_URL, "name": "two"},
        ])

        entries = archive_entries(response)
        assert list(entries) == ["one.mp4", "two.webm"]
        assert entries == {"one.mp4": b"first-bytes", "two.webm": b"second-bytes"}

    def test_an_empty_request_gives_an_empty_archive(self, fetch):
        response = self.post(self.client_for(self.user), [])

        body = b"".join(response.streaming_content)
        assert response.status_code == status.HTTP_200_OK
        assert len(body) == EMPTY_ARCHIVE_SIZE
        assert zipfile.ZipFile(io.BytesIO(body)).namelist() == []
        fetch.assert_not_called()

    def test_the_name_is_used_exactly_as_sent(self, fetch):
        fetch.return_value = upstream_response()

        response = self.post(self.client_for(self.user), [{"url": FIRST_URL, "name": " spaced.mp4 "}])

        assert list(archive_entries(response)) == [" spaced.mp4 .mp4"]


@patch(FETCH)
class VideoArchiveAllowlistTest(VideoArchiveTestBase):
    """Only the course's own video addresses are ever fetched."""

    def assert_refused_without_fetching(self, response, fetch, address):
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"non_field_errors": [f"Invalid video download url: {address}"]}
        fetch.assert_not_called()

    def test_an_address_outside_the_course_is_refused(self, fetch):
        response = self.post(self.client_for(self.user), [{"url": SSRF_URL, "name": "x"}])

        self.assert_refused_without_fetching(response, fetch, SSRF_URL)

    def test_a_video_of_another_course_is_refused(self, fetch):
        response = self.post(self.client_for(self.user), [{"url": OTHER_COURSE_URL, "name": "x.mp4"}])

        self.assert_refused_without_fetching(response, fetch, OTHER_COURSE_URL)

    def test_one_bad_address_refuses_the_whole_request(self, fetch):
        response = self.post(self.client_for(self.user), [
            {"url": FIRST_URL, "name": "one.mp4"},
            {"url": SSRF_URL, "name": "x"},
        ])

        self.assert_refused_without_fetching(response, fetch, SSRF_URL)

    def test_an_address_is_matched_exactly(self, fetch):
        padded = f" {FIRST_URL}"

        response = self.post(self.client_for(self.user), [{"url": padded, "name": "x"}])

        self.assert_refused_without_fetching(response, fetch, padded)

    def test_the_legacy_address_refuses_the_same_request(self, fetch):
        response = self.put_legacy(self.client_for(self.user), [{"url": SSRF_URL, "name": "x"}])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"developer_message": [f"Invalid video download url: {SSRF_URL}"]}
        fetch.assert_not_called()

    def share_a_video_then_remove_it_from_this_course(self):
        """Give both courses one video, then remove it from this course only."""
        self.create_course_video(
            "shared", self.course.id, [("profile1", SHARED_URL)], also_in=[self.other_course.id],
        )
        remove_video_for_course(str(self.course.id), "shared")

    def test_a_video_removed_from_the_course_is_refused(self, fetch):
        self.share_a_video_then_remove_it_from_this_course()

        response = self.post(self.client_for(self.user), [{"url": SHARED_URL, "name": "x.mp4"}])

        self.assert_refused_without_fetching(response, fetch, SHARED_URL)

    def test_the_legacy_address_refuses_a_video_removed_from_the_course(self, fetch):
        self.share_a_video_then_remove_it_from_this_course()

        response = self.put_legacy(self.client_for(self.user), [{"url": SHARED_URL, "name": "x.mp4"}])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"developer_message": [f"Invalid video download url: {SHARED_URL}"]}
        fetch.assert_not_called()

    def test_a_video_removed_from_one_course_is_still_archived_by_a_course_that_keeps_it(self, fetch):
        self.share_a_video_then_remove_it_from_this_course()
        fetch.return_value = upstream_response()

        response = self.post(
            self.client_for(self.user), [{"url": SHARED_URL, "name": "x.mp4"}], course_key=self.other_course.id,
        )

        assert archive_entries(response) == {"x.mp4": b"video-bytes"}
        fetch.assert_called_once_with(SHARED_URL, stream=True, allow_redirects=True)


@ddt.ddt
@patch(FETCH)
class VideoArchiveRequestValidationTest(VideoArchiveTestBase):
    """
    A malformed body is refused with a 400 before anything is fetched.

    The error handling publishes the error of a list entry as the Python text of
    that entry's errors, with "{}" for each valid entry, rather than mapping the
    entry's path to its messages; and the detail of every validation error is
    the Python text of all the errors. The exact text is pinned, so a change in
    that formatting shows up here.
    """

    def assert_invalid(self, response, fetch, field="files"):
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert list(envelope["errors"]) == [field]
        fetch.assert_not_called()
        return envelope

    def test_a_missing_list_is_refused(self, fetch):
        response = self.client_for(self.user).post(self.url(), {}, format="json")

        envelope = self.assert_invalid(response, fetch)
        assert envelope["errors"]["files"] == ["This field is required."]

    def test_the_detail_of_a_validation_error_is_the_text_of_all_the_errors(self, fetch):
        response = self.client_for(self.user).post(self.url(), {}, format="json")

        envelope = self.assert_invalid(response, fetch)
        assert envelope["detail"] == "{'files': [ErrorDetail(string='This field is required.', code='required')]}"

    def test_a_list_of_the_wrong_type_is_refused(self, fetch):
        response = self.client_for(self.user).post(self.url(), {"files": "x"}, format="json")

        envelope = self.assert_invalid(response, fetch)
        assert envelope["errors"]["files"] == [
            "{'non_field_errors': [ErrorDetail(string='Expected a list of items but got type \"str\".', "
            "code='not_a_list')]}"
        ]

    @ddt.data("url", "name")
    def test_an_entry_missing_a_member_is_refused(self, member, fetch):
        entry = {"url": FIRST_URL, "name": "one.mp4"}
        del entry[member]

        response = self.post(self.client_for(self.user), [entry])

        envelope = self.assert_invalid(response, fetch)
        assert envelope["errors"]["files"] == [MISSING_MEMBER_ERROR.format(member=member)]

    def test_a_valid_entry_beside_an_invalid_one_is_published_as_an_empty_map(self, fetch):
        response = self.post(self.client_for(self.user), [{"url": FIRST_URL, "name": "one.mp4"}, {"name": "two.mp4"}])

        envelope = self.assert_invalid(response, fetch)
        assert envelope["errors"]["files"] == ["{}", MISSING_MEMBER_ERROR.format(member="url")]

    def test_a_null_entry_is_refused(self, fetch):
        response = self.post(self.client_for(self.user), [None])

        envelope = self.assert_invalid(response, fetch)
        assert envelope["errors"]["files"] == ["[ErrorDetail(string='This field may not be null.', code='null')]"]

    def test_a_null_name_is_refused(self, fetch):
        response = self.post(self.client_for(self.user), [{"url": FIRST_URL, "name": None}])

        envelope = self.assert_invalid(response, fetch)
        assert envelope["errors"]["files"] == [
            "{'name': [ErrorDetail(string='This field may not be null.', code='null')]}"
        ]

    def test_malformed_json_is_refused(self, fetch):
        response = self.client_for(self.user).post(self.url(), "{not json", content_type="application/json")

        assert_error_envelope(response, expected_status=400)
        fetch.assert_not_called()


@patch(FETCH)
class VideoArchiveMediaTypeTest(VideoArchiveTestBase):
    """A body that is not JSON is refused before anything is fetched."""

    # The indexed syntax the default form parsers would turn back into the list.
    FORM_FIELDS = {"files[0]url": FIRST_URL, "files[0]name": "one.mp4"}

    def assert_unsupported(self, response, fetch):
        # The status and the envelope are pinned, not the error-type slug, which
        # the shared error catalog decides for every API.
        assert_error_envelope(response, expected_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        fetch.assert_not_called()

    def test_a_form_encoded_body_is_refused(self, fetch):
        fetch.return_value = upstream_response()

        response = self.client_for(self.user).post(
            self.url(), urlencode(self.FORM_FIELDS), content_type="application/x-www-form-urlencoded"
        )

        self.assert_unsupported(response, fetch)

    def test_a_plain_text_body_is_refused(self, fetch):
        fetch.return_value = upstream_response()

        response = self.client_for(self.user).post(
            self.url(), json.dumps({"files": [{"url": FIRST_URL, "name": "one.mp4"}]}), content_type="text/plain"
        )

        self.assert_unsupported(response, fetch)

    def test_a_multipart_body_is_refused(self, fetch):
        fetch.return_value = upstream_response()

        response = self.client_for(self.user).post(self.url(), self.FORM_FIELDS, format="multipart")

        # Studio's request-tracking middleware reads the form fields of a
        # multipart request before the view runs, which leaves the JSON parser
        # an empty body: the request is refused as one without files.
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"files": ["This field is required."]}
        fetch.assert_not_called()

    def test_the_view_parses_json_only(self, fetch):
        assert [parser.media_type for parser in CourseVideoArchiveView().get_parsers()] == ["application/json"]

    def test_the_legacy_address_fails_on_a_form_encoded_body(self, fetch):
        client = self.client_for(self.user)
        client.raise_request_exception = False

        response = client.put(
            self.legacy_url(), urlencode(self.FORM_FIELDS), content_type="application/x-www-form-urlencoded"
        )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        fetch.assert_not_called()


@patch(FETCH)
class VideoArchiveAcceptHeaderTest(VideoArchiveTestBase):
    """
    The archive is sent to a request that also accepts JSON, in which errors are
    sent; a request that accepts the archive's type alone is refused.
    """

    def test_a_request_accepting_the_archive_and_json_gets_the_archive(self, fetch):
        fetch.return_value = upstream_response()

        response = self.post(
            self.client_for(self.user),
            [{"url": FIRST_URL, "name": "one.mp4"}],
            HTTP_ACCEPT="application/zip, application/json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "application/zip"
        assert archive_entries(response) == {"one.mp4": b"video-bytes"}

    def test_a_request_accepting_only_the_archive_is_refused_before_anything_is_fetched(self, fetch):
        fetch.return_value = upstream_response()

        response = self.post(
            self.client_for(self.user), [{"url": FIRST_URL, "name": "one.mp4"}], HTTP_ACCEPT="application/zip"
        )

        # The status and the envelope are pinned, not the error-type slug, which
        # the shared error catalog decides for every API.
        assert_error_envelope(response, expected_status=status.HTTP_406_NOT_ACCEPTABLE)
        fetch.assert_not_called()


@ddt.ddt
@patch(FETCH)
class VideoArchiveAuthorizationTest(VideoArchiveTestBase):
    """Studio read access to the course is required; everyone else is refused before any fetch."""

    def assert_allowed(self, user, fetch):
        """Assert ``user`` receives the archive of one requested video."""
        fetch.return_value = upstream_response()
        response = self.post(self.client_for(user), [{"url": FIRST_URL, "name": "one.mp4"}])
        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "application/zip"
        assert archive_entries(response) == {"one.mp4": b"video-bytes"}

    def test_global_staff_is_allowed(self, fetch):
        self.assert_allowed(self.user, fetch)

    def test_course_staff_is_allowed(self, fetch):
        self.assert_allowed(self.user_with_role(CourseStaffRole), fetch)

    def test_course_instructor_is_allowed(self, fetch):
        self.assert_allowed(self.user_with_role(CourseInstructorRole), fetch)

    def assert_refused(self, response, fetch, expected_status, slug):
        assert_error_envelope(response, expected_status=expected_status, expected_type_slug=slug)
        fetch.assert_not_called()

    def test_anonymous_is_refused(self, fetch):
        response = self.post(self.client_for(None), [{"url": FIRST_URL, "name": "one.mp4"}])

        self.assert_refused(response, fetch, 401, "authn")
        assert response["WWW-Authenticate"] == 'JWT realm="api"'

    def test_user_without_a_role_is_refused(self, fetch):
        response = self.post(self.client_for(UserFactory.create()), [{"url": FIRST_URL, "name": "one.mp4"}])

        self.assert_refused(response, fetch, 403, "authz")

    def test_limited_staff_is_refused(self, fetch):
        user = self.user_with_role(CourseLimitedStaffRole)

        response = self.post(self.client_for(user), [{"url": FIRST_URL, "name": "one.mp4"}])

        self.assert_refused(response, fetch, 403, "authz")

    def test_staff_of_another_course_is_refused(self, fetch):
        user = self.user_with_role(CourseStaffRole, course_key=self.other_course.id)

        response = self.post(self.client_for(user), [{"url": FIRST_URL, "name": "one.mp4"}])

        self.assert_refused(response, fetch, 403, "authz")

    def test_ccx_course_is_refused_even_to_global_staff(self, fetch):
        response = self.post(self.client_for(self.user), [], course_key=CCX_COURSE_KEY)

        self.assert_refused(response, fetch, 403, "authz")

    def test_missing_course_is_refused_to_a_caller_without_access(self, fetch):
        response = self.post(self.client_for(UserFactory.create()), [], course_key=MISSING_COURSE_KEY)

        self.assert_refused(response, fetch, 403, "authz")

    def test_missing_course_is_reported_to_global_staff(self, fetch):
        response = self.post(self.client_for(self.user), [], course_key=MISSING_COURSE_KEY)

        self.assert_refused(response, fetch, 404, "not-found")

    def test_a_missing_course_is_reported_before_the_body_is_read(self, fetch):
        response = self.client_for(self.user).post(self.url(MISSING_COURSE_KEY), {}, format="json")

        self.assert_refused(response, fetch, 404, "not-found")

    @ddt.data(*VERSION_ONLY_COURSE_KEYS)
    def test_a_key_carrying_only_a_version_is_not_found_by_global_staff(self, key, fetch):
        response = self.post(self.client_for(self.user), [{"url": FIRST_URL, "name": "one.mp4"}], course_key=key)

        self.assert_refused(response, fetch, 404, "not-found")
        assert response.json()["detail"] == "Not found."

    @ddt.data(*VERSION_ONLY_COURSE_KEYS)
    def test_a_key_carrying_only_a_version_is_not_found_by_a_caller_without_a_role(self, key, fetch):
        response = self.post(
            self.client_for(UserFactory.create()), [{"url": FIRST_URL, "name": "one.mp4"}], course_key=key,
        )

        self.assert_refused(response, fetch, 404, "not-found")
        assert response.json()["detail"] == "Not found."

    @ddt.data(*VERSION_ONLY_COURSE_KEYS)
    def test_a_key_carrying_only_a_version_still_needs_authentication(self, key, fetch):
        response = self.post(self.client_for(None), [{"url": FIRST_URL, "name": "one.mp4"}], course_key=key)

        self.assert_refused(response, fetch, 401, "authn")

    def test_the_legacy_address_has_no_route_for_a_version_only_key(self, fetch):
        # The legacy pattern needs a separator inside the key.
        with pytest.raises(Resolver404):
            resolve(f"/api/contentstore/v1/videos/{VERSION_ONLY_COURSE_KEYS[0]}/download")

    def test_the_view_declares_its_checks_in_order(self, fetch):
        # The JWT middleware appends its own restricted-application check to
        # views that accept JWTs, so only the declared head of the list is fixed.
        assert [klass.__name__ for klass in CourseVideoArchiveView.permission_classes[:3]] == [
            "IsAuthenticated", "HasFullCourseKey", "HasStudioReadAccess",
        ]


class VideoArchiveUnexpectedFailureTest(VideoArchiveTestBase):
    """
    An unexpected failure while the archive is set up, at both addresses.

    The new address answers with the generic 500 body from inside the view, so
    the exception never reaches Django: ``got_request_exception`` is not sent,
    and the only log record is Django's one-line 500 notice, without the
    traceback. The legacy address lets the exception reach Django, which sends
    the signal and logs the traceback. Both sides are pinned, so a change in how
    the error handling reports failures shows up here.
    """

    BODY = {"files": [{"url": FIRST_URL, "name": "one.mp4"}]}

    def request_while_failing(self, method, address, target):
        """Send the body as global staff while ``target`` raises; return the response, signals and log records."""
        signals = []

        def receiver(sender, **kwargs):
            signals.append(sender)

        got_request_exception.connect(receiver)
        self.addCleanup(got_request_exception.disconnect, receiver)
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(user=self.user)
        with patch(target, side_effect=RuntimeError("video store unreachable at edxval-3")):
            with self.assertLogs(level="ERROR") as logs:
                response = getattr(client, method)(address, self.BODY, format="json")
        return response, signals, logs.records

    def test_the_failure_is_answered_with_the_generic_body(self):
        response, __, __ = self.request_while_failing("post", self.url(), SERVICE_ARCHIVE_BUILDER)

        envelope = assert_error_envelope(response, expected_status=500, expected_type_slug="internal")
        assert envelope["detail"] == "An unexpected error occurred. Please try again later."
        assert "edxval-3" not in response.content.decode()

    def test_the_failure_does_not_reach_django(self):
        __, signals, records = self.request_while_failing("post", self.url(), SERVICE_ARCHIVE_BUILDER)

        assert not signals
        assert [(record.name, record.getMessage(), record.exc_info) for record in records] == [
            ("django.request", f"Internal Server Error: {self.url()}", None),
        ]

    def test_the_legacy_address_hands_the_failure_to_django(self):
        __, signals, records = self.request_while_failing("put", self.legacy_url(), LEGACY_ARCHIVE_BUILDER)

        assert len(signals) == 1
        assert "django.request" in {
            record.name for record in records if record.exc_info and record.exc_info[0] is RuntimeError
        }


@patch(FETCH)
class VideoArchiveAuthenticationTest(VideoArchiveTestBase):
    """The endpoint uses the platform's default authentication."""

    def test_session_caller_succeeds(self, fetch):
        fetch.return_value = upstream_response()
        client = APIClient()
        assert client.login(username=self.user.username, password=self.user_password)

        response = self.post(client, [{"url": FIRST_URL, "name": "one.mp4"}])

        assert archive_entries(response) == {"one.mp4": b"video-bytes"}

    def test_session_caller_needs_a_csrf_token(self, fetch):
        client = APIClient(enforce_csrf_checks=True)
        assert client.login(username=self.user.username, password=self.user_password)

        response = self.post(client, [{"url": FIRST_URL, "name": "one.mp4"}])
        legacy = self.put_legacy(client, [{"url": FIRST_URL, "name": "one.mp4"}])

        envelope = assert_error_envelope(response, expected_status=403, expected_type_slug="authz")
        assert envelope["detail"].startswith("CSRF Failed")
        assert legacy.status_code == status.HTTP_403_FORBIDDEN
        fetch.assert_not_called()

    def test_jwt_caller_succeeds(self, fetch):
        fetch.return_value = upstream_response()

        response = self.post(
            APIClient(), [{"url": FIRST_URL, "name": "one.mp4"}],
            HTTP_AUTHORIZATION=f"JWT {generate_jwt(self.user)}",
        )

        assert archive_entries(response) == {"one.mp4": b"video-bytes"}

    def bearer_header(self, user):
        token = AccessTokenFactory(user=user, application=ApplicationFactory()).token
        return f"Bearer {token}"

    def test_bearer_token_is_refused(self, fetch):
        response = self.post(
            APIClient(), [{"url": FIRST_URL, "name": "one.mp4"}], HTTP_AUTHORIZATION=self.bearer_header(self.user),
        )

        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
        fetch.assert_not_called()

    def test_the_legacy_address_still_accepts_a_bearer_token(self, fetch):
        response = self.put_legacy(APIClient(), [], HTTP_AUTHORIZATION=self.bearer_header(self.user))

        assert response.status_code == status.HTTP_200_OK

    def deactivated_global_staff(self):
        """Log a global staff member in, then deactivate the account behind the live session."""
        password = "password-12345"
        staff = UserFactory.create(is_staff=True, password=password)
        session_client = APIClient()
        assert session_client.login(username=staff.username, password=password)
        staff.is_active = False
        staff.save()
        return staff, session_client

    def test_inactive_global_staff_is_refused_over_a_session(self, fetch):
        __, session_client = self.deactivated_global_staff()

        response = self.post(session_client, [{"url": FIRST_URL, "name": "one.mp4"}])

        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
        fetch.assert_not_called()

    def test_inactive_global_staff_still_reaches_the_legacy_address_over_a_session(self, fetch):
        __, session_client = self.deactivated_global_staff()

        response = self.put_legacy(session_client, [])

        assert response.status_code == status.HTTP_200_OK

    def test_inactive_global_staff_is_admitted_over_jwt(self, fetch):
        staff, __ = self.deactivated_global_staff()

        response = self.post(APIClient(), [], HTTP_AUTHORIZATION=f"JWT {generate_jwt(staff)}")

        assert response.status_code == status.HTTP_200_OK
        assert len(b"".join(response.streaming_content)) == EMPTY_ARCHIVE_SIZE

    def test_the_view_does_not_declare_authentication_classes(self, fetch):
        assert "authentication_classes" not in CourseVideoArchiveView.__dict__
        assert [klass.__name__ for klass in CourseVideoArchiveView.authentication_classes] == [
            "DefaultJwtAuthentication", "DefaultSessionAuthentication",
        ]


@patch(FETCH)
class VideoArchiveThrottleTest(VideoArchiveTestBase):
    """Archive requests are rate-limited per user, in one budget with the legacy download address."""

    def budget(self):
        return VideoDownloadThrottle().num_requests

    def test_the_view_uses_the_download_throttle(self, fetch):
        assert CourseVideoArchiveView.throttle_classes == (VideoDownloadThrottle,)
        assert self.budget() == 12

    def test_requests_past_the_budget_are_throttled(self, fetch):
        client = self.client_for(self.user)
        for __ in range(self.budget()):
            assert self.post(client, []).status_code == status.HTTP_200_OK

        response = self.post(client, [])

        assert_error_envelope(response, expected_status=429, expected_type_slug="rate-limited")
        assert response["Retry-After"] == "3600"

    def test_the_budget_is_shared_with_the_legacy_address(self, fetch):
        client = self.client_for(self.user)
        for __ in range(self.budget()):
            assert self.put_legacy(client, []).status_code == status.HTTP_200_OK

        response = self.post(client, [])

        assert_error_envelope(response, expected_status=429, expected_type_slug="rate-limited")

    def test_the_budget_is_per_user(self, fetch):
        client = self.client_for(self.user)
        for __ in range(self.budget()):
            self.post(client, [])

        response = self.post(self.client_for(self.user_with_role(CourseStaffRole)), [])

        assert response.status_code == status.HTTP_200_OK

    def test_a_refused_caller_spends_no_budget(self, fetch):
        client = self.client_for(UserFactory.create())
        responses = [self.post(client, []) for __ in range(self.budget() + 2)]

        assert {response.status_code for response in responses} == {status.HTTP_403_FORBIDDEN}

    def test_the_legacy_address_spends_budget_on_a_refused_caller(self, fetch):
        client = self.client_for(UserFactory.create())
        responses = [self.put_legacy(client, []) for __ in range(self.budget() + 1)]

        assert responses[-2].status_code == status.HTTP_403_FORBIDDEN
        assert responses[-1].status_code == status.HTTP_429_TOO_MANY_REQUESTS


@ddt.ddt
@patch(FETCH)
class VideoArchiveParityTest(VideoArchiveTestBase):
    """For the same request and caller, both addresses send the same archive."""

    def pair(self, files, responses_for_each, course_key=None, user=None):
        """POST to the new address and PUT to the legacy one, each fetch answered alike."""
        client = self.client_for(user or self.user)
        with patch(FETCH, side_effect=list(responses_for_each())):
            new = self.post(client, files, course_key)
            new_body = b"".join(new.streaming_content) if new.status_code == 200 else None
        with patch(FETCH, side_effect=list(responses_for_each())):
            legacy = self.put_legacy(client, files, course_key)
            legacy_body = b"".join(legacy.streaming_content) if legacy.status_code == 200 else None
        return legacy, legacy_body, new, new_body

    def assert_same_archive(self, files, responses_for_each, expected_entries):
        """Assert both addresses send byte-identical archives holding ``expected_entries``."""
        legacy, legacy_body, new, new_body = self.pair(files, responses_for_each)
        assert (legacy.status_code, new.status_code) == (status.HTTP_200_OK, status.HTTP_200_OK)
        for header in ("Content-Type", "Content-Disposition"):
            assert new[header] == legacy[header]
        assert new_body == legacy_body
        with zipfile.ZipFile(io.BytesIO(new_body)) as archive:
            assert {name: archive.read(name) for name in archive.namelist()} == expected_entries

    def test_a_name_with_an_extension(self, fetch):
        self.assert_same_archive(
            [{"url": FIRST_URL, "name": "one.mp4"}],
            lambda: [upstream_response(b"a", "video/mp4")],
            {"one.mp4": b"a"},
        )

    def test_an_extension_from_the_content_type(self, fetch):
        self.assert_same_archive(
            [{"url": FIRST_URL, "name": "one"}],
            lambda: [upstream_response(b"a", "video/mp4")],
            {"one.mp4": b"a"},
        )

    def test_an_unknown_content_type(self, fetch):
        self.assert_same_archive(
            [{"url": FIRST_URL, "name": "one"}],
            lambda: [upstream_response(b"a", "application/x-not-a-real-type")],
            {"one": b"a"},
        )

    def test_several_files(self, fetch):
        self.assert_same_archive(
            [{"url": FIRST_URL, "name": "one.mp4"}, {"url": SECOND_URL, "name": "two"}],
            lambda: [upstream_response(b"a", "video/mp4"), upstream_response(b"b", "video/webm")],
            {"one.mp4": b"a", "two.webm": b"b"},
        )

    def test_an_empty_list(self, fetch):
        legacy, legacy_body, new, new_body = self.pair([], lambda: [])

        assert new_body == legacy_body
        assert len(new_body) == EMPTY_ARCHIVE_SIZE
        assert new["Content-Disposition"] == legacy["Content-Disposition"] == (
            f"attachment; filename={self.course.id}_videos.zip"
        )

    def received_until_the_failure(self, send, second_fetch, failure):
        """
        Request two videos whose second fetch fails; return the bytes streamed before the failure.

        The status is sent before the archive, so the response is a 200 and the
        failure surfaces while the body is being read.
        """
        with patch(FETCH, side_effect=[upstream_response(b"first-bytes", "video/mp4"), second_fetch]) as fetch:
            response = send(
                self.client_for(self.user),
                [{"url": FIRST_URL, "name": "one.mp4"}, {"url": SECOND_URL, "name": "two.webm"}],
            )
            assert response.status_code == status.HTTP_200_OK
            received = []
            with pytest.raises(failure):
                read_into(received, response.streaming_content)
            assert [call.args[0] for call in fetch.call_args_list] == [FIRST_URL, SECOND_URL]
        return b"".join(received)

    @ddt.data(
        # The video store answers the second fetch with a server error.
        (failing_upstream_response, requests.HTTPError),
        # The connection to the video store fails on the second fetch.
        (lambda: requests.ConnectionError("Connection reset"), requests.ConnectionError),
    )
    @ddt.unpack
    def test_a_fetch_failing_part_way_stops_the_archive(self, second_fetch, failure, fetch):
        new = self.received_until_the_failure(self.post, second_fetch(), failure)
        legacy = self.received_until_the_failure(self.put_legacy, second_fetch(), failure)
        with patch(FETCH, return_value=upstream_response(b"first-bytes", "video/mp4")):
            response = self.post(self.client_for(self.user), [{"url": FIRST_URL, "name": "one.mp4"}])
            first_video_only = b"".join(response.streaming_content)

        # Both addresses stop after the first video's entry, before the
        # archive's directory: what arrives is the archive of the first video
        # alone, cut short, and no zip reader can open it.
        assert new == legacy
        assert first_video_only[:len(new)] == new
        assert first_video_only[len(new):].startswith(CENTRAL_DIRECTORY_SIGNATURE)
        with pytest.raises(zipfile.BadZipFile):
            zipfile.ZipFile(io.BytesIO(new))

    @ddt.data(
        (None, status.HTTP_401_UNAUTHORIZED, {"developer_message"}),
        ("no-role", status.HTTP_403_FORBIDDEN, {"developer_message"}),
        ("missing-course", status.HTTP_404_NOT_FOUND, {"developer_message", "error_code"}),
        ("bad-url", status.HTTP_400_BAD_REQUEST, {"developer_message"}),
    )
    @ddt.unpack
    def test_error_bodies(self, case, expected_status, legacy_keys, fetch):
        client = {
            None: self.client_for(None),
            "no-role": self.client_for(UserFactory.create()),
        }.get(case, self.client_for(self.user))
        course_key = MISSING_COURSE_KEY if case == "missing-course" else None
        files = [{"url": SSRF_URL if case == "bad-url" else FIRST_URL, "name": "x"}]

        legacy = self.put_legacy(client, files, course_key)
        new = self.post(client, files, course_key)

        assert legacy.status_code == new.status_code == expected_status
        assert set(legacy.json()) == legacy_keys
        expected_new_keys = {"type", "title", "status", "detail", "instance"}
        if case == "bad-url":
            expected_new_keys.add("errors")
        assert set(new.json()) == expected_new_keys
        fetch.assert_not_called()


class VideoArchiveQueryCountTest(VideoArchiveTestBase):
    """
    The new address costs no more queries than the legacy one for the same caller.

    Each error answered by the new address ends with one extra statement, a
    rollback of the request's savepoint, issued by the standard error handling.
    """

    #: Set from the pytest fixture below, which class-based tests cannot request directly.
    django_assert_num_queries = None

    @pytest.fixture(autouse=True)
    def _assert_num_queries(self, django_assert_num_queries):
        self.django_assert_num_queries = django_assert_num_queries

    def measure(self, client, files, legacy_count, new_count, course_key=None):
        """Warm the per-process caches, then count the queries of each address."""
        with patch(FETCH, return_value=upstream_response()):
            self.put_legacy(client, files, course_key)
            self.post(client, files, course_key)
            with self.django_assert_num_queries(legacy_count):
                self.put_legacy(client, files, course_key)
            with self.django_assert_num_queries(new_count):
                self.post(client, files, course_key)

    def test_global_staff(self):
        self.measure(self.client_for(self.user), [{"url": FIRST_URL, "name": "a"}], 11, 11)

    def test_refused_address(self):
        self.measure(self.client_for(self.user), [{"url": SSRF_URL, "name": "a"}], 11, 12)

    def test_refused_caller(self):
        self.measure(self.client_for(UserFactory.create()), [], 8, 7)

    def test_missing_course(self):
        self.measure(self.client_for(self.user), [], 8, 9, course_key=MISSING_COURSE_KEY)

    def test_anonymous(self):
        self.measure(self.client_for(None), [], 5, 6)


@ddt.ddt
class VideoArchiveUrlContractTest(VideoArchiveTestBase):
    """The archive address, and how unusable addresses and requests are answered."""

    def test_conforming_address(self):
        assert self.url() == f"/api/authoring/v2/courses/{self.course.id}/video_archives/"

    def test_the_address_resolves_to_the_view(self):
        match = resolve(self.url())

        assert match.func.cls is CourseVideoArchiveView
        assert match.kwargs == {"course_key": self.course.id}

    def test_the_legacy_address_is_unchanged(self):
        assert self.legacy_url() == f"/api/contentstore/v1/videos/{self.course.id}/download"
        assert resolve(self.legacy_url()).func.view_class.__name__ == "VideoDownloadView"

    def assert_json_not_found(self, address):
        """POST to ``address`` as a JSON client; assert the 404 error envelope and return it."""
        response = self.client_for(self.user).post(
            address, {"files": []}, format="json", HTTP_ACCEPT="application/json",
        )
        assert response["Content-Type"] == "application/json"
        return assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    def test_malformed_course_key_is_answered_with_the_error_envelope(self):
        self.assert_json_not_found("/api/authoring/v2/courses/not-a-key/video_archives/")

    def test_deprecated_course_key_is_answered_with_the_error_envelope(self):
        self.assert_json_not_found("/api/authoring/v2/courses/Org/Course/Run/video_archives/")

    def test_a_block_key_in_place_of_a_course_key_is_answered_with_the_error_envelope(self):
        self.assert_json_not_found("/api/authoring/v2/courses/i4x://Org/Course/video/abc/video_archives/")

    def test_an_unusable_course_key_is_claimed_by_the_fallback_route(self):
        address = "/api/authoring/v2/courses/Org/Course/Run/video_archives/"

        assert resolve(address).func.cls is UnknownRouteView
        assert reverse(
            "authoring_v2:course_video_archive_list_unmatched", kwargs={"course_key": "Org/Course/Run"},
        ) == address
        assert reverse(
            "authoring_v2:course_video_archive_detail_unmatched",
            kwargs={"course_key": "Org/Course/Run", "subpath": "abc"},
        ) == f"{address}abc/"

    def test_the_legacy_address_still_accepts_a_deprecated_course_key(self):
        match = resolve("/api/contentstore/v1/videos/Org/Course/Run/download")

        assert match.func.view_class.__name__ == "VideoDownloadView"
        assert match.kwargs == {"course_id": "Org/Course/Run"}

    @ddt.data("video_archives/abc/", "video_archives//", "video_archives/abc/def/")
    def test_an_address_below_the_archives_is_answered_with_the_error_envelope(self, rest):
        address = f"/api/authoring/v2/courses/{self.course.id}/{rest}"

        assert resolve(address).func.cls is UnknownRouteView
        self.assert_json_not_found(address)

    def test_an_address_below_the_archives_without_the_trailing_slash_is_redirected(self):
        address = f"/api/authoring/v2/courses/{self.course.id}/video_archives/abc"

        response = self.client_for(self.user).post(address, {"files": []}, format="json")

        assert response.status_code == status.HTTP_301_MOVED_PERMANENTLY
        assert response["Location"] == f"{address}/"

    def test_the_address_requires_the_trailing_slash(self):
        with pytest.raises(Resolver404):
            resolve(self.url().rstrip("/"))

    def test_a_post_without_the_trailing_slash_is_redirected(self):
        response = self.client_for(self.user).post(self.url().rstrip("/"), {"files": []}, format="json")

        assert response.status_code == status.HTTP_301_MOVED_PERMANENTLY
        assert response["Location"] == self.url()

    def test_other_methods_are_refused(self):
        client = self.client_for(self.user)
        for response in (client.get(self.url()), client.put(self.url(), {"files": []}, format="json")):
            assert_error_envelope(response, expected_status=405)
            assert response["Allow"] == "POST, OPTIONS"

    def test_an_accept_header_admitting_only_zip_is_refused_before_authentication(self):
        response = self.post(self.client_for(None), [], HTTP_ACCEPT="application/zip")

        assert_error_envelope(response, expected_status=406)
