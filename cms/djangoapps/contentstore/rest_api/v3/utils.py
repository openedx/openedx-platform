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
  * :func:`apply_field_selection` – ADR 0036 helper. Drops every top-level
    key not listed in the caller's ``?fields=`` CSV. No-op when ``?fields=``
    is absent. Use this when an action returns a wide flat object and clients
    want to request a subset (e.g. ``?fields=id,display_name,courses``).
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
            by :class:`openedx.core.lib.api.mixins.StandardizedErrorMixin`)
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


def apply_field_selection(data, fields_csv):
    """
    ADR 0036 — drop every top-level key not listed in ``fields_csv``.

    Args:
        data: a ``dict`` (typically ``serializer.data``). Anything else is
            returned untouched.
        fields_csv: the raw value of the ``?fields=`` query parameter. ``None``
            or empty string → no filtering (the full ``data`` is returned).

    Returns:
        A new ``dict`` containing only the requested top-level keys, or the
        original ``data`` if filtering is not applicable.

    Note:
        Only top-level keys are honoured. Dotted paths (``?fields=children.x``)
        are stripped to their first segment (``children``) — full dotted-path
        traversal is intentionally left to a future implementation per the
        ADR 0036 guidance to "reject silent over-fetching" via that syntax.
    """
    if not fields_csv or not isinstance(data, dict):
        return data
    wanted = {name.strip().split(".", 1)[0] for name in fields_csv.split(",") if name.strip()}
    if not wanted:
        return data
    return {key: value for key, value in data.items() if key in wanted}
