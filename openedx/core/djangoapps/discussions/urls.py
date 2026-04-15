"""
Configure URL endpoints for the djangoapp
"""
from django.conf import settings
from django.urls import re_path

from .views import (
    CombinedDiscussionsConfigurationView,
    DiscussionsConfigurationSettingsView,
    DiscussionsProvidersView,
    SyncDiscussionTopicsView,
)

urlpatterns = [
    re_path(
        fr'^v0/{settings.COURSE_KEY_PATTERN}$',
        CombinedDiscussionsConfigurationView.as_view(),
        name='discussions',
    ),
    re_path(
        fr'^v0/course/{settings.COURSE_KEY_PATTERN}/settings$',
        DiscussionsConfigurationSettingsView.as_view(),
        name='discussions-settings',
    ),
    re_path(
        fr'^v0/course/{settings.COURSE_KEY_PATTERN}/providers$',
        DiscussionsProvidersView.as_view(),
        name='discussions-providers',
    ),
    re_path(
        fr'^v0/course/{settings.COURSE_KEY_PATTERN}/sync_discussion_topics$',
        SyncDiscussionTopicsView.as_view(),
        name='sync-discussion-topics',
    ),
]
