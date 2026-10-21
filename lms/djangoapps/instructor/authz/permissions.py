"""
DRF permission class to enforce AuthZ in the Instructor Dashboard views.
"""

import logging
from collections.abc import Mapping

from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework.permissions import BasePermission

from common.djangoapps.student.roles import enable_authz_course_authoring
from lms.djangoapps.instructor.authz.api import user_has_any_instructor_permission
from lms.djangoapps.instructor.authz.legacy import bridgekeeper_check

log = logging.getLogger(__name__)


class InstructorAuthzPermission(BasePermission):
    """
    Replacement of ``InstructorPermission`` for views migrated to AuthZ.

    It is a drop-in replacement of
    ``lms.djangoapps.instructor.permissions.InstructorPermission``.

    With ``authz.enable_course_authoring`` enabled for the course only AuthZ is
    consulted; legacy roles are ignored.

    Attributes expected in the view:
        authz_permissions (Collection[str] | Mapping[str, Collection[str]]):
            The AuthZ permission identifiers required, or a mapping of HTTP
            method to them when the view needs different permissions per
            method. It is always a collection, even for a single permission,
            like ``permission_classes``. Unlike ``permission_classes``, where
            all the classes must pass (AND), holding any one of the
            permissions is enough (OR). Methods missing from the mapping are
            denied with ``authz.enable_course_authoring`` enabled for the
            course.
        permission_name (str): The legacy ``bridgekeeper`` permission, the one
            ``InstructorPermission`` uses. It is enforced when
            ``authz.enable_course_authoring`` is disabled for the course.
        legacy_permission_check (Callable[[User, CourseKey], bool]): Optional.
            Replaces the ``permission_name`` check, for legacy rules that are
            not a bridgekeeper permission.
        course_id_url_kwarg (str): Optional. The name of the URL kwarg holding
            the course key. Defaults to ``"course_id"``, the one every
            Instructor Dashboard v2 view uses; set it on the view when a
            migrated endpoint uses a different name (e.g. the cohorts API's
            ``course_key_string``).
    """

    def has_permission(self, request, view):
        """
        Check whether the user holds the permission the view requires.

        Args:
            request (Request): The DRF request.
            view (APIView): The view, with ``authz_permissions`` and the legacy
                permission attributes.

        Returns:
            bool: True if access is granted.

        Raises:
            Http404: If the course key URL kwarg of the view is missing or not
                a valid course key.
            ImproperlyConfigured: If the view does not define
                ``authz_permissions``, or defines a bare string instead of a
                collection.
        """
        course_id_url_kwarg = getattr(view, "course_id_url_kwarg", "course_id")
        try:
            course_key = CourseKey.from_string(view.kwargs.get(course_id_url_kwarg))
        except (InvalidKeyError, TypeError) as exc:
            raise Http404("Invalid course key") from exc

        authz_permissions = getattr(view, "authz_permissions", None)
        if authz_permissions is None:
            raise ImproperlyConfigured(f"{type(view).__name__} must define 'authz_permissions'.")
        _validate_authz_permissions(view, authz_permissions)
        if isinstance(authz_permissions, Mapping):
            authz_permissions = authz_permissions.get(request.method)

        legacy_check = getattr(view, "legacy_permission_check", None)
        if legacy_check is None and getattr(view, "permission_name", None):
            legacy_check = bridgekeeper_check(view.permission_name)

        if authz_permissions is None:
            if enable_authz_course_authoring(course_key):
                # Fail closed: a method without an AuthZ permission is never allowed by AuthZ.
                log.warning(
                    "%s %s has no AuthZ permission, denying access",
                    type(view).__name__,
                    request.method,
                    extra={"course_key": str(course_key)},
                )
                return False
            return bool(legacy_check and legacy_check(request.user, course_key))
        return user_has_any_instructor_permission(request.user, authz_permissions, course_key, legacy_check)


def _validate_authz_permissions(view, authz_permissions):
    """
    Reject a bare string where a collection is expected.

    A string is iterable, so a forgotten comma in ``(PERMISSION)`` would be
    read character by character instead of failing.
    """
    values = authz_permissions.values() if isinstance(authz_permissions, Mapping) else (authz_permissions,)
    if any(isinstance(value, str) for value in values):
        raise ImproperlyConfigured(
            f"{type(view).__name__}.authz_permissions must be a collection of permissions, "
            "e.g. (PERMISSION,), not a string."
        )
