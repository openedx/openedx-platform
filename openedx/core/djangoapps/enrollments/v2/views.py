"""
API Views for the Enrollment API — v2.

This module is the v2 incarnation of the v1 enrollment views, restructured
to apply the FC-0118 ADRs from the start:

  * ADR 0025 – ``serializer_class`` on every viewset/view
  * ADR 0026 – explicit ``authentication_classes`` + ``permission_classes``
  * ADR 0034 – auth standardization (OEP-0042). All four v2 viewsets/views use
    ``(JwtAuthentication, EnrollmentCrossDomainSessionAuth)``;
    ``BearerAuthenticationAllowInactiveUser`` has been removed per the
    deprecation policy. ``EnrollmentCrossDomainSessionAuth`` is retained
    (rather than relying on the platform-default ``SessionAuthentication``)
    because these endpoints must accept cross-domain Studio/LMS CSRF-validated
    session cookies.
  * ADR 0027 – ``drf_spectacular`` for OpenAPI schema generation
  * ADR 0028 – consolidated into ``ViewSet`` classes registered via
    ``DefaultRouter`` where the URL shape allows it
  * ADR 0029 – standardized error envelope via :class:`StandardizedErrorMixin`
  * ADR 0031 – business logic centralized in
    :class:`EnrollmentOperationsService` (``v2.view_services``)
  * ADR 0032 – ``DefaultPagination`` 7-field envelope on list endpoints
  * ADR 0033 – OEP-68 parameter naming (``course_key`` preferred,
    ``course_id`` as deprecated alias) plus standard ``ordering`` whitelist
  * ADR 0036 – ``?view=minimal`` on the enrollment ``list`` and singleton
    ``retrieve`` actions. By default each enrollment record embeds the full
    ``course_details`` sub-object (which itself includes a ``course_modes``
    list and other heavy fields). When ``?view=minimal`` is requested, the
    embedded sub-object is flattened to a single ``course_id`` string so
    callers that only need to know which courses a user is enrolled in (AI
    agents, sync pipelines) can skip the per-row sub-object payload.

Existing v1 endpoints at ``/api/enrollment/v1/`` are unchanged — v2 is a
parallel new version mounted at ``/api/enrollment/v2/``.
"""

import logging

from django.contrib.auth import get_user_model
from django.utils.decorators import method_decorator
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiRequest,
    OpenApiResponse,
    extend_schema,
)
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.paginators import DefaultPagination
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.disable_rate_limit import can_disable_rate_limit
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.cors_csrf.decorators import ensure_csrf_cookie_cross_domain
from openedx.core.djangoapps.enrollments import api
from openedx.core.djangoapps.enrollments.errors import CourseEnrollmentError
from openedx.core.djangoapps.enrollments.serializers import (
    CourseEnrollmentAllowedSerializer,
    CourseEnrollmentsApiListSerializer,
    CourseEnrollmentSerializer,
    CourseSerializer,
)
from openedx.core.djangoapps.enrollments.v2.forms import EnrollmentsAdminListForm
from openedx.core.djangoapps.enrollments.v2.paginators import EnrollmentsAdminListPagination
from openedx.core.djangoapps.enrollments.v2.serializers import UserRolesResponseSerializer
from openedx.core.djangoapps.enrollments.v2.view_services import EnrollmentOperationsService
from openedx.core.djangoapps.enrollments.views import (
    ApiKeyPermissionMixIn,
    EnrollmentCrossDomainSessionAuth,
    EnrollmentUserThrottle,
)
from openedx.core.djangoapps.user_api.accounts.permissions import CanRetireUser
from openedx.core.lib.api.mixins import StandardizedErrorMixin
from openedx.core.lib.api.permissions import ApiKeyHeaderPermissionIsAuthenticated

log = logging.getLogger(__name__)
User = get_user_model()

# ADR 0031 — single shared service instance for the v2 enrollment operations.
_OPS = EnrollmentOperationsService()


# ---------------------------------------------------------------------------
# ADR 0027 — shared OpenAPI parameter and response building blocks
# ---------------------------------------------------------------------------
def _path_param(name: str, description: str) -> OpenApiParameter:
    return OpenApiParameter(
        name=name, description=description, required=True, type=str, location=OpenApiParameter.PATH,
    )


