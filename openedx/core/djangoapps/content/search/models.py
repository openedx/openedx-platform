"""Database models for content search"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _
from opaque_keys.edx.django.models import LearningContextKeyField
from rest_framework.request import Request

from common.djangoapps.student.role_helpers import get_course_roles
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from openedx.core.djangoapps.content_libraries.api import get_libraries_for_user

from .access_rules import SearchScopeTooLarge


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


def get_access_ids_for_request(request: Request, omit_orgs: list[str] = None, limit: int | None = None) -> list[int]:
    """
    Returns a list of SearchAccess.id values for courses and content libraries that the requesting user has been
    individually granted access to.

    Omits any courses/libraries with orgs in the `omit_orgs` list.
    With `limit`, read at most limit+1 libraries/IDs and reject excessive library
    sets instead of constructing an unbounded library list. The extra ID allows
    the caller to distinguish a complete result from overflow.
    """
    omit_orgs = omit_orgs or []

    course_roles = get_course_roles(request.user)
    course_clause = models.Q(context_key__in=[
        role.course_id
        for role in course_roles
        if (
            role.role in [CourseInstructorRole.ROLE, CourseStaffRole.ROLE]
            and role.org not in omit_orgs
        )
    ])

    libraries = get_libraries_for_user(user=request.user).exclude(org__short_name__in=omit_orgs)
    if limit is not None:
        # Fetch one extra row to detect overflow. Never enumerate all authorized libraries.
        libraries = list(libraries[:limit + 1])
        if len(libraries) > limit:
            raise SearchScopeTooLarge("Library permissions require an explicit library scope.")
    library_clause = models.Q(context_key__in=[
        lib.library_key for lib in libraries
        if lib.library_key.org not in omit_orgs
    ])

    # Sort by descending access ID to simulate prioritizing the "most recently created context keys".
    access_ids = (
        SearchAccess.objects.filter(course_clause | library_clause).order_by('-id').values_list("id", flat=True)
    )
    if limit is not None:
        access_ids = access_ids[:limit + 1]
    return list(access_ids)


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


def has_library_search_access(user, library_key) -> bool:
    """Check one exact library through the existing permission-filtered queryset."""
    return get_libraries_for_user(user=user).filter(
        org__short_name=library_key.org, slug=library_key.slug,
    ).exists()
