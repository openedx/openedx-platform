"""Database models for content search"""

from __future__ import annotations

import logging

from django.db import DatabaseError, models
from django.utils.translation import gettext_lazy as _
from opaque_keys.edx.django.models import LearningContextKeyField
from openedx_authz.constants.permissions import COURSES_VIEW_COURSE
from rest_framework.request import Request

from common.djangoapps.student.role_helpers import get_course_roles
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from openedx.core.djangoapps.authz.scopes import (
    authz_grants_platform_access,
    get_authz_flag_enabled_course_keys,
    get_authz_flag_enabled_org_keys,
    get_authz_org_scoped_course_keys,
    get_authz_platform_course_keys,
    get_authz_platform_orgs,
)
from openedx.core.djangoapps.content_libraries.api import get_libraries_for_user

log = logging.getLogger(__name__)

# The authz permission that admits a user to the search filter for a scope: a
# user is included for a scope iff they hold ``view_course`` on it.
VIEW_COURSE_ACTION = COURSES_VIEW_COURSE.action.external_key


class SearchAccess(models.Model):  # noqa: DJ008
    """
    Stores a numeric ID for each ContextKey.

    We use this shorter ID instead of the full ContextKey when determining a user's access to search-indexed course and
    library content because:

    a) in some deployments, users may be granted access to more than 1_000 individual courses, and
    b) the search filter request is stored in the JWT, which is limited to 8Kib.

    .. no_pii:
    """
    id = models.BigAutoField(
        primary_key=True,
        help_text=_(
            "Numeric ID for each Course / Library context. This ID will generally require fewer bits than the full "
            "LearningContextKey, allowing more courses and libraries to be represented in content search filters."
        ),
    )
    context_key = LearningContextKeyField(
        max_length=255, unique=True, null=False,
    )


def get_access_ids_for_request(request: Request, omit_orgs: list[str] = None) -> list[int]:
    """
    Returns a list of SearchAccess.id values for courses and content libraries that the requesting user has been
    individually grated access to.

    Omits any courses/libraries with orgs in the `omit_orgs` list.
    """
    omit_orgs = omit_orgs or []

    course_roles = get_course_roles(request.user)
    course_keys = set(
        role.course_id
        for role in course_roles
        if (
            role.role in [CourseInstructorRole.ROLE, CourseStaffRole.ROLE]
            and role.org not in omit_orgs
        )
    )

    # Courses the user holds an authz per-course grant on.
    course_keys.update(_get_authz_course_keys_for_search(request.user, omit_orgs))

    # Force-on-override courses surfaced by a platform-wide authz grant.
    course_keys.update(_get_authz_platform_course_keys_for_search(request.user, omit_orgs))

    # Force-on-override courses inside an org the user holds an org-wide authz grant on.
    course_keys.update(_get_authz_org_scoped_course_keys_for_search(request.user, omit_orgs))

    course_clause = models.Q(context_key__in=list(course_keys))

    libraries = get_libraries_for_user(user=request.user)
    library_clause = models.Q(context_key__in=[
        lib.library_key for lib in libraries
        if lib.library_key.org not in omit_orgs
    ])

    # Sort by descending access ID to simulate prioritizing the "most recently created context keys".
    return list(
        SearchAccess.objects.filter(
            course_clause | library_clause
        ).order_by('-id').values_list("id", flat=True)
    )


def authz_has_platform_access(user) -> bool:
    """
    Return whether the user's platform-wide authz grant lets search drop its
    access filter entirely (grant present and the flag's global switch on).

    Fails open on a ``DatabaseError`` (logged, returns ``False``); any other
    exception propagates.
    """
    try:
        return authz_grants_platform_access(user, VIEW_COURSE_ACTION)
    except DatabaseError as exc:
        log.warning(
            "Could not load authz role assignments for user %r; "
            "falling back to per-scope search access. Error: %s",
            user.username,
            exc,
        )
        return False


