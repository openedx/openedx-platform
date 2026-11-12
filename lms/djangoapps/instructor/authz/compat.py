"""
Compatibility of the legacy course roles with the AuthZ enforcement of the Instructor Dashboard.

openedx-authz maps some legacy course roles to AuthZ roles that hold no Instructor
Dashboard permission, only a compatibility permission (``courses.legacy_*_permissions``)
that stands for the legacy role. Before AuthZ those roles had the access their legacy
rules gave them, so enforcing only the dashboard permissions would take it away.

A user that holds one of these compatibility permissions is evaluated with the legacy
check, as if ``authz.enable_course_authoring`` was disabled, when AuthZ denies the
dashboard permissions. The legacy rule decides, so the user keeps exactly the access
the legacy role had.
"""

from django.contrib.auth.models import AbstractUser
from opaque_keys.edx.keys import CourseKey
from openedx_authz import api as authz_api
from openedx_authz.authz import (
    COURSES_LEGACY_BETA_TESTER_PERMISSIONS,
    COURSES_LEGACY_DATA_RESEARCHER_PERMISSIONS,
    COURSES_LEGACY_LIMITED_STAFF_ROLE_PERMISSIONS,
)

# Compatibility permission of each legacy role that keeps its legacy access with AuthZ.
# Add the one of a legacy role here when openedx-authz maps it without dashboard permissions.
LEGACY_ROLE_COMPAT_PERMISSIONS = (
    COURSES_LEGACY_LIMITED_STAFF_ROLE_PERMISSIONS.identifier,
    COURSES_LEGACY_DATA_RESEARCHER_PERMISSIONS.identifier,
    COURSES_LEGACY_BETA_TESTER_PERMISSIONS.identifier,
)


def holds_legacy_role_compat_permission(user: AbstractUser, course_key: CourseKey) -> bool:
    """
    Return whether the user holds the compatibility permission of a legacy role.

    Args:
        user (AbstractUser): The user whose access is checked.
        course_key (CourseKey): The course where the permission is checked.

    Returns:
        bool: True if the user holds at least one of ``LEGACY_ROLE_COMPAT_PERMISSIONS``.
    """
    return any(
        authz_api.is_user_allowed(user.username, permission, str(course_key))
        for permission in LEGACY_ROLE_COMPAT_PERMISSIONS
    )
