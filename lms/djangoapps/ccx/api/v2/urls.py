"""
CCX Coach API v2 URLs.

`api/ccx_coach/v2/`
"""

from django.conf import settings
from django.urls import re_path

from lms.djangoapps.ccx.api.v2 import views

app_name = 'ccx_coach_v2'
urlpatterns = [
    re_path(
        fr'^courses/{settings.COURSE_ID_PATTERN}/metadata$',
        views.CCXCoachMetadataView.as_view(),
        name='metadata',
    ),
    re_path(
        fr'^courses/{settings.COURSE_ID_PATTERN}/create_ccx$',
        views.CreateCCXView.as_view(),
        name='create_ccx',
    ),
]
