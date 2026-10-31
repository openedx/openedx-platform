"""
Custom permissions for the content store views.
"""

from openedx_authz.constants.permissions import COURSES_VIEW_PAGES_AND_RESOURCES
from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission

from common.djangoapps.student.auth import (
    check_course_advanced_settings_access,
    has_studio_read_access,
    has_studio_write_access,
)
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import user_has_course_permission
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.lib.api.view_utils import validate_course_key

COURSE_NOT_FOUND_MESSAGE = "The requested course does not exist."


class HasStudioWriteAccess(BasePermission):
    """
    Check if the user has write access to studio.
    """

    def has_permission(self, request, view):
        """
        Check if the user has write access to studio.
        """
        course_key_string = view.kwargs.get("course_key_string")
        course_key = validate_course_key(course_key_string)
        return has_studio_write_access(request.user, course_key)


class CanViewPagesAndResources(BasePermission):
    """
    Allow callers who may view the pages and resources of the course in the path.

    Reads the parsed ``course_key`` URL argument. On courses where
    policy-based authoring permissions are enabled, only that policy is
    consulted; elsewhere the caller needs Studio read access to the course.
    """

    def has_permission(self, request, view):
        course_key = view.kwargs.get("course_key")
        if course_key is None:
            return False
        return user_has_course_permission(
            request.user,
            COURSES_VIEW_PAGES_AND_RESOURCES.identifier,
            course_key,
            LegacyAuthoringPermission.READ,
        )


class HasStudioReadAccess(BasePermission):
    """
    Allow callers with Studio read access to the course in the path.

    Reads the parsed ``course_key`` URL argument.
    """

    def has_permission(self, request, view):
        course_key = view.kwargs.get("course_key")
        if course_key is None:
            return False
        return has_studio_read_access(request.user, course_key)


class IsExistingCourse(BasePermission):
    """
    Answer 404 when the course in the path does not exist.

    Reads the parsed ``course_key`` URL argument. A key that names only a
    content version parses, but no course run is behind it, so it is answered
    as an unknown course. List this class after the authentication check, so
    an anonymous caller learns nothing about which courses exist, and before
    any class that checks roles on the course, so a signed-in caller is told
    that a course is missing before being refused.
    """

    def has_permission(self, request, view):
        course_key = view.kwargs.get("course_key")
        if course_key is None:
            return False
        if course_key.org is None or not CourseOverview.course_exists(course_key):
            raise NotFound(COURSE_NOT_FOUND_MESSAGE)
        return True


class CanViewAdvancedSettings(BasePermission):
    """
    Allow callers who may view the advanced settings of the course in the path.

    Reads the parsed ``course_key`` URL argument. On courses where
    policy-based authoring permissions are enabled, only that policy is
    consulted; elsewhere the caller needs Studio read access to the course.
    Advanced settings being restricted to staff site-wide does not apply here:
    course teams also change page and resource toggles through these settings.
    """

    def has_permission(self, request, view):
        course_key = view.kwargs.get("course_key")
        if course_key is None:
            return False
        return check_course_advanced_settings_access(request.user, course_key, access_type="read")


class CanManageAdvancedSettings(BasePermission):
    """
    Allow callers who may change the advanced settings of the course in the path.

    Reads the parsed ``course_key`` URL argument. On courses where
    policy-based authoring permissions are enabled, only that policy is
    consulted; elsewhere the caller needs Studio write access to the course.
    Advanced settings being restricted to staff site-wide does not apply here,
    as for ``CanViewAdvancedSettings``.
    """

    def has_permission(self, request, view):
        course_key = view.kwargs.get("course_key")
        if course_key is None:
            return False
        return check_course_advanced_settings_access(request.user, course_key, access_type="write")
