"""Contenstore API v2 URLs."""

from django.conf import settings  # noqa: I001
from django.urls import path, re_path

from cms.djangoapps.contentstore.rest_api.v2.views import downstreams, home, utils

app_name = "v2"

urlpatterns = [
    path(
        "home/courses",
        home.HomePageCoursesViewV2.as_view(),
        name="courses",
    ),
    re_path(
        r'^downstreams/$',
        downstreams.DownstreamListView.as_view(),
        name="downstreams_list",
    ),
    re_path(
        fr'^downstreams/{settings.USAGE_KEY_PATTERN}$',
        downstreams.DownstreamView.as_view(),
        name="downstream"
    ),
    re_path(
        f'^downstreams/{settings.COURSE_KEY_PATTERN}/summary$',
        downstreams.DownstreamSummaryView.as_view(),
        name='upstream-summary-list'
    ),
    re_path(
        fr'^downstreams/{settings.USAGE_KEY_PATTERN}/sync$',
        downstreams.SyncFromUpstreamView.as_view(),
        name="sync_from_upstream"
    ),
    re_path(
        '^validate/numerical-input/$',
        utils.NumericalInputValidationView.as_view(),
        name='numerical_input_validation'),
]
