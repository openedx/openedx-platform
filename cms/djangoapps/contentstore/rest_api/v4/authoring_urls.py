"""Authoring API v4 URLs (ADR 0038 conforming mount for Contentstore v4)."""

from django.urls import path

from cms.djangoapps.contentstore.rest_api.v4.views import home

app_name = "authoring_v4"

urlpatterns = [
    path(
        "courses/",
        home.HomeCoursesViewSet.as_view({"get": "list"}),
        name="course_list",
    ),
]
