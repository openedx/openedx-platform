"""
Unit tests for course settings views.
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import ddt
import pytz
from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.urls import reverse
from edx_toggles.toggles import WaffleSwitch
from edx_toggles.toggles.testutils import override_waffle_switch
from edxval.api import (
    create_profile,
    create_video,
    get_3rd_party_transcription_plans,
    get_transcript_credentials_state_for_org,
    get_transcript_preferences,
)
from rest_framework import status
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url
from cms.djangoapps.contentstore.video_storage_handlers import get_all_transcript_languages

from ...mixins import PermissionAccessMixin


@ddt.ddt
class CourseVideosViewTest(CourseTestCase, PermissionAccessMixin):
    """
    Tests for CourseVideosView.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:course_videos",
            kwargs={"course_id": self.course.id},
        )

    def test_course_videos_response(self):
        """Check successful response content"""
        response = self.client.get(self.url)
        expected_response = {
            "image_upload_url": reverse_course_url("video_images_handler", str(self.course.id)),
            "video_handler_url": reverse_course_url("videos_handler", str(self.course.id)),
            "encodings_download_url": reverse_course_url("video_encodings_download", str(self.course.id)),
            "default_video_image_url": staticfiles_storage.url(settings.VIDEO_IMAGE_DEFAULT_FILENAME),
            "previous_uploads": [],
            "concurrent_upload_limit": settings.VIDEO_UPLOAD_PIPELINE.get("CONCURRENT_UPLOAD_LIMIT", 0),
            "video_supported_file_formats": [".mp4", ".mov"],
            "video_upload_max_file_size": "5",
            "video_image_settings": {
                "video_image_upload_enabled": False,
                "max_size": settings.VIDEO_IMAGE_SETTINGS["VIDEO_IMAGE_MAX_BYTES"],
                "min_size": settings.VIDEO_IMAGE_SETTINGS["VIDEO_IMAGE_MIN_BYTES"],
                "max_width": settings.VIDEO_IMAGE_MAX_WIDTH,
                "max_height": settings.VIDEO_IMAGE_MAX_HEIGHT,
                "supported_file_formats": settings.VIDEO_IMAGE_SUPPORTED_FILE_FORMATS
            },
            "is_video_transcript_enabled": False,
            "is_ai_translations_enabled": False,
            "active_transcript_preferences": None,
            "transcript_credentials": None,
            "transcript_available_languages": get_all_transcript_languages(),
            "video_transcript_settings": {
                "transcript_download_handler_url": reverse('transcript_download_handler'),
                "transcript_upload_handler_url": reverse('transcript_upload_handler'),
                "transcript_delete_handler_url": reverse_course_url("transcript_delete_handler", str(self.course.id)),
                "trancript_download_file_format": "srt",
                "transcript_preferences_handler_url": None,
                "transcript_credentials_handler_url": None,
                "transcription_plans": None
            },
            "pagination_context": {}
        }

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertDictEqual(expected_response, response.data)  # noqa: PT009

    @override_waffle_switch(WaffleSwitch(  # pylint: disable=toggle-missing-annotation
        'videos.video_image_upload_enabled', __name__
    ), True)
    def test_video_image_upload_enabled(self):
        """
        Make sure if the feature flag is enabled we have updated the dict keys in response.
        """
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn("video_image_settings", response.data)  # noqa: PT009

        imageSettings = response.data["video_image_settings"]
        self.assertIn("video_image_upload_enabled", imageSettings)  # noqa: PT009
        self.assertTrue(imageSettings["video_image_upload_enabled"])  # noqa: PT009

    def test_VideoTranscriptEnabledFlag_enabled(self):
        """
        Make sure if the feature flags are enabled we have updated the dict keys in response.
        """
        with patch('openedx.core.djangoapps.video_config.models.VideoTranscriptEnabledFlag.feature_enabled') as feature:
            feature.return_value = True
            response = self.client.get(self.url)
            self.assertIn("is_video_transcript_enabled", response.data)  # noqa: PT009
            self.assertTrue(response.data["is_video_transcript_enabled"])  # noqa: PT009

            expect_active_preferences = get_transcript_preferences(str(self.course.id))
            self.assertIn("active_transcript_preferences", response.data)  # noqa: PT009
            self.assertEqual(expect_active_preferences, response.data["active_transcript_preferences"])  # noqa: PT009

            expected_credentials = get_transcript_credentials_state_for_org(self.course.id.org)
            self.assertIn("transcript_credentials", response.data)  # noqa: PT009
            self.assertDictEqual(expected_credentials, response.data["transcript_credentials"])  # noqa: PT009

            transcript_settings = response.data["video_transcript_settings"]

            expected_plans = get_3rd_party_transcription_plans()
            self.assertIn("transcription_plans", transcript_settings)  # noqa: PT009
            self.assertDictEqual(expected_plans, transcript_settings["transcription_plans"])  # noqa: PT009

            expected_preference_handler = reverse_course_url(
                'transcript_preferences_handler',
                str(self.course.id)
            )
            self.assertIn("transcript_preferences_handler_url", transcript_settings)  # noqa: PT009
            self.assertEqual(expected_preference_handler, transcript_settings["transcript_preferences_handler_url"])  # noqa: PT009  # pylint: disable=line-too-long

            expected_credentials_handler = reverse_course_url(
                'transcript_credentials_handler',
                str(self.course.id)
            )
            self.assertIn("transcript_credentials_handler_url", transcript_settings)  # noqa: PT009
            self.assertEqual(expected_credentials_handler, transcript_settings["transcript_credentials_handler_url"])  # noqa: PT009  # pylint: disable=line-too-long
        with patch(
            'openedx.core.djangoapps.video_config.toggles.XPERT_TRANSLATIONS_UI.is_enabled'
        ) as xpertTranslationfeature:
            xpertTranslationfeature.return_value = True
            response = self.client.get(self.url)
            self.assertIn("is_ai_translations_enabled", response.data)  # noqa: PT009
            self.assertTrue(response.data["is_ai_translations_enabled"])  # noqa: PT009


