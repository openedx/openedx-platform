"""Helpers for resolving openedx-authz scopes into concrete course keys."""
from opaque_keys.edx.keys import CourseKey
from openedx_authz.api.data import (
    CourseOverviewData,
    OrgCourseOverviewGlobData,
    PlatformCourseOverviewGlobData,
    ScopeData,
)

from common.djangoapps.student.roles import enable_authz_course_authoring
from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


def _get_course_keys_for_org_scope(org_keys: set[str]):
    """
    Convert a set of organization keys into specific course keys.
    """

    return CourseOverview.get_all_courses(orgs=org_keys).values_list('id', flat=True)


def _get_course_keys_from_platform_scope() -> set[CourseKey]:
    """
    Resolve course keys for a platform-wide Authz scope.

    When the AuthZ course authoring feature flag is globally enabled, all courses
    are returned without per-course validation. Otherwise, only courses with the
    per-course toggle enabled are included.

    Returns:
        set[CourseKey]: Course keys accessible on the platform.
    """
    course_keys = CourseOverview.get_all_courses().values_list("id", flat=True)

    if core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.is_enabled():
        return set(course_keys)

    return {course_key for course_key in course_keys if enable_authz_course_authoring(course_key)}


def get_course_keys_from_scopes(authz_scopes: list[ScopeData]) -> set[CourseKey]:
    """
    Convert authorization scopes into a set of accessible course keys.

    This function processes authorization scopes with the following precedence:
    1. Platform-wide access (PlatformCourseOverviewGlobData): Returns all courses
       when the AuthZ course authoring toggle is globally enabled; otherwise only
       courses with the per-course toggle enabled
    2. Course-specific access (CourseOverviewData): Returns individual course keys
    3. Organization-wide access (OrgCourseOverviewGlobData): Returns all courses in specified orgs

    For non-platform scopes, only courses with the authz course authoring toggle
    enabled are included.

    Args:
        authz_scopes: List of authorization scope data objects from the authz system.

    Returns:
        set[CourseKey]: Set of course keys the user has access to based on their scopes.
    """
    if any(isinstance(access, PlatformCourseOverviewGlobData) for access in authz_scopes):
        return _get_course_keys_from_platform_scope()

    course_keys = set()
    org_keys = set()

    for access in authz_scopes:
        if isinstance(access, CourseOverviewData) and access.course_key:
            if enable_authz_course_authoring(access.course_key):
                course_keys.add(access.course_key)
        elif isinstance(access, OrgCourseOverviewGlobData) and access.org:
            org_keys.add(access.org)

    if org_keys:
        course_keys.update(
            key for key in _get_course_keys_for_org_scope(org_keys)
            if enable_authz_course_authoring(key)
        )

    return course_keys
