"""
API Views for Studio xblock CRUD — v1.

Standardizes the v0 XblockView + XblockCreateView pair into a single
XblockViewSet applying the FC-0118 ADRs:

  * ADR 0025 - serializer_class
  * ADR 0026 - explicit authentication_classes + permission_classes
  * ADR 0028 - consolidated into XblockViewSet via DefaultRouter
  * ADR 0029 - standardized error envelope via StandardizedErrorMixin
  * ADR 0034 - already compliant. ``authentication_classes`` is
    ``(JwtAuthentication, SessionAuthenticationAllowInactiveUser)`` — no
    ``BearerAuthentication`` / ``OAuth2Authentication`` to remove. This view
    is set explicitly (rather than relying on platform defaults) because it
    needs ``SessionAuthenticationAllowInactiveUser`` instead of the default
    ``SessionAuthentication`` so inactive Studio authors can still hit the
    endpoint while their session is being verified.
  * ADR 0036 - minimal/flattened views. ``retrieve`` accepts a ``?view=minimal``
    query parameter that strips the (tree-shaped) xblock response to a small
    set of structural fields. The full xblock response is kept as the default
    for backwards compatibility with the existing Studio frontend; new clients
    SHOULD opt into ``?view=minimal`` whenever the full nested payload is not
    required.

    Note on ``?fields=`` — the underlying ``retrieve_xblock_response`` already
    interprets ``?fields=`` with **legacy semantics** as a "type of response"
    selector (``?fields=graderType``, ``?fields=ancestorInfo``,
    ``?fields=customReadToken``). To avoid breaking existing callers, v1 does
    NOT repurpose ``?fields=`` as the ADR 0036 CSV-subset selector — use
    ``?view=minimal`` instead. A future v2 may reconcile these names.
"""
import json
import logging

from django.http import JsonResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiRequest, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from cms.djangoapps.contentstore.rest_api.v0.serializers import XblockSerializer
from cms.djangoapps.contentstore.rest_api.v0.views.utils import validate_request_with_serializer
from cms.djangoapps.contentstore.rest_api.v1.views.permissions import HasCourseAuthorAccess
from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import (
    create_xblock_response,
    delete_xblock_response,
    retrieve_xblock_response,
    update_xblock_response,
)
from common.djangoapps.util.json_request import expect_json_in_class_view
from openedx.core.lib.api.mixins import StandardizedErrorMixin

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ADR 0027 — shared OpenAPI parameter and response building blocks
# ---------------------------------------------------------------------------
_USAGE_KEY_PATH_PARAMETER = OpenApiParameter(
    name="usage_key_string",
    description=(
        "Usage key identifying the xblock (e.g. "
        "``block-v1:edX+DemoX+Demo_Course+type@vertical+block@abcd``). Also "
        "accepts legacy ``i4x://`` locators."
    ),
    required=True,
    type=str,
    location=OpenApiParameter.PATH,
)

# ADR 0036 — declare the ``?view=minimal`` preset in the OpenAPI schema so
# consumers (Swagger UI, generated SDK clients) can discover it. Only the
# ``retrieve`` action honours this parameter today.
_VIEW_QUERY_PARAMETER = OpenApiParameter(
    name="view",
    description=(
        "ADR 0036 response preset. ``minimal`` drops heavy/contextual xblock "
        "fields (``data``, ``metadata``, ``fields``, ``student_view_data``, "
        "``edited_on``, ``published`` …) and keeps only the structural fields "
        "(``id``, ``display_name``, ``category``, ``children``, "
        "``has_children``, ``studio_url``). Omit the parameter to receive "
        "the full xblock response."
    ),
    required=False,
    type=str,
    location=OpenApiParameter.QUERY,
    enum=["minimal"],
)

