"""
Unit tests for the course video uploads API.
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import ddt
import pytest
import pytz
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import patched_settings
from edx_django_utils.cache import TieredCache
from edx_rest_framework_extensions.auth.jwt.tests.utils import generate_jwt
from edx_rest_framework_extensions.testing import assert_error_envelope
from edxval.api import create_profile, create_video
from edxval.models import CourseVideo, EncodedVideo, Video
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.test import APIClient

import cms.envs
from cms.djangoapps.contentstore.rest_api.v0.views.authoring_videos import (
    VideosCreateUploadView,
    VideosUploadsView,
)
from cms.djangoapps.contentstore.rest_api.v1.serializers.video_uploads import CourseVideoSerializer
from cms.djangoapps.contentstore.rest_api.v1.views.unknown_route import UnknownRouteView
from cms.djangoapps.contentstore.rest_api.v1.views.video_uploads import CourseVideoUploadsViewSet
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.lib.spectacular import cms_api_filter, cms_mark_superseded_paths
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.video_pipeline.models import VideoUploadsEnabledByDefault
from xmodule.modulestore.tests.factories import CourseFactory

LIST_URL_NAME = "authoring_v1:course_video_list"
DETAIL_URL_NAME = "authoring_v1:course_video_detail"
LEGACY_LIST_URL_NAME = "cms.djangoapps.contentstore:v0:cms_api_create_videos_upload"
LEGACY_DETAIL_URL_NAME = "cms.djangoapps.contentstore:v0:cms_api_videos_uploads"

ENUM_POSTPROCESSING_HOOK = "drf_spectacular.hooks.postprocess_schema_enums"
SUPERSEDED_PATHS_HOOK = "cms.lib.spectacular.cms_mark_superseded_paths"

# The post-processing hooks the CMS registers. Registering any hook replaces
# drf-spectacular's own list, so the enum hook has to be named again alongside.
REGISTERED_POSTPROCESSING_HOOKS = [ENUM_POSTPROCESSING_HOOK, SUPERSEDED_PATHS_HOOK]

PRODUCTION_SETTINGS = "cms.envs.production"
DEVSTACK_SETTINGS = "cms.envs.devstack"
SETTINGS_DIRECTORY = Path(cms.envs.__file__).resolve().parent
REPO_ROOT = SETTINGS_DIRECTORY.parents[1]
MOCK_CONFIG = SETTINGS_DIRECTORY / "mock.yml"

# The schema settings that decide the addresses the document publishes, and the
# subset of them a document generated in-process has to be given.
SCHEMA_SETTING_KEYS = (
    "PREPROCESSING_HOOKS",
    "POSTPROCESSING_HOOKS",
    "SCHEMA_PATH_PREFIX",
    "SCHEMA_PATH_PREFIX_TRIM",
    "SERVERS",
)
GENERATION_SETTING_KEYS = SCHEMA_SETTING_KEYS[:-1]
SETTINGS_MARKER = "schema settings: "


def schema_settings(module, config_file=MOCK_CONFIG):
    """
    Return the schema settings of the deployment settings module ``module``.

    Deployment settings share their mutable defaults with the settings the test
    process runs under and would alter them on import, so they are read in a
    separate process.
    """
    script = (
        "import importlib, json, sys\n"
        "values = importlib.import_module(sys.argv[1]).SPECTACULAR_SETTINGS\n"
        "print(sys.argv[2] + json.dumps({key: values[key] for key in sys.argv[3:]}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, module, SETTINGS_MARKER, *SCHEMA_SETTING_KEYS],
        capture_output=True,
        check=True,
        cwd=REPO_ROOT,
        env={**os.environ, "CMS_CFG": str(config_file), "SERVICE_VARIANT": "cms"},
        text=True,
    )
    reported = next(
        line for line in completed.stdout.splitlines() if line.startswith(SETTINGS_MARKER)
    )
    return json.loads(reported[len(SETTINGS_MARKER):])


VIDEO_UPLOAD_PIPELINE = {
    "BUCKET": "test_bucket",
    "ROOT_PATH": "test_root",
    "CONCURRENT_UPLOAD_LIMIT": 4,
    "VEM_S3_BUCKET": "vem_test_bucket",
}

# Every way one video row of this version may differ from the same row of the
# legacy listing, as the JSON path it shows up at and why it is accepted. The
# row parity tests suppress exactly these paths and assert that each of them
# genuinely occurs, so a row that diverges anywhere else, or that stops
# diverging here, fails. Differences that belong to a whole response rather
# than to a row - the pagination envelope, the created status code, the error
# bodies, the Accept-header redirect and the accepted course key forms - are
# each proven by their own test.
ROW_DIFFERENCES = [
    (
        "status_nontranslated",
        "the untranslated companion field is gone; 'status' carries that value",
    ),
    (
        "created",
        "timestamps keep the microsecond precision the video store records, which the legacy "
        "encoder truncated to milliseconds",
    ),
]

# Fields that legitimately differ per request and are ignored when diffing.
VOLATILE_FIELDS = {"instance", "next", "previous"}


def _normalize(obj, path=""):
    """Flatten ``obj`` to a {json_path: value} map, dropping volatile fields."""
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


def _matches(json_path, declared):
    return json_path == declared or json_path.startswith(declared + ".") or json_path.startswith(declared + "[")


def _diff(legacy, new, declared=()):
    """Return (changed paths, paths not covered by a declared difference)."""
    left, right = _normalize(legacy), _normalize(new)
    changed = {key for key in set(left) | set(right) if left.get(key, "<absent>") != right.get(key, "<absent>")}
    unexpected = {key for key in changed if not any(_matches(key, path) for path in declared)}
    return changed, unexpected


@override_settings(ENABLE_VIDEO_UPLOAD_PIPELINE=True, VIDEO_UPLOAD_PIPELINE=VIDEO_UPLOAD_PIPELINE)
class CourseVideoUploadsTestBase(CourseTestCase):
    """Fixtures shared by every course video uploads test."""

    def setUp(self):
        super().setUp()
        self.course.video_upload_pipeline = {"course_video_upload_token": "test_token"}
        self.save_course()

        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.user)

        self.list_url = reverse(LIST_URL_NAME, kwargs={"course_key": self.course.id})
        # Microsecond-bearing, as every stored creation time is, so that the
        # precision the two versions publish is compared rather than assumed.
        self.created = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=pytz.utc)

    def detail_url(self, edx_video_id, course_key=None):
        """Return the member address of one video."""
        return reverse(
            DETAIL_URL_NAME,
            kwargs={"course_key": course_key or self.course.id, "edx_video_id": edx_video_id},
        )

    def create_videos(self, count=2, status_value="file_complete", course=None):
        """Create ``count`` videos attached to the course and return their ids."""
        course = course or self.course
        video_ids = []
        for index in range(count):
            edx_video_id = f"video-{status_value}-{index}"
            create_video({
                "edx_video_id": edx_video_id,
                "client_video_id": f"{edx_video_id}.mp4",
                "duration": 42.0 + index,
                "status": status_value,
                "courses": [str(course.id)],
                "encoded_videos": [],
            })
            video_ids.append(edx_video_id)
        self.set_created(video_ids)
        return video_ids

    def set_created(self, video_ids, base=None, age=None):
        """
        Pin the creation time of ``video_ids``.

        ``created`` is assigned by the database on insert, so it is rewritten
        here to make ordering deterministic and, with ``age``, to age a video
        past the upload window.
        """
        base = base or self.created
        for offset, edx_video_id in enumerate(video_ids):
            created = base + timedelta(seconds=offset)
            if age:
                created = datetime.now(pytz.utc) - age
            Video.objects.filter(edx_video_id=edx_video_id).update(created=created)

    def create_course_author(self, role_class, course=None):
        """Create a user holding ``role_class`` on the course and return them."""
        user = UserFactory.create(is_staff=False)
        role_class((course or self.course).id).add_users(user)
        return user

    def client_for(self, user):
        """Return an API client authenticated as ``user``."""
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def legacy_detail_url(self, edx_video_id, course=None):
        """Return the legacy v0 member address."""
        return reverse(
            LEGACY_DETAIL_URL_NAME,
            kwargs={"course_id": str((course or self.course).id), "edx_video_id": edx_video_id},
        )

    def legacy_list_url(self, course=None):
        """Return the legacy v0 create address."""
        return reverse(LEGACY_LIST_URL_NAME, kwargs={"course_id": str((course or self.course).id)})


class CurrentWorkflowTestBase(CourseVideoUploadsTestBase):
    """
    Fixtures for a course on the current video workflow.

    Such a course holds no course video upload token; video uploads are enabled
    for the whole platform instead. This is the only configuration in which a
    video reaches a transcription status, so it is the only one in which the
    encodes-ready branch of the display status and the transcription status are
    reachable at all.
    """

    def setUp(self):
        super().setUp()
        self.course.video_upload_pipeline = {}
        self.save_course()
        VideoUploadsEnabledByDefault.objects.create(enabled=True, enabled_for_all_courses=True)
        self.addCleanup(TieredCache.delete_all_tiers, VideoUploadsEnabledByDefault.cache_key_name())


def _mock_bucket(mock_boto3_resource, upload_url="http://example.com/put_video"):
    """Point ``boto3.resource`` at a bucket whose pre-signed URLs are predictable."""
    mock_s3_client = Mock()
    mock_s3_client.generate_presigned_url.return_value = upload_url
    mock_bucket = Mock()
    mock_bucket.name = "vem_test_bucket"
    mock_bucket.meta.client = mock_s3_client
    mock_resource = Mock()
    mock_resource.Bucket.return_value = mock_bucket
    mock_boto3_resource.return_value = mock_resource
    return mock_s3_client


@ddt.ddt
class CourseVideoListTest(CourseVideoUploadsTestBase):
    """Tests for listing a course's videos."""

    def test_list_returns_pagination_envelope(self):
        self.create_videos(count=2)
        response = self.api_client.get(self.list_url)
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == {
            "count", "num_pages", "current_page", "start", "next", "previous", "results",
        }
        assert response.data["count"] == 2

    def test_list_row_shape(self):
        self.create_videos(count=1)
        response = self.api_client.get(self.list_url)
        assert set(response.data["results"][0]) == {
            "edx_video_id",
            "client_video_id",
            "created",
            "duration",
            "status",
            "error_description",
            "course_video_image_url",
            "download_link",
            "file_size",
            "transcripts",
            "transcription_status",
            "transcript_urls",
        }

    def test_list_excludes_other_courses(self):
        other_course = CourseFactory.create()
        other_course.video_upload_pipeline = {"course_video_upload_token": "test_token"}
        other_course.save()
        self.store.update_item(other_course, self.user.id)
        self.create_videos(count=1, status_value="elsewhere", course=other_course)
        self.create_videos(count=1)

        response = self.api_client.get(self.list_url)
        assert [row["edx_video_id"] for row in response.data["results"]] == ["video-file_complete-0"]

    def test_list_excludes_removed_videos(self):
        video_ids = self.create_videos(count=2)
        CourseVideo.objects.filter(
            course_id=str(self.course.id), video__edx_video_id=video_ids[0]
        ).update(is_hidden=True)

        response = self.api_client.get(self.list_url)
        assert [row["edx_video_id"] for row in response.data["results"]] == [video_ids[1]]

    def test_list_defaults_to_newest_first(self):
        video_ids = self.create_videos(count=3)
        response = self.api_client.get(self.list_url)
        assert [row["edx_video_id"] for row in response.data["results"]] == list(reversed(video_ids))

    def test_list_honours_ordering(self):
        video_ids = self.create_videos(count=3)
        response = self.api_client.get(self.list_url, {"ordering": "created"})
        assert [row["edx_video_id"] for row in response.data["results"]] == video_ids

    def test_list_rejects_unknown_ordering(self):
        response = self.api_client.get(self.list_url, {"ordering": "status"})
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert "ordering" in response.data["errors"]

    def test_list_reduces_an_oversized_page_size_to_the_maximum(self):
        self.create_videos(count=101)
        response = self.api_client.get(self.list_url, {"page_size": 500})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 100
        assert response.data["count"] == 101
        assert response.data["num_pages"] == 2

    def test_list_accepts_the_maximum_page_size(self):
        self.create_videos(count=11)
        response = self.api_client.get(self.list_url, {"page_size": 100})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 11
        assert response.data["num_pages"] == 1

    def test_list_returns_ten_videos_a_page_by_default(self):
        self.create_videos(count=11)
        response = self.api_client.get(self.list_url)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 10
        assert response.data["count"] == 11
        assert response.data["num_pages"] == 2

    @ddt.data("abc", "0", "-1")
    def test_list_rejects_an_unusable_page(self, page):
        response = self.api_client.get(self.list_url, {"page": page})
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert "page" in response.data["errors"]

    @ddt.data("0", "-1", "abc")
    def test_list_rejects_an_unusable_page_size(self, page_size):
        response = self.api_client.get(self.list_url, {"page_size": page_size})
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert "page_size" in response.data["errors"]

    @ddt.data("full", "miniml", "MINIMAL")
    def test_list_rejects_an_unknown_view(self, view):
        response = self.api_client.get(self.list_url, {"view": view})
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert "view" in response.data["errors"]

    def test_list_paginates(self):
        self.create_videos(count=3)
        response = self.api_client.get(self.list_url, {"page": 2, "page_size": 1})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 3
        assert response.data["num_pages"] == 3
        assert response.data["current_page"] == 2
        assert response.data["start"] == 1
        assert len(response.data["results"]) == 1

    def test_list_minimal_view_is_opt_in(self):
        self.create_videos(count=1)
        full = self.api_client.get(self.list_url)
        minimal = self.api_client.get(self.list_url, {"view": "minimal"})
        assert set(minimal.data["results"][0]) == {
            "edx_video_id", "client_video_id", "status", "created", "duration",
        }
        assert set(minimal.data["results"][0]) < set(full.data["results"][0])

    def test_the_serializer_is_built_with_the_request_context(self):
        captured = {}

        class ContextCapturingSerializer(CourseVideoSerializer):
            """Record the context the view builds its serializers with."""

            def __init__(self, *args, **kwargs):
                captured.update(kwargs.get("context") or {})
                super().__init__(*args, **kwargs)

        self.create_videos(count=1)
        with patch.object(
            CourseVideoUploadsViewSet, "serializer_class", ContextCapturingSerializer
        ):
            response = self.api_client.get(self.list_url)

        assert response.status_code == status.HTTP_200_OK
        assert captured["request"].path == self.list_url
        assert isinstance(captured["view"], CourseVideoUploadsViewSet)
        assert "format" in captured

    def test_list_404_when_pipeline_disabled(self):
        with override_settings(ENABLE_VIDEO_UPLOAD_PIPELINE=False):
            response = self.api_client.get(self.list_url)
        assert_error_envelope(response, expected_status=404, expected_type_slug="videos/uploads-not-configured")

    def test_list_404_when_course_not_configured(self):
        self.course.video_upload_pipeline = {}
        self.save_course()
        response = self.api_client.get(self.list_url)
        assert_error_envelope(response, expected_status=404, expected_type_slug="videos/uploads-not-configured")

    def test_list_404_for_a_page_past_the_end(self):
        self.create_videos(count=1)
        response = self.api_client.get(self.list_url, {"page": 5})
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    def test_list_404_for_unknown_course(self):
        unknown = CourseKey.from_string("course-v1:no+such+course")
        response = self.api_client.get(reverse(LIST_URL_NAME, kwargs={"course_key": unknown}))
        assert_error_envelope(response, expected_status=404)


