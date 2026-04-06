"""
course live API URLs.
"""


from django.conf import settings
from django.urls import re_path

from openedx.core.djangoapps.course_live.views import (
    CourseLiveConfigurationView,
    CourseLiveIframeView,
    CourseLiveProvidersView,
)

urlpatterns = [
    re_path(fr'^course/{settings.COURSE_ID_PATTERN}/?$',
            CourseLiveConfigurationView.as_view(), name='course_live'),
    re_path(fr'^providers/{settings.COURSE_ID_PATTERN}/?$',
            CourseLiveProvidersView.as_view(), name='live_providers'),
    re_path(fr'^iframe/{settings.COURSE_ID_PATTERN}/?$',
            CourseLiveIframeView.as_view(), name='live_iframe'),
]
