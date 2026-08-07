"""HomeCoursesViewSet for getting courses available to the logged-in user (v4)."""

from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import (
    SessionAuthenticationAllowInactiveUser,
)
from edx_rest_framework_extensions.paginators import DefaultPagination
from rest_framework import serializers as _serializers
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v4.serializers.home import (
    CourseHomeTabSerializerV4,
)
from cms.djangoapps.contentstore.utils import get_course_context_v2
from openedx.core.lib.api.mixins import StandardizedErrorMixin


class _HomeCoursesAutoSchema(AutoSchema):
    """Custom AutoSchema that treats the 'list' action as a single-object response."""

    def _is_list_view(self, serializer=None):
        if self.view.action == 'list':
            return False
        return super()._is_list_view(serializer)


class HomePageCoursesPaginator(DefaultPagination):
    """
    ADR 0032 - standard pagination for the Studio home courses list (v4).

    Extends ``DefaultPagination`` (edx-rest-framework-extensions) which
    provides the 7-field response envelope:
    ``count``, ``num_pages``, ``current_page``, ``start``,
    ``next``, ``previous``, ``results``.

    Overrides ``paginate_queryset`` to handle ``filter`` objects returned
    by ``get_course_context_v2``.
    """

    page_size_query_param = "page_size"

    def paginate_queryset(self, queryset, request, view=None):
        """
        Paginate a queryset, converting ``filter`` objects to lists first.

        ``get_course_context_v2`` may return a ``filter`` object; the base
        ``PageNumberPagination`` cannot measure its length without materialising
        it first, so we do that here.
        """
        if isinstance(queryset, filter):
            queryset = list(queryset)
        return super().paginate_queryset(queryset, request, view)


def _query_param(
    name: str, description: str, deprecated: bool = False
) -> OpenApiParameter:
    """Build a string-typed, optional query parameter for OpenAPI docs."""
    return OpenApiParameter(
        name=name,
        description=description,
        required=False,
        type=str,
        location=OpenApiParameter.QUERY,
        deprecated=deprecated,
    )


_HOME_COURSES_QUERY_PARAMETERS = [
    _query_param("org", "Filter by course org"),
    _query_param("search", "Filter by course name, org, or number"),
    _query_param(
        "ordering",
        "Order by course field: display_name, org, number, or run (ADR 0033 standard parameter).",
    ),
    _query_param(
        "order",
        "Deprecated alias for 'ordering' (ADR 0033). Use 'ordering' instead.",
        deprecated=True,
    ),
    _query_param("active_only", "Filter to active courses only"),
    _query_param("archived_only", "Filter to archived courses only"),
    _query_param("page", "Page number for pagination"),
    _query_param("page_size", "Number of courses per page (default 10, max 100)"),
]

_UNAUTHENTICATED_RESPONSE = OpenApiResponse(
    description="The requester is not authenticated."
)

# ADR 0033: emitted as an HTTP ``Deprecation`` header when the legacy ``order``
# parameter is used instead of the DRF-standard ``ordering``.
_LEGACY_ORDER_DEPRECATION_HEADER = (
    "Parameter 'order' is deprecated. Use 'ordering' instead. "
    "Support will be removed in release '<release_name>'."
)


def _maybe_set_legacy_order_deprecation_header(
    request: Request, response: Response
) -> Response:
    """Set the ADR 0033 Deprecation header when the legacy ``order`` parameter is used."""
    if "order" in request.query_params:
        response["Deprecation"] = _LEGACY_ORDER_DEPRECATION_HEADER
    return response