# ADR 0036 — the underlying ``retrieve_xblock_response`` accepts a legacy
# ``?fields=`` selector with **type-of-response** semantics (not the ADR 0036
# CSV-subset semantics). Documented here as a deprecated parameter so callers
# can see it in Swagger UI and know it's a legacy pass-through.
_LEGACY_FIELDS_QUERY_PARAMETER = OpenApiParameter(
    name="fields",
    description=(
        "**Legacy pass-through** (v0/v1 semantics). Selects a *type* of "
        "response rather than a subset of top-level keys:\n"
        "  * ``fields=graderType`` — return the grader-type value directly\n"
        "  * ``fields=ancestorInfo`` — return concise ancestor info\n"
        "  * ``fields=customReadToken`` — include parent + children on the "
        "response\n"
        "Note: this is **not** the ADR 0036 ``?fields=`` CSV subset "
        "convention. New callers should use ``?view=minimal`` instead."
    ),
    required=False,
    type=str,
    location=OpenApiParameter.QUERY,
    deprecated=True,
)

_COMMON_ERROR_RESPONSES = {
    401: OpenApiResponse(description="The requester is not authenticated."),
    403: OpenApiResponse(description="The requester does not have permission for this xblock's course."),
    404: OpenApiResponse(description="The requested xblock does not exist."),
    406: OpenApiResponse(description="Requested representation is not available (e.g. non-JSON ``Accept``)."),
}


# ADR 0036 — top-level keys kept when ``?view=minimal`` is requested. Chosen so
# the response is structurally complete (callers can navigate the tree by id
# and fetch full nodes on demand) without any heavy/contextual fields
# (student_view_data, completion, OLX metadata, etc.).
_MINIMAL_VIEW_FIELDS = frozenset({
    "id",
    "display_name",
    "category",
    "children",
    "has_children",
    "studio_url",
})


def _apply_minimal_view(response):
    """
    ADR 0036 — when ``?view=minimal`` was requested, drop every top-level key
    not in :data:`_MINIMAL_VIEW_FIELDS` from ``response``. No-op for non-JSON
    or non-2xx responses.
    """
    if not isinstance(response, JsonResponse) or response.status_code >= 300:
        return response
    try:
        body = json.loads(response.content.decode("utf-8") or "{}")
    except (ValueError, AttributeError):
        return response
    if not isinstance(body, dict):
        # If the handler returned a non-object payload (e.g. `?fields=graderType`
        # which returns the grader-type value directly), there's nothing to
        # filter — return the response untouched.
        return response
    return JsonResponse({k: v for k, v in body.items() if k in _MINIMAL_VIEW_FIELDS})


