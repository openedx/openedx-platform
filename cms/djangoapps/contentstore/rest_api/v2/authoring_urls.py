"""Contentstore API v2 authoring URLs."""

from django.urls import path

from cms.djangoapps.contentstore.rest_api.v2.views.course_outline import CourseOutlineViewSet
from cms.djangoapps.contentstore.rest_api.v2.views.course_settings import CourseSettingsViewSet
from cms.djangoapps.contentstore.rest_api.v2.views.rerun_defaults import CourseRerunDefaultsViewSet
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
    path(
        "courses/<course_key:course_key>/outline/",
        CourseOutlineViewSet.as_view({"get": "retrieve"}),
        name="course_outline",
    ),
    path(
        "courses/<course_key:course_key>/rerun_defaults/",
        CourseRerunDefaultsViewSet.as_view({"get": "retrieve"}),
        name="course_rerun_defaults",
    ),
    path(
        "courses/<course_key:course_key>/settings/",
        CourseSettingsViewSet.as_view({"get": "retrieve"}),
        name="course_settings",
    ),
]
