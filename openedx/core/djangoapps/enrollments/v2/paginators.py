"""
Pagination for the Enrollment API — v2.

ADR 0032 — uses :class:`DefaultPagination` from
``edx-rest-framework-extensions``, which provides the standard 7-field
envelope: ``count``, ``num_pages``, ``current_page``, ``start``, ``next``,
``previous``, ``results``.

Distinct from v1's :class:`openedx.core.djangoapps.enrollments.paginators.CourseEnrollmentsApiListPagination`
(which is a :class:`CursorPagination` subclass with a 3-field envelope).
v2 introduces the new shape — clients that need the legacy shape stay on
``/api/enrollment/v1/`` until they migrate.
"""

from edx_rest_framework_extensions.paginators import DefaultPagination


class EnrollmentsAdminListPagination(DefaultPagination):
    """
    ADR 0032 — standard pagination for the admin enrollments list API
    (GET /api/enrollment/v2/enrollments/).

    Defaults sized for an admin-facing bulk-query endpoint:
    page_size 100, max 100.
    """

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 100
