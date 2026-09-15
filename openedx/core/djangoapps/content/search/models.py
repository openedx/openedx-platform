"""Database models for content search"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import DatabaseError, models
from django.utils.translation import gettext_lazy as _
from opaque_keys import InvalidKeyError
from opaque_keys.edx.django.models import LearningContextKeyField
from opaque_keys.edx.keys import CourseKey
from openedx_authz.api.data import (
    CourseOverviewData,
    OrgCourseOverviewGlobData,
    PlatformCourseOverviewGlobData,
)
from rest_framework.request import Request

from common.djangoapps.student.role_helpers import get_course_roles
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseStaffRole,
    authz_get_all_course_assignments_for_user,
)
from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.content_libraries.api import get_libraries_for_user
from openedx.core.lib.cache_utils import request_cached

log = logging.getLogger(__name__)

User = get_user_model()


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

    # When authz is enabled, also include courses where the user has an authz role assignment.
    # This ensures authz-only users (editor/auditor without legacy roles) can search their courses.
    course_keys.update(_get_authz_course_keys(request.user, omit_orgs))

    # A platform-wide (course-v1:*) authz grant, when the flag is not globally on,
    # surfaces the individual force-on-override courses here (force-on orgs go into
    # the org clause via _get_user_orgs).
    course_keys.update(get_authz_platform_course_keys(request.user, omit_orgs))

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


@request_cached(arg_map_function=lambda user: user.username)
def _get_cached_authz_course_assignments(user: User):
    """
    Returns the user's course-relevant authz role assignments, cached per request.

    Delegates to ``authz_get_all_course_assignments_for_user``, the shared
    course-role helper in ``student.roles`` (which also backs the legacy-compat
    ``RoleCache`` path), so the set of scope types search cares about --
    ``CourseOverviewData`` (per-course), ``OrgCourseOverviewGlobData`` (org-wide),
    and ``PlatformCourseOverviewGlobData`` (platform-wide) -- is defined in one
    place rather than re-derived here. That single fetch is shared by all three
    search helpers below (per-course keys, org keys, platform access), so each
    filters the same materialized set by scope type without a second enforcer
    round-trip within one request.

    Keyed on ``user.username`` via ``arg_map_function`` (a ``User`` object's
    default repr would otherwise be an unstable cache key), so the helpers share
    one cache entry. Not wrapped in a try/except here: callers own the fail-open
    decision, and ``request_cached`` never caches a raised exception, so a
    transient ``DatabaseError`` on the first call is re-attempted on the second.
    """
    return authz_get_all_course_assignments_for_user(user)


def _get_authz_course_keys(user: User, omit_orgs: list[str]) -> set[str]:
    """
    Returns serialized course keys from the user's authz role assignments where
    the authz course authoring flag is enabled.

    Filters the shared per-request assignment set (see
    ``_get_cached_authz_course_assignments``) to the per-course
    (``CourseOverviewData``) scopes, then to only courses where the flag is
    active (supporting both global enablement and per-course overrides).

    Keys are returned as strings (not ``CourseKey`` objects) to match the legacy
    ``get_course_roles`` branch, whose ``course_id`` is already a string. This
    keeps the unioned ``course_keys`` set type-homogeneous so a course held via
    both a legacy and an authz role de-duplicates to a single entry.

    Fails open: if the authz lookup hits a database error, it is logged and an
    empty set is returned so that search degrades to legacy-role access rather
    than returning a 500. Any other (unexpected) exception propagates.
    """
    try:
        assignments = _get_cached_authz_course_assignments(user)
    except DatabaseError as exc:
        log.warning(
            "Could not load authz role assignments for user %r; "
            "falling back to legacy course roles for search access. Error: %s",
            user.username,
            exc,
        )
        return set()

    course_keys = set()
    for assignment in assignments:
        if not isinstance(assignment.scope, CourseOverviewData):
            continue
        try:
            course_key = CourseKey.from_string(assignment.scope.external_key)
        except InvalidKeyError:
            # A non-course scope (e.g. a library) can legitimately appear here; skip it.
            continue
        if course_key.org not in omit_orgs and core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.is_enabled(course_key):
            # Store the serialized form to match the legacy branch's string
            # course_id, so the unioned set de-duplicates across both paths.
            course_keys.add(str(course_key))
    return course_keys


def get_authz_org_keys(user: User, omit_orgs: list[str]) -> set[str]:
    """
    Returns org short_names from the user's org-level (glob) authz course role
    assignments where the authz course authoring flag is enabled for that org.

    An authz role can be granted at an org-wide scope (e.g. ``course-v1:Org+*``),
    which surfaces as an ``OrgCourseOverviewGlobData`` scope rather than a
    per-course ``CourseOverviewData`` scope. Such a grant means "all courses in
    this org", so it belongs in the Meilisearch ``org IN [...]`` clause -- one
    entry per org rather than fanning out to every course id (which would burn
    ``MAX_ACCESS_IDS_IN_FILTER`` slots) and mirrors how legacy org staff roles
    are handled.

    Filters the shared per-request assignment set (see
    ``_get_cached_authz_course_assignments``) to the org-glob scopes, so this and
    ``_get_authz_course_keys`` share a single enforcer fetch within one request.
    The flag is resolved at the org tier via ``CourseWaffleFlag.is_enabled_for_org``
    (org override takes precedence, else the global switch), since an org-wide
    grant is not tied to a single course.

    Fails open on a database error (logged, returns an empty set) so search
    degrades to legacy access rather than 500-ing; any other exception
    propagates. Orgs already covered by ``omit_orgs`` are skipped.
    """
    try:
        assignments = _get_cached_authz_course_assignments(user)
    except DatabaseError as exc:
        log.warning(
            "Could not load authz org role assignments for user %r; "
            "falling back to legacy roles for search access. Error: %s",
            user.username,
            exc,
        )
        return set()

    org_keys = set()
    for assignment in assignments:
        if not isinstance(assignment.scope, OrgCourseOverviewGlobData):
            continue
        org = assignment.scope.org
        if (
            org
            and org not in omit_orgs
            and core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.is_enabled_for_org(org)
        ):
            org_keys.add(org)
    return org_keys


def _user_has_platform_course_grant(user: User) -> bool:
    """
    Returns whether the user holds a platform-wide (``course-v1:*``) authz course
    role grant, irrespective of the flag.

    A platform-level grant surfaces as a ``PlatformCourseOverviewGlobData`` scope
    and means "all courses across the whole platform" -- the authz analogue of
    legacy global staff. This is the raw grant check; whether it grants search
    access to a given scope is decided per-scope against the flag by the callers
    below.

    Fails open on a database error (logged, returns ``False``) so search degrades
    to the per-scope legacy/authz filter rather than 500-ing; any other exception
    propagates.
    """
    try:
        assignments = _get_cached_authz_course_assignments(user)
    except DatabaseError as exc:
        log.warning(
            "Could not load authz role assignments for user %r; "
            "falling back to per-scope search access. Error: %s",
            user.username,
            exc,
        )
        return False

    return any(
        isinstance(assignment.scope, PlatformCourseOverviewGlobData)
        for assignment in assignments
    )


def authz_has_platform_access(user: User) -> bool:
    """
    Returns whether the user should see ALL search content by virtue of a
    platform-wide (``course-v1:*``) authz course grant.

    "See everything" only holds when the user has the platform grant AND the
    authz course authoring flag is enabled on the global switch -- because a
    globally-on flag means every course/org resolves on (barring a force-off
    override), so the grant genuinely spans the whole platform and the caller can
    drop the search filter entirely.

    When the global switch is OFF, a platform grant is NOT see-everything: the
    flag is only on for the specific orgs/courses that have a force-on override,
    so the grant must be expanded to just those scopes instead (see
    ``get_authz_platform_orgs`` / ``get_authz_platform_course_keys``). This
    mirrors how the per-course and org-glob helpers resolve the flag per scope.
    """
    if not _user_has_platform_course_grant(user):
        return False
    return core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.is_enabled()


def get_authz_platform_orgs(user: User, omit_orgs: list[str]) -> set[str]:
    """
    Returns org short_names a platform-wide (``course-v1:*``) authz grant should
    surface in the ``org IN [...]`` search clause when the flag is NOT globally on.

    A platform grant means "all courses everywhere", but with the global switch
    off only the orgs that carry a force-on override are actually flag-enabled --
    so those are the orgs whose content the grant should reveal. Returns the empty
    set when the user has no platform grant, or when the flag is globally on (in
    which case ``authz_has_platform_access`` already short-circuits to
    see-everything and this expansion is unnecessary).

    Orgs already covered by ``omit_orgs`` are skipped.
    """
    if not _user_has_platform_course_grant(user):
        return set()
    if core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.is_enabled():
        # Globally on -> see-everything handles it; no per-org expansion needed.
        return set()

    # Import here to avoid a model import at project startup.
    from openedx.core.djangoapps.waffle_utils.models import WaffleFlagOrgOverrideModel

    flag_name = core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.name
    on = WaffleFlagOrgOverrideModel.ALL_CHOICES.on
    force_on_orgs = (
        WaffleFlagOrgOverrideModel.objects
        .current_set()
        .filter(waffle_flag=flag_name, override_choice=on)
        .values_list("org", flat=True)
    )
    return {org for org in force_on_orgs if org and org not in omit_orgs}


def get_authz_platform_course_keys(user: User, omit_orgs: list[str]) -> set[str]:
    """
    Returns serialized course keys a platform-wide (``course-v1:*``) authz grant
    should surface in the ``access_id`` search clause when the flag is NOT
    globally on.

    Complements ``get_authz_platform_orgs``: with the global switch off, a course
    that carries its own force-on override is flag-enabled even if its org is not,
    so the platform grant should reveal it. Courses whose org is in ``omit_orgs``
    (already covered by the org clause, including the force-on orgs) are skipped to
    avoid burning ``access_id`` slots on content the org clause already admits.

    Returns the empty set when the user has no platform grant or the flag is
    globally on (see-everything handles that case). Keys are returned as strings
    to match the legacy/authz course-key branches for set-union de-dup.
    """
    if not _user_has_platform_course_grant(user):
        return set()
    if core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.is_enabled():
        return set()

    # Import here to avoid a model import at project startup.
    from openedx.core.djangoapps.waffle_utils.models import WaffleFlagCourseOverrideModel

    flag_name = core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.name
    on = WaffleFlagCourseOverrideModel.ALL_CHOICES.on
    force_on_courses = (
        WaffleFlagCourseOverrideModel.objects
        .current_set()
        .filter(waffle_flag=flag_name, override_choice=on)
        .values_list("course_id", flat=True)
    )
    course_keys = set()
    for course_id in force_on_courses:
        if not course_id:
            continue
        course_key = CourseKey.from_string(str(course_id))
        if course_key.org not in omit_orgs:
            course_keys.add(str(course_key))
    return course_keys


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
