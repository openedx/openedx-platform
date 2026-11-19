"""
Resolve a user's openedx-authz grants for a given permission action into the
concrete scopes those grants enable.

Course-authoring grants resolve to course keys and org short_names under
``AUTHZ_COURSE_AUTHORING_FLAG``. Content-library grants resolve to library keys
and org short_names; libraries have no authoring waffle flag, so a library grant
is authoritative on its own.

Scopes that were already fetched can also be resolved into concrete course keys
with ``get_authz_course_keys_from_scopes``.
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import LibraryLocatorV2
from openedx_authz.api.data import (
    ContentLibraryData,
    CourseOverviewData,
    OrgContentLibraryGlobData,
    OrgCourseOverviewGlobData,
    PlatformContentLibraryGlobData,
    PlatformCourseOverviewGlobData,
    ScopeData,
)
from openedx_authz.api.users import get_scopes_for_user_and_permission

from common.djangoapps.student.roles import enable_authz_course_authoring
from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.lib.cache_utils import request_cached

log = logging.getLogger(__name__)

User = get_user_model()


@request_cached(arg_map_function=lambda arg: getattr(arg, "username", str(arg)))
def get_cached_scopes_for_permission(user: User, action: str):
    """
    Return the scopes where the user holds ``action``, cached per request.

    The result is a mixed list of ``ScopeData``: per-course
    (``CourseOverviewData``), org-glob (``OrgCourseOverviewGlobData``), and
    platform-glob (``PlatformCourseOverviewGlobData``). Cached once per
    ``(user, action)`` within a request. Does not swallow errors, so a failed
    lookup is never cached.
    """
    return get_scopes_for_user_and_permission(user.username, action)


def get_authz_flag_enabled_course_keys(user: User, action: str) -> set[CourseKey]:
    """
    Return the ``CourseKey`` for each per-course grant of ``action`` where the
    course-authoring flag is enabled for that course (global switch or a
    per-course override).

    A scope whose ``external_key`` is not a parseable course key (e.g. a library)
    is skipped. May raise on a lookup failure.
    """
    course_keys: set[CourseKey] = set()
    for scope in get_cached_scopes_for_permission(user, action):
        if not isinstance(scope, CourseOverviewData):
            continue
        try:
            course_key = CourseKey.from_string(scope.external_key)
        except InvalidKeyError:
            # A non-course scope (e.g. a library) can legitimately appear here; skip it.
            continue
        if core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.is_enabled(course_key):
            course_keys.add(course_key)
    return course_keys


def get_authz_flag_enabled_org_keys(user: User, action: str) -> set[str]:
    """
    Return the org short_name for each org-wide (``course-v1:Org+*``) grant of
    ``action`` where the course-authoring flag is enabled for that org.

    The flag is resolved at the org tier (org override, else the global switch).
    May raise on a lookup failure.
    """
    org_keys: set[str] = set()
    for scope in get_cached_scopes_for_permission(user, action):
        if not isinstance(scope, OrgCourseOverviewGlobData):
            continue
        org = scope.org
        if org and core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.is_enabled_for_org(org):
            org_keys.add(org)
    return org_keys


def _user_authz_org_grant_scopes(user: User, action: str) -> set[str]:
    """
    Return the org short_names of every org-wide (``course-v1:Org+*``) grant of
    ``action`` the user holds, regardless of the flag. May raise on a lookup
    failure.
    """
    return {
        scope.org
        for scope in get_cached_scopes_for_permission(user, action)
        if isinstance(scope, OrgCourseOverviewGlobData) and scope.org
    }


def get_authz_org_scoped_course_keys(user: User, action: str) -> set[CourseKey]:
    """
    Return the ``CourseKey`` for each force-on-override course that falls inside
    one of the user's org-wide grants of ``action`` but whose org is not itself
    flag-enabled.

    Considers only force-on course overrides, and only within the user's granted
    orgs. Courses whose org is already flag-enabled are omitted (that org is
    enabled wholesale). May raise on a lookup failure.
    """
    granted_orgs = _user_authz_org_grant_scopes(user, action)
    if not granted_orgs:
        return set()

    flag = core_toggles.AUTHZ_COURSE_AUTHORING_FLAG
    return {
        course_key
        for course_key in flag.get_force_on_course_keys()
        if course_key.org in granted_orgs and not flag.is_enabled_for_org(course_key.org)
    }


def user_has_platform_grant(user: User, action: str) -> bool:
    """
    Return whether the user holds a platform-wide (``course-v1:*``) grant of
    ``action``, regardless of the flag. May raise on a lookup failure.
    """
    return any(
        isinstance(scope, PlatformCourseOverviewGlobData)
        for scope in get_cached_scopes_for_permission(user, action)
    )


def authz_grants_platform_access(user: User, action: str) -> bool:
    """
    Return whether the user's platform-wide (``course-v1:*``) grant of ``action``
    grants unrestricted access to all content.

    True only when the user holds the platform grant and the flag's global switch
    is on. When the global switch is off the grant is not unrestricted (it expands
    to the force-on override scopes instead). May raise on a lookup failure.
    """
    if not user_has_platform_grant(user, action):
        return False
    return core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.is_enabled()


def get_authz_platform_orgs(user: User, action: str) -> set[str]:
    """
    Return the force-on-override org short_names a platform-wide (``course-v1:*``)
    grant of ``action`` surfaces when the flag's global switch is off.

    Returns the empty set when the user has no platform grant, or when the global
    switch is on (unrestricted access applies instead). May raise on a lookup
    failure.
    """
    if not user_has_platform_grant(user, action):
        return set()
    if core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.is_enabled():
        return set()
    return core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.get_force_on_orgs()


def get_authz_platform_course_keys(user: User, action: str) -> set[CourseKey]:
    """
    Return the ``CourseKey`` for each force-on-override course a platform-wide
    (``course-v1:*``) grant of ``action`` surfaces when the flag's global switch
    is off.

    Returns the empty set when the user has no platform grant, or when the global
    switch is on (unrestricted access applies instead). May raise on a lookup
    failure.
    """
    if not user_has_platform_grant(user, action):
        return set()
    if core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.is_enabled():
        return set()
    return core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.get_force_on_course_keys()


# ---------------------------------------------------------------------------
# Content-library scope resolvers.
#
# Libraries have no authoring waffle flag, so a ``view_library`` grant is
# authoritative on its own -- there is no per-scope flag gate or force-on
# override tier like the course side carries.
# ---------------------------------------------------------------------------


def get_authz_library_keys(user: User, action: str) -> set[LibraryLocatorV2]:
    """
    Return the ``LibraryLocatorV2`` for each per-library grant of ``action``.

    Org-wide (``lib:Org:*``) and platform-wide (``lib:*``) grants are not
    enumerated here -- they are handled by ``get_authz_library_org_keys`` and
    ``authz_grants_all_libraries`` respectively. May raise on a lookup failure.
    """
    return {
        scope.library_key
        for scope in get_cached_scopes_for_permission(user, action)
        if isinstance(scope, ContentLibraryData)
    }


def get_authz_library_org_keys(user: User, action: str) -> set[str]:
    """
    Return the org short_name for each org-wide (``lib:Org:*``) grant of
    ``action``. May raise on a lookup failure.
    """
    return {
        scope.org
        for scope in get_cached_scopes_for_permission(user, action)
        if isinstance(scope, OrgContentLibraryGlobData) and scope.org
    }


def authz_grants_all_libraries(user: User, action: str) -> bool:
    """
    Return whether the user holds a platform-wide (``lib:*``) grant of ``action``,
    which admits every library. May raise on a lookup failure.
    """
    return any(
        isinstance(scope, PlatformContentLibraryGlobData)
        for scope in get_cached_scopes_for_permission(user, action)
    )


# ---------------------------------------------------------------------------
# Scope-to-course-key expansion shared by Studio course listing and tagging.
# ---------------------------------------------------------------------------


def _get_course_keys_for_org_scope(org_keys: set[str]):
    """Return the course keys of every course that belongs to the given orgs.

    The flag is not applied here; callers are responsible for filtering the result.

    Args:
        org_keys: Org short_names, matched case-insensitively.

    Returns:
        A queryset of ``CourseKey`` values, one per course in those orgs.
    """
    return CourseOverview.get_all_courses(orgs=org_keys).values_list('id', flat=True)


def _get_course_keys_from_platform_scope() -> set[CourseKey]:
    """Resolve the course keys covered by a platform-wide (``course-v1:*``) scope.

    When the AuthZ course authoring flag is globally enabled, every course is
    returned without per-course validation. Otherwise, only courses where the flag
    is enabled (for example through a per-course override) are returned.

    Returns:
        The course keys the platform-wide scope grants access to.
    """
    course_keys = CourseOverview.get_all_courses().values_list("id", flat=True)

    if core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.is_enabled():
        return set(course_keys)

    return {course_key for course_key in course_keys if enable_authz_course_authoring(course_key)}


def get_authz_course_keys_from_scopes(authz_scopes: list[ScopeData]) -> set[CourseKey]:
    """Return the course keys where the authz flag is enabled, from already-fetched scopes.

    A platform-wide scope (``PlatformCourseOverviewGlobData``) short-circuits the
    rest: it already covers every course, so the other scopes are ignored. Without
    one, the result is the union of:

    * the course of each ``CourseOverviewData`` scope, and
    * every course in the org of each ``OrgCourseOverviewGlobData`` scope.

    In every case a course is included only if ``enable_authz_course_authoring``
    is true for it, so courses that still rely on legacy roles are left out.

    Args:
        authz_scopes: Scopes returned by openedx-authz for a user and permission.
            Scopes of other types, such as content libraries, are ignored.

    Returns:
        The set of course keys the scopes grant access to.
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


@request_cached(arg_map_function=lambda arg: getattr(arg, "username", str(arg)))
def get_authz_course_org_names(user: User, permission: str) -> set[str]:
    """Return the org short_names of the courses where the user holds ``permission`` through openedx-authz.

    The user's scopes (course, org-wide and platform-wide) are expanded with
    ``get_authz_course_keys_from_scopes``, so only courses that have switched to
    openedx-authz are taken into account.

    The result is cached once per ``(user, permission)`` within a request, since expanding the
    scopes hits the course overviews. May raise on a lookup failure.

    Args:
        user: The user whose scopes are resolved.
        permission: The permission identifier, such as ``courses.manage_tags``.

    Returns:
        The org short_names of the courses the user's grants of ``permission`` cover.
    """
    scopes = list(get_cached_scopes_for_permission(user, permission))
    return {course_key.org for course_key in get_authz_course_keys_from_scopes(scopes)}