def _get_authz_course_keys_for_search(user, omit_orgs: list[str]) -> set[str]:
    """
    Return the serialized course-key strings the user holds an authz per-course
    ``view_course`` grant on, excluding orgs in ``omit_orgs``.

    Fails open on a ``DatabaseError`` (logged, returns an empty set); any other
    exception propagates.
    """
    try:
        course_keys = get_authz_flag_enabled_course_keys(user, VIEW_COURSE_ACTION)
    except DatabaseError as exc:
        log.warning(
            "Could not load authz role assignments for user %r; "
            "falling back to legacy course roles for search access. Error: %s",
            user.username,
            exc,
        )
        return set()
    return {str(key) for key in course_keys if key.org not in omit_orgs}


def _get_authz_org_keys_for_search(user, omit_orgs: list[str]) -> set[str]:
    """
    Return the org short_names the user holds an org-wide authz ``view_course``
    grant on, excluding orgs in ``omit_orgs``.

    Fails open on a ``DatabaseError`` (logged, returns an empty set); any other
    exception propagates.
    """
    try:
        org_keys = get_authz_flag_enabled_org_keys(user, VIEW_COURSE_ACTION)
    except DatabaseError as exc:
        log.warning(
            "Could not load authz org role assignments for user %r; "
            "falling back to legacy roles for search access. Error: %s",
            user.username,
            exc,
        )
        return set()
    return {org for org in org_keys if org not in omit_orgs}


def _get_authz_platform_org_keys_for_search(user, omit_orgs: list[str]) -> set[str]:
    """
    Return the force-on-override org short_names a platform-wide authz grant
    surfaces when the flag's global switch is off, excluding orgs in ``omit_orgs``.

    Fails open on a ``DatabaseError`` (logged, returns an empty set); any other
    exception propagates.
    """
    try:
        org_keys = get_authz_platform_orgs(user, VIEW_COURSE_ACTION)
    except DatabaseError as exc:
        log.warning(
            "Could not load authz platform role assignments for user %r; "
            "falling back to legacy roles for search access. Error: %s",
            user.username,
            exc,
        )
        return set()
    return {org for org in org_keys if org not in omit_orgs}


def _get_authz_platform_course_keys_for_search(user, omit_orgs: list[str]) -> set[str]:
    """
    Return the serialized course-key strings for the force-on-override courses a
    platform-wide authz grant surfaces when the flag's global switch is off,
    excluding orgs in ``omit_orgs``.

    Fails open on a ``DatabaseError`` (logged, returns an empty set); any other
    exception propagates.
    """
    try:
        course_keys = get_authz_platform_course_keys(user, VIEW_COURSE_ACTION)
    except DatabaseError as exc:
        log.warning(
            "Could not load authz platform role assignments for user %r; "
            "falling back to legacy roles for search access. Error: %s",
            user.username,
            exc,
        )
        return set()
    return {str(key) for key in course_keys if key.org not in omit_orgs}


def _get_authz_org_scoped_course_keys_for_search(user, omit_orgs: list[str]) -> set[str]:
    """
    Return the serialized course-key strings for force-on-override courses inside
    an org the user holds an org-wide authz grant on, when that org's flag is off,
    excluding orgs in ``omit_orgs``.

    Fails open on a ``DatabaseError`` (logged, returns an empty set); any other
    exception propagates.
    """
    try:
        course_keys = get_authz_org_scoped_course_keys(user, VIEW_COURSE_ACTION)
    except DatabaseError as exc:
        log.warning(
            "Could not load authz org-scoped role assignments for user %r; "
            "falling back to legacy roles for search access. Error: %s",
            user.username,
            exc,
        )
        return set()
    return {str(key) for key in course_keys if key.org not in omit_orgs}


class IncrementalIndexCompleted(models.Model):  # noqa: DJ008
    """
    Stores the contex keys of aleady indexed courses and libraries for incremental indexing.

    .. no_pii:
    """

    context_key = LearningContextKeyField(
        max_length=255,
        unique=True,
        null=False,
    )
