"""Database models for content search"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _
from opaque_keys.edx.django.models import LearningContextKeyField
from rest_framework.request import Request

from common.djangoapps.student.role_helpers import get_course_roles
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from openedx.core.djangoapps.content_libraries.api import get_libraries_for_user


class SearchAccess(models.Model):  # noqa: DJ008
    """
    Stores a numeric ID for each ContextKey.

    We use this shorter ID instead of the full ContextKey when determining a user's access to search-indexed course and
    library content because:

    a) in some deployments, users may be granted access to more than 1_000 individual courses, and
    b) the search filter request is stored in the JWT, which is limited to 8Kib.
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
    course_clause = models.Q(context_key__in=[
        role.course_id
        for role in course_roles
        if (
            role.role in [CourseInstructorRole.ROLE, CourseStaffRole.ROLE]
            and role.org not in omit_orgs
        )
    ])

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


class IncrementalIndexCompleted(models.Model):  # noqa: DJ008
    """
    Stores the contex keys of aleady indexed courses and libraries for incremental indexing.
    """

    context_key = LearningContextKeyField(
        max_length=255,
        unique=True,
        null=False,
    )
