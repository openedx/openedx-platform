"""
Content Groups REST API v2 URLs
"""
from django.urls import re_path

from openedx.core.constants import COURSE_ID_PATTERN
from openedx.core.djangoapps.course_groups.rest_api import views

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
]