def _query_param(name: str, description: str, *, required: bool = False, type_=str,
                 deprecated: bool = False) -> OpenApiParameter:
    return OpenApiParameter(
        name=name, description=description, required=required, type=type_,
        location=OpenApiParameter.QUERY, deprecated=deprecated,
    )


_COURSE_ID_PATH_PARAM = _path_param("course_id", "Course ID (e.g. course-v1:org+course+run).")
_USERNAME_PATH_PARAM = _path_param("username", "Username of the user.")
_USER_QUERY_PARAM = _query_param("user", "Username of the user whose enrollments to list.")
_INCLUDE_EXPIRED_QUERY_PARAM = _query_param(
    "include_expired", "If '1', include expired enrollment modes in the response.",
)
_PAGE_QUERY_PARAM = _query_param("page", "Page number to retrieve. Default 1.")
_PAGE_SIZE_QUERY_PARAM = _query_param("page_size", "Items per page (default 10, max 100).")

# ADR 0036 decision #3 — document the ``?view=`` variant in OpenAPI so it's
# discoverable. ``?view=minimal`` collapses each enrollment's embedded
# ``course_details`` sub-object to a single ``course_id`` string; omit to
# receive the full default shape declared by the 200 response schema.
_VIEW_QUERY_PARAM = OpenApiParameter(
    name="view",
    description=(
        "ADR 0036 response preset. ``minimal`` collapses the embedded "
        "``course_details`` sub-object on each enrollment to a single "
        "``course_id`` string (drops ``course_modes`` and other heavy "
        "course-detail fields). Omit the parameter to receive the full response."
    ),
    required=False,
    type=str,
    location=OpenApiParameter.QUERY,
    enum=["minimal"],
)

_RESP_UNAUTHENTICATED = OpenApiResponse(description="The requester is not authenticated.")
_RESP_FORBIDDEN = OpenApiResponse(description="The requester does not have permission for this operation.")
_RESP_NOT_FOUND = OpenApiResponse(description="The requested resource does not exist.")
_RESP_BAD_REQUEST = OpenApiResponse(description="Invalid request data or parameters.")


# ---------------------------------------------------------------------------
# ADR 0033 — Deprecation-header helpers (OEP-68 parameter naming)
# ---------------------------------------------------------------------------
def _build_legacy_param_deprecation_header(legacy_to_preferred):
    """
    Build the ADR 0033 ``Deprecation`` HTTP header value for one or more
    legacy parameter names, each paired with its OEP-68-compliant
    replacement.

    Example: ``[('course_id', 'course_key')]`` →
    ``"Parameter 'course_id' is deprecated. Use 'course_key' instead. ..."``
    """
    parts = [
        f"Parameter '{legacy}' is deprecated. Use '{preferred}' instead."
        for legacy, preferred in legacy_to_preferred
    ]
    parts.append("Support will be removed in release '<release_name>'.")
    return " ".join(parts)


def _maybe_set_legacy_param_deprecation_header(request, response, alias_pairs):
    """Set the ADR 0033 ``Deprecation`` HTTP header on the response when any
    legacy parameter name from ``alias_pairs`` is present in the request."""
    used = [(legacy, preferred) for legacy, preferred in alias_pairs if legacy in request.query_params]
    if used:
        response["Deprecation"] = _build_legacy_param_deprecation_header(used)
    return response


# ---------------------------------------------------------------------------
# ADR 0036 — minimal enrollment view helper
# ---------------------------------------------------------------------------
def _to_minimal_enrollment(enrollment_dict):
    """
    ADR 0036 — collapse the embedded ``course_details`` sub-object on a serialized
    enrollment dict down to a single ``course_id`` string. Heavy fields such as
    ``course_modes`` are dropped. The enrollment-level fields (``created``,
    ``mode``, ``is_active``, ``user``) are kept.

    Returns a new dict — the original is not mutated.
    """
    if not isinstance(enrollment_dict, dict):
        return enrollment_dict
    minimal = {k: v for k, v in enrollment_dict.items() if k != "course_details"}
    details = enrollment_dict.get("course_details") or {}
    if isinstance(details, dict):
        minimal["course_id"] = details.get("course_id")
    return minimal


