"""Permissions for the v2 authoring API."""

from opaque_keys.edx.locator import CourseLocator
from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission

from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import user_has_course_permission
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

try:
    from openedx_authz.constants.permissions import COURSES_VIEW_COURSE
except ImportError:
    COURSES_VIEW_COURSE = None

COURSE_NOT_FOUND_MESSAGE = "The requested course does not exist."


class CanViewCourse(BasePermission):
    """
    Allow callers who may view the course in Studio.

    Reads the parsed ``course_key`` or ``usage_key`` URL argument.
    Must follow ``IsAuthenticated`` in ``permission_classes``.
    """

    def has_permission(self, request, view):
        course_key = view.kwargs.get("course_key")
        if course_key is None:
            usage_key = view.kwargs.get("usage_key")
            if usage_key is None:
                return False
            course_key = usage_key.context_key
            if not isinstance(course_key, CourseLocator):
                raise NotFound(COURSE_NOT_FOUND_MESSAGE)
        if course_key.org is None or not CourseOverview.course_exists(course_key):
            raise NotFound(COURSE_NOT_FOUND_MESSAGE)
        return user_has_course_permission(
            request.user,
            COURSES_VIEW_COURSE.identifier if COURSES_VIEW_COURSE else "courses.view_course",
            course_key,
            LegacyAuthoringPermission.READ,
        )
