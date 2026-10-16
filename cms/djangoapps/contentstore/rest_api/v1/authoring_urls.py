"""Contentstore API v1 authoring URLs."""

from django.urls import path

from cms.djangoapps.contentstore.rest_api.v1.views import (
    XblockViewSet,
    YoutubeTranscriptChecksViewSet,
    YoutubeTranscriptImportsViewSet,
)
from cms.djangoapps.contentstore.rest_api.v1.views.unknown_route import UnknownRouteView
from cms.djangoapps.contentstore.rest_api.v1.views.video_uploads import CourseVideoUploadsViewSet

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
    path(
        "courses/<course_key:course_key>/videos/",
        CourseVideoUploadsViewSet.as_view({"get": "list", "post": "create"}),
        name="course_video_list",
    ),
    path(
        "courses/<course_key:course_key>/videos/<str:edx_video_id>/",
        CourseVideoUploadsViewSet.as_view({"get": "retrieve", "delete": "destroy"}),
        name="course_video_detail",
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
    # Reached only by a video address whose course key the routes above refuse,
    # malformed or in the deprecated slash-separated form. They answer with the
    # API error body rather than the site's HTML error page. They match video
    # addresses and nothing else, so a resource added under courses/ is never
    # shadowed, and only slash-terminated ones, so the redirect to the
    # slash-terminated form still happens first.
    path(
        "courses/<path:course_key>/videos/",
        UnknownRouteView.as_view(),
        name="course_video_list_unmatched",
    ),
    path(
        "courses/<path:course_key>/videos/<str:edx_video_id>/",
        UnknownRouteView.as_view(),
        name="course_video_detail_unmatched",
    ),
]
