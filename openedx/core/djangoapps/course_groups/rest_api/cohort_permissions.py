"""
Permissions for the v2 cohorts REST API.
"""
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import permissions

from common.djangoapps.student.roles import GlobalStaff
from lms.djangoapps.courseware.access import administrative_accesses_to_course_for_user
from lms.djangoapps.discussion.django_comment_client.utils import get_user_role_names
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_MODERATOR,
)

DISCUSSION_PRIVILEGED_ROLES = {
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_MODERATOR,
    FORUM_ROLE_COMMUNITY_TA,
}


class CanManageCohorts(permissions.BasePermission):
    """
    Grants access to the cohort endpoints of a course.

    Course staff and instructors may read and write. Discussion moderators,
    administrators and community TAs may read only. Global staff and
    superusers may do either.

    A course key that cannot be parsed is denied rather than raising, so a
    malformed path cannot surface as a server error.
    """

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if GlobalStaff().has_user(user) or user.is_superuser:
            return True

        try:
            course_key = CourseKey.from_string(view.kwargs.get("course_key_string", ""))
        except InvalidKeyError:
            return False

        _, staff_access, instructor_access = administrative_accesses_to_course_for_user(user, course_key)
        if staff_access or instructor_access:
            return True

        moderates_discussions = bool(get_user_role_names(user, course_key) & DISCUSSION_PRIVILEGED_ROLES)
        return moderates_discussions and request.method in permissions.SAFE_METHODS
