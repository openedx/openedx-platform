"""Contentstore API v1 authoring URLs."""

from django.urls import path

from cms.djangoapps.contentstore.rest_api.v1.views import (
    XblockViewSet,
    YoutubeTranscriptChecksViewSet,
    YoutubeTranscriptImportsViewSet,
)
from cms.djangoapps.contentstore.rest_api.v1.views.advanced_settings import CourseAdvancedSettingsViewSet
from cms.djangoapps.contentstore.rest_api.v1.views.custom_pages import (
    CourseCustomPageOrderViewSet,
    CourseCustomPagesViewSet,
    CourseTabsViewSet,
)

app_name = "authoring_v1"

urlpatterns = [
    path(
        "courses/<course_key:course_key>/youtube_transcript_checks/",
        YoutubeTranscriptChecksViewSet.as_view({"get": "retrieve"}),
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
    path(
        "courses/<course_key:course_key>/advanced_settings/",
        CourseAdvancedSettingsViewSet.as_view({"get": "retrieve", "patch": "partial_update"}),
        name="course_advanced_settings",
    ),
    # The course is named by the required course_key query parameter.
    path(
        "custom_pages/",
        CourseCustomPagesViewSet.as_view({"get": "list"}),
        name="custom_page_list",
    ),
    path(
        "courses/<course_key:course_key>/custom_page_order/",
        CourseCustomPageOrderViewSet.as_view({"put": "update"}),
        name="course_custom_page_order",
    ),
    path(
        "courses/<course_key:course_key>/tabs/<str:tab_id>/",
        CourseTabsViewSet.as_view({"patch": "partial_update"}),
        name="course_tab_detail",
    ),
]
