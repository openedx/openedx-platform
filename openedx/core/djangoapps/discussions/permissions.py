"""
API library for Django REST Framework permissions-oriented workflows
"""
from openedx_authz import api as authz_api
from openedx_authz.constants.permissions import (
    COURSES_MANAGE_PAGES_AND_RESOURCES,
    COURSES_VIEW_PAGES_AND_RESOURCES,
)
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole, GlobalStaff
from lms.djangoapps.discussion.django_comment_client.utils import has_discussion_privileges
from openedx.core import toggles as core_toggles
from openedx.core.lib.api.view_utils import validate_course_key

DEFAULT_MESSAGE = "You're not authorized to perform this operation."
PERMISSION_MESSAGES = {
    "change_provider": "Must be global staff to change discussion provider after the course has started.",
}


def _legacy_is_staff_or_course_team(user, course_key):
    """Legacy permission check: allows global staff, course instructor, course staff, or discussion moderators/TAs."""
    if GlobalStaff().has_user(user):
        return True
    return (
        CourseInstructorRole(course_key).has_user(user)
        or CourseStaffRole(course_key).has_user(user)
        or has_discussion_privileges(user, course_key)
    )


class HasPagesAndResourcesAccess(BasePermission):
    """
    Check if user has access to Pages & Resources.

    When the authz feature flag is enabled, uses authz permissions.
    GET requests check view permission, all others check manage permission.
    Falls back to has_discussion_privileges when authz denies access
    (transitional until discussion roles are migrated to authz).
    When the flag is off, falls back to legacy behavior: global staff,
    course instructor, course staff, or discussion privileges.
    """

    def has_permission(self, request, view):
        course_key_string = view.kwargs.get('course_key_string')
        course_key = validate_course_key(course_key_string)

        if core_toggles.enable_authz_course_authoring(course_key):
            if request.method == 'GET':
                authz_perm = COURSES_VIEW_PAGES_AND_RESOURCES.identifier
            else:
                authz_perm = COURSES_MANAGE_PAGES_AND_RESOURCES.identifier
            if authz_api.is_user_allowed(request.user.username, authz_perm, str(course_key)):
                return True
            return has_discussion_privileges(request.user, course_key)

        return _legacy_is_staff_or_course_team(request.user, course_key)


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
