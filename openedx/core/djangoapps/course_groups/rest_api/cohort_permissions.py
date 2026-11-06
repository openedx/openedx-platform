"""
Permissions for the v2 cohorts REST API.
"""
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import permissions

from common.djangoapps.student.roles import GlobalStaff
from lms.djangoapps.courseware.access import administrative_accesses_to_course_for_user, has_access
from lms.djangoapps.discussion.django_comment_client.utils import get_user_role_names
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_MODERATOR,
)
from xmodule.modulestore.django import modulestore

DISCUSSION_PRIVILEGED_ROLES = {
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_MODERATOR,
    FORUM_ROLE_COMMUNITY_TA,
}

# Discussion moderators may read; HEAD is GET without a body.
MODERATOR_READ_METHODS = ("GET", "HEAD")


def course_key_from_view(view):
    """
    Return the course key named in the request path, or None if it cannot be parsed.

    A malformed key is denied rather than raised, so it cannot surface as a
    server error.
    """
    try:
        return CourseKey.from_string(view.kwargs.get("course_key", ""))
    except InvalidKeyError:
        return None


class IsCohortCourseAdmin(permissions.BasePermission):
    """
    Grants full access to the course's staff and instructors, global staff and superusers.

    This is the only permission on cohort membership and on the course's cohort
    settings: those responses carry learners' usernames, email addresses and
    full names, so the read-only discussion roles are not admitted there.
    """

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if GlobalStaff().has_user(user) or user.is_superuser:
            return True
        course_key = course_key_from_view(view)
        if course_key is None:
            return False
        _, staff_access, instructor_access = administrative_accesses_to_course_for_user(user, course_key)
        return bool(staff_access or instructor_access)


class CanReadCohortsAsDiscussionModerator(permissions.BasePermission):
    """
    Grants read-only access to discussion administrators, moderators and community TAs.

    Applies to the cohort list and a single cohort only, so that discussion
    staff can see which cohorts exist when moderating divided discussions. The
    caller must also be able to load the course: a course that has not started
    yet, or that is hidden from learners, is refused to anyone without course
    staff access.
    """

    def has_permission(self, request, view):
        user = request.user
        if request.method not in MODERATOR_READ_METHODS:
            return False
        if not (user and user.is_authenticated):
            return False
        course_key = course_key_from_view(view)
        if course_key is None:
            return False
        if not get_user_role_names(user, course_key) & DISCUSSION_PRIVILEGED_ROLES:
            return False
        course = modulestore().get_course(course_key)
        if course is None:
            return False
        return bool(has_access(user, "load", course, course_key))
