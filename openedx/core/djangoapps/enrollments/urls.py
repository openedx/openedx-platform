"""
URLs for the Enrollment API

"""

from django.conf import settings
from django.urls import path, re_path

from .views import (
    CourseEnrollmentsApiListView,
    EnrollmentAllowedView,
    EnrollmentCourseDetailView,
    EnrollmentListView,
    EnrollmentUserRolesView,
    EnrollmentView,
    UnenrollmentView,
)

urlpatterns = [
    re_path(
        r"^enrollment/{username},{course_key}$".format(
            username=settings.USERNAME_PATTERN, course_key=settings.COURSE_ID_PATTERN
        ),
        EnrollmentView.as_view(),
        name="courseenrollment",
    ),
    re_path(rf"^enrollment/{settings.COURSE_ID_PATTERN}$", EnrollmentView.as_view(), name="courseenrollment"),
    path("enrollment", EnrollmentListView.as_view(), name="courseenrollments"),
    re_path(r"^enrollments/?$", CourseEnrollmentsApiListView.as_view(), name="courseenrollmentsapilist"),
    re_path(
        rf"^course/{settings.COURSE_ID_PATTERN}$", EnrollmentCourseDetailView.as_view(), name="courseenrollmentdetails"
    ),
    path("unenroll/", UnenrollmentView.as_view(), name="unenrollment"),
    path("roles/", EnrollmentUserRolesView.as_view(), name="roles"),
    path("enrollment_allowed/", EnrollmentAllowedView.as_view(), name="courseenrollmentallowed"),
]
