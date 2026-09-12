"""Authoring API v3 URLs (ADR 0038 conforming mount for Contentstore v3)."""

from django.urls import path

from cms.djangoapps.contentstore.rest_api.v3.views import AuthoringGradingViewSet, CourseDetailsViewSet, HomeViewSet

app_name = "authoring_v3"

urlpatterns = [
    path(
        "home/",
        HomeViewSet.as_view({"get": "list"}),
        name="home",
    ),
    path(
        "home/courses/",
        HomeViewSet.as_view({"get": "courses"}),
        name="home_courses",
    ),
    path(
        "home/libraries/",
        HomeViewSet.as_view({"get": "libraries"}),
        name="home_libraries",
    ),
    path(
        "courses/<course_key:course_id>/details/",
        CourseDetailsViewSet.as_view({"get": "retrieve", "put": "update"}),
        name="course_details",
    ),
    path(
        "courses/<course_key:course_key>/grading/",
        AuthoringGradingViewSet.as_view({"patch": "partial_update"}),
        name="course_grading",
    ),
]
