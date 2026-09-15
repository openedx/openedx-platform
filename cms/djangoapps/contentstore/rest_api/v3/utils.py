"""
Shared utilities for v3 contentstore API viewsets.

Houses the small helpers and OpenAPI constants that more than one v3 viewset
needs, so the per-viewset modules stay focused on action bodies and don't
drift apart over time.

Currently provides:
  * :func:`resolve_course_key` – parse-and-verify a course key string,
    raising ``NotFound`` for unparseable keys or missing courses (replaces
    the legacy ``@verify_course_exists()`` decorator from v1 and avoids
    relying on ``DeveloperErrorViewMixin``).
  * :data:`COMMON_ERROR_RESPONSES` – the shared ``@extend_schema(responses=...)``
    fragment for the 401 / 403 / 404 cases every v3 course-scoped viewset
    can raise.
"""

from drf_spectacular.utils import OpenApiResponse
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework.exceptions import NotFound

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


def resolve_course_key(course_key: str) -> CourseKey:
    """
    Parse ``course_key`` (string) into a :class:`CourseKey` and verify the
    course exists.

    Raises:
        rest_framework.exceptions.NotFound: if the string is unparseable
            *or* the course does not exist. The ADR 0029 envelope (wired in
            by :class:`edx_rest_framework_extensions.mixins.StandardizedErrorMixin`)
            renders both as a structured 404.

    OEP-68: the parameter name is ``course_key`` rather than the legacy
    ``course_id``. The function is intentionally agnostic to which URL kwarg
    name the caller used — callers may pass the value of either kwarg as a
    positional argument.
    """
    try:
        parsed = CourseKey.from_string(course_key)
    except InvalidKeyError as exc:
        raise NotFound("The provided course key cannot be parsed.") from exc
    if not CourseOverview.course_exists(parsed):
        raise NotFound(f"Course {course_key} not found.")
    return parsed


COMMON_ERROR_RESPONSES = {
    401: OpenApiResponse(description="The requester is not authenticated."),
    403: OpenApiResponse(description="The requester cannot access the specified course."),
    404: OpenApiResponse(description="The requested course does not exist."),
}
