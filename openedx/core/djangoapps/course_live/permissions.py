"""
API library for Django REST Framework permissions-oriented workflows
"""
from openedx_authz.authz import COURSES_MANAGE_PAGES_AND_RESOURCES, COURSES_VIEW_PAGES_AND_RESOURCES
from rest_framework.permissions import SAFE_METHODS, BasePermission

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseStaffRole,
    GlobalStaff,
    enable_authz_course_authoring,
)
from openedx.core.djangoapps.authz.decorators import user_has_course_permission
from openedx.core.lib.api.view_utils import validate_course_key


class IsStaffOrInstructor(BasePermission):
    """
    Check if user is global or course staff

    Permission that checks to see if the user is global staff, course
    staff, course admin,If none of those conditions are met, HTTP403 is returned.

    When AuthZ course authoring is enabled for the course, read requests require
    ``courses.view_pages_and_resources`` and write requests require
    ``courses.manage_pages_and_resources`` instead.
    """

    def has_permission(self, request, view):
        course_key_string = view.kwargs.get('course_id')
        course_key = validate_course_key(course_key_string)

        if enable_authz_course_authoring(course_key):
            if request.method in SAFE_METHODS:
                authz_permission = COURSES_VIEW_PAGES_AND_RESOURCES.identifier
            else:
                authz_permission = COURSES_MANAGE_PAGES_AND_RESOURCES.identifier
            return user_has_course_permission(request.user, authz_permission, course_key)

        if GlobalStaff().has_user(request.user):
            return True

        return (
            CourseInstructorRole(course_key).has_user(request.user) or
            CourseStaffRole(course_key).has_user(request.user)
        )


class IsEnrolledOrStaff(BasePermission):
    """
    Check if user is enrolled in the course or staff
    """

    def has_permission(self, request, view):
        course_key_string = view.kwargs.get('course_id')
        course_key = validate_course_key(course_key_string)

        if GlobalStaff().has_user(request.user):
            return True

        return (
            CourseInstructorRole(course_key).has_user(request.user) or
            CourseStaffRole(course_key).has_user(request.user) or
            CourseEnrollment.is_enrolled(request.user, course_key)
        )