class VideoDownloadViewTest(CourseTestCase):
    """
    Tests for VideoDownloadView.

    The download endpoint fetches each requested ``files[].url`` server-side and
    returns the bytes inside a zip. Those URLs must therefore be restricted to
    the course's own video URLs, otherwise the endpoint is an SSRF primitive
    (see GHSA-fpf9-9rpr-jvrx).
    """

    ALLOWED_URL = "http://example.com/profile1/test.mp4"
    # An internal address an attacker might try to reach via SSRF.
    SSRF_URL = "http://169.254.169.254/latest/meta-data/"

    def setUp(self):
        super().setUp()
        # reverse() with only course_id resolves to the download route (the
        # usage route with the same name additionally requires edx_video_id).
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:video_usage",
            kwargs={"course_id": self.course.id},
        )
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.user)
        create_profile("profile1")
        create_video({
            "edx_video_id": "test-video",
            "client_video_id": "test.mp4",
            "duration": 42.0,
            "status": "file_complete",
            "courses": [str(self.course.id)],
            "created": datetime.now(pytz.utc),
            "encoded_videos": [
                {
                    "profile": "profile1",
                    "url": self.ALLOWED_URL,
                    "file_size": 1600,
                    "bitrate": 100,
                },
            ],
        })

    @patch("cms.djangoapps.contentstore.video_storage_handlers.requests.get")
    def test_download_allowed_url(self, mock_get):
        """A URL that belongs to the course's videos is fetched and zipped."""
        mock_get.return_value = MagicMock(
            content=b"video-bytes",
            headers={"Content-Type": "video/mp4"},
        )
        response = self.api_client.put(
            self.url,
            data={"files": [{"url": self.ALLOWED_URL, "name": "test.mp4"}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        mock_get.assert_called_once_with(self.ALLOWED_URL, allow_redirects=True)

    @patch("cms.djangoapps.contentstore.video_storage_handlers.requests.get")
    def test_rejects_url_not_belonging_to_course(self, mock_get):
        """
        A URL that is not one of the course's video URLs is rejected before any
        server-side request is made (SSRF protection).
        """
        response = self.api_client.put(
            self.url,
            data={"files": [{"url": self.SSRF_URL, "name": "evil.txt"}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)  # noqa: PT009
        mock_get.assert_not_called()

    @patch("cms.djangoapps.contentstore.video_storage_handlers.requests.get")
    def test_rejects_when_any_url_is_disallowed(self, mock_get):
        """
        A request mixing an allowed URL with a disallowed one is rejected
        outright, without fetching the allowed URL either.
        """
        response = self.api_client.put(
            self.url,
            data={"files": [
                {"url": self.ALLOWED_URL, "name": "test.mp4"},
                {"url": self.SSRF_URL, "name": "evil.txt"},
            ]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)  # noqa: PT009
        mock_get.assert_not_called()

    @patch("cms.djangoapps.contentstore.video_storage_handlers.requests.get")
    def test_non_staff_user_denied(self, mock_get):
        """A user without studio read access cannot reach the fetch path."""
        __, nonstaff_user = self.create_non_staff_authed_user_client()
        client = APIClient()
        client.force_authenticate(user=nonstaff_user)
        response = client.put(
            self.url,
            data={"files": [{"url": self.ALLOWED_URL, "name": "test.mp4"}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009
        mock_get.assert_not_called()
