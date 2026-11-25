"""
Permission classes for v1 contentstore API views (ADR 0026).
"""
import logging

from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission

from common.djangoapps.student.auth import has_course_author_access

log = logging.getLogger(__name__)


class HasCourseAuthorAccess(BasePermission):
    """
    ADR 0026: replaces the @course_author_access_required decorator.

    Reads ``view.course_key`` (a CourseKey instance), which the view sets in
    ``initial()`` before DRF runs permission checks.
    Returns 403 if the authenticated user lacks authoring rights on that
    course, or if no course key could be derived, and 404 for a key that
    names only a version, which has no course run to grant access to.
    """

    def has_permission(self, request, view):
        course_key = getattr(view, "course_key", None)
        if not course_key:
            return False
        # A key that names only a version has no course run behind it, so the
        # role lookup raises InvalidKeyError instead of refusing it. There is
        # nothing to grant access to, so the address holds no course.
        if course_key.org is None:
            raise NotFound(f"Course {course_key} not found.")
        return has_course_author_access(request.user, course_key)
