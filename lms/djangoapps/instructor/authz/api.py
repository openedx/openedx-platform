"""
Public API to enforce AuthZ permissions in the Instructor Dashboard.

When the ``authz.enable_course_authoring`` flag is enabled for a course, the
permission is resolved exclusively through AuthZ (same semantics as Studio, see
``openedx.core.djangoapps.authz.decorators``). When it is disabled, the legacy
check is used, so the pre-AuthZ behavior is preserved.
"""

import logging
from collections.abc import Callable, Iterable

from django.contrib.auth.models import AbstractUser
from opaque_keys.edx.keys import CourseKey
from openedx_authz import api as authz_api

from common.djangoapps.student.roles import enable_authz_course_authoring
from lms.djangoapps.instructor.authz.constants import INSTRUCTOR_DASHBOARD_PERMISSIONS
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
    is consulted and legacy roles are ignored. When it is disabled, only the
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
    enabled for the course, only AuthZ is consulted. With it disabled, ``legacy_check`` is used
    if given; otherwise the default legacy check of each permission is tried.

    Args:
        user (AbstractUser): The user whose access is checked.
        authz_permissions (Iterable[str]): The AuthZ permission identifiers, any
            of which grants access.
        course_key (CourseKey): The course where the permissions are checked.
        legacy_check (LegacyCheck | None): The pre-AuthZ check of the whole set,
            used only when AuthZ is disabled for the course.

    Returns:
        bool: True if the user holds at least one of the permissions.
    """
    authz_permissions = tuple(authz_permissions)
    if not authz_permissions:
        return False
    if legacy_check is not None and not enable_authz_course_authoring(course_key):
        # The legacy check covers the whole set, so it does not depend on which
        # permission is evaluated: run and log it once instead of once per permission.
        is_allowed = bool(legacy_check(user, course_key))
        _log_decision(ACCESS_PATH_LEGACY, is_allowed, user, authz_permissions, course_key)
        return is_allowed
    return any(
        user_has_instructor_permission(user, permission, course_key, legacy_check) for permission in authz_permissions
    )


def get_instructor_authz_permissions(
    user: AbstractUser, course_key: CourseKey, permissions: Iterable[str] = INSTRUCTOR_DASHBOARD_PERMISSIONS
) -> dict[str, bool]:
    """
    Return the effective value of each permission for the user in the course.

    Each value follows the same rules as ``user_has_instructor_permission``,
    using the default legacy check of the permission.

    Args:
        user (AbstractUser): The user whose permissions are resolved.
        course_key (CourseKey): The course where the permissions are resolved.
        permissions (Iterable[str]): The permission identifiers to resolve.
            Defaults to all the Instructor Dashboard ones.

    Returns:
        dict[str, bool]: Mapping of permission identifier to its value, e.g.
        ``{"courses.enroll_learners": True}``.
    """
    return {permission: user_has_instructor_permission(user, permission, course_key) for permission in permissions}


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
