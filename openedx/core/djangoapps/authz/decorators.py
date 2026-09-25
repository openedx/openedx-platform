"""Decorators for AuthZ-based permissions enforcement."""
import logging
from collections.abc import Callable
from functools import wraps

from django.contrib.auth.models import AbstractUser
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from openedx_authz import api as authz_api
from rest_framework import status

from common.djangoapps.student.roles import enable_authz_course_authoring
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
    if enable_authz_course_authoring(course_key):
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


def user_has_course_permission_for_upstream(
    request,
    authz_permission: str,
    upstream_key,
    param_name: str = "course_id",
) -> bool:
    """
    Check an AuthZ course permission for the course of a downstream block, but only if
    that downstream's upstream link actually points at ``upstream_key``.

    Meant for endpoints that are normally scoped to a library resource (``upstream_key``,
    a ``LibraryUsageLocatorV2`` or ``LibraryContainerLocator``) but should also grant
    access to a user reviewing that exact resource from a course they can't otherwise
    view the library from, e.g. a Course Auditor reviewing pending library updates. The
    caller is expected to fall back to its regular resource-level permission check when
    this returns False.

    The query param must be the *downstream block's* usage key, not a bare course id: we
    resolve its real upstream link and compare it against ``upstream_key`` ourselves,
    rather than trusting the caller's claim that the two are related. Otherwise, holding
    the permission in any course would grant access to any library resource, whether or
    not that course actually uses it.

    Returns False (never raises) for any reason the bypass doesn't apply: the param is
    absent, not a valid usage key, the downstream doesn't exist, has no upstream link, or
    that link doesn't match ``upstream_key``.

    Deliberately doesn't use cms.lib.xblock.upstream_sync.UpstreamLink: this module is a
    dependency of low-level apps like content_libraries and xblock (per the "low-level
    apps should not depend on high-level apps" import-linter contract), so it can't import
    from upstream_sync without creating a cycle. We only need the raw upstream reference,
    so we read the block's ``upstream`` field directly (relying on it having the mixin
    that provides that field applied elsewhere, not on importing that mixin ourselves) and
    parse it ourselves, the same way UpstreamLink.get_for_block does internally.
    """
    # Imported locally: this pulls in xmodule-only code, which isn't available to every
    # process that imports this module (e.g. a pure LMS process without Studio installed).
    from opaque_keys.edx.locator import (  # pylint: disable=import-outside-toplevel
        LibraryContainerLocator,
        LibraryUsageLocatorV2,
    )

    from xmodule.modulestore.django import modulestore  # pylint: disable=import-outside-toplevel
    from xmodule.modulestore.exceptions import ItemNotFoundError  # pylint: disable=import-outside-toplevel

    downstream_id = request.GET.get(param_name)
    if not downstream_id:
        return False
    try:
        downstream_key = UsageKey.from_string(downstream_id)
    except InvalidKeyError:
        return False
    try:
        downstream = modulestore().get_item(downstream_key)
    except ItemNotFoundError:
        return False

    upstream_ref = getattr(downstream, "upstream", None)
    if not upstream_ref:
        return False
    try:
        downstream_upstream_key = LibraryUsageLocatorV2.from_string(upstream_ref)
    except InvalidKeyError:
        try:
            downstream_upstream_key = LibraryContainerLocator.from_string(upstream_ref)
        except InvalidKeyError:
            return False

    if downstream_upstream_key != upstream_key:
        return False
    return user_has_course_permission(request.user, authz_permission, downstream_key.course_key)