class CourseVideoRetrieveTest(CourseVideoUploadsTestBase):
    """Tests for retrieving one of a course's videos."""

    def test_retrieve_returns_the_addressed_video(self):
        video_ids = self.create_videos(count=2)
        response = self.api_client.get(self.detail_url(video_ids[0]))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["edx_video_id"] == video_ids[0]

    def test_retrieve_reports_encoding_and_transcripts(self):
        create_profile("desktop_mp4")
        create_video({
            "edx_video_id": "encoded",
            "client_video_id": "encoded.mp4",
            "duration": 12.0,
            "status": "file_complete",
            "courses": [str(self.course.id)],
            "encoded_videos": [{
                "profile": "desktop_mp4",
                "url": "http://example.com/encoded.mp4",
                "file_size": 1600,
                "bitrate": 100,
            }],
        })
        response = self.api_client.get(self.detail_url("encoded"))
        assert response.data["download_link"] == "http://example.com/encoded.mp4"
        assert response.data["file_size"] == 1600
        assert response.data["transcripts"] == []
        assert response.data["transcript_urls"] == {}

    def test_retrieve_minimal_view(self):
        video_ids = self.create_videos(count=1)
        response = self.api_client.get(self.detail_url(video_ids[0]), {"view": "minimal"})
        assert set(response.data) == {
            "edx_video_id", "client_video_id", "status", "created", "duration",
        }

    def test_retrieve_rejects_an_unknown_view(self):
        video_ids = self.create_videos(count=1)
        response = self.api_client.get(self.detail_url(video_ids[0]), {"view": "full"})
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert "view" in response.data["errors"]

    def test_retrieve_404_for_unknown_video(self):
        response = self.api_client.get(self.detail_url("no-such-video"))
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    def test_retrieve_404_when_pipeline_disabled(self):
        video_ids = self.create_videos(count=1)
        with override_settings(ENABLE_VIDEO_UPLOAD_PIPELINE=False):
            response = self.api_client.get(self.detail_url(video_ids[0]))
        assert_error_envelope(response, expected_status=404, expected_type_slug="videos/uploads-not-configured")

    def test_retrieve_404_for_video_of_another_course(self):
        other_course = CourseFactory.create()
        other_course.video_upload_pipeline = {"course_video_upload_token": "test_token"}
        other_course.save()
        self.store.update_item(other_course, self.user.id)
        video_ids = self.create_videos(count=1, status_value="elsewhere", course=other_course)

        response = self.api_client.get(self.detail_url(video_ids[0]))
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    def test_retrieve_404_for_removed_video(self):
        video_ids = self.create_videos(count=1)
        CourseVideo.objects.filter(
            course_id=str(self.course.id), video__edx_video_id=video_ids[0]
        ).update(is_hidden=True)
        response = self.api_client.get(self.detail_url(video_ids[0]))
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")


