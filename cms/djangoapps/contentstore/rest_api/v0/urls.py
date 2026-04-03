""" Contenstore API v0 URLs. """

from django.conf import settings
from django.urls import path, re_path

from openedx.core.constants import COURSE_ID_PATTERN

from .views import (
    AdvancedCourseSettingsView,
    APIHeartBeatView,
    AuthoringGradingView,
    CourseTabListView,
    CourseTabReorderView,
    CourseTabSettingsView,
    LinkCheckStatusView,
    LinkCheckView,
    RerunLinkUpdateStatusView,
    RerunLinkUpdateView,
    TranscriptView,
    YoutubeTranscriptCheckView,
    YoutubeTranscriptUploadView,
    assets,
    authoring_videos,
    xblock
)

app_name = "v0"

VIDEO_ID_PATTERN = r'(?P<edx_video_id>[-\w]+)'

urlpatterns = [
    re_path(
        fr"^advanced_settings/{COURSE_ID_PATTERN}$",
        AdvancedCourseSettingsView.as_view(),
        name="course_advanced_settings",
    ),
    re_path(
        fr"^tabs/{COURSE_ID_PATTERN}$",
        CourseTabListView.as_view(),
        name="course_tab_list",
    ),
    re_path(
        fr"^tabs/{COURSE_ID_PATTERN}/settings$",
        CourseTabSettingsView.as_view(),
        name="course_tab_settings",
    ),
    re_path(
        fr"^tabs/{COURSE_ID_PATTERN}/reorder$",
        CourseTabReorderView.as_view(),
        name="course_tab_reorder",
    ),

    # Authoring API
    path(
        'heartbeat', APIHeartBeatView.as_view(), name='heartbeat'
    ),
    re_path(
        fr'^file_assets/{settings.COURSE_ID_PATTERN}$',
        assets.AssetsCreateRetrieveView.as_view(), name='cms_api_create_retrieve_assets'
    ),
    re_path(
        fr'^file_assets/{settings.COURSE_ID_PATTERN}/{settings.ASSET_KEY_PATTERN}$',
        assets.AssetsUpdateDestroyView.as_view(), name='cms_api_update_destroy_assets'
    ),
    re_path(
        fr'^videos/encodings/{settings.COURSE_ID_PATTERN}$',
        authoring_videos.VideoEncodingsDownloadView.as_view(), name='cms_api_videos_encodings'
    ),
    re_path(
        fr'grading/{settings.COURSE_ID_PATTERN}$',
        AuthoringGradingView.as_view(), name='cms_api_update_grading'
    ),
    path(
        'videos/features',
        authoring_videos.VideoFeaturesView.as_view(), name='cms_api_videos_features'
    ),
    re_path(
        fr'^videos/images/{settings.COURSE_ID_PATTERN}/{VIDEO_ID_PATTERN}$',
        authoring_videos.VideoImagesView.as_view(), name='cms_api_videos_images'
    ),
    re_path(
        fr'^videos/uploads/{settings.COURSE_ID_PATTERN}$',
        authoring_videos.VideosCreateUploadView.as_view(), name='cms_api_create_videos_upload'
    ),
    re_path(
        fr'^videos/uploads/{settings.COURSE_ID_PATTERN}/{VIDEO_ID_PATTERN}$',
        authoring_videos.VideosUploadsView.as_view(), name='cms_api_videos_uploads'
    ),
    re_path(
        fr'^video_transcripts/{settings.COURSE_ID_PATTERN}$',
        TranscriptView.as_view(), name='cms_api_video_transcripts'
    ),
    re_path(
        fr'^xblock/{settings.COURSE_ID_PATTERN}$',
        xblock.XblockCreateView.as_view(), name='cms_api_create_xblock'
    ),
    re_path(
        fr'^xblock/{settings.COURSE_ID_PATTERN}/{settings.USAGE_KEY_PATTERN}$',
        xblock.XblockView.as_view(), name='cms_api_xblock'
    ),
    re_path(
        fr'^youtube_transcripts/{settings.COURSE_ID_PATTERN}/check?$',
        YoutubeTranscriptCheckView.as_view(), name='cms_api_youtube_transcripts_check'
    ),
    re_path(
        fr'^youtube_transcripts/{settings.COURSE_ID_PATTERN}/upload?$',
        YoutubeTranscriptUploadView.as_view(), name='cms_api_youtube_transcripts_upload'
    ),

    # Course Optimizer
    re_path(
        fr'^link_check/{settings.COURSE_ID_PATTERN}$',
        LinkCheckView.as_view(), name='link_check'
    ),
    re_path(
        fr'^link_check_status/{settings.COURSE_ID_PATTERN}$',
        LinkCheckStatusView.as_view(), name='link_check_status'
    ),

    re_path(
        fr'^rerun_link_update/{settings.COURSE_ID_PATTERN}$',
        RerunLinkUpdateView.as_view(), name='rerun_link_update'
    ),
    re_path(
        fr'^rerun_link_update_status/{settings.COURSE_ID_PATTERN}$',
        RerunLinkUpdateStatusView.as_view(), name='rerun_link_update_status'
    ),
]
