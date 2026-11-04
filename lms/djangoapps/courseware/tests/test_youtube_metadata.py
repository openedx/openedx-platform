"""Optional configuration and transport behavior of platform YouTube metadata."""
from unittest.mock import Mock, patch

import ddt
import requests
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from common.djangoapps.student.tests.factories import TEST_PASSWORD, UserFactory
from lms.djangoapps.courseware.views.views import load_metadata_from_youtube


@ddt.ddt
class YouTubeMetadataTests(TestCase):
    """Missing optional keys must not fail video metadata requests."""

    @ddt.data("", None, "PUT_YOUR_API_KEY_HERE")
    @patch("lms.djangoapps.courseware.views.views.requests.get")
    def test_missing_key_returns_empty_metadata(self, key, get):
        user = UserFactory()
        assert self.client.login(username=user.username, password=TEST_PASSWORD)
        with override_settings(YOUTUBE_API_KEY=key):
            response = self.client.get(reverse("yt_video_metadata"), {"id": "video-id"})
        assert response.status_code == 200
        assert response.json() == {}
        get.assert_not_called()

    @override_settings(YOUTUBE_API_KEY="")
    @patch("lms.djangoapps.courseware.views.views.requests.get")
    def test_missing_video_id_keeps_existing_error(self, get):
        user = UserFactory()
        assert self.client.login(username=user.username, password=TEST_PASSWORD)
        response = self.client.get(reverse("yt_video_metadata"))
        assert response.status_code == 500
        assert response.json() == {}
        get.assert_not_called()

    def test_endpoint_requires_authentication(self):
        response = self.client.get(reverse("yt_video_metadata"), {"id": "video-id"})
        assert response.status_code == 302

    @override_settings(YOUTUBE_API_KEY="test-key")
    @patch("lms.djangoapps.courseware.views.views.requests.get")
    def test_configured_key_returns_metadata(self, get):
        payload = {"items": [{"id": "video-id", "contentDetails": {"duration": "PT1M"}}]}
        get.return_value = Mock(status_code=200, json=Mock(return_value=payload))
        request = RequestFactory().get("/", HTTP_REFERER="https://example.com/video")
        assert load_metadata_from_youtube("video-id", request) == (payload, 200)
        get.assert_called_once_with(
            "https://www.googleapis.com/youtube/v3/videos/",
            params={"id": "video-id", "part": "contentDetails", "key": "test-key"},
            timeout=1.5, headers={"Referer": "https://example.com/video"},
        )

    @ddt.data(403, 404, 503)
    @override_settings(YOUTUBE_API_KEY="test-key")
    @patch("lms.djangoapps.courseware.views.views.requests.get")
    def test_configured_key_preserves_upstream_status(self, code, get):
        get.return_value = Mock(status_code=code, text="Upstream error")
        assert load_metadata_from_youtube("video-id", RequestFactory().get("/")) == ({}, code)

    @ddt.data(requests.Timeout, requests.ConnectionError)
    @override_settings(YOUTUBE_API_KEY="test-key")
    @patch("lms.djangoapps.courseware.views.views.requests.get")
    def test_configured_key_preserves_transport_failure(self, error, get):
        get.side_effect = error()
        assert load_metadata_from_youtube("video-id", RequestFactory().get("/")) == ({}, 500)

    @override_settings(YOUTUBE_API_KEY="test-key")
    @patch("lms.djangoapps.courseware.views.views.requests.get")
    def test_configured_key_preserves_invalid_json_response(self, get):
        get.return_value = Mock(status_code=200, text="Invalid JSON", json=Mock(side_effect=ValueError))
        assert load_metadata_from_youtube("video-id", RequestFactory().get("/")) == ({}, 200)