@extend_schema(tags=["openedx-platform-sdk"])
class HomeCoursesViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    ViewSet for course listing (v4). Registered via DefaultRouter (basename ``home-courses``).

    Router-generated URLs::

        GET  /api/contentstore/v4/home/courses/  → list

    Supersedes ``HomePageCoursesViewV2`` at ``/api/contentstore/v2/home/courses``.

    ADR compliance:
        - 0025: ``serializer_class`` attribute for schema generation
        - 0026: explicit ``authentication_classes`` and ``permission_classes``
        - 0027: ``drf_spectacular`` for OpenAPI documentation
        - 0028: ViewSet with DefaultRouter registration
        - 0029: standardized error envelope via ``StandardizedErrorMixin``
        - 0032: 7-field pagination envelope via ``DefaultPagination``
        - 0033: ``ordering`` parameter; ``order`` kept as deprecated alias
        - 0036: **out of scope.** This endpoint returns a flat paginated list
          governed by ADR 0032; ADR 0036 explicitly excludes flat lists from
          its ``?view=`` / ``?depth=`` / minimal-by-default requirements. Each
          course item carries 9 thin top-level fields (``course_key``,
          ``display_name``, ``lms_link``, ``cms_link``, ``number``, ``org``,
          ``rerun_link``, ``run``, ``url``, ``is_active``) — no nested
          children, no embedded full sub-objects, no tree shape. Per-item
          ``?fields=`` subset filtering is a possible follow-up (would require
          a dynamic-fields serializer mixin and per-field schema documentation)
          but is intentionally NOT added here to keep the v4 contract stable
          for the existing Studio frontend.
        - 0034: already compliant. ``authentication_classes`` is
          ``(JwtAuthentication, SessionAuthenticationAllowInactiveUser)`` — no
          ``BearerAuthentication`` / ``OAuth2Authentication`` to remove.
          Explicit declaration kept so ``SessionAuthenticationAllowInactiveUser``
          is used instead of the default ``SessionAuthentication``.
    """

    schema = _HomeCoursesAutoSchema()
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated,)
    serializer_class = CourseHomeTabSerializerV4

    def get_serializer(self, *args, **kwargs):
        """Instantiate and return the configured serializer class."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="List courses for the Studio home page (paginated)",
        description=(
            "Returns a paginated list of all courses available to the logged-in user, "
            "with optional filtering and ordering. "
            "Supersedes ``GET /api/contentstore/v2/home/courses``."
        ),
        parameters=_HOME_COURSES_QUERY_PARAMETERS,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="PaginatedV4HomeCoursesResponse",
                    fields={
                        "count": _serializers.IntegerField(help_text="Total number of courses."),
                        "num_pages": _serializers.IntegerField(help_text="Total number of pages."),
                        "current_page": _serializers.IntegerField(help_text="Current page number."),
                        "start": _serializers.IntegerField(
                            help_text="Zero-based index of the first item on this page."
                        ),
                        "next": _serializers.CharField(
                            allow_null=True, help_text="URL for the next page, or null."
                        ),
                        "previous": _serializers.CharField(
                            allow_null=True, help_text="URL for the previous page, or null."
                        ),
                        "results": CourseHomeTabSerializerV4(),
                    },
                ),
                description="Paginated course list retrieved successfully.",
            ),
            401: _UNAUTHENTICATED_RESPONSE,
        },
    )
    def list(self, request: Request):
        """
        Get a paginated list of all courses available to the logged-in user.

        **Example Request**

            GET /api/contentstore/v4/home/courses/
            GET /api/contentstore/v4/home/courses/?org=edX
            GET /api/contentstore/v4/home/courses/?search=E2E
            GET /api/contentstore/v4/home/courses/?ordering=-org
            GET /api/contentstore/v4/home/courses/?order=-org
            GET /api/contentstore/v4/home/courses/?active_only=true
            GET /api/contentstore/v4/home/courses/?archived_only=true
            GET /api/contentstore/v4/home/courses/?page=2
            GET /api/contentstore/v4/home/courses/?page_size=20

        **Pagination Parameters**

            - ``page`` (int): Page number to retrieve. Default is 1.
            - ``page_size`` (int): Items per page. Default is 10, max is 100.

        **Response Envelope (ADR 0032)**

            - ``count`` (int): Total number of courses matching the filters.
            - ``num_pages`` (int): Total number of pages.
            - ``current_page`` (int): The current page number.
            - ``start`` (int): The 0-based index of the first course on this page.
            - ``next`` (str|null): URL for the next page, or null on the last page.
            - ``previous`` (str|null): URL for the previous page, or null on the first page.
            - ``results`` (dict): Course data for the current page.

        **Example Response**

        ```json
        {
            "count": 1,
            "num_pages": 1,
            "current_page": 1,
            "start": 0,
            "next": null,
            "previous": null,
            "results": {
                "courses": [
                    {
                        "course_key": "course-v1:edX+E2E-101+course",
                        "display_name": "E2E Test Course",
                        "lms_link": "//localhost:18000/courses/course-v1:edX+E2E-101+course",
                        "cms_link": "//localhost:18010/course/course-v1:edX+E2E-101+course",
                        "number": "E2E-101",
                        "org": "edX",
                        "rerun_link": "/course_rerun/course-v1:edX+E2E-101+course",
                        "run": "course",
                        "url": "/course/course-v1:edX+E2E-101+course",
                        "is_active": true
                    }
                ],
                "in_process_course_actions": []
            }
        }
        ```
        """
        courses, in_process_course_actions = get_course_context_v2(request)
        paginator = HomePageCoursesPaginator()
        courses_page = paginator.paginate_queryset(courses, request, view=self)
        serializer = self.get_serializer(
            {
                "courses": courses_page,
                "in_process_course_actions": in_process_course_actions,
            }
        )
        response = paginator.get_paginated_response(serializer.data)
        return _maybe_set_legacy_order_deprecation_header(request, response)