@ddt.ddt
class CurrentWorkflowStatusTest(CurrentWorkflowTestBase):
    """What a course on the current video workflow reports for each stored status."""

    def row(self, status_value):
        """Create one video in ``status_value`` and return its row from the listing."""
        self.create_videos(count=1, status_value=status_value)
        response = self.api_client.get(self.list_url)
        assert response.status_code == status.HTTP_200_OK
        result, = response.data["results"]
        return result

    @ddt.data(
        ("transcription_in_progress", "Transcription in Progress"),
        ("transcript_ready", "Transcript Ready"),
        ("partial_failure", "Partial Failure"),
        ("transcript_failed", "Transcript Failed"),
    )
    @ddt.unpack
    def test_transcription_status_names_the_transcription_state(self, status_value, expected):
        row = self.row(status_value)
        assert row["transcription_status"] == expected
        assert row["status"] == "Ready"

    @ddt.data(
        ("ingest", "In Progress"),
        ("file_complete", "Ready"),
        ("upload_completed", "Uploaded"),
        ("pipeline_error", "Failed"),
    )
    @ddt.unpack
    def test_transcription_status_is_empty_before_transcription_starts(self, status_value, expected):
        row = self.row(status_value)
        assert row["transcription_status"] == ""
        assert row["status"] == expected

    def test_an_invalid_token_is_reported_as_a_youtube_duplicate(self):
        row = self.row("invalid_token")
        assert row["status"] == "YouTube Duplicate"
        assert row["transcription_status"] == ""

    def test_the_member_reports_the_same_transcription_state(self):
        video_ids = self.create_videos(count=1, status_value="transcript_failed")
        response = self.api_client.get(self.detail_url(video_ids[0]))
        assert response.data["transcription_status"] == "Transcript Failed"
        assert response.data["status"] == "Ready"


class UploadTokenStatusTest(CourseVideoUploadsTestBase):
    """A course still holding an upload token reports no transcription state."""

    def test_a_transcription_status_is_reported_as_the_display_status_only(self):
        self.create_videos(count=1, status_value="transcription_in_progress")
        response = self.api_client.get(self.list_url)
        result, = response.data["results"]
        assert result["status"] == "Transcription in Progress"
        assert result["transcription_status"] == ""


