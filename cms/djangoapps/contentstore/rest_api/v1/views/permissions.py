"""
Permission classes for v1 contentstore API views (ADR 0026).
"""
import logging

from rest_framework.permissions import BasePermission

from common.djangoapps.student.auth import has_course_author_access

log = logging.getLogger(__name__)


class HasCourseAuthorAccess(BasePermission):
    """
    ADR 0026: replaces the @course_author_access_required decorator.

    Reads ``view.kwargs["course_key"]`` (a CourseKey instance) that is
    injected by XblockViewSet.initial() before DRF runs permission checks.
    Returns 403 if the authenticated user lacks authoring rights on that
    course, or if no course key could be derived.
    """

    def has_permission(self, request, view):
        course_key = getattr(view, "course_key", None)
        if not course_key:
            return False
        return has_course_author_access(request.user, course_key)
