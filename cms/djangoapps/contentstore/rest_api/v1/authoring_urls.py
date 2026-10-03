"""Contentstore API v1 authoring URLs."""

from django.urls import path

from cms.djangoapps.contentstore.rest_api.v1.views import (
    YoutubeTranscriptChecksViewSet,
    YoutubeTranscriptImportsViewSet,
)

app_name = "authoring_v1"

urlpatterns = [
    path(
        "courses/<course_key:course_key>/youtube_transcript_checks/",
        YoutubeTranscriptChecksViewSet.as_view({"get": "list"}),
        name="youtube_transcript_check_list",
    ),
    path(
        "courses/<course_key:course_key>/youtube_transcript_imports/",
        YoutubeTranscriptImportsViewSet.as_view({"post": "create"}),
        name="youtube_transcript_import_list",
    ),
]
