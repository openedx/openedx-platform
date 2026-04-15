"""Decorators for AuthZ-based permissions enforcement."""
import logging
from collections.abc import Callable
from functools import wraps

from django.contrib.auth.models import AbstractUser
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from openedx_authz import api as authz_api
from rest_framework import status

from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.authz.constants import LEGACY_PERMISSION_HANDLER_MAP, LegacyAuthoringPermission
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin

log = logging.getLogger(__name__)


def authz_permission_required(
        authz_permission: str,
        legacy_permission: LegacyAuthoringPermission | None = None) -> Callable:
    """
    Decorator enforcing course author permissions via AuthZ
    with optional legacy fallback.

    This decorator checks if the requesting user has the specified AuthZ permission for the course.
    If AuthZ is not enabled for the course, and a legacy_permission is provided, it falls back to checking
    the legacy permission.

    Raises:
        DeveloperErrorResponseException: If the user does not have the required permissions.
    """

    def decorator(view_func):

        @wraps(view_func)
        def _wrapped_view(self, request, course_id, *args, **kwargs):
            course_key = get_course_key(course_id)

            if not user_has_course_permission(
                request.user,
                authz_permission,
                course_key,
                legacy_permission
            ):
                raise DeveloperErrorViewMixin.api_error(
                    status_code=status.HTTP_403_FORBIDDEN,
                    developer_message="You do not have permission to perform this action.",
                    error_code="permission_denied",
                )

            return view_func(self, request, course_key, *args, **kwargs)

        return _wrapped_view

    return decorator


def user_has_course_permission(
    user: AbstractUser,
    authz_permission: str,
    course_key: CourseKey,
    legacy_permission: LegacyAuthoringPermission | None = None,
) -> bool:
    """
    Checks if the user has the specified AuthZ permission for the course,
    with optional fallback to legacy permissions.
    """
    if core_toggles.enable_authz_course_authoring(course_key):
        # If AuthZ is enabled for this course, check the permission via AuthZ only.
        is_user_allowed = authz_api.is_user_allowed(user.username, authz_permission, str(course_key))
        log.info(
            "AuthZ permission granted = {}".format(is_user_allowed),  # noqa: UP032
            extra={
                "user_id": user.id,
                "authz_permission": authz_permission,
                "course_key": str(course_key),
            },
        )
        return is_user_allowed

    # If AuthZ is not enabled for this course, fall back to legacy course author
    # access check if legacy_permission is provided.
    has_legacy_permission: Callable | None = LEGACY_PERMISSION_HANDLER_MAP.get(legacy_permission)
    if legacy_permission and has_legacy_permission and has_legacy_permission(user, course_key):
        log.info(
            "AuthZ fallback used",
            extra={
                "user_id": user.id,
                "authz_permission": authz_permission,
                "legacy_permission": legacy_permission,
                "course_key": str(course_key),
            },
        )
        return True

    log.info(
        "AuthZ permission denied",
        extra={
            "user_id": user.id,
            "authz_permission": authz_permission,
            "course_key": str(course_key),
        },
    )
    return False


def get_course_key(course_id: str) -> CourseKey:
    """
    Given a course_id string, attempts to parse it as a CourseKey.
    If that fails, attempts to parse it as a UsageKey and extract the course key from it.
    """
    try:
        return CourseKey.from_string(course_id)
    except InvalidKeyError:
        # If the course_id doesn't match the COURSE_KEY_PATTERN, it might be a usage key.
        # Attempt to parse it as such and extract the course key.
        usage_key = UsageKey.from_string(course_id)
        return usage_key.course_key