def _is_minimal_view_requested(request) -> bool:
    """Return True when the caller asked for the ADR 0036 minimal preset."""
    return request.query_params.get("view") == "minimal"


# ===========================================================================
# EnrollmentViewSet — consolidates list / create / unenroll / allowed
# ===========================================================================
@can_disable_rate_limit
@extend_schema(tags=["openedx-platform-sdk"])
class EnrollmentViewSet(StandardizedErrorMixin, viewsets.ViewSet, ApiKeyPermissionMixIn):
    """
    Canonical ViewSet for the v2 Enrollment API.

    Consolidates the v1 ``EnrollmentListView`` + ``UnenrollmentView`` +
    ``EnrollmentAllowedView`` into a single router-registered ViewSet
    (ADR 0028). Per-action permissions are declared via the ``@action``
    decorator's ``permission_classes`` kwarg.

    Router URLs (registered at ``basename="enrollment"``)::

        GET    /api/enrollment/v2/enrollment/                 → list
        POST   /api/enrollment/v2/enrollment/                 → create
        POST   /api/enrollment/v2/enrollment/unenroll/        → unenroll
        GET    /api/enrollment/v2/enrollment/enrollment_allowed/  → allowed (GET)
        POST   /api/enrollment/v2/enrollment/enrollment_allowed/  → allowed (POST)
        DELETE /api/enrollment/v2/enrollment/enrollment_allowed/  → allowed (DELETE)
    """

    # ADR 0034 — JWT + cross-domain session (BearerAuthenticationAllowInactiveUser
    # removed per OEP-0042). EnrollmentCrossDomainSessionAuth retained because the
    # endpoint must accept cross-domain Studio/LMS CSRF-validated session cookies;
    # the platform-default SessionAuthentication would reject those.
    authentication_classes = (
        JwtAuthentication,
        EnrollmentCrossDomainSessionAuth,
    )
    permission_classes = (ApiKeyHeaderPermissionIsAuthenticated,)
    throttle_classes = (EnrollmentUserThrottle,)
    serializer_class = CourseEnrollmentSerializer
    pagination_class = DefaultPagination  # ADR 0032

    def get_serializer_class(self):
        if self.action == "allowed":
            return CourseEnrollmentAllowedSerializer
        return self.serializer_class

    def get_serializer(self, *args, **kwargs):
        return self.get_serializer_class()(*args, **kwargs)

    # ------------------------------------------------------------------
    # list — GET /enrollment/
    # ------------------------------------------------------------------
    @extend_schema(
        summary="List enrollments for a user (paginated)",
        description=(
            "Returns a paginated list of enrollments for the currently logged-in user, or for "
            "the user named by the 'user' query parameter. Staff/admin/api-key access is required "
            "to view another user's enrollments — otherwise the list is filtered to courses the "
            "requester staffs. Supports the ADR 0036 ``?view=minimal`` preset (see parameter "
            "description)."
        ),
        parameters=[_USER_QUERY_PARAM, _PAGE_QUERY_PARAM, _PAGE_SIZE_QUERY_PARAM, _VIEW_QUERY_PARAM],
        responses={
            200: OpenApiResponse(
                response=CourseEnrollmentSerializer(many=True),
                description=(
                    "Paginated enrollment list. The schema below is the full "
                    "default shape; when ``?view=minimal`` is supplied each "
                    "enrollment's ``course_details`` is collapsed to a single "
                    "``course_id`` string (ADR 0036)."
                ),
            ),
            401: _RESP_UNAUTHENTICATED,
        },
    )
    @method_decorator(ensure_csrf_cookie_cross_domain)
    def list(self, request):
        """
        List enrollments for the currently logged-in user (paginated).

        ADR 0036 — when ``?view=minimal`` is supplied, each enrollment's embedded
        ``course_details`` sub-object is collapsed to a single ``course_id``
        string; ``course_modes`` and the other heavy course-detail fields are
        dropped. Default response shape is unchanged for backwards compatibility.
        """
        username = request.GET.get("user", request.user.username)
        enrollments = _OPS.list_enrollments_for_user(
            request_user=request.user,
            target_username=username,
            has_api_key=self.has_api_key_permissions(request),
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(enrollments, request, view=self)
        data = self.get_serializer(page, many=True).data
        if _is_minimal_view_requested(request):
            data = [_to_minimal_enrollment(item) for item in data]
        return paginator.get_paginated_response(data)

    # ------------------------------------------------------------------
    # create — POST /enrollment/
    # ------------------------------------------------------------------
    @extend_schema(
        summary="Create or update an enrollment",
        description=(
            "Enrolls a user in a course. Server-to-server calls may deactivate or modify the "
            "mode of existing enrollments; all other requests create or reactivate enrollments. "
            "The request body must include course_details.course_id."
        ),
        request=OpenApiRequest(request=CourseEnrollmentSerializer),
        responses={
            200: OpenApiResponse(
                response=CourseEnrollmentSerializer,
                description="Enrollment created, reactivated, or updated successfully.",
            ),
            400: _RESP_BAD_REQUEST,
            403: _RESP_FORBIDDEN,
            404: _RESP_NOT_FOUND,
        },
    )
    @method_decorator(ensure_csrf_cookie_cross_domain)
    def create(self, request):
        """Enroll a user in a course (or update an existing enrollment)."""
        course_id = request.data.get("course_details", {}).get("course_id")
        if not course_id:
            raise ValidationError("Course ID must be specified to create a new enrollment.")
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError as exc:
            raise ValidationError(f"No course '{course_id}' found for enrollment") from exc
        return Response(_OPS.create_or_update_enrollment(
            request=request,
            has_api_key=self.has_api_key_permissions(request),
            course_id=course_key,
        ))

    # ------------------------------------------------------------------
    # unenroll — @action POST /enrollment/unenroll/
    # ------------------------------------------------------------------
    @extend_schema(
        summary="Unenroll a user from all courses (retirement)",
        description=(
            "Privileged retirement-pipeline use only. Unenrolls the named user from every active "
            "enrollment. The request must be made by a service user with CanRetireUser permission, "
            "not the user being unenrolled."
        ),
        request=OpenApiRequest(
            request={"type": "object", "properties": {"username": {"type": "string"}}, "required": ["username"]},
        ),
        responses={
            200: OpenApiResponse(description="List of courses from which the user was unenrolled."),
            204: OpenApiResponse(description="User has no active enrollments."),
            400: _RESP_BAD_REQUEST,
            404: _RESP_NOT_FOUND,
        },
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="unenroll",
        permission_classes=[permissions.IsAuthenticated, CanRetireUser],
    )
    def unenroll(self, request):
        """Unenroll the specified user from all courses (retirement pipeline)."""
        result = _OPS.unenroll_user_for_retirement(request.data.get("username"))
        if result is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(result)

    # ------------------------------------------------------------------
    # allowed — @action GET/POST/DELETE /enrollment/enrollment_allowed/
    # ------------------------------------------------------------------
    @extend_schema(
        summary="Manage CourseEnrollmentAllowed records (admin-only)",
        description=(
            "GET lists allowed enrollments for an email; POST creates a new one; DELETE removes "
            "an existing one by email + course_id. Admin-only."
        ),
        request=OpenApiRequest(request=CourseEnrollmentAllowedSerializer),
        parameters=[_query_param("email", "Email to query (GET only). Defaults to the requester's email.")],
        responses={
            200: OpenApiResponse(
                response=CourseEnrollmentAllowedSerializer(many=True),
                description="GET success — list of allowed enrollments for the email.",
            ),
            201: OpenApiResponse(
                response=CourseEnrollmentAllowedSerializer,
                description="POST success — allowed enrollment created.",
            ),
            204: OpenApiResponse(description="DELETE success — allowed enrollment deleted."),
            400: _RESP_BAD_REQUEST,
            403: _RESP_FORBIDDEN,
            404: OpenApiResponse(description="DELETE: allowed enrollment not found."),
            409: OpenApiResponse(description="POST: allowed enrollment already exists."),
        },
    )
    @action(
        detail=False,
        methods=["get", "post", "delete"],
        url_path="enrollment_allowed",
        permission_classes=[permissions.IsAdminUser],
        throttle_classes=[EnrollmentUserThrottle],
    )
    def allowed(self, request):
        """Retrieve, create, or delete CourseEnrollmentAllowed records. Admin-only."""
        if request.method == "GET":
            user_email = request.query_params.get("email") or request.user.email
            enrollments_allowed = _OPS.list_allowed_for_email(user_email)
            return Response(
                status=status.HTTP_200_OK,
                data=self.get_serializer(enrollments_allowed, many=True).data,
            )

        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        if request.method == "POST":
            enrollment_allowed = _OPS.create_allowed_enrollment(serializer)
            return Response(
                status=status.HTTP_201_CREATED,
                data=self.get_serializer(enrollment_allowed).data,
            )

        # DELETE
        _OPS.delete_allowed_enrollment(
            email=serializer.validated_data.get("email"),
            course_id=serializer.validated_data.get("course_id"),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# EnrollmentRetrieveView — singleton GET /enrollment/{course_id}
# ===========================================================================
# Kept as a standalone APIView because the {username},{course_id} URL form
# (comma-separated, both optional) is not expressible via DefaultRouter.
@extend_schema(tags=["openedx-platform-sdk"])
class EnrollmentRetrieveView(StandardizedErrorMixin, ApiKeyPermissionMixIn, APIView):
    """GET enrollment for a course (and optionally a named user)."""

    # ADR 0034 — JWT + cross-domain session (BearerAuthenticationAllowInactiveUser
    # removed per OEP-0042). EnrollmentCrossDomainSessionAuth retained because the
    # endpoint must accept cross-domain Studio/LMS CSRF-validated session cookies;
    # the platform-default SessionAuthentication would reject those.
    authentication_classes = (
        JwtAuthentication,
        EnrollmentCrossDomainSessionAuth,
    )
    permission_classes = (ApiKeyHeaderPermissionIsAuthenticated,)
    throttle_classes = (EnrollmentUserThrottle,)
    serializer_class = CourseEnrollmentSerializer

    @extend_schema(
        summary="Retrieve a user's enrollment in a course",
        description=(
            "Returns the current user's enrollment for the specified course, or the named user's "
            "enrollment when invoked with the {username},{course_id} URL form (server-to-server or "
            "staff only)."
        ),
        parameters=[_USERNAME_PATH_PARAM, _COURSE_ID_PATH_PARAM],
        responses={
            200: OpenApiResponse(
                response=CourseEnrollmentSerializer,
                description="Enrollment retrieved successfully (or empty body if no enrollment).",
            ),
            400: _RESP_BAD_REQUEST,
            404: _RESP_NOT_FOUND,
        },
    )
    @method_decorator(ensure_csrf_cookie_cross_domain)
    def get(self, request, course_id=None, username=None):
        """
        Return the enrollment for ``(username, course_id)``.

        When ``username`` is omitted (the ``GET /enrollment/{course_id}``
        URL form), the request user is used. Non-staff callers may only
        look up their own enrollment; any cross-user lookup without
        ``has_api_key`` or staff privileges raises ``NotFound`` (so the
        caller cannot probe for the existence of other users' enrollments).
        """
        if username is None:
            username = request.user.username

        if (
            username != request.user.username
            and not self.has_api_key_permissions(request)
            and not request.user.is_staff
        ):
            # Hide existence of other users' enrollments.
            raise NotFound()

        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError as exc:
            raise ValidationError(f"No course '{course_id}' found for enrollment") from exc

        try:
            enrollment = CourseEnrollment.objects.get(user__username=username, course_id=course_key)
        except CourseEnrollment.DoesNotExist:
            return Response(None)
        except CourseEnrollmentError as exc:
            raise ValidationError(
                f"An error occurred while retrieving enrollments for user "
                f"'{username}' in course '{course_id}'"
            ) from exc

        data = self.serializer_class(enrollment).data
        if _is_minimal_view_requested(request):
            data = _to_minimal_enrollment(data)
        return Response(data)


# ===========================================================================
# UserRolesView — GET /roles/  (singleton list endpoint for the current user)
# ===========================================================================
@extend_schema(tags=["openedx-platform-sdk"])
class UserRolesView(StandardizedErrorMixin, APIView):
    """List the current user's course-level roles."""

    # ADR 0034 — JWT + cross-domain session (BearerAuthenticationAllowInactiveUser
    # removed per OEP-0042). EnrollmentCrossDomainSessionAuth retained because the
    # endpoint must accept cross-domain Studio/LMS CSRF-validated session cookies;
    # the platform-default SessionAuthentication would reject those.
    authentication_classes = (
        JwtAuthentication,
        EnrollmentCrossDomainSessionAuth,
    )
    permission_classes = (ApiKeyHeaderPermissionIsAuthenticated,)
    throttle_classes = (EnrollmentUserThrottle,)
    serializer_class = UserRolesResponseSerializer

    # ADR 0033: ``course_key`` is the preferred filter name (OEP-68);
    # ``course_id`` is retained as a deprecated alias.
    _LEGACY_PARAM_ALIASES = (("course_id", "course_key"),)

    @extend_schema(
        summary="List the current user's course roles",
        description=(
            "Returns the list of course-level roles held by the currently logged-in user, plus "
            "an is_staff flag. Optionally filters by course_key (or course_id, deprecated)."
        ),
        parameters=[
            _query_param("course_key", "If provided, only roles for this course are returned (OEP-68)."),
            _query_param(
                "course_id", "Deprecated alias for 'course_key' (ADR 0033). Use 'course_key' instead.",
                deprecated=True,
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=UserRolesResponseSerializer,
                description="Roles retrieved successfully.",
            ),
            400: _RESP_BAD_REQUEST,
        },
    )
    @method_decorator(ensure_csrf_cookie_cross_domain)
    def get(self, request):
        """
        List the current user's course-level roles.

        Optionally filtered by ``course_key`` (preferred, OEP-68) or
        ``course_id`` (deprecated alias). When both are present,
        ``course_key`` wins and the response carries the ADR 0033
        ``Deprecation`` HTTP header.
        """
        try:
            course_key = request.GET.get("course_key") or request.GET.get("course_id")
            roles_data = api.get_user_roles(request.user.username)
            if course_key:
                roles_data = [role for role in roles_data if str(role.course_id) == course_key]
        except Exception as exc:  # pylint: disable=broad-except
            raise ValidationError(
                f"An error occurred while retrieving roles for user '{request.user.username}'"
            ) from exc

        serializer = self.serializer_class({
            "roles": list(roles_data),
            "is_staff": request.user.is_staff,
        })
        response = Response(serializer.data)
        return _maybe_set_legacy_param_deprecation_header(
            request, response, self._LEGACY_PARAM_ALIASES,
        )


# ===========================================================================
# CourseEnrollmentDetailView — GET /course/{course_id}  (public, no auth)
# ===========================================================================
@extend_schema(tags=["openedx-platform-sdk"])
class CourseEnrollmentDetailView(StandardizedErrorMixin, APIView):
    """Get enrollment information about a particular course."""

    authentication_classes = ()
    permission_classes = ()
    throttle_classes = (EnrollmentUserThrottle,)
    serializer_class = CourseSerializer

    @extend_schema(
        summary="Get enrollment details for a course",
        description=(
            "Returns the course schedule and supported enrollment modes. No authentication "
            "required. Use ?include_expired=1 to include expired enrollment modes."
        ),
        parameters=[_COURSE_ID_PATH_PARAM, _INCLUDE_EXPIRED_QUERY_PARAM],
        responses={
            200: OpenApiResponse(
                response=CourseSerializer,
                description="Course enrollment details retrieved successfully.",
            ),
            400: _RESP_BAD_REQUEST,
            404: _RESP_NOT_FOUND,
        },
    )
    def get(self, request, course_id=None):
        """
        Return enrollment-related details for the specified course.

        Public (no authentication required). The response includes the
        course schedule and supported enrollment modes; pass
        ``?include_expired=1`` to include expired enrollment modes.
        """
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError as exc:
            raise ValidationError(f"No course found for course ID '{course_id}'") from exc
        try:
            course_overview = CourseOverview.get_from_id(course_key)
        except CourseOverview.DoesNotExist as exc:
            raise NotFound(f"No course found for course ID '{course_id}'") from exc

        include_expired = bool(request.GET.get("include_expired", ""))
        serializer = self.serializer_class(course_overview, include_expired=include_expired)
        return Response(serializer.data)


# ===========================================================================
# EnrollmentsAdminListView — GET /enrollments/  (admin paginated list)
# ===========================================================================
@extend_schema(
    tags=["openedx-platform-sdk"],
    summary="List all course enrollments (admin-only, paginated)",
    description=(
        "Admin-only paginated list of CourseEnrollment records, optionally filtered by "
        "course_key, course_keys, username, or email, and optionally ordered."
    ),
    parameters=[
        _query_param("course_key", "Filter to enrollments for this course (OEP-68)."),
        _query_param("course_keys", "Comma-separated list of course keys (OEP-68)."),
        _query_param(
            "course_id", "Deprecated alias for 'course_key' (ADR 0033). Use 'course_key' instead.",
            deprecated=True,
        ),
        _query_param(
            "course_ids", "Deprecated alias for 'course_keys' (ADR 0033). Use 'course_keys' instead.",
            deprecated=True,
        ),
        _query_param("username", "Comma-separated list of usernames."),
        _query_param("email", "Comma-separated list of emails."),
        _query_param("ordering", "Order results by one of: created, -created, id, -id (ADR 0033 §3)."),
        _PAGE_QUERY_PARAM,
        _PAGE_SIZE_QUERY_PARAM,
    ],
    responses={
        200: OpenApiResponse(
            response=CourseEnrollmentsApiListSerializer(many=True),
            description="Paginated list of course enrollments.",
        ),
        400: _RESP_BAD_REQUEST,
        401: _RESP_UNAUTHENTICATED,
        403: _RESP_FORBIDDEN,
    },
)
class EnrollmentsAdminListView(StandardizedErrorMixin, ListAPIView):
    """Admin-only paginated enrollment list with OEP-68 filter aliases."""

    # ADR 0034 — JWT + cross-domain session (BearerAuthenticationAllowInactiveUser
    # removed per OEP-0042). EnrollmentCrossDomainSessionAuth retained because the
    # endpoint must accept cross-domain Studio/LMS CSRF-validated session cookies;
    # the platform-default SessionAuthentication would reject those.
    authentication_classes = (
        JwtAuthentication,
        EnrollmentCrossDomainSessionAuth,
    )
    permission_classes = (permissions.IsAdminUser,)
    throttle_classes = (EnrollmentUserThrottle,)
    serializer_class = CourseEnrollmentsApiListSerializer
    pagination_class = EnrollmentsAdminListPagination

    # ADR 0033 §3 — whitelist of allowed values for the ``ordering`` param.
    ALLOWED_ORDERING_FIELDS = frozenset({"created", "-created", "id", "-id"})

    # ADR 0033 §2 / OEP-68 alias pairs accepted by this endpoint.
    _LEGACY_PARAM_ALIASES = (
        ("course_id", "course_key"),
        ("course_ids", "course_keys"),
    )

    def get_queryset(self):
        form = EnrollmentsAdminListForm(self.request.query_params)
        if not form.is_valid():
            raise ValidationError(form.errors)

        queryset = CourseEnrollment.objects.all().select_related("user", "course")
        course_id = form.cleaned_data.get("course_id")
        course_ids = form.cleaned_data.get("course_ids")
        usernames = form.cleaned_data.get("username")
        emails = form.cleaned_data.get("email")

        if course_id:
            queryset = queryset.filter(course_id=course_id)
        if course_ids:
            queryset = queryset.filter(course_id__in=course_ids)
        if usernames:
            queryset = queryset.filter(user__username__in=usernames)
        if emails:
            queryset = queryset.filter(user__email__in=emails)

        ordering = self.request.query_params.get("ordering")
        if ordering in self.ALLOWED_ORDERING_FIELDS:
            queryset = queryset.order_by(ordering)
        return queryset

    def list(self, request, *args, **kwargs):
        """Override to emit the ADR 0033 Deprecation header when legacy params used."""
        response = super().list(request, *args, **kwargs)
        return _maybe_set_legacy_param_deprecation_header(
            request, response, self._LEGACY_PARAM_ALIASES,
        )
