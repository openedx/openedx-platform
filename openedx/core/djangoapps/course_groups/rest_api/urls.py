"""
Cohorts and content groups REST API v2 URLs.
"""
from django.urls import re_path

from openedx.core.constants import COURSE_ID_PATTERN
from openedx.core.djangoapps.course_groups.constants import USERNAME_LOOKUP_REGEX
from openedx.core.djangoapps.course_groups.rest_api import cohort_views, views

urlpatterns = [
    re_path(
        fr'^v2/courses/{COURSE_ID_PATTERN}/group_configurations$',
        views.GroupConfigurationsListView.as_view(),
        name='group_configurations_list'
    ),
    re_path(
        fr'^v2/courses/{COURSE_ID_PATTERN}/group_configurations/(?P<configuration_id>\d+)$',
        views.GroupConfigurationDetailView.as_view(),
        name='group_configurations_detail'
    ),

    # Cohorts. Every route carries a required trailing slash and resolves a
    # single address; the list and detail routes are registered separately
    # rather than sharing one pattern with an optional identifier.
    re_path(
        fr'^v2/courses/{COURSE_ID_PATTERN}/cohorts/$',
        cohort_views.CohortViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='cohort_list',
    ),
    re_path(
        fr'^v2/courses/{COURSE_ID_PATTERN}/cohorts/(?P<cohort_id>[0-9]+)/$',
        cohort_views.CohortViewSet.as_view({'get': 'retrieve', 'patch': 'partial_update'}),
        name='cohort_detail',
    ),
    re_path(
        fr'^v2/courses/{COURSE_ID_PATTERN}/cohorts/(?P<cohort_id>[0-9]+)/users/$',
        cohort_views.CohortMemberViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='cohort_member_list',
    ),
    re_path(
        fr'^v2/courses/{COURSE_ID_PATTERN}/cohorts/(?P<cohort_id>[0-9]+)/users/(?P<username>{USERNAME_LOOKUP_REGEX})/$',
        cohort_views.CohortMemberViewSet.as_view({'delete': 'destroy'}),
        name='cohort_member_detail',
    ),
    re_path(
        fr'^v2/courses/{COURSE_ID_PATTERN}/cohort_settings/$',
        cohort_views.CohortSettingsView.as_view(),
        name='course_cohort_settings',
    ),
]
