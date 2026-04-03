"""
Contains all the URLs for the Course Home
"""


from django.conf import settings
from django.urls import re_path

from lms.djangoapps.course_home_api.course_metadata.views import CourseHomeMetadataView
from lms.djangoapps.course_home_api.dates.views import DatesTabView
from lms.djangoapps.course_home_api.outline.views import (
    CourseNavigationBlocksView,
    OutlineTabView,
    dismiss_welcome_message,
    save_course_goal,
    unsubscribe_from_course_goal_by_token,
)
from lms.djangoapps.course_home_api.progress.views import ProgressTabView

# This API is a BFF ("backend for frontend") designed for the learning MFE. It's not versioned because there is no
# guarantee of stability over time. It may change from one Open edX release to another. Don't write any scripts
# that depend on it.

urlpatterns = []

# URL for Course metadata content
urlpatterns += [
    re_path(
        fr'course_metadata/{settings.COURSE_KEY_PATTERN}',
        CourseHomeMetadataView.as_view(),
        name='course-metadata'
    ),
]

# Dates Tab URLs
urlpatterns += [
    re_path(
        fr'dates/{settings.COURSE_KEY_PATTERN}',
        DatesTabView.as_view(),
        name='dates-tab'
    ),
]

# Outline Tab URLs
urlpatterns += [
    re_path(
        fr'outline/{settings.COURSE_KEY_PATTERN}',
        OutlineTabView.as_view(),
        name='outline-tab'
    ),
    re_path(
        fr'navigation/{settings.COURSE_KEY_PATTERN}',
        CourseNavigationBlocksView.as_view(),
        name='course-navigation'
    ),
    re_path(
        r'dismiss_welcome_message',
        dismiss_welcome_message,
        name='dismiss-welcome-message'
    ),
    re_path(
        r'save_course_goal',
        save_course_goal,
        name='save-course-goal'
    ),
    re_path(
        r'unsubscribe_from_course_goal/(?P<token>[^/]*)$',
        unsubscribe_from_course_goal_by_token,
        name='unsubscribe-from-course-goal'
    ),
]

# Progress Tab URLs
urlpatterns += [
    re_path(
        fr'progress/{settings.COURSE_KEY_PATTERN}/(?P<student_id>[^/]+)',
        ProgressTabView.as_view(),
        name='progress-tab-other-student'
    ),
    re_path(
        fr'progress/{settings.COURSE_KEY_PATTERN}',
        ProgressTabView.as_view(),
        name='progress-tab'
    ),
]
