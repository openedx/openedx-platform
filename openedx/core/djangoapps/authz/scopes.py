"""
Resolve a user's openedx-authz grants for a given permission action into the
concrete course keys and org short_names those grants enable under
``AUTHZ_COURSE_AUTHORING_FLAG``.
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from openedx_authz.api.data import (
    CourseOverviewData,
    OrgCourseOverviewGlobData,
    PlatformCourseOverviewGlobData,
)
from openedx_authz.api.users import get_scopes_for_user_and_permission

from openedx.core import toggles as core_toggles
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
