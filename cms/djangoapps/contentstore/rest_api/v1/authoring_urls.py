"""Authoring API v1 URLs."""

from django.urls import path

from cms.djangoapps.contentstore.rest_api.v1.error_types import register_request_error_types
from cms.djangoapps.contentstore.rest_api.v1.views.unknown_route import UnknownRouteView
from cms.djangoapps.contentstore.rest_api.v1.views.video_uploads import CourseVideoUploadsViewSet

app_name = "authoring_v1"

register_request_error_types()

urlpatterns = [
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
    # The three routes below stand behind the two above and are reached only by a
    # request the ones above turned down: a course key the converter refuses
    # because it is malformed or in the deprecated slash-separated form, or an
    # address under a course that this API does not serve. They answer with the
    # API error body, which a client can read, instead of the site's HTML error
    # page. Order is what keeps them out of the way of real requests, and each
    # accepts only addresses ending in a slash so that the redirect to the
    # slash-terminated form keeps working.
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
    path(
        "courses/<path:course_key>/",
        UnknownRouteView.as_view(),
        name="course_unmatched",
    ),
]
