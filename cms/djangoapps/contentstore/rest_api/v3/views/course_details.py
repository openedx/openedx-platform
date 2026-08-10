"""
API Views for course details — v3.

This module is the v3 incarnation of the v1 ``course_details`` endpoint,
restructured to apply the FC-0118 ADRs:

  * ADR 0025 – ``serializer_class`` on the viewset
  * ADR 0026 – explicit ``authentication_classes`` + ``permission_classes``
  * ADR 0027 – ``drf_spectacular`` for OpenAPI schema generation
  * ADR 0028 – consolidated into a single DRF ``ViewSet`` registered via
    ``DefaultRouter`` (replaces ``CourseDetailsView`` ``APIView``)
  * ADR 0029 – standardized error envelope via :class:`StandardizedErrorMixin`
    (v3-scoped — does not change the project-wide DRF ``EXCEPTION_HANDLER``
    setting)
  * ADR 0034 – already compliant. ``authentication_classes`` is
    ``(JwtAuthentication, SessionAuthenticationAllowInactiveUser)`` — no
    ``BearerAuthentication`` / ``OAuth2Authentication`` to remove.
    Explicit declaration kept (rather than relying on platform defaults)
    so that ``SessionAuthenticationAllowInactiveUser`` is used instead of
    the default ``SessionAuthentication``.

Permission model note:
    PR #38365 proposed a class-level ``HasStudioReadAccess`` permission. The
    current v1 view has since evolved to use the ``openedx_authz`` permission
    framework with a schedule-vs-details classification that gates updates on
    *different* permissions depending on the payload. That granularity cannot
    be hoisted to a single class-level permission, so the per-action checks
    remain inline (gated by ``IsAuthenticated`` at the class level).
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import OpenApiParameter, OpenApiRequest, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from openedx_authz.constants.permissions import (
    COURSES_EDIT_DETAILS,
    COURSES_EDIT_SCHEDULE,
    COURSES_VIEW_SCHEDULE_AND_DETAILS,
)
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v1.serializers import CourseDetailsSerializer
from cms.djangoapps.contentstore.rest_api.v1.views.course_details import _classify_update
from cms.djangoapps.contentstore.rest_api.v3.utils import (
    COMMON_ERROR_RESPONSES,
    apply_field_selection,
    resolve_course_key,
)
from cms.djangoapps.contentstore.utils import update_course_details
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import user_has_course_permission
from openedx.core.djangoapps.models.course_details import CourseDetails
from openedx.core.lib.api.mixins import StandardizedErrorMixin
from xmodule.modulestore.django import modulestore

_COURSE_ID_PARAMETER = OpenApiParameter(
    name="course_id",
    description="Course ID",
    required=True,
    type=str,
    location=OpenApiParameter.PATH,
)

# ADR 0036 — document the minimal/full response variants in OpenAPI (decision #3).
# Declaring these as query parameters is what makes the presets discoverable by
# OpenAPI consumers (Swagger UI, generated SDK clients, etc.). The 200 response
# schema below points at the full ``CourseDetailsSerializer``; ``?view=minimal``
# returns the subset of top-level keys listed in :data:`_MINIMAL_VIEW_FIELDS`.
_VIEW_QUERY_PARAMETER = OpenApiParameter(
    name="view",
    description=(
        "ADR 0036 response preset. ``minimal`` drops heavy fields (overview, "
        "syllabus, description, instructor_info, learning_info, banner/video "
        "assets, license) leaving only identification, schedule, and flags. "
        "Omit the parameter to receive the full response."
    ),
    required=False,
    type=str,
    location=OpenApiParameter.QUERY,
    enum=["minimal"],
)
_FIELDS_QUERY_PARAMETER = OpenApiParameter(
    name="fields",
    description=(
        "ADR 0036 explicit field selection. Comma-separated list of top-level "
        "keys to include in the response (e.g. ``course_id,title,start_date``). "
        "When combined with ``?view=``, the preset is applied first and "
        "``?fields=`` is applied to the result. Unknown keys are silently "
        "skipped."
    ),
    required=False,
    type=str,
    location=OpenApiParameter.QUERY,
)

# ADR 0036 — the ``CourseDetailsSerializer`` has ~40 top-level fields plus a
# nested ``instructor_info`` sub-object with bios and image URLs and a
# ``learning_info`` long-form list. When ``?view=minimal`` is requested,
# everything outside :data:`_MINIMAL_VIEW_FIELDS` is dropped so server-to-server
# and AI-agent callers can fetch just the identification + schedule + flags
# without paying for the heavy text and embedded sub-objects.
_MINIMAL_VIEW_FIELDS = frozenset({
    "course_id",
    "org",
    "run",
    "title",
    "subtitle",
    "language",
    "self_paced",
    "start_date",
    "end_date",
    "enrollment_start",
    "enrollment_end",
    "certificate_available_date",
    "certificates_display_behavior",
    "has_changes",
})


def _apply_view_preset(data, view_preset):
    """ADR 0036 — drop everything outside ``_MINIMAL_VIEW_FIELDS`` when ``?view=minimal``."""
    if view_preset != "minimal" or not isinstance(data, dict):
        return data
    return {key: value for key, value in data.items() if key in _MINIMAL_VIEW_FIELDS}


@extend_schema(tags=["openedx-platform-sdk"])
class CourseDetailsViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    ViewSet for course details (v3). Registered via DefaultRouter (basename ``course_details``).

    Router-generated URLs::

        GET  /api/contentstore/v3/course_details/{course_id}/  → retrieve
        PUT  /api/contentstore/v3/course_details/{course_id}/  → update

    Supersedes ``CourseDetailsView`` at ``/api/contentstore/v1/course_details/{course_id}``.
    """

    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated,)
    serializer_class = CourseDetailsSerializer

    # Matches both slash-separated (org/course/run) and plus-separated (course-v1:org+course+run) IDs
    lookup_field = "course_id"
    lookup_value_regex = r"[^/+]+(?:/|\+)[^/+]+(?:/|\+)[^/?]+"

    @extend_schema(
        summary="Retrieve a course's details",
        description=(
            "Get an object containing the course details for the specified course. "
            "Supports the ADR 0036 ``?view=minimal`` preset and ``?fields=`` "
            "explicit field selection (see the parameter descriptions for details)."
        ),
        parameters=[_COURSE_ID_PARAMETER, _VIEW_QUERY_PARAMETER, _FIELDS_QUERY_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=CourseDetailsSerializer,
                description=(
                    "Course details retrieved successfully. The schema below is "
                    "the full default response; when ``?view=minimal`` and/or "
                    "``?fields=`` is supplied, the response contains a subset of "
                    "these top-level keys (see ADR 0036)."
                ),
            ),
            **COMMON_ERROR_RESPONSES,
        },
    )
    def retrieve(self, request: Request, course_id: str):
        """
        Get an object containing all the course details.

        **Example Request**

            GET /api/contentstore/v3/course_details/{course_id}/
            GET /api/contentstore/v3/course_details/{course_id}/?view=minimal
            GET /api/contentstore/v3/course_details/{course_id}/?fields=course_id,title

        ADR 0036:
            * ``?view=minimal`` drops heavy fields (overview, syllabus, description,
              instructor_info, learning_info, banner/video assets, license, etc.)
              leaving only identification, schedule, and flags.
            * ``?fields=...`` keeps an arbitrary CSV subset of top-level keys.
            * ``?fields=`` and ``?view=`` may be combined — ``?view=minimal``
              is applied first, then ``?fields=`` is applied to the result.
        """
        course_key = resolve_course_key(course_id)
        if not user_has_course_permission(
            request.user,
            COURSES_VIEW_SCHEDULE_AND_DETAILS.identifier,
            course_key,
            LegacyAuthoringPermission.READ,
        ):
            self.permission_denied(request)

        course_details = CourseDetails.fetch(course_key)
        data = self.serializer_class(course_details).data
        # ADR 0036 — preset first, then explicit CSV subset.
        data = _apply_view_preset(data, request.query_params.get("view"))
        data = apply_field_selection(data, request.query_params.get("fields"))
        return Response(data)

    @extend_schema(
        summary="Update a course's details",
        description="Update the details for the specified course.",
        request=OpenApiRequest(request=CourseDetailsSerializer),
        parameters=[_COURSE_ID_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=CourseDetailsSerializer,
                description="Course details updated successfully.",
            ),
            400: OpenApiResponse(description="Bad request — invalid data."),
            **COMMON_ERROR_RESPONSES,
        },
    )
    def update(self, request: Request, course_id: str):
        """
        Update a course's details.

        **Example Request**

            PUT /api/contentstore/v3/course_details/{course_id}/

        **PUT Parameters**

        The data sent for a put request should follow a similar format as
        is returned by a ``GET`` request. Multiple details can be updated in
        a single request, however only the ``value`` field can be updated;
        any other fields, if included, will be ignored.

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned,
        along with all the course's details similar to a ``GET`` request.
        """
        course_key = resolve_course_key(course_id)
        is_schedule_update, is_details_update = _classify_update(request.data, course_key)

        if not is_schedule_update and not is_details_update:
            # No updatable fields provided — fall through to a details-permission check
            # so the caller gets 403 if they lack edit-details rights.
            is_details_update = True

        if is_schedule_update and not user_has_course_permission(
            request.user,
            COURSES_EDIT_SCHEDULE.identifier,
            course_key,
            LegacyAuthoringPermission.READ,
        ):
            self.permission_denied(request)

        if is_details_update and not user_has_course_permission(
            request.user,
            COURSES_EDIT_DETAILS.identifier,
            course_key,
            LegacyAuthoringPermission.READ,
        ):
            self.permission_denied(request)

        course_block = modulestore().get_course(course_key)

        try:
            updated_data = update_course_details(request, course_key, request.data, course_block)
        except DjangoValidationError as err:
            raise DRFValidationError(err.message) from err

        serializer = self.serializer_class(updated_data)
        return Response(serializer.data)
