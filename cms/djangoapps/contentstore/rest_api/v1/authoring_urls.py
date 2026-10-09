"""Contentstore API v1 authoring URLs."""

from django.urls import path

from cms.djangoapps.contentstore.rest_api.v1.views import (
    XblockViewSet,
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
    # The viewset has no ``list`` action, so the collection accepts POST only.
    path(
        "xblocks/",
        XblockViewSet.as_view({"post": "create"}),
        name="xblock_list",
    ),
    path(
        "xblocks/<usage_key:usage_key_string>/",
        XblockViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="xblock_detail",
    ),
]
