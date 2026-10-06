"""Contentstore API v2 authoring URLs."""

from django.urls import path

from cms.djangoapps.contentstore.rest_api.v2.views.textbooks import CourseTextbooksViewSet
from cms.djangoapps.contentstore.rest_api.v2.views.video_settings import CourseVideoSettingsViewSet

app_name = "authoring_v2"

urlpatterns = [
    path(
        "courses/<course_key:course_key>/textbooks/",
        CourseTextbooksViewSet.as_view({"get": "list"}),
        name="course_textbook_list",
    ),
    path(
        "courses/<course_key:course_key>/video_settings/",
        CourseVideoSettingsViewSet.as_view({"get": "retrieve"}),
        name="course_video_settings",
    ),
]
