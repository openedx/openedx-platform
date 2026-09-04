"""Database models for content search"""

from __future__ import annotations

import logging

from django.db import DatabaseError, models
from django.utils.translation import gettext_lazy as _
from opaque_keys import InvalidKeyError
from opaque_keys.edx.django.models import LearningContextKeyField
from opaque_keys.edx.keys import CourseKey
from openedx_authz.api.data import CourseOverviewData
from openedx_authz.api.users import get_user_role_assignments_per_scope_type
from rest_framework.request import Request

from common.djangoapps.student.role_helpers import get_course_roles
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.content_libraries.api import get_libraries_for_user

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


def _get_authz_course_keys(username: str, omit_orgs: list[str]) -> set[CourseKey]:
    """
    Returns course keys from the user's authz role assignments where the
    authz course authoring flag is enabled.

    Queries authz unconditionally, then filters to only courses where the
    flag is active (supporting both global enablement and per-course overrides).

    Fails open: if the authz lookup hits a database error, it is logged and an
    empty set is returned so that search degrades to legacy-role access rather
    than returning a 500. Any other (unexpected) exception propagates.
    """
    try:
        assignments = get_user_role_assignments_per_scope_type(
            user_external_key=username,
            scope_types=(CourseOverviewData,),
        )
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
        try:
            course_key = CourseKey.from_string(assignment.scope.external_key)
        except InvalidKeyError:
            # A non-course scope (e.g. a library) can legitimately appear here; skip it.
            continue
        if course_key.org not in omit_orgs and core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.is_enabled(course_key):
            course_keys.add(course_key)
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
