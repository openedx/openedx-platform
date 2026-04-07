""" Program Enrollments API v1 URLs. """


from django.conf import settings
from django.urls import path, re_path

from openedx.core.constants import COURSE_ID_PATTERN

from .constants import PROGRAM_UUID_PATTERN
from .views import (
    EnrollmentDataResetView,
    ProgramCourseEnrollmentOverviewView,
    ProgramCourseEnrollmentsView,
    ProgramCourseGradesView,
    ProgramEnrollmentsView,
    UserProgramCourseEnrollmentView,
    UserProgramReadOnlyAccessView,
)

app_name = 'v1'

urlpatterns = [
    path(
        'programs/enrollments/',
        UserProgramReadOnlyAccessView.as_view(),
        name='learner_program_enrollments'
    ),
    path(
        'programs/readonly_access/',
        UserProgramReadOnlyAccessView.as_view(),
        name='user_program_readonly_access'
    ),
    re_path(
        fr'^programs/{PROGRAM_UUID_PATTERN}/enrollments/$',
        ProgramEnrollmentsView.as_view(),
        name='program_enrollments'
    ),
    re_path(
        r'^programs/{program_uuid}/courses/{course_id}/enrollments/'.format(
            program_uuid=PROGRAM_UUID_PATTERN,
            course_id=COURSE_ID_PATTERN
        ),
        ProgramCourseEnrollmentsView.as_view(),
        name="program_course_enrollments"
    ),
    re_path(
        r'^programs/{program_uuid}/courses/{course_id}/grades/'.format(
            program_uuid=PROGRAM_UUID_PATTERN,
            course_id=COURSE_ID_PATTERN
        ),
        ProgramCourseGradesView.as_view(),
        name="program_course_grades"
    ),
    re_path(
        r'^programs/{program_uuid}/overview/'.format(
            program_uuid=PROGRAM_UUID_PATTERN,
        ),
        ProgramCourseEnrollmentOverviewView.as_view(),
        name="program_course_enrollments_overview"
    ),
    re_path(
        r'^users/{username}/programs/{program_uuid}/courses'.format(
            username=settings.USERNAME_PATTERN,
            program_uuid=PROGRAM_UUID_PATTERN,
        ),
        UserProgramCourseEnrollmentView.as_view(),
        name="user_program_course_enrollments"
    ),
    path(
        'integration-reset',
        EnrollmentDataResetView.as_view(),
        name="reset_enrollment_data",
    )
]
