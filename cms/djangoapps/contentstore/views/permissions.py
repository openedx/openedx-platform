"""
Custom permissions for the content store views.
"""

from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission

from common.djangoapps.student.auth import has_studio_read_access, has_studio_write_access
from openedx.core.lib.api.view_utils import validate_course_key


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


class HasFullCourseKey(BasePermission):
    """
    Answer 404 for a course key in the path that carries only a content version.

    Reads the parsed ``course_key`` URL argument. A key such as
    ``course-v1:version@<id>`` parses, but names no organization, course or
    run: no course can be found by it, and Studio's role checks raise an error
    on it instead of refusing it. The request is answered as an address that
    holds nothing, as for a malformed key. List this class after the
    authentication check and before any class that checks roles on the course.
    """

    def has_permission(self, request, view):
        course_key = view.kwargs.get("course_key")
        if course_key is not None and course_key.org is None:
            raise NotFound()
        return True
