"""
Permissions for cohorts API
"""

from opaque_keys.edx.keys import CourseKey
from rest_framework import permissions

from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole, GlobalStaff
from lms.djangoapps.discussion.django_comment_client.utils import get_user_role_names
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_MODERATOR,
)


class IsStaffOrAdmin(permissions.BasePermission):
    """
    Permission that checks if the user is staff or an admin.
    """

    def has_permission(self, request, view):
        """Returns true if the user is admin or staff and request method is GET."""
        if GlobalStaff().has_user(request.user) or request.user.is_superuser:
            return True
        course_key = CourseKey.from_string(view.kwargs.get('course_key_string'))
        user_roles = get_user_role_names(request.user, course_key)
        has_discussion_privileges = bool(user_roles & {
            FORUM_ROLE_ADMINISTRATOR,
            FORUM_ROLE_MODERATOR,
            FORUM_ROLE_COMMUNITY_TA,
        })
        return (
            CourseInstructorRole(course_key).has_user(request.user) or
            CourseStaffRole(course_key).has_user(request.user) or
            has_discussion_privileges and request.method == "GET"
        )
