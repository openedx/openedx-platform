"""
Public API to enforce AuthZ permissions in the Instructor Dashboard.

When the ``authz.enable_course_authoring`` flag is enabled for a course, the
permission is resolved exclusively through AuthZ (same semantics as Studio, see
``openedx.core.djangoapps.authz.decorators``). When it is disabled, the legacy
check is used, so the pre-AuthZ behavior is preserved.

The legacy roles that AuthZ maps without dashboard permissions are the exception,
see ``compat.py``: with AuthZ enabled they are evaluated with the legacy check.
"""

import logging
from collections.abc import Callable, Iterable

from django.contrib.auth.models import AbstractUser
from opaque_keys.edx.keys import CourseKey
from openedx_authz import api as authz_api

from common.djangoapps.student.roles import enable_authz_course_authoring
from lms.djangoapps.instructor.authz.compat import holds_legacy_role_compat_permission
from lms.djangoapps.instructor.authz.legacy import LEGACY_PERMISSION_CHECKS

log = logging.getLogger(__name__)

# Signature of a legacy check: ``(user, course_key) -> bool``.
LegacyCheck = Callable[[AbstractUser, CourseKey], bool]

ACCESS_PATH_AUTHZ = "authz"
ACCESS_PATH_LEGACY = "legacy"


def user_has_instructor_permission(
    user: AbstractUser,
    authz_permission: str,
    course_key: CourseKey,
    legacy_check: LegacyCheck | None = None,
) -> bool:
    """
    Return whether the user holds an AuthZ permission in the course.

    When ``authz.enable_course_authoring`` is enabled for the course, only AuthZ
    is consulted and legacy roles are ignored, except those of ``compat.py``, which
    fall back to the legacy check when AuthZ denies. When it is disabled, only the
    legacy check is evaluated. The path that resolved the check (``authz`` or
    ``legacy``) is logged.

    Args:
        user (AbstractUser): The user whose access is checked.
        authz_permission (str): The AuthZ permission identifier, e.g.
            ``courses.view_enrollments``.
        course_key (CourseKey): The course where the permission is checked.
        legacy_check (LegacyCheck | None): The pre-AuthZ check, used only when
            AuthZ is disabled for the course. If omitted, the default one
            registered for ``authz_permission`` in ``LEGACY_PERMISSION_CHECKS``
            is used, and a permission without any legacy check is denied.

    Returns:
        bool: True if the user holds the permission.
    """
    if enable_authz_course_authoring(course_key):
        is_allowed = authz_api.is_user_allowed(user.username, authz_permission, str(course_key))
        if is_allowed or not holds_legacy_role_compat_permission(user, course_key):
            _log_decision(ACCESS_PATH_AUTHZ, is_allowed, user, (authz_permission,), course_key)
            return is_allowed

    legacy_check = legacy_check or LEGACY_PERMISSION_CHECKS.get(authz_permission)
    is_allowed = bool(legacy_check and legacy_check(user, course_key))
    _log_decision(ACCESS_PATH_LEGACY, is_allowed, user, (authz_permission,), course_key)
    return is_allowed


def user_has_any_instructor_permission(
    user: AbstractUser,
    authz_permissions: Iterable[str],
    course_key: CourseKey,
    legacy_check: LegacyCheck | None = None,
) -> bool:
    """
    Return whether the user holds at least one of the AuthZ permissions.

    An empty collection grants no access.

    It is meant for the visibility of a tab, which is shown when the user can do
    anything in it, not only a specific action. With ``authz.enable_course_authoring``
    enabled for the course, only AuthZ is consulted, except for the legacy roles of
    ``compat.py``. With it disabled, ``legacy_check`` is used if given; otherwise the
    default legacy check of each permission is tried.

    Args:
        user (AbstractUser): The user whose access is checked.
        authz_permissions (Iterable[str]): The AuthZ permission identifiers, any
            of which grants access.
        course_key (CourseKey): The course where the permissions are checked.
        legacy_check (LegacyCheck | None): The pre-AuthZ check of the whole set,
            used when AuthZ is disabled for the course, and for the legacy roles of ``compat.py``.

    Returns:
        bool: True if the user holds at least one of the permissions.
    """
    permissions = tuple(authz_permissions)
    if not permissions:
        return False

    if enable_authz_course_authoring(course_key):
        course_id = str(course_key)
        if any(authz_api.is_user_allowed(user.username, p, course_id) for p in permissions):
            _log_decision(ACCESS_PATH_AUTHZ, True, user, permissions, course_key)
            return True
        if not holds_legacy_role_compat_permission(user, course_key):
            _log_decision(ACCESS_PATH_AUTHZ, False, user, permissions, course_key)
            return False

    if legacy_check is not None:
        # The legacy check covers the whole set: run and log it once.
        is_allowed = bool(legacy_check(user, course_key))
        _log_decision(ACCESS_PATH_LEGACY, is_allowed, user, permissions, course_key)
        return is_allowed

    return any(user_has_instructor_permission(user, p, course_key) for p in permissions)


def _log_decision(
    path: str, is_allowed: bool, user: AbstractUser, authz_permissions: Iterable[str], course_key: CourseKey
):
    """
    Log the path that resolved a permission check, and its outcome.

    Args:
        path (str): The path that resolved the check.
        is_allowed (bool): Whether access was granted.
        user (AbstractUser): The user checked.
        authz_permissions (Iterable[str]): The AuthZ permission identifiers checked.
        course_key (CourseKey): The course where it was checked.
    """
    log.debug(
        "Instructor dashboard permission %s via %s",
        "granted" if is_allowed else "denied",
        path,
        extra={
            "user_id": user.id,
            "authz_permissions": list(authz_permissions),
            "course_key": str(course_key),
            "access_path": path,
        },
    )
