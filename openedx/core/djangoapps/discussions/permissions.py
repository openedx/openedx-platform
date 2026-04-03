"""
API library for Django REST Framework permissions-oriented workflows
"""
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole, GlobalStaff
from lms.djangoapps.discussion.django_comment_client.utils import has_discussion_privileges
from openedx.core.lib.api.view_utils import validate_course_key

DEFAULT_MESSAGE = "You're not authorized to perform this operation."
PERMISSION_MESSAGES = {
    "change_provider": "Must be global staff to change discussion provider after the course has started.",
}


class IsStaffOrCourseTeam(BasePermission):
    """
    Check if user is global or course staff

    Permission that checks to see if the user is global staff, course
    staff, course admin, or has discussion privileges. If none of those conditions are
    met, HTTP403 is returned.
    """

    def has_permission(self, request, view):
        course_key_string = view.kwargs.get('course_key_string')
        course_key = validate_course_key(course_key_string)

        if GlobalStaff().has_user(request.user):
            return True

        return (
            CourseInstructorRole(course_key).has_user(request.user) or
            CourseStaffRole(course_key).has_user(request.user) or
            has_discussion_privileges(request.user, course_key)
        )


def user_permissions_for_course(course, user):
    """
    Return the user's permissions over the discussion configuration of the course.
    """
    return {
        "change_provider": not course.has_started() or GlobalStaff().has_user(user),
    }


def check_course_permissions(course, user, permission):
    """
    Check the user has permissions for the operation over the course configuration.

    Raises PermissionDenied if the user does not have permission
    """
    permissions = user_permissions_for_course(course, user)
    granted = permissions.get(permission)
    if not granted:
        raise PermissionDenied(PERMISSION_MESSAGES.get(permission, DEFAULT_MESSAGE))
