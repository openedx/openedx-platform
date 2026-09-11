"""Database models for content search"""

from __future__ import annotations

import logging

from django.db import DatabaseError, models
from django.utils.translation import gettext_lazy as _
from opaque_keys import InvalidKeyError
from opaque_keys.edx.django.models import LearningContextKeyField
from opaque_keys.edx.keys import CourseKey
from openedx_authz.api.data import CourseOverviewData, OrgCourseOverviewGlobData
from openedx_authz.api.users import get_user_role_assignments
from rest_framework.request import Request

from common.djangoapps.student.role_helpers import get_course_roles
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.content_libraries.api import get_libraries_for_user
from openedx.core.lib.cache_utils import request_cached

log = logging.getLogger(__name__)


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
    course_keys.update(_get_authz_course_keys(request.user.username, omit_orgs))

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


@request_cached()
def _get_cached_authz_assignments(username: str):
    """
    Returns the user's full authz role-assignment set, cached per request.

    ``get_user_role_assignments_per_scope_type`` fetches the user's entire
    assignment set from the enforcer regardless of the scope types requested,
    then filters in Python. Both search helpers below need a different slice of
    that same set (per-course vs org-glob scopes), so caching the single
    underlying whole-set fetch lets them each filter by scope type without
    paying for a second enforcer round-trip within one request.

    Keyed on ``username`` only (the sole argument), so the two helpers share the
    cache entry. Not wrapped in a try/except here: callers own the fail-open
    decision, and ``request_cached`` never caches a raised exception, so a
    transient ``DatabaseError`` on the first call is re-attempted on the second.
    """
    return get_user_role_assignments(user_external_key=username)


def _get_authz_course_keys(username: str, omit_orgs: list[str]) -> set[str]:
    """
    Returns serialized course keys from the user's authz role assignments where
    the authz course authoring flag is enabled.

    Filters the per-request-cached whole assignment set to the per-course
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
        assignments = _get_cached_authz_assignments(username)
    except DatabaseError as exc:
        log.warning(
            "Could not load authz role assignments for user %r; "
            "falling back to legacy course roles for search access. Error: %s",
            username,
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


def get_authz_org_keys(username: str, omit_orgs: list[str]) -> set[str]:
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

    Filters the per-request-cached whole assignment set to the org-glob scopes,
    so this and ``_get_authz_course_keys`` share a single enforcer fetch within
    one request. The flag is resolved at the org tier via
    ``CourseWaffleFlag.is_enabled_for_org`` (org override takes precedence, else
    the global switch), since an org-wide grant is not tied to a single course.

    Fails open on a database error (logged, returns an empty set) so search
    degrades to legacy access rather than 500-ing; any other exception
    propagates. Orgs already covered by ``omit_orgs`` are skipped.
    """
    try:
        assignments = _get_cached_authz_assignments(username)
    except DatabaseError as exc:
        log.warning(
            "Could not load authz org role assignments for user %r; "
            "falling back to legacy roles for search access. Error: %s",
            username,
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
