"""
Unit tests for course settings views.
"""
from unittest.mock import patch

import ddt
from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.urls import reverse
from edx_toggles.toggles import WaffleSwitch
from edx_toggles.toggles.testutils import override_waffle_switch
from edxval.api import (
    get_3rd_party_transcription_plans,
    get_transcript_credentials_state_for_org,
    get_transcript_preferences,
)
from rest_framework import status

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
