"""
Instructor API endpoint urls.
"""

from django.urls import include, path, re_path

from openedx.core.constants import COURSE_ID_PATTERN
from .views import api_urls

urlpatterns = [
    re_path(rf'^courses/{COURSE_ID_PATTERN}/instructor/api/', include(api_urls.urlpatterns)),
    path(
        'api/instructor/v1/',
        include((api_urls.v1_api_urls, 'lms.djangoapps.instructor'), namespace='instructor_api_v1'),
    ),
    # New v2 API endpoints are being introduced here
    # They are intended to be used by MFEs and other API clients.
    # For now, they are in in api_urls.v2_api_urls but the right place for them
    # should be in a new module lms.djangoapps.instructor.api.v2.urls.py so they can be
    # maintained separately.
    # Saying that, it is likely the api_urls.v2_api_urls will be moved there in the near future.
    path(
        'api/instructor/v2/',
        include((api_urls.v2_api_urls, 'lms.djangoapps.instructor'), namespace='instructor_api_v2'),
    ),
]
