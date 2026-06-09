"""
API Views for Studio xblock CRUD — v1.

Standardizes the v0 XblockView + XblockCreateView pair into a single
XblockViewSet applying the FC-0118 ADRs:

  * ADR 0025 - serializer_class
  * ADR 0026 - explicit authentication_classes + permission_classes
  * ADR 0028 - consolidated into XblockViewSet via DefaultRouter
  * ADR 0029 - standardized error envelope via StandardizedErrorMixin
"""
import json
import logging

from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from cms.djangoapps.contentstore.rest_api.mixins import StandardizedErrorMixin
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

log = logging.getLogger(__name__)


class XblockViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    ViewSet for xblock CRUD operations (v1 — ADR 0028).

    Router-generated URLs:
      POST   /api/contentstore/v1/xblock/                      → create
      GET    /api/contentstore/v1/xblock/{usage_key_string}/   → retrieve
      PUT    /api/contentstore/v1/xblock/{usage_key_string}/   → update
      PATCH  /api/contentstore/v1/xblock/{usage_key_string}/   → partial_update
      DELETE /api/contentstore/v1/xblock/{usage_key_string}/   → destroy
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

    @expect_json_in_class_view
    @validate_request_with_serializer
    def create(self, request):
        """Create a new xblock under the given parent."""
        return create_xblock_response(request)

    @expect_json_in_class_view
    def retrieve(self, request, usage_key_string=None):
        """Retrieve an xblock by its usage key."""
        return retrieve_xblock_response(request, usage_key_string)

    @expect_json_in_class_view
    @validate_request_with_serializer
    def update(self, request, usage_key_string=None):
        """Fully update an xblock."""
        return update_xblock_response(request, usage_key_string)

    @expect_json_in_class_view
    @validate_request_with_serializer
    def partial_update(self, request, usage_key_string=None):
        """Partially update an xblock."""
        return update_xblock_response(request, usage_key_string)

    @expect_json_in_class_view
    def destroy(self, request, usage_key_string=None):
        """Delete an xblock."""
        return delete_xblock_response(request, usage_key_string)
