""" Contenstore API v1 URLs. """

from django.conf import settings
from django.urls import path, re_path

from openedx.core.constants import COURSE_ID_PATTERN

from .views import (
    ContainerChildrenView,
    ContainerHandlerView,
    CourseCertificatesView,
    CourseDetailsView,
    CourseGradingView,
    CourseGroupConfigurationsView,
    CourseIndexView,
    CourseRerunView,
    CourseSettingsView,
    CourseTeamView,
    CourseTextbooksView,
    CourseVideosView,
    CourseWaffleFlagsView,
    HelpUrlsView,
    HomePageCoursesView,
    HomePageLibrariesView,
    HomePageView,
    ProctoredExamSettingsView,
    ProctoringErrorsView,
    VideoDownloadView,
    VideoUsageView,
    vertical_container_children_redirect_view,
)

app_name = 'v1'

VIDEO_ID_PATTERN = r'(?P<edx_video_id>[-\w]+)'

urlpatterns = [
    path(
        'home',
        HomePageView.as_view(),
        name="home"
    ),
    path(
        'home/courses',
        HomePageCoursesView.as_view(),
        name="courses"),
    path(
        'home/libraries',
        HomePageLibrariesView.as_view(),
        name="libraries"),
    re_path(
        fr'^videos/{COURSE_ID_PATTERN}$',
        CourseVideosView.as_view(),
        name="course_videos"
    ),
    re_path(
        fr'^videos/{COURSE_ID_PATTERN}/{VIDEO_ID_PATTERN}/usage$',
        VideoUsageView.as_view(),
        name="video_usage"
    ),
    re_path(
        fr'^videos/{COURSE_ID_PATTERN}/download$',
        VideoDownloadView.as_view(),
        name="video_usage"
    ),
    re_path(
        fr'^proctored_exam_settings/{COURSE_ID_PATTERN}$',
        ProctoredExamSettingsView.as_view(),
        name="proctored_exam_settings"
    ),
    re_path(
        fr'^proctoring_errors/{COURSE_ID_PATTERN}$',
        ProctoringErrorsView.as_view(),
        name="proctoring_errors"
    ),
    re_path(
        fr'^course_settings/{COURSE_ID_PATTERN}$',
        CourseSettingsView.as_view(),
        name="course_settings"
    ),
    re_path(
        fr'^course_index/{COURSE_ID_PATTERN}$',
        CourseIndexView.as_view(),
        name="course_index"
    ),
    re_path(
        fr'^course_details/{COURSE_ID_PATTERN}$',
        CourseDetailsView.as_view(),
        name="course_details"
    ),
    re_path(
        fr'^course_team/{COURSE_ID_PATTERN}$',
        CourseTeamView.as_view(),
        name="course_team"
    ),
    re_path(
        fr'^course_grading/{COURSE_ID_PATTERN}$',
        CourseGradingView.as_view(),
        name="course_grading"
    ),
    path(
        'help_urls',
        HelpUrlsView.as_view(),
        name="help_urls"
    ),
    re_path(
        fr'^course_rerun/{COURSE_ID_PATTERN}$',
        CourseRerunView.as_view(),
        name="course_rerun"
    ),
    re_path(
        fr'^textbooks/{COURSE_ID_PATTERN}$',
        CourseTextbooksView.as_view(),
        name="textbooks"
    ),
    re_path(
        fr'^certificates/{COURSE_ID_PATTERN}$',
        CourseCertificatesView.as_view(),
        name="certificates"
    ),
    re_path(
        fr'^group_configurations/{COURSE_ID_PATTERN}$',
        CourseGroupConfigurationsView.as_view(),
        name="group_configurations"
    ),
    re_path(
        fr'^container_handler/{settings.USAGE_KEY_PATTERN}$',
        ContainerHandlerView.as_view(),
        name="container_handler"
    ),
    # Deprecated url, please use `container_children` url below
    re_path(
        fr'^container/vertical/{settings.USAGE_KEY_PATTERN}/children$',
        vertical_container_children_redirect_view,
        name="container_vertical"
    ),
    re_path(
        fr'^container/{settings.USAGE_KEY_PATTERN}/children$',
        ContainerChildrenView.as_view(),
        name="container_children"
    ),
    re_path(
        fr'^course_waffle_flags(?:/{COURSE_ID_PATTERN})?$',
        CourseWaffleFlagsView.as_view(),
        name="course_waffle_flags"
    ),

    # Authoring API
    # Do not use under v1 yet (Nov. 23). The Authoring API is still experimental and the v0 versions should be used
]
