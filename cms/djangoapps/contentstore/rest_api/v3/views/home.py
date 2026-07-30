"""
API Views for Studio course home — v3.

This module is the v3 incarnation of the v1 ``home`` endpoints, restructured
to apply the FC-0118 ADRs:

  * ADR 0025 – ``serializer_class`` (with per-action ``get_serializer_class``)
  * ADR 0026 – explicit ``authentication_classes`` + ``permission_classes``
  * ADR 0028 – consolidated into a single DRF ``ViewSet`` registered via
    ``DefaultRouter`` (replaces the three legacy ``APIView`` classes
    ``HomePageView`` / ``HomePageCoursesView`` / ``HomePageLibrariesView``)
  * ADR 0029 – standardized error envelope, opted in via
    :class:`StandardizedErrorMixin` (v3-scoped — does not change the
    project-wide DRF ``EXCEPTION_HANDLER`` setting)
  * ADR 0036 – field selection via ``?fields=`` (e.g. ``?fields=courses``).
    The ``list`` action returns a wide ``StudioHomeSerializer`` payload with
    ~25 top-level keys; clients that only need a subset can request it
    explicitly. The flat-list ``courses`` and ``libraries`` actions are
    out of scope (single-key dict around a list) and do not honour
    ``?fields=``.
  * ADR 0034 – already compliant. ``authentication_classes`` is
    ``(JwtAuthentication, SessionAuthenticationAllowInactiveUser)`` —
    no ``BearerAuthentication`` / ``OAuth2Authentication`` to remove.
    The explicit declaration is kept (rather than relying on platform
    defaults) so that ``SessionAuthenticationAllowInactiveUser`` is used
    instead of the default ``SessionAuthentication``.
"""

import edx_api_doc_tools as apidocs
from django.conf import settings
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import OpenApiParameter, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from organizations import api as org_api
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v1.serializers import (
    CourseHomeTabSerializer,
    LibraryTabSerializer,
    StudioHomeSerializer,
)
from cms.djangoapps.contentstore.rest_api.v3.utils import apply_field_selection
from cms.djangoapps.contentstore.utils import get_course_context, get_home_context, get_library_context
from openedx.core.lib.api.mixins import StandardizedErrorMixin


class _HomeAutoSchema(AutoSchema):
    """Custom AutoSchema that treats the 'list' action as a single-object response."""

    def _is_list_view(self, serializer=None):
        if self.view.action == 'list':
            return False
        return super()._is_list_view(serializer)


@extend_schema(tags=["openedx-platform-sdk"])
class HomeViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    ViewSet for the Studio home page. Registered via DefaultRouter (basename ``home``).

    Router-generated URLs:
      GET  /api/contentstore/v3/home/           → list      (aggregated home context)
      GET  /api/contentstore/v3/home/courses/   → courses   (course list only)
      GET  /api/contentstore/v3/home/libraries/ → libraries (library list only)
    """

    schema = _HomeAutoSchema()
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated,)
    serializer_class = StudioHomeSerializer

    def get_serializer_class(self):
        """Return the appropriate serializer class for the current action."""
        if self.action == 'courses':
            return CourseHomeTabSerializer
        if self.action == 'libraries':
            return LibraryTabSerializer
        return StudioHomeSerializer

    def get_serializer(self, *args, **kwargs):
        """Return a serializer instance using the action-appropriate class."""
        return self.get_serializer_class()(*args, **kwargs)

    @extend_schema(
        responses={(200, "application/json"): StudioHomeSerializer},
        parameters=[
            OpenApiParameter("org", str, OpenApiParameter.QUERY, description="Filter by course org"),
            OpenApiParameter(
                "fields",
                str,
                OpenApiParameter.QUERY,
                description=(
                    "ADR 0036 explicit field selection. Comma-separated list "
                    "of top-level keys to include in the response (e.g. "
                    "``courses,libraries,studio_name``). Omit for the full "
                    "response. Unknown keys are silently skipped."
                ),
            ),
        ],
    )
    def list(self, request: Request):
        """
        Get an object containing all courses and libraries on home page.

        **Example Request**

            GET /api/contentstore/v3/home/
            GET /api/contentstore/v3/home/?fields=courses,libraries  (ADR 0036)
        """
        home_context = get_home_context(request, True)
        home_context.update({
            # 'allow_to_create_new_org' is actually about auto-creating organizations
            # (e.g. when creating a course or library), so we add an additional test.
            'allow_to_create_new_org': (
                home_context['can_create_organizations'] and
                org_api.is_autocreate_enabled()
            ),
            'studio_name': settings.STUDIO_NAME,
            'studio_short_name': settings.STUDIO_SHORT_NAME,
            'studio_request_email': settings.FEATURES.get('STUDIO_REQUEST_EMAIL', ''),
            'tech_support_email': settings.TECH_SUPPORT_EMAIL,
            'platform_name': settings.PLATFORM_NAME,
            'user_is_active': request.user.is_active,
        })
        serializer = self.get_serializer(home_context)
        # ADR 0036 — drop top-level keys not requested via ?fields=.
        return Response(apply_field_selection(serializer.data, request.query_params.get("fields")))

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                "org",
                apidocs.ParameterLocation.QUERY,
                description="Query param to filter by course org",
            )],
        responses={
            200: CourseHomeTabSerializer,
            401: "The requester is not authenticated.",
        },
    )
    @action(detail=False, methods=['get'], url_path='courses', url_name='courses')
    def courses(self, request: Request):
        """
        Get an object containing all courses.

        **Example Request**

            GET /api/contentstore/v3/home/courses/
        """
        active_courses, archived_courses, in_process_course_actions = get_course_context(request)
        courses_context = {
            "courses": active_courses,
            "archived_courses": archived_courses,
            "in_process_course_actions": in_process_course_actions,
        }
        serializer = self.get_serializer(courses_context)
        return Response(serializer.data)

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                "org",
                apidocs.ParameterLocation.QUERY,
                description="Query param to filter by course org",
            ),
            apidocs.query_parameter(
                "is_migrated",
                bool,
                description=(
                    "Query param to filter by migrated status of library."
                    " If present (true or false), it will filter by migration status"
                    " else it will return all legacy libraries."
                ),
            )
        ],
        responses={
            200: LibraryTabSerializer,
            401: "The requester is not authenticated.",
        },
    )
    @action(detail=False, methods=['get'], url_path='libraries', url_name='libraries')
    def libraries(self, request: Request):
        """
        Get an object containing all libraries on home page.

        **Example Request**

            GET /api/contentstore/v3/home/libraries/
        """
        library_context = get_library_context(request)
        serializer = self.get_serializer(library_context)
        return Response(serializer.data)
