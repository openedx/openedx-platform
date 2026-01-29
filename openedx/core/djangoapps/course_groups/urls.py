"""
Cohort API URLs
"""


from django.conf import settings
from django.urls import include, re_path

import lms.djangoapps.instructor.views.api
import openedx.core.djangoapps.course_groups.views

urlpatterns = [
    re_path(
        r'^v1/settings/{}$'.format(
            settings.COURSE_KEY_PATTERN,
        ),
        openedx.core.djangoapps.course_groups.views.CohortSettings.as_view(),
        name='cohort_settings',
    ),
    re_path(
        r'^v1/courses/{}/cohorts/(?P<cohort_id>[0-9]+)?$'.format(
            settings.COURSE_KEY_PATTERN,
        ),
        openedx.core.djangoapps.course_groups.views.CohortHandler.as_view(),
        name='cohort_handler',
    ),
    re_path(
        r'^v1/courses/{}/cohorts/(?P<cohort_id>[0-9]+)/users/(?P<username>.+)?$'.format(
            settings.COURSE_KEY_PATTERN,
        ),
        openedx.core.djangoapps.course_groups.views.CohortUsers.as_view(),
        name='cohort_users',
    ),
    re_path(
        r'^v1/courses/{}/users?$'.format(
            settings.COURSE_KEY_PATTERN,
        ),
        lms.djangoapps.instructor.views.api.CohortCSV.as_view(),
        name='cohort_users_csv',
    ),
    # v2 Content Groups API
    re_path(r'', include('openedx.core.djangoapps.course_groups.rest_api.urls')),
]
