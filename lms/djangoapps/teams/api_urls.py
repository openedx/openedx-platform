"""
Defines the URL routes for the Team API.
"""


from django.conf import settings
from django.urls import path, re_path

from .views import (
    MembershipBulkManagementView,
    MembershipDetailView,
    MembershipListView,
    TeamsAssignmentsView,
    TeamsDetailView,
    TeamsListView,
    TopicDetailView,
    TopicListView,
)

TEAM_ID_PATTERN = r'(?P<team_id>[a-z\d_-]+)'
TOPIC_ID_PATTERN = r'(?P<topic_id>[A-Za-z\d_.-]+)'

urlpatterns = [
    path(
        'v0/teams/',
        TeamsListView.as_view(),
        name="teams_list"
    ),
    re_path(
        r'^v0/teams/{team_id_pattern}$'.format(
            team_id_pattern=TEAM_ID_PATTERN,
        ),
        TeamsDetailView.as_view(),
        name="teams_detail"
    ),
    re_path(
        r'^v0/teams/{team_id_pattern}/assignments$'.format(
            team_id_pattern=TEAM_ID_PATTERN,
        ),
        TeamsAssignmentsView.as_view(),
        name="teams_assignments_list"
    ),
    path(
        'v0/topics/',
        TopicListView.as_view(),
        name="topics_list"
    ),
    re_path(
        r'^v0/topics/{topic_id_pattern},{course_id_pattern}$'.format(
            topic_id_pattern=TOPIC_ID_PATTERN,
            course_id_pattern=settings.COURSE_ID_PATTERN,
        ),
        TopicDetailView.as_view(),
        name="topics_detail"
    ),
    path(
        'v0/team_membership/',
        MembershipListView.as_view(),
        name="team_membership_list"
    ),
    re_path(
        r'^v0/team_membership/{team_id_pattern},{username_pattern}$'.format(
            team_id_pattern=TEAM_ID_PATTERN,
            username_pattern=settings.USERNAME_PATTERN,
        ),
        MembershipDetailView.as_view(),
        name="team_membership_detail"
    ),
    re_path(
        r'^v0/bulk_team_membership/{course_id_pattern}$'.format(
            course_id_pattern=settings.COURSE_ID_PATTERN,
        ),
        MembershipBulkManagementView.as_view(),
        name="team_membership_bulk_management"
    )
]