@extend_schema(tags=["openedx-platform-sdk"])
class XblockViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    ViewSet for xblock CRUD operations (v1 — ADR 0028).

    Router-generated URLs:
      POST   /api/contentstore/v1/xblock/                      → create
      GET    /api/contentstore/v1/xblock/{usage_key_string}/   → retrieve
      PUT    /api/contentstore/v1/xblock/{usage_key_string}/   → update
      PATCH  /api/contentstore/v1/xblock/{usage_key_string}/   → partial_update
      DELETE /api/contentstore/v1/xblock/{usage_key_string}/   → destroy

    Query parameters (ADR 0036, GET only):
      ?view=minimal   Drop heavy / contextual fields from the response,
                      keeping only structural fields (id, display_name,
                      category, children, has_children, studio_url).
                      Default response is the full xblock payload.
    """

    authentication_classes = (
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated, HasCourseAuthorAccess)
    serializer_class = XblockSerializer
    lookup_field = "usage_key_string"
    lookup_value_regex = r'(?:i4x://?[^/]+/[^/]+/[^/]+/[^@]+(?:@[^/]+)?)|(?:[^/]+)'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.course_key = None

    def initial(self, request, *args, **kwargs):
        """
        Derive course_key and store it as self.course_key before DRF runs
        permission checks.

        Detail actions (GET/PUT/PATCH/DELETE): course_key is extracted from
        the usage key embedded in the URL.
        Create action (POST): course_key is extracted from parent_locator in
        the raw request body. We read request._request.body (Django's cached
        bytes) rather than request.data to avoid consuming the WSGI stream
        before @expect_json_in_class_view runs.
        """
        usage_key_string = kwargs.get("usage_key_string")
        if usage_key_string:
            try:
                self.course_key = UsageKey.from_string(usage_key_string).course_key
            except InvalidKeyError:
                self.course_key = None
        else:
            try:
                # pylint: disable=protected-access
                body = json.loads(request._request.body or b'{}')
                parent_locator = body.get("parent_locator", "")
                self.course_key = (
                    UsageKey.from_string(parent_locator).course_key
                    if parent_locator else None
                )
            except (ValueError, InvalidKeyError):
                self.course_key = None
        super().initial(request, *args, **kwargs)

    @extend_schema(
        summary="Create an xblock",
        description=(
            "Create a new xblock under a parent block. The ``parent_locator`` "
            "field on the request body identifies the parent and (implicitly) "
            "the course."
        ),
        request=OpenApiRequest(XblockSerializer),
        responses={
            200: OpenApiResponse(
                response=XblockSerializer,
                description="The xblock was created successfully.",
            ),
            400: OpenApiResponse(description="Request body failed validation."),
            **_COMMON_ERROR_RESPONSES,
        },
    )
    @expect_json_in_class_view
    @validate_request_with_serializer
    def create(self, request):
        """Create a new xblock under the given parent."""
        return create_xblock_response(request)

    @extend_schema(
        summary="Retrieve an xblock",
        description=(
            "Retrieve an xblock (and, by default, its nested tree) by usage "
            "key. Supports ADR 0036 ``?view=minimal`` to strip contextual "
            "fields, plus the legacy ``?fields=`` type-of-response selector."
        ),
        parameters=[
            _USAGE_KEY_PATH_PARAMETER,
            _VIEW_QUERY_PARAMETER,
            _LEGACY_FIELDS_QUERY_PARAMETER,
        ],
        responses={
            200: OpenApiResponse(
                response=XblockSerializer,
                description="The xblock representation.",
            ),
            **_COMMON_ERROR_RESPONSES,
        },
    )
    @expect_json_in_class_view
    def retrieve(self, request, usage_key_string=None):
        """
        Retrieve an xblock by its usage key.

        ADR 0036 — honours ``?view=minimal``; everything else is delegated to
        ``retrieve_xblock_response`` (which keeps its legacy ``?fields=`` /
        ``?fields=ancestorInfo`` / ``?fields=customReadToken`` semantics).
        """
        response = retrieve_xblock_response(request, usage_key_string)
        if request.GET.get("view") == "minimal":
            response = _apply_minimal_view(response)
        return response

    @extend_schema(
        summary="Update an xblock",
        description="Fully update an xblock identified by its usage key.",
        parameters=[_USAGE_KEY_PATH_PARAMETER],
        request=OpenApiRequest(XblockSerializer),
        responses={
            200: OpenApiResponse(
                response=XblockSerializer,
                description="The xblock was updated successfully.",
            ),
            400: OpenApiResponse(description="Request body failed validation."),
            **_COMMON_ERROR_RESPONSES,
        },
    )
    @expect_json_in_class_view
    @validate_request_with_serializer
    def update(self, request, usage_key_string=None):
        """Fully update an xblock."""
        return update_xblock_response(request, usage_key_string)

    @extend_schema(
        summary="Partially update an xblock",
        description=(
            "Partially update an xblock identified by its usage key. Only the "
            "fields present in the request body are updated."
        ),
        parameters=[_USAGE_KEY_PATH_PARAMETER],
        request=OpenApiRequest(XblockSerializer),
        responses={
            200: OpenApiResponse(
                response=XblockSerializer,
                description="The xblock was updated successfully.",
            ),
            400: OpenApiResponse(description="Request body failed validation."),
            **_COMMON_ERROR_RESPONSES,
        },
    )
    @expect_json_in_class_view
    @validate_request_with_serializer
    def partial_update(self, request, usage_key_string=None):
        """Partially update an xblock."""
        return update_xblock_response(request, usage_key_string)

    @extend_schema(
        summary="Delete an xblock",
        description="Delete an xblock identified by its usage key.",
        parameters=[_USAGE_KEY_PATH_PARAMETER],
        responses={
            200: OpenApiResponse(description="The xblock was deleted successfully."),
            **_COMMON_ERROR_RESPONSES,
        },
    )
    @expect_json_in_class_view
    def destroy(self, request, usage_key_string=None):
        """Delete an xblock."""
        return delete_xblock_response(request, usage_key_string)
