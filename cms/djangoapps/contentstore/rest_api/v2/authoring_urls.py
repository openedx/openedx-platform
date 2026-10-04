"""Contentstore API v2 authoring URLs."""

from django.urls import path, re_path

from cms.djangoapps.contentstore.rest_api.v1.views.unknown_route import UnknownRouteView
from cms.djangoapps.contentstore.rest_api.v2.views.video_archives import CourseVideoArchiveView
from cms.djangoapps.contentstore.rest_api.v2.views.video_usages import CourseVideoUsageViewSet

app_name = "authoring_v2"

urlpatterns = [
    path(
        "courses/<course_key:course_key>/video_usages/<str:edx_video_id>/",
        CourseVideoUsageViewSet.as_view({"get": "retrieve"}),
        name="course_video_usage_detail",
    ),
    path(
        "courses/<course_key:course_key>/video_archives/",
        CourseVideoArchiveView.as_view(),
        name="course_video_archive_list",
    ),
    # Every other address under these two collections holds nothing: a course
    # key the routes above refuse (malformed, or in the deprecated
    # slash-separated form), the usage collection without a video id, an empty
    # video id, or a path below a usage or below the archives. They answer with
    # the API error body rather than the site's HTML error page. They match
    # addresses under these two collections and nothing else, so a resource
    # added under courses/ is never shadowed, and only slash-terminated ones, so
    # the redirect to the slash-terminated form still happens first. A path
    # converter cannot match an empty segment, hence the patterns for the
    # addresses below each collection.
    path(
        "courses/<path:course_key>/video_usages/",
        UnknownRouteView.as_view(),
        name="course_video_usage_list_unmatched",
    ),
    re_path(
        r"^courses/(?P<course_key>.+)/video_usages/(?P<subpath>.*)/$",
        UnknownRouteView.as_view(),
        name="course_video_usage_detail_unmatched",
    ),
    path(
        "courses/<path:course_key>/video_archives/",
        UnknownRouteView.as_view(),
        name="course_video_archive_list_unmatched",
    ),
    re_path(
        r"^courses/(?P<course_key>.+)/video_archives/(?P<subpath>.*)/$",
        UnknownRouteView.as_view(),
        name="course_video_archive_detail_unmatched",
    ),
]