@ddt.ddt
class CourseVideoCreateTest(CourseVideoUploadsTestBase):
    """Tests for creating upload slots."""

    @patch("cms.djangoapps.contentstore.video_storage_handlers.boto3.resource")
    def test_create_returns_one_slot_per_file(self, mock_boto3_resource):
        _mock_bucket(mock_boto3_resource)
        response = self.api_client.post(
            self.list_url,
            {"files": [
                {"file_name": "first.mp4", "content_type": "video/mp4"},
                {"file_name": "second.mov", "content_type": "video/quicktime"},
            ]},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert [entry["file_name"] for entry in response.data["files"]] == ["first.mp4", "second.mov"]
        for entry in response.data["files"]:
            assert set(entry) == {"file_name", "upload_url", "edx_video_id"}
            assert entry["upload_url"] == "http://example.com/put_video"
            assert Video.objects.filter(edx_video_id=entry["edx_video_id"], status="upload").exists()
            assert CourseVideo.objects.filter(
                course_id=str(self.course.id), video__edx_video_id=entry["edx_video_id"]
            ).exists()

    @patch("cms.djangoapps.contentstore.video_storage_handlers.boto3.resource")
    def test_create_presigns_with_the_requested_content_type(self, mock_boto3_resource):
        mock_s3_client = _mock_bucket(mock_boto3_resource)
        self.api_client.post(
            self.list_url,
            {"files": [{"file_name": "first.mov", "content_type": "video/quicktime"}]},
            format="json",
        )
        params = mock_s3_client.generate_presigned_url.call_args[1]["Params"]
        assert params["ContentType"] == "video/quicktime"
        assert params["Metadata"]["client_video_id"] == "first.mov"
        assert params["Metadata"]["course_key"] == str(self.course.id)

    def test_create_rejects_unsupported_content_type(self):
        response = self.api_client.post(
            self.list_url,
            {"files": [{"file_name": "first.webm", "content_type": "video/webm"}]},
            format="json",
        )
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert response.data["errors"]["files[0].content_type"] == [
            '"video/webm" is not a valid choice.'
        ]

    def test_create_names_the_entry_each_message_belongs_to(self):
        response = self.api_client.post(
            self.list_url,
            {"files": [
                {"file_name": "first.mp4", "content_type": "video/mp4"},
                {"content_type": "video/avi"},
            ]},
            format="json",
        )
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert response.data["errors"] == {
            "files[1].file_name": ["This field is required."],
            "files[1].content_type": ['"video/avi" is not a valid choice.'],
        }

    def test_create_rejects_non_ascii_file_name(self):
        response = self.api_client.post(
            self.list_url,
            {"files": [{"file_name": "nón-ascii-näme.mp4", "content_type": "video/mp4"}]},
            format="json",
        )
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert response.data["errors"]["files[0].file_name"] == [
            "The file name must contain only ASCII characters."
        ]

    def test_create_rejects_missing_files(self):
        response = self.api_client.post(self.list_url, {}, format="json")
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert response.data["errors"]["files"] == ["This field is required."]

    def test_create_rejects_empty_files(self):
        response = self.api_client.post(self.list_url, {"files": []}, format="json")
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert response.data["errors"]["files"] == ["This list may not be empty."]

    def test_create_reports_every_message_as_a_string(self):
        response = self.api_client.post(self.list_url, {"files": []}, format="json")
        errors = response.data["errors"]
        assert errors
        for messages in errors.values():
            assert all(isinstance(message, str) for message in messages)
        assert "ErrorDetail" not in json.dumps(errors)

    def test_create_rejects_unexpected_fields(self):
        response = self.api_client.post(
            self.list_url,
            {"files": [{"file_name": "first.mp4", "content_type": "video/mp4"}], "extra": 1},
            format="json",
        )
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert "extra" in response.data["errors"]

    def post_with_store_refusal(self, refusal_status):
        """Create an upload slot against a video store that refuses with ``refusal_status``."""
        with patch(
            "cms.djangoapps.contentstore.rest_api.v1.video_uploads_service.videos_post",
            return_value=({"error": "internal bucket detail"}, refusal_status),
        ):
            return self.api_client.post(
                self.list_url,
                {"files": [{"file_name": "first.mp4", "content_type": "video/mp4"}]},
                format="json",
            )

    def test_create_reports_a_stable_message_when_the_store_refuses(self):
        response = self.post_with_store_refusal(400)
        assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert "internal bucket detail" not in json.dumps(response.data)

    @ddt.data(
        (403, "authz"),
        (404, "not-found"),
    )
    @ddt.unpack
    def test_create_keeps_the_status_the_store_refused_with(self, refusal_status, expected_slug):
        response = self.post_with_store_refusal(refusal_status)
        assert_error_envelope(
            response, expected_status=refusal_status, expected_type_slug=expected_slug
        )
        assert "internal bucket detail" not in json.dumps(response.data)

    def test_create_reports_an_unexpected_refusal_as_a_server_error(self):
        response = self.post_with_store_refusal(418)
        assert_error_envelope(response, expected_status=500, expected_type_slug="internal")

    def test_create_404_when_pipeline_disabled(self):
        with override_settings(ENABLE_VIDEO_UPLOAD_PIPELINE=False):
            response = self.api_client.post(
                self.list_url,
                {"files": [{"file_name": "first.mp4", "content_type": "video/mp4"}]},
                format="json",
            )
        assert_error_envelope(response, expected_status=404, expected_type_slug="videos/uploads-not-configured")


class CourseVideoDestroyTest(CourseVideoUploadsTestBase):
    """Tests for removing a video from a course."""

    def test_destroy_detaches_the_video(self):
        video_ids = self.create_videos(count=1)
        response = self.api_client.delete(self.detail_url(video_ids[0]))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not response.data
        course_video = CourseVideo.objects.get(
            course_id=str(self.course.id), video__edx_video_id=video_ids[0]
        )
        assert course_video.is_hidden is True
        assert Video.objects.filter(edx_video_id=video_ids[0]).exists()

    def test_destroy_leaves_other_courses_alone(self):
        other_course = CourseFactory.create()
        create_video({
            "edx_video_id": "shared",
            "client_video_id": "shared.mp4",
            "duration": 1.0,
            "status": "file_complete",
            "courses": [str(self.course.id), str(other_course.id)],
            "encoded_videos": [],
        })
        self.api_client.delete(self.detail_url("shared"))
        assert CourseVideo.objects.get(
            course_id=str(other_course.id), video__edx_video_id="shared"
        ).is_hidden is False

    def test_destroy_accepts_a_video_already_detached_from_the_course(self):
        # A detached video is invisible to the reads but still removable, as it
        # is on the legacy endpoints.
        video_ids = self.create_videos(count=1)
        self.api_client.delete(self.detail_url(video_ids[0]))

        assert self.api_client.get(self.detail_url(video_ids[0])).status_code == (
            status.HTTP_404_NOT_FOUND
        )
        again = self.api_client.delete(self.detail_url(video_ids[0]))
        assert again.status_code == status.HTTP_204_NO_CONTENT
        assert CourseVideo.objects.get(
            course_id=str(self.course.id), video__edx_video_id=video_ids[0]
        ).is_hidden is True

    def test_destroy_404_for_unknown_video(self):
        response = self.api_client.delete(self.detail_url("no-such-video"))
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    def test_destroy_404_when_pipeline_disabled(self):
        video_ids = self.create_videos(count=1)
        with override_settings(ENABLE_VIDEO_UPLOAD_PIPELINE=False):
            response = self.api_client.delete(self.detail_url(video_ids[0]))
        assert_error_envelope(response, expected_status=404, expected_type_slug="videos/uploads-not-configured")
        assert CourseVideo.objects.get(
            course_id=str(self.course.id), video__edx_video_id=video_ids[0]
        ).is_hidden is False


class CourseVideoRequestErrorTest(CourseVideoUploadsTestBase):
    """A request the API refuses to read is reported as the caller's error, not the server's."""

    def test_a_malformed_body_is_reported_as_a_client_error(self):
        response = self.api_client.post(
            self.list_url, data="{not json", content_type="application/json"
        )
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["title"] == "Malformed Request"

    def test_an_unsupported_method_is_reported_as_such(self):
        response = self.api_client.put(self.list_url, {}, format="json")
        envelope = assert_error_envelope(
            response, expected_status=405, expected_type_slug="method-not-allowed"
        )
        assert envelope["title"] == "Method Not Allowed"

    def test_an_unsupported_media_type_is_reported_as_such(self):
        response = self.api_client.post(
            self.list_url, data="file_name,content_type", content_type="text/csv"
        )
        envelope = assert_error_envelope(
            response, expected_status=415, expected_type_slug="unsupported-media-type"
        )
        assert envelope["title"] == "Unsupported Media Type"

    def test_an_unsatisfiable_accept_header_is_reported_as_such(self):
        response = self.api_client.get(self.list_url, HTTP_ACCEPT="application/xml")
        envelope = assert_error_envelope(
            response, expected_status=406, expected_type_slug="not-acceptable"
        )
        assert envelope["title"] == "Not Acceptable"


@ddt.ddt
class CourseVideoAuthorizationTest(CourseVideoUploadsTestBase):
    """Who may reach each operation, and who may not."""

    def setUp(self):
        super().setUp()
        self.video_id = self.create_videos(count=1)[0]

    def call(self, client, operation, course_key=None):
        """Issue ``operation`` against the course video endpoints as ``client``."""
        course_key = course_key or self.course.id
        list_url = reverse(LIST_URL_NAME, kwargs={"course_key": course_key})
        detail_url = reverse(
            DETAIL_URL_NAME, kwargs={"course_key": course_key, "edx_video_id": self.video_id}
        )
        if operation == "list":
            return client.get(list_url)
        if operation == "retrieve":
            return client.get(detail_url)
        if operation == "create":
            with patch("cms.djangoapps.contentstore.video_storage_handlers.boto3.resource") as resource:
                _mock_bucket(resource)
                return client.post(
                    list_url,
                    {"files": [{"file_name": "first.mp4", "content_type": "video/mp4"}]},
                    format="json",
                )
        return client.delete(detail_url)

    @ddt.data(
        ("list", status.HTTP_200_OK),
        ("retrieve", status.HTTP_200_OK),
        ("create", status.HTTP_201_CREATED),
        ("destroy", status.HTTP_204_NO_CONTENT),
    )
    @ddt.unpack
    def test_global_staff_is_allowed(self, operation, expected_status):
        assert self.call(self.client_for(self.user), operation).status_code == expected_status

    @ddt.data(
        ("list", status.HTTP_200_OK),
        ("retrieve", status.HTTP_200_OK),
        ("create", status.HTTP_201_CREATED),
        ("destroy", status.HTTP_204_NO_CONTENT),
    )
    @ddt.unpack
    def test_course_staff_is_allowed(self, operation, expected_status):
        author = self.create_course_author(CourseStaffRole)
        assert self.call(self.client_for(author), operation).status_code == expected_status

    @ddt.data(
        ("list", status.HTTP_200_OK),
        ("retrieve", status.HTTP_200_OK),
        ("create", status.HTTP_201_CREATED),
        ("destroy", status.HTTP_204_NO_CONTENT),
    )
    @ddt.unpack
    def test_course_instructor_is_allowed(self, operation, expected_status):
        author = self.create_course_author(CourseInstructorRole)
        assert self.call(self.client_for(author), operation).status_code == expected_status

    @ddt.data("list", "retrieve", "create", "destroy")
    def test_anonymous_is_refused(self, operation):
        response = self.call(self.client_for(None), operation)
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    @ddt.data("list", "retrieve", "create", "destroy")
    def test_user_without_a_role_is_refused(self, operation):
        stranger = UserFactory.create(is_staff=False)
        response = self.call(self.client_for(stranger), operation)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    @ddt.data("list", "retrieve", "create", "destroy")
    def test_limited_staff_is_refused(self, operation):
        limited = self.create_course_author(CourseLimitedStaffRole)
        response = self.call(self.client_for(limited), operation)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    @ddt.data("list", "retrieve", "create", "destroy")
    def test_author_of_another_course_is_refused(self, operation):
        other_course = CourseFactory.create()
        author = self.create_course_author(CourseStaffRole, course=other_course)
        response = self.call(self.client_for(author), operation)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    @ddt.data("list", "retrieve", "create", "destroy")
    def test_ccx_course_key_is_refused(self, operation):
        course_id = self.course.id
        ccx_key = CourseKey.from_string(
            f"ccx-v1:{course_id.org}+{course_id.course}+{course_id.run}+ccx@1"
        )
        response = self.call(self.client_for(self.user), operation, course_key=ccx_key)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    @ddt.data("list", "retrieve", "create", "destroy")
    def test_a_course_the_caller_cannot_read_in_studio_is_refused(self, operation):
        # Studio's own read check refuses with its framework-level error, which
        # carries no status of its own; the operation must still answer 403.
        with patch(
            "cms.djangoapps.contentstore.rest_api.v1.video_uploads_service._get_and_validate_course",
            side_effect=DjangoPermissionDenied(),
        ):
            response = self.call(self.client_for(self.user), operation)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    @ddt.data("list", "retrieve", "create", "destroy")
    def test_a_course_that_cannot_be_loaded_is_reported_as_missing(self, operation):
        with patch(
            "cms.djangoapps.contentstore.rest_api.v1.video_uploads_service._get_and_validate_course",
            side_effect=Http404(),
        ):
            response = self.call(self.client_for(self.user), operation)
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    @ddt.data("list", "retrieve", "create", "destroy")
    def test_no_domain_record_is_touched_when_refused(self, operation):
        stranger = UserFactory.create(is_staff=False)
        before = (Video.objects.count(), CourseVideo.objects.count())
        self.call(self.client_for(stranger), operation)
        assert (Video.objects.count(), CourseVideo.objects.count()) == before


class CourseVideoReadOnlyTest(CourseVideoUploadsTestBase):
    """Reading the course's videos must not change any of them."""

    def setUp(self):
        super().setUp()
        self.create_videos(count=1, status_value="upload")
        self.stuck_id = "video-upload-0"
        self.set_created([self.stuck_id], age=timedelta(hours=48))

    def row_counts(self):
        return (Video.objects.count(), CourseVideo.objects.count(), EncodedVideo.objects.count())

    def test_list_reports_a_stuck_upload_as_failed_without_changing_it(self):
        before = Video.objects.get(edx_video_id=self.stuck_id).status
        counts = self.row_counts()

        response = self.api_client.get(self.list_url)

        assert response.data["results"][0]["status"] == "Failed"
        assert Video.objects.get(edx_video_id=self.stuck_id).status == before
        assert self.row_counts() == counts

    def test_head_on_the_list_changes_nothing(self):
        before = Video.objects.get(edx_video_id=self.stuck_id).status
        counts = self.row_counts()

        response = self.api_client.head(self.list_url)

        assert response.status_code == status.HTTP_200_OK
        assert Video.objects.get(edx_video_id=self.stuck_id).status == before
        assert self.row_counts() == counts

    def test_head_on_the_member_changes_nothing(self):
        before = Video.objects.get(edx_video_id=self.stuck_id).status
        counts = self.row_counts()

        response = self.api_client.head(self.detail_url(self.stuck_id))

        assert response.status_code == status.HTTP_200_OK
        assert Video.objects.get(edx_video_id=self.stuck_id).status == before
        assert self.row_counts() == counts

    def test_retrieve_reports_a_stuck_upload_as_failed_without_changing_it(self):
        before = Video.objects.get(edx_video_id=self.stuck_id).status
        counts = self.row_counts()

        response = self.api_client.get(self.detail_url(self.stuck_id))

        assert response.data["status"] == "Failed"
        assert Video.objects.get(edx_video_id=self.stuck_id).status == before
        assert self.row_counts() == counts

    def test_the_legacy_route_still_reconciles_a_stuck_upload(self):
        from cms.djangoapps.contentstore.utils import reverse_course_url

        assert Video.objects.get(edx_video_id=self.stuck_id).status == "upload"
        response = self.client.get_json(reverse_course_url("videos_handler", str(self.course.id)))
        assert response.status_code == status.HTTP_200_OK
        assert Video.objects.get(edx_video_id=self.stuck_id).status == "upload_failed"


class CourseVideoAuthenticationTest(CourseVideoUploadsTestBase):
    """The endpoints use the platform's default authentication."""

    def test_session_caller_succeeds(self):
        client = APIClient()
        client.login(username=self.user.username, password=self.user_password)
        assert client.get(self.list_url).status_code == status.HTTP_200_OK

    def test_jwt_caller_succeeds(self):
        client = APIClient()
        token = generate_jwt(self.user)
        assert client.get(
            self.list_url, HTTP_AUTHORIZATION=f"JWT {token}"
        ).status_code == status.HTTP_200_OK

    def deactivated_session_client(self, is_staff=False):
        """Log a course author in, then deactivate the account behind the live session."""
        password = "password-12345"
        author = UserFactory.create(is_staff=is_staff, password=password)
        CourseStaffRole(self.course.id).add_users(author)
        client = APIClient()
        assert client.login(username=author.username, password=password)
        author.is_active = False
        author.save()
        return client

    def test_inactive_author_is_refused(self):
        response = self.deactivated_session_client().get(self.list_url)

        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    def test_inactive_author_is_refused_by_the_legacy_address_too(self):
        video_ids = self.create_videos(count=1)
        response = self.deactivated_session_client().get(
            self.legacy_detail_url(video_ids[0]), HTTP_ACCEPT="application/json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_inactive_global_staff_is_refused(self):
        response = self.deactivated_session_client(is_staff=True).get(self.list_url)

        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    def test_inactive_global_staff_still_reaches_the_legacy_address(self):
        video_ids = self.create_videos(count=1)
        response = self.deactivated_session_client(is_staff=True).get(
            self.legacy_detail_url(video_ids[0]), HTTP_ACCEPT="application/json"
        )

        assert response.status_code == status.HTTP_200_OK

    def test_the_viewset_does_not_declare_authentication_classes(self):
        assert "authentication_classes" not in CourseVideoUploadsViewSet.__dict__
        assert not any(
            "Bearer" in klass.__name__ or "OAuth2" in klass.__name__
            for klass in CourseVideoUploadsViewSet.authentication_classes
        )


class CourseVideoUrlContractTest(CourseVideoUploadsTestBase):
    """The addresses, their names, and what happens to keys they do not accept."""

    def test_conforming_addresses(self):
        course_key = "course-v1:edX+DemoX+Demo_Course"
        assert reverse(LIST_URL_NAME, kwargs={"course_key": course_key}) == (
            f"/api/authoring/v1/courses/{course_key}/videos/"
        )
        assert reverse(
            DETAIL_URL_NAME, kwargs={"course_key": course_key, "edx_video_id": "abc-123"}
        ) == f"/api/authoring/v1/courses/{course_key}/videos/abc-123/"

    def test_conforming_addresses_resolve_to_the_viewset(self):
        assert resolve(self.list_url).func.cls is CourseVideoUploadsViewSet
        assert resolve(self.detail_url("abc-123")).func.cls is CourseVideoUploadsViewSet

    def test_a_usable_course_key_reaches_the_viewset_and_not_the_standby_routes(self):
        """A key the converter accepts must be served by the endpoints, whatever stands behind them."""
        list_match, detail_match = resolve(self.list_url), resolve(self.detail_url("abc-123"))

        assert (list_match.url_name, detail_match.url_name) == ("course_video_list", "course_video_detail")
        assert list_match.kwargs["course_key"] == self.course.id
        assert detail_match.kwargs["course_key"] == self.course.id
        assert isinstance(detail_match.kwargs["course_key"], CourseKey)

    def test_legacy_addresses_are_unchanged(self):
        course_id = str(self.course.id)
        assert self.legacy_list_url() == f"/api/contentstore/v0/videos/uploads/{course_id}"
        assert self.legacy_detail_url("abc-123") == (
            f"/api/contentstore/v0/videos/uploads/{course_id}/abc-123"
        )
        assert resolve(self.legacy_list_url()).func.cls is VideosCreateUploadView
        assert resolve(self.legacy_detail_url("abc-123")).func.cls is VideosUploadsView

    def assert_json_not_found(self, url):
        """Assert that ``url`` answers with the API error body rather than an error page."""
        response = self.api_client.get(url)
        assert response["Content-Type"].startswith("application/json")
        return assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    def test_deprecated_course_key_is_not_routed(self):
        self.assert_json_not_found("/api/authoring/v1/courses/Org/Course/Run/videos/")

    def test_deprecated_course_key_is_not_routed_on_the_member_address(self):
        self.assert_json_not_found("/api/authoring/v1/courses/Org/Course/Run/videos/abc-123/")

    def test_the_legacy_addresses_still_accept_a_deprecated_course_key(self):
        legacy_match = resolve("/api/contentstore/v0/videos/uploads/Org/Course/Run/abc-123")
        assert legacy_match.func.cls is VideosUploadsView
        assert legacy_match.kwargs["course_id"] == "Org/Course/Run"

    def test_the_legacy_list_address_still_accepts_a_deprecated_course_key(self):
        legacy_match = resolve("/api/contentstore/v0/videos/uploads/Org/Course/Run")
        assert legacy_match.func.cls is VideosCreateUploadView
        assert legacy_match.kwargs["course_id"] == "Org/Course/Run"

    def test_malformed_course_key_is_not_routed(self):
        self.assert_json_not_found("/api/authoring/v1/courses/a+b+c/videos/")

    def test_an_unknown_address_under_a_course_is_not_routed(self):
        self.assert_json_not_found("/api/authoring/v1/courses/course-v1:edX+DemoX+Demo_Course/no_such_thing/")

    def test_an_unknown_member_of_a_course_is_not_routed(self):
        self.assert_json_not_found("/api/authoring/v1/courses/a+b+c/videos/abc-123/")

    def test_an_address_outside_a_course_is_left_to_the_site(self):
        """
        Only addresses under a course carry the API error body.

        An address that names no course at all is beyond what this API claims, and
        is answered by the site the same way as any other address it does not serve.
        """
        from django.urls import Resolver404

        with pytest.raises(Resolver404):
            resolve("/api/authoring/v1/no/such/thing/")
        assert self.api_client.get("/api/authoring/v1/no/such/thing/").status_code == 404

    def test_unmatched_addresses_are_claimed_by_routes_that_can_be_reversed(self):
        """Each address the endpoints turn down is claimed by a named route of its own."""
        unusable_key = "a+b+c"
        addresses = {
            "authoring_v1:course_video_list_unmatched":
                f"/api/authoring/v1/courses/{unusable_key}/videos/",
            "authoring_v1:course_video_detail_unmatched":
                f"/api/authoring/v1/courses/{unusable_key}/videos/abc-123/",
            "authoring_v1:course_unmatched":
                f"/api/authoring/v1/courses/{unusable_key}/",
        }
        kwargs = {
            "authoring_v1:course_video_detail_unmatched": {"edx_video_id": "abc-123"},
        }
        for name, address in addresses.items():
            reversed_address = reverse(name, kwargs={"course_key": unusable_key, **kwargs.get(name, {})})
            assert reversed_address == address, name
            assert resolve(address).func.cls is UnknownRouteView, name

    def test_an_unclaimed_address_needs_no_authentication(self):
        response = self.client_for(None).get("/api/authoring/v1/courses/a+b+c/videos/")
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    def test_addresses_require_the_trailing_slash(self):
        from django.urls import Resolver404

        with pytest.raises(Resolver404):
            resolve(self.list_url.rstrip("/"))
        with pytest.raises(Resolver404):
            resolve(self.detail_url("abc-123").rstrip("/"))

    def test_the_trailing_slash_is_still_appended(self):
        response = self.api_client.get(self.list_url.rstrip("/"))
        assert response.status_code == status.HTTP_301_MOVED_PERMANENTLY
        assert response["Location"] == self.list_url

    def test_url_names_are_unique_to_the_authoring_namespace(self):
        from django.urls import NoReverseMatch

        for name in ("course_video_list", "course_video_detail"):
            with pytest.raises(NoReverseMatch):
                reverse(f"cms.djangoapps.contentstore:v1:{name}", kwargs={"course_key": self.course.id})


class CourseVideoQueryCountTest(CourseVideoUploadsTestBase):
    """The cost of a page must not grow with the size of the course."""

    #: Set from the pytest fixture below, which class-based tests cannot request directly.
    django_assert_num_queries = None

    @pytest.fixture(autouse=True)
    def _assert_num_queries(self, django_assert_num_queries):
        self.django_assert_num_queries = django_assert_num_queries

    def warm_caches(self):
        """Issue requests first so per-process caches do not skew the measured count."""
        for __ in range(2):
            self.api_client.get(self.list_url, {"page_size": 1})

    def test_list_query_count(self):
        self.create_videos(count=10)
        self.warm_caches()
        with self.django_assert_num_queries(21):
            self.api_client.get(self.list_url, {"page_size": 10})

    def test_list_query_count_is_independent_of_course_size(self):
        self.create_videos(count=100)
        self.warm_caches()
        with self.django_assert_num_queries(21):
            self.api_client.get(self.list_url, {"page_size": 10})

    def test_list_query_count_grows_only_with_the_page(self):
        self.create_videos(count=10)
        self.warm_caches()
        with self.django_assert_num_queries(13):
            self.api_client.get(self.list_url, {"page_size": 2})

    def test_retrieve_query_count(self):
        video_ids = self.create_videos(count=10)
        self.warm_caches()
        with self.django_assert_num_queries(13):
            self.api_client.get(self.detail_url(video_ids[0]))

    @patch("cms.djangoapps.contentstore.video_storage_handlers.boto3.resource")
    def test_create_query_count(self, mock_boto3_resource):
        _mock_bucket(mock_boto3_resource)
        body = {"files": [
            {"file_name": "first.mp4", "content_type": "video/mp4"},
            {"file_name": "second.mp4", "content_type": "video/mp4"},
        ]}
        self.warm_caches()
        self.api_client.post(self.list_url, body, format="json")
        with self.django_assert_num_queries(16):
            self.api_client.post(self.list_url, body, format="json")


class RowParityMixin:
    """Comparing one row of the legacy listing against the same row of this version."""

    def legacy_and_new_row(self, edx_video_id):
        """Fetch both listings and return the (legacy, new) row for one video."""
        legacy = self.api_client.get(
            self.legacy_detail_url(edx_video_id), HTTP_ACCEPT="application/json"
        )
        new = self.api_client.get(self.list_url, {"page_size": 100})

        assert legacy.status_code == status.HTTP_200_OK
        assert new.status_code == status.HTTP_200_OK
        legacy_rows = {row["edx_video_id"]: row for row in json.loads(legacy.content)["videos"]}
        new_rows = {row["edx_video_id"]: row for row in json.loads(new.content)["results"]}
        assert set(legacy_rows) == set(new_rows)
        return legacy_rows[edx_video_id], new_rows[edx_video_id]

    def assert_row_parity(self, legacy_row, new_row):
        """Assert the two rows differ in every declared way, and in no other way."""
        declared = [path for path, __ in ROW_DIFFERENCES]
        changed, unexpected = _diff(legacy_row, new_row, declared)
        assert not unexpected, f"undeclared differences: {sorted(unexpected)}"
        for path in declared:
            assert any(_matches(key, path) for key in changed), (
                f"declared difference did not occur: {path}"
            )
        assert new_row["status"] == legacy_row["status_nontranslated"]


class CourseVideoParityTest(RowParityMixin, CourseVideoUploadsTestBase):
    """The new addresses return what the legacy ones do, apart from the declared differences."""

    def test_list_rows_match_the_legacy_listing(self):
        create_profile("desktop_mp4")
        create_video({
            "edx_video_id": "parity",
            "client_video_id": "parity.mp4",
            "duration": 42.0,
            "status": "file_complete",
            "courses": [str(self.course.id)],
            "encoded_videos": [{
                "profile": "desktop_mp4",
                "url": "http://example.com/parity.mp4",
                "file_size": 1600,
                "bitrate": 100,
            }],
        })
        self.set_created(["parity"])

        self.assert_row_parity(*self.legacy_and_new_row("parity"))

    def test_created_keeps_the_precision_the_legacy_encoder_dropped(self):
        video_ids = self.create_videos(count=1)
        legacy_row, new_row = self.legacy_and_new_row(video_ids[0])

        assert legacy_row["created"] == "2026-01-02T03:04:05.123Z"
        assert new_row["created"] == "2026-01-02T03:04:05.123456Z"

    def test_the_status_is_never_locale_translated(self):
        video_ids = self.create_videos(count=1)
        with patch(
            "cms.djangoapps.contentstore.video_storage_handlers._", side_effect="<{}>".format
        ):
            legacy_row, new_row = self.legacy_and_new_row(video_ids[0])

        assert legacy_row["status"] == "<Ready>"
        assert new_row["status"] == "Ready"

    @patch("cms.djangoapps.contentstore.video_storage_handlers.boto3.resource")
    def test_create_matches_the_legacy_create(self, mock_boto3_resource):
        mock_s3_client = _mock_bucket(mock_boto3_resource)
        body = {"files": [{"file_name": "parity.mp4", "content_type": "video/mp4"}]}

        legacy = self.api_client.post(self.legacy_list_url(), body, format="json")
        legacy_params = mock_s3_client.generate_presigned_url.call_args[1]
        new = self.api_client.post(self.list_url, body, format="json")
        new_params = mock_s3_client.generate_presigned_url.call_args[1]

        assert legacy.status_code == status.HTTP_200_OK
        assert new.status_code == status.HTTP_201_CREATED

        legacy_entry, = json.loads(legacy.content)["files"]
        new_entry, = json.loads(new.content)["files"]
        assert set(legacy_entry) == set(new_entry)
        assert legacy_entry["file_name"] == new_entry["file_name"]
        assert legacy_entry["upload_url"] == new_entry["upload_url"]
        assert legacy_entry["edx_video_id"] != new_entry["edx_video_id"]

        assert legacy_params["ExpiresIn"] == new_params["ExpiresIn"]
        assert legacy_params["Params"]["Bucket"] == new_params["Params"]["Bucket"]
        assert legacy_params["Params"]["ContentType"] == new_params["Params"]["ContentType"]
        assert legacy_params["Params"]["Metadata"] == new_params["Params"]["Metadata"]

        for entry in (legacy_entry, new_entry):
            video = Video.objects.get(edx_video_id=entry["edx_video_id"])
            assert video.status == "upload"
            assert video.client_video_id == "parity.mp4"
            assert CourseVideo.objects.filter(
                course_id=str(self.course.id), video=video
            ).exists()

    def test_delete_matches_the_legacy_delete(self):
        video_ids = self.create_videos(count=2)

        legacy = self.api_client.delete(self.legacy_detail_url(video_ids[0]))
        new = self.api_client.delete(self.detail_url(video_ids[1]))

        assert legacy.status_code == new.status_code == status.HTTP_204_NO_CONTENT
        for edx_video_id in video_ids:
            assert CourseVideo.objects.get(
                course_id=str(self.course.id), video__edx_video_id=edx_video_id
            ).is_hidden is True

    def test_unauthenticated_error_shapes(self):
        client = self.client_for(None)
        legacy = client.get(self.legacy_detail_url("any"), HTTP_ACCEPT="application/json")
        new = client.get(self.list_url)

        assert legacy.status_code == new.status_code == status.HTTP_401_UNAUTHORIZED
        assert set(json.loads(legacy.content)) == {"developer_message"}
        assert_error_envelope(new, expected_status=401, expected_type_slug="authn")

    def test_forbidden_error_shapes(self):
        client = self.client_for(UserFactory.create(is_staff=False))
        legacy = client.get(self.legacy_detail_url("any"), HTTP_ACCEPT="application/json")
        new = client.get(self.list_url)

        assert legacy.status_code == new.status_code == status.HTTP_403_FORBIDDEN
        assert json.loads(legacy.content)["error_code"] == "user_permissions"
        assert_error_envelope(new, expected_status=403, expected_type_slug="authz")

    def test_pipeline_disabled_error_shapes(self):
        with override_settings(ENABLE_VIDEO_UPLOAD_PIPELINE=False):
            legacy = self.api_client.get(self.legacy_detail_url("any"), HTTP_ACCEPT="application/json")
            new = self.api_client.get(self.list_url)

        assert legacy.status_code == new.status_code == status.HTTP_404_NOT_FOUND
        assert legacy.content == b""
        assert_error_envelope(new, expected_status=404, expected_type_slug="videos/uploads-not-configured")

    def test_invalid_request_body_error_shapes(self):
        body = {"files": [{"file_name": "first.mp4"}]}
        legacy = self.api_client.post(self.legacy_list_url(), body, format="json")
        new = self.api_client.post(self.list_url, body, format="json")

        assert legacy.status_code == new.status_code == status.HTTP_400_BAD_REQUEST
        assert legacy.content == b""
        assert_error_envelope(new, expected_status=400, expected_type_slug="validation")

    def test_missing_video_error_shapes(self):
        legacy = self.api_client.delete(self.legacy_detail_url("no-such-video"))
        new = self.api_client.delete(self.detail_url("no-such-video"))

        assert legacy.status_code == new.status_code == status.HTTP_404_NOT_FOUND
        assert "developer_message" in json.loads(legacy.content)
        assert_error_envelope(new, expected_status=404, expected_type_slug="not-found")

    def test_the_legacy_accept_header_branch_is_not_carried_over(self):
        video_ids = self.create_videos(count=1)

        legacy = self.api_client.get(self.legacy_detail_url(video_ids[0]), HTTP_ACCEPT="*/*")
        new = self.api_client.get(self.list_url, HTTP_ACCEPT="*/*")

        assert legacy.status_code == status.HTTP_302_FOUND
        assert legacy["Location"]
        assert new.status_code == status.HTTP_200_OK
        assert "Location" not in new
        assert set(json.loads(new.content)) == {
            "count", "num_pages", "current_page", "start", "next", "previous", "results",
        }

    def test_the_listing_is_wrapped_in_the_page_envelope(self):
        self.create_videos(count=3)

        legacy = self.api_client.get(
            self.legacy_detail_url("video-file_complete-0"), HTTP_ACCEPT="application/json"
        )
        new = self.api_client.get(self.list_url, {"page": 2, "page_size": 1})

        assert set(json.loads(legacy.content)) == {"videos"}
        assert set(json.loads(new.content)) == {
            "count", "num_pages", "current_page", "start", "next", "previous", "results",
        }

    def test_minimal_view_is_a_subset_of_the_default(self):
        video_ids = self.create_videos(count=1)
        default = self.api_client.get(self.detail_url(video_ids[0]))
        minimal = self.api_client.get(self.detail_url(video_ids[0]), {"view": "minimal"})
        assert set(minimal.data) < set(default.data)
        for field in minimal.data:
            assert minimal.data[field] == default.data[field]


@ddt.ddt
class CurrentWorkflowParityTest(RowParityMixin, CurrentWorkflowTestBase):
    """Row parity for a course whose videos reach the encodes-ready and transcription states."""

    @ddt.data(
        "ingest",
        "file_complete",
        "upload_completed",
        "invalid_token",
        "transcription_in_progress",
        "transcript_ready",
        "partial_failure",
        "transcript_failed",
    )
    def test_rows_match_the_legacy_listing(self, status_value):
        video_ids = self.create_videos(count=1, status_value=status_value)

        self.assert_row_parity(*self.legacy_and_new_row(video_ids[0]))

    def test_transcription_status_matches_the_legacy_listing(self):
        video_ids = self.create_videos(count=1, status_value="partial_failure")

        legacy_row, new_row = self.legacy_and_new_row(video_ids[0])

        assert legacy_row["transcription_status"] == "Partial Failure"
        assert new_row["transcription_status"] == legacy_row["transcription_status"]


class CourseVideoSchemaTest(CourseVideoUploadsTestBase):
    """What the generated schema promises, measured against what the endpoints return."""

    def generated_schema(self):
        """Generate the OpenAPI document for the course video addresses."""
        from cms.djangoapps.contentstore.rest_api.v1 import authoring_urls

        return SchemaGenerator(patterns=authoring_urls.urlpatterns).get_schema(request=None, public=True)

    def resolve(self, schema, body):
        """Return the component ``body`` refers to, or ``body`` itself."""
        if "$ref" in body:
            return schema["components"]["schemas"][body["$ref"].rsplit("/", 1)[-1]]
        return body

    def response_schema(self, schema, path_suffix, method="get", code="200"):
        """Return the declared response body for one operation, following any reference."""
        path = next(path for path in schema["paths"] if path.endswith(path_suffix))
        body = schema["paths"][path][method]["responses"][code]["content"]["application/json"]["schema"]
        return self.resolve(schema, body)

    def representations(self, schema, declared):
        """Return the video representations ``declared`` allows, by component name."""
        variant = self.resolve(schema, declared)
        return {
            member["$ref"].rsplit("/", 1)[-1]: self.resolve(schema, member)
            for member in variant["oneOf"]
        }

    def test_only_the_two_video_addresses_are_documented(self):
        """The routes that stand behind the endpoints serve no operation and must stay unpublished."""
        assert set(self.generated_schema()["paths"]) == {
            "/courses/{course_key}/videos/",
            "/courses/{course_key}/videos/{edx_video_id}/",
        }

    def test_the_list_response_is_declared_as_the_page_envelope(self):
        envelope = self.response_schema(self.generated_schema(), "/videos/")
        assert envelope["type"] == "object"
        assert set(envelope["properties"]) == {
            "count", "num_pages", "current_page", "start", "next", "previous", "results",
        }
        assert set(envelope["required"]) == {"count", "num_pages", "current_page", "start", "results"}
        assert envelope["properties"]["results"]["type"] == "array"

    def test_the_declared_list_envelope_is_the_one_returned(self):
        self.create_videos(count=1)
        envelope = self.response_schema(self.generated_schema(), "/videos/")
        response = self.api_client.get(self.list_url)
        assert set(response.data) == set(envelope["properties"])

    def test_both_video_representations_are_declared(self):
        schema = self.generated_schema()
        envelope = self.response_schema(schema, "/videos/")
        declared = self.representations(schema, envelope["properties"]["results"]["items"])
        assert set(declared) == {"CourseVideo", "CourseVideoMinimal"}

    def test_the_declared_full_representation_is_the_one_returned(self):
        self.create_videos(count=1)
        schema = self.generated_schema()
        envelope = self.response_schema(schema, "/videos/")
        declared = self.representations(schema, envelope["properties"]["results"]["items"])["CourseVideo"]
        row = self.api_client.get(self.list_url).data["results"][0]
        assert set(row) == set(declared["properties"]) == set(declared["required"])

    def test_the_declared_minimal_representation_is_the_one_returned(self):
        self.create_videos(count=1)
        schema = self.generated_schema()
        envelope = self.response_schema(schema, "/videos/")
        declared = self.representations(
            schema, envelope["properties"]["results"]["items"]
        )["CourseVideoMinimal"]
        row = self.api_client.get(self.list_url, {"view": "minimal"}).data["results"][0]
        assert set(row) == set(declared["properties"]) == set(declared["required"])

    def test_the_member_response_declares_both_representations(self):
        schema = self.generated_schema()
        video_ids = self.create_videos(count=1)
        declared = self.representations(
            schema, schema["paths"]["/courses/{course_key}/videos/{edx_video_id}/"]["get"][
                "responses"]["200"]["content"]["application/json"]["schema"]
        )
        assert set(declared) == {"CourseVideo", "CourseVideoMinimal"}
        full = self.api_client.get(self.detail_url(video_ids[0])).data
        minimal = self.api_client.get(self.detail_url(video_ids[0]), {"view": "minimal"}).data
        assert set(full) == set(declared["CourseVideo"]["properties"])
        assert set(minimal) == set(declared["CourseVideoMinimal"]["properties"])

    def test_the_minimal_representation_is_named_by_the_view_parameter(self):
        schema = self.generated_schema()
        parameters = schema["paths"]["/courses/{course_key}/videos/"]["get"]["parameters"]
        view_parameter = next(item for item in parameters if item["name"] == "view")
        assert view_parameter["schema"]["enum"] == ["minimal"]
        declared = self.representations(
            schema,
            self.response_schema(schema, "/videos/")["properties"]["results"]["items"],
        )["CourseVideoMinimal"]
        for field in declared["properties"]:
            assert field in view_parameter["description"]

    def declared_examples(self, schema, path, method, code):
        """Return the declared response examples of one operation, by title."""
        content = schema["paths"][path][method]["responses"][code]["content"]["application/json"]
        return {name: example["value"] for name, example in content["examples"].items()}

    def test_the_member_404_names_both_causes(self):
        schema = self.generated_schema()
        declared = self.declared_examples(
            schema, "/courses/{course_key}/videos/{edx_video_id}/", "get", "404"
        )
        assert {example["type"] for example in declared.values()} == {
            "https://docs.openedx.org/errors/videos/uploads-not-configured",
            "https://docs.openedx.org/errors/not-found",
        }

    def test_the_declared_404_types_are_the_ones_returned(self):
        schema = self.generated_schema()
        declared = {
            example["type"]
            for example in self.declared_examples(
                schema, "/courses/{course_key}/videos/{edx_video_id}/", "get", "404"
            ).values()
        }
        video_ids = self.create_videos(count=1)
        unknown = self.api_client.get(self.detail_url("no-such-video"))
        with override_settings(ENABLE_VIDEO_UPLOAD_PIPELINE=False):
            unconfigured = self.api_client.get(self.detail_url(video_ids[0]))
        assert {unknown.data["type"], unconfigured.data["type"]} == declared

    def test_the_create_404_names_only_the_cause_it_can_have(self):
        schema = self.generated_schema()
        declared = self.declared_examples(schema, "/courses/{course_key}/videos/", "post", "404")
        assert {example["type"] for example in declared.values()} == {
            "https://docs.openedx.org/errors/videos/uploads-not-configured",
        }

    def test_the_create_response_is_declared_as_the_upload_slots(self):
        slots = self.response_schema(self.generated_schema(), "/videos/", method="post", code="201")
        assert set(slots["properties"]) == {"files"}


class CmsSchemaHookTest(CourseVideoUploadsTestBase):
    """The schema hooks admit the new paths and flag the superseded ones."""

    def endpoint(self, path):
        return (path, path, "get", None)

    def test_filter_admits_the_authoring_prefix(self):
        admitted = cms_api_filter([
            self.endpoint("/api/authoring/v1/courses/course-v1:a+b+c/videos/"),
            self.endpoint("/api/contentstore/v0/videos/uploads/course-v1:a+b+c"),
            self.endpoint("/login"),
        ])
        assert [entry[0] for entry in admitted] == [
            "/api/authoring/v1/courses/course-v1:a+b+c/videos/",
            "/api/contentstore/v0/videos/uploads/course-v1:a+b+c",
        ]

    def test_post_processing_marks_only_the_superseded_operations(self):
        schema = {
            "paths": {
                "/v0/videos/uploads/{course_id}": {"post": {}},
                "/v0/videos/uploads/{course_id}/{edx_video_id}": {"get": {}, "delete": {}},
                "/v0/videos/images/{course_id}/{edx_video_id}": {"post": {}},
                "/api/authoring/v1/courses/{course_key}/videos/": {"get": {}, "post": {}},
            }
        }
        result = cms_mark_superseded_paths(schema, None, None, False)

        deprecated = {
            (path, method)
            for path, item in result["paths"].items()
            for method, operation in item.items()
            if operation.get("deprecated")
        }
        assert deprecated == {
            ("/v0/videos/uploads/{course_id}", "post"),
            ("/v0/videos/uploads/{course_id}/{edx_video_id}", "get"),
            ("/v0/videos/uploads/{course_id}/{edx_video_id}", "delete"),
        }

    def test_post_processing_also_matches_untrimmed_paths(self):
        schema = {"paths": {"/api/contentstore/v0/videos/uploads/{course_id}": {"post": {}}}}
        result = cms_mark_superseded_paths(schema, None, None, False)
        assert result["paths"]["/api/contentstore/v0/videos/uploads/{course_id}"]["post"]["deprecated"] is True

    def authoring_schema(self, hooks):
        """Generate the authoring document with ``hooks`` as the post-processing list."""
        from cms.djangoapps.contentstore.rest_api.v1 import authoring_urls

        with patched_settings({"POSTPROCESSING_HOOKS": hooks}):
            return SchemaGenerator(patterns=authoring_urls.urlpatterns).get_schema(
                request=None, public=True
            )

    def test_enum_components_survive_the_registered_post_processing(self):
        deprecation_only = self.authoring_schema([SUPERSEDED_PATHS_HOOK])
        registered = self.authoring_schema(REGISTERED_POSTPROCESSING_HOOKS)

        assert not [
            name for name in deprecation_only["components"]["schemas"] if name.endswith("Enum")
        ]
        assert "ContentTypeEnum" in registered["components"]["schemas"]

    def test_post_processing_leaves_other_schema_members_alone(self):
        schema = {"paths": {"/v0/videos/uploads/{course_id}": {"post": {}, "parameters": []}}}
        result = cms_mark_superseded_paths(schema, None, None, False)
        assert not result["paths"]["/v0/videos/uploads/{course_id}"]["parameters"]


@ddt.ddt
class CmsSchemaAddressTest(TestCase):
    """The published document addresses every operation the way it is mounted."""

    # One operation of every Studio API version the document covers, with the
    # address a client reaches by joining the published path to a server, plus
    # both new addresses.
    PUBLISHED_PATHS = {
        "/api/contentstore/v0/videos/uploads/{course_id}":
            "/api/contentstore/v0/videos/uploads/course-v1:a+b+c",
        "/api/contentstore/v1/videos/{course_id}":
            "/api/contentstore/v1/videos/course-v1:a+b+c",
        "/api/contentstore/v2/home/courses":
            "/api/contentstore/v2/home/courses",
        "/api/contentstore/v3/course_details/{course_id}/":
            "/api/contentstore/v3/course_details/course-v1:a+b+c/",
        "/api/contentstore/v4/home/courses/":
            "/api/contentstore/v4/home/courses/",
        "/api/authoring/v1/courses/{course_key}/videos/":
            "/api/authoring/v1/courses/course-v1:a+b+c/videos/",
        "/api/authoring/v1/courses/{course_key}/videos/{edx_video_id}/":
            "/api/authoring/v1/courses/course-v1:a+b+c/videos/video-1/",
    }

    def cms_schema(self, module=PRODUCTION_SETTINGS):
        """Generate the Studio document with the schema settings of ``module``."""
        read = schema_settings(module)
        with patched_settings({key: read[key] for key in GENERATION_SETTING_KEYS}):
            return SchemaGenerator().get_schema(request=None, public=True)

    def config_without_public_host(self):
        """Write a deployment configuration that leaves the public host unset."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = Path(directory.name) / "cms.yml"
        config.write_text("".join(
            line for line in MOCK_CONFIG.read_text().splitlines(keepends=True)
            if not line.startswith("AUTHORING_API_URL:")
        ))
        return config

    def test_the_document_publishes_the_mounted_addresses(self):
        assert set(self.PUBLISHED_PATHS) <= set(self.cms_schema()["paths"])

    def test_the_document_publishes_no_other_authoring_address(self):
        published = self.cms_schema()["paths"]
        authoring = {path for path in published if path.startswith("/api/authoring/")}
        assert authoring == {path for path in self.PUBLISHED_PATHS if path.startswith("/api/authoring/")}

    def test_no_published_path_is_shortened(self):
        published = sorted(self.cms_schema()["paths"])
        assert published, "the pre-processing hook admitted no path"
        assert [path for path in published if not path.startswith("/api/")] == []

    def test_every_published_path_is_an_address_that_resolves(self):
        for published, address in self.PUBLISHED_PATHS.items():
            assert resolve(address).func, published

    @ddt.data(PRODUCTION_SETTINGS, DEVSTACK_SETTINGS)
    def test_the_settings_publish_paths_in_full(self, module):
        assert schema_settings(module)["SCHEMA_PATH_PREFIX_TRIM"] is False

    @ddt.data(PRODUCTION_SETTINGS, DEVSTACK_SETTINGS)
    def test_the_servers_serve_every_published_path(self, module):
        servers = schema_settings(module)["SERVERS"]
        assert [server["description"] for server in servers] == ["Public", "Local"]
        assert [server for server in servers if "/api/contentstore" in server["url"]] == []

    @ddt.data(PRODUCTION_SETTINGS, DEVSTACK_SETTINGS)
    def test_an_unset_public_host_is_left_out_of_the_servers(self, module):
        servers = schema_settings(module, config_file=self.config_without_public_host())["SERVERS"]
        assert [server["description"] for server in servers] == ["Local"]
        assert all(server["url"] for server in servers)
