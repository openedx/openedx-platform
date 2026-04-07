"""
URLs for course_info API
"""


from django.conf import settings
from django.urls import path, re_path

from .views import (
    BlocksInfoInCourseView,
    CourseEnrollmentDetailsView,
    CourseGoalsRecordUserActivity,
    CourseHandoutsList,
    CourseUpdatesList,
)

urlpatterns = [
    re_path(
        fr'^{settings.COURSE_ID_PATTERN}/handouts$',
        CourseHandoutsList.as_view(),
        name='course-handouts-list'
    ),
    re_path(
        fr'^{settings.COURSE_ID_PATTERN}/updates$',
        CourseUpdatesList.as_view(),
        name='course-updates-list'
    ),
    re_path(
        fr'^{settings.COURSE_ID_PATTERN}/enrollment_details$',
        CourseEnrollmentDetailsView.as_view(),
        name='course-enrollment-details'
    ),
    path('record_user_activity', CourseGoalsRecordUserActivity.as_view(), name='record_user_activity'),
    path('blocks/', BlocksInfoInCourseView.as_view(), name="blocks_info_in_course"),
]
