"""
API Views for course grading settings — v3.

This module is the v3 incarnation of the v0 ``AuthoringGradingView`` endpoint,
restructured to apply the FC-0118 ADRs from the start:

  * ADR 0025 – ``serializer_class`` on the viewset
  * ADR 0026 – explicit ``authentication_classes`` + ``permission_classes``
  * ADR 0027 – ``drf_spectacular`` for OpenAPI schema generation
  * ADR 0028 – consolidated into a single DRF ``ViewSet`` registered via
    ``DefaultRouter`` (replaces ``AuthoringGradingView`` ``APIView``)
  * ADR 0029 – standardized error envelope via :class:`StandardizedErrorMixin`
    (v3-scoped — does not change the project-wide DRF ``EXCEPTION_HANDLER``
    setting)
  * ADR 0033 / OEP-68 – the URL kwarg, action parameter, and OpenAPI parameter
    are named ``course_key`` (the OEP-68-standardized name) rather than the
    legacy ``course_id``. Since this is a brand-new versioned API, no
    deprecated alias is needed — clients on the v0 endpoint continue to use
    ``course_id`` there.
  * ADR 0036 – **largely out of scope.** The ``CourseGradingModelSerializer``
    response is a single top-level ``graders`` list of small fixed-shape
    objects (type, min_count, drop_count, short_label, weight, id) — no
    tree nesting, no embedded sub-objects, no ``children`` field, no wide
    flat object that would benefit from ``?view=minimal`` / ``?fields=``.

    The one ADR 0036 concern is anti-pattern #3 (unbounded child list): the
    ``graders`` array has no upper bound in the serializer. In practice each
    course has typically ≤8 graders (Homework, Lab, Exam, etc.) and the
    update flow is exercised only by course-authoring staff, so the
    real-world payload is always small. A hard cap is enforced upstream of
    this endpoint by :func:`CourseGradingModel.update_from_json`; we surface
    that as a documentation note rather than re-implement the bound here.
  * ADR 0034 – auth standardization (OEP-0042).
    ``authentication_classes`` is ``(JwtAuthentication, SessionAuthenticationAllowInactiveUser)``;
    ``BearerAuthenticationAllowInactiveUser`` has been removed per the
    deprecation policy. ``SessionAuthenticationAllowInactiveUser`` is
    retained (rather than relying on the platform-default
    ``SessionAuthentication``) so Studio authors whose accounts are
    temporarily inactive can still update grading.

Permission model note:
    PR #38363 proposed a class-level ``HasStudioReadAccess`` permission. The
    current v0 view has since evolved to use the ``openedx_authz`` permission
    framework (``COURSES_EDIT_GRADING_SETTINGS``), which is more specific to
    grading and aligns with the platform-wide authz direction.

    The v3 viewset preserves the openedx_authz model via an *inline*
    ``user_has_course_permission`` check inside the action body (rather than
    the ``@authz_permission_required`` decorator). The decorator raises
    ``DeveloperErrorResponseException`` — a plain ``Exception`` subclass that
    does not flow through DRF's exception handler, so it would bypass
    :class:`StandardizedErrorMixin` and surface as an unstructured 500.
    Raising ``rest_framework.exceptions.PermissionDenied`` directly keeps the
    ADR 0029 envelope intact.
"""

from drf_spectacular.utils import OpenApiParameter, OpenApiRequest, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from openedx_authz.constants.permissions import COURSES_EDIT_GRADING_SETTINGS
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v0.serializers import CourseGradingModelSerializer
from cms.djangoapps.contentstore.rest_api.v3.utils import COMMON_ERROR_RESPONSES, resolve_course_key
from cms.djangoapps.models.settings.course_grading import CourseGradingModel
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import user_has_course_permission
from openedx.core.djangoapps.credit.tasks import update_credit_course_requirements
from openedx.core.lib.api.mixins import StandardizedErrorMixin

_COURSE_KEY_PARAMETER = OpenApiParameter(
    name="course_key",
    description="OEP-68 course key (e.g. course-v1:org+course+run).",
    required=True,
    type=str,
    location=OpenApiParameter.PATH,
)


@extend_schema(tags=["openedx-platform-sdk"])
class AuthoringGradingViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    ViewSet for course grading settings (v3). Registered via DefaultRouter
    (basename ``authoring_grading``).

    Router-generated URL::

        PATCH /api/contentstore/v3/authoring_grading/{course_key}/  → partial_update

    Supersedes ``AuthoringGradingView`` at ``POST /api/contentstore/v0/grading/{course_id}``.
    """

    # ADR 0034 — JWT + session-with-inactive-user (BearerAuthenticationAllowInactiveUser
    # removed per OEP-0042). SessionAuthenticationAllowInactiveUser is retained
    # (instead of relying on the platform-default SessionAuthentication) so Studio
    # authors whose accounts are temporarily inactive can still update grading.
    authentication_classes = (
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated,)
    serializer_class = CourseGradingModelSerializer

    # DefaultRouter lookup: matches course-v1:org+course+run (+ or / separators).
    # OEP-68: the kwarg name is ``course_key`` (not the legacy ``course_id``).
    lookup_field = "course_key"
    lookup_value_regex = r"[^/+]+(?:/|\+)[^/+]+(?:/|\+)[^/?]+"

    def get_serializer(self, *args, **kwargs):
        """Instantiate and return the configured serializer class."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="Update a course's grading settings",
        description="Partially update the grading settings for the specified course.",
        request=OpenApiRequest(request=CourseGradingModelSerializer),
        parameters=[_COURSE_KEY_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=CourseGradingModelSerializer,
                description="Grading settings updated successfully.",
            ),
            **COMMON_ERROR_RESPONSES,
        },
    )
    def partial_update(self, request: Request, course_key: str):
        """
        Update a course's grading settings.

        **Example Request**

            PATCH /api/contentstore/v3/authoring_grading/{course_key}/

        **PATCH Parameters**

        The request body should follow the ``CourseGradingModelSerializer``
        schema. Example::

            {
                "graders": [
                    {
                        "type": "Homework",
                        "min_count": 1,
                        "drop_count": 0,
                        "short_label": "",
                        "weight": 100,
                        "id": 0
                    }
                ],
                "grade_cutoffs": {"A": 0.75, "B": 0.63, "C": 0.57, "D": 0.5},
                "grace_period": {"hours": 12, "minutes": 0},
                "minimum_grade_credit": 0.7,
                "is_credit_course": true
            }

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned
        with the updated grading data serialized via
        :class:`CourseGradingModelSerializer`.
        """
        parsed_course_key = resolve_course_key(course_key)

        # Per-action authorization (ADR 0026): kept inline rather than
        # behind ``@authz_permission_required`` because that decorator
        # raises ``DeveloperErrorResponseException`` (not a DRF exception),
        # which bypasses :class:`StandardizedErrorMixin`. Raising
        # ``PermissionDenied`` directly flows through the ADR 0029 envelope.
        if not user_has_course_permission(
            request.user,
            COURSES_EDIT_GRADING_SETTINGS.identifier,
            parsed_course_key,
            LegacyAuthoringPermission.READ,
        ):
            raise PermissionDenied("You do not have permission to perform this action.")

        if "minimum_grade_credit" in request.data:
            update_credit_course_requirements.delay(str(parsed_course_key))

        updated_data = CourseGradingModel.update_from_json(parsed_course_key, request.data, request.user)
        serializer = self.get_serializer(updated_data)
        return Response(serializer.data)
