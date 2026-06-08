"""
Public rest API endpoints for the CMS API — v0 xblock (DEPRECATED).

.. deprecated::
    These views are superseded by ``XblockViewSet`` in
    ``cms.djangoapps.contentstore.rest_api.v1.views.xblock``.
    Use ``/api/contentstore/v1/xblock/`` going forward.
    These v0 endpoints will be removed in a future release.
"""
import logging
import warnings

from django.views.decorators.csrf import csrf_exempt
from rest_framework.generics import CreateAPIView, RetrieveUpdateDestroyAPIView

from cms.djangoapps.contentstore.api import course_author_access_required
from cms.djangoapps.contentstore.xblock_storage_handlers import view_handlers
from common.djangoapps.util.json_request import expect_json_in_class_view
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, view_auth_classes

from ..serializers import XblockSerializer
from .utils import validate_request_with_serializer

log = logging.getLogger(__name__)
handle_xblock = view_handlers.handle_xblock

_DEPRECATION_MSG = (
    "The v0 xblock API (/api/contentstore/v0/xblock/) is deprecated. "
    "Use /api/contentstore/v1/xblock/ instead."
)


@view_auth_classes()
class XblockView(DeveloperErrorViewMixin, RetrieveUpdateDestroyAPIView):
    """
    **DEPRECATED** — use ``/api/contentstore/v1/xblock/{usage_key_string}/`` instead.

    Public rest API endpoints for the CMS API.
    course_key: required argument, needed to authorize course authors.
    usage_key_string (optional):
    xblock identifier, for example in the form of "block-v1:<course id>+type@<type>+block@<block id>"
    """
    serializer_class = XblockSerializer

    # pylint: disable=arguments-differ
    @course_author_access_required
    @expect_json_in_class_view
    def retrieve(self, request, course_key, usage_key_string=None):
        warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
        return handle_xblock(request, usage_key_string)

    @course_author_access_required
    @expect_json_in_class_view
    @validate_request_with_serializer
    def update(self, request, course_key, usage_key_string=None):
        warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
        return handle_xblock(request, usage_key_string)

    @course_author_access_required
    @expect_json_in_class_view
    @validate_request_with_serializer
    def partial_update(self, request, course_key, usage_key_string=None):
        warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
        return handle_xblock(request, usage_key_string)

    @course_author_access_required
    @expect_json_in_class_view
    def destroy(self, request, course_key, usage_key_string=None):
        warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
        return handle_xblock(request, usage_key_string)


@view_auth_classes()
class XblockCreateView(DeveloperErrorViewMixin, CreateAPIView):
    """
    **DEPRECATED** — use ``POST /api/contentstore/v1/xblock/`` instead.

    Public rest API endpoints for the CMS API.
    course_key: required argument, needed to authorize course authors.
    usage_key_string (optional):
    xblock identifier, for example in the form of "block-v1:<course id>+type@<type>+block@<block id>"
    """
    serializer_class = XblockSerializer

    # pylint: disable=arguments-differ
    @csrf_exempt
    @course_author_access_required
    @expect_json_in_class_view
    @validate_request_with_serializer
    def create(self, request, course_key, usage_key_string=None):
        warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
        return handle_xblock(request, usage_key_string)
