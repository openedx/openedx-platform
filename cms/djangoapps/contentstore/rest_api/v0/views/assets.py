"""
Public rest API endpoints for the CMS API Assets.
"""
import logging

from django.views.decorators.csrf import csrf_exempt
from rest_framework.generics import CreateAPIView, DestroyAPIView, RetrieveAPIView, UpdateAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser

from cms.djangoapps.contentstore.api import course_author_access_required
from cms.djangoapps.contentstore.asset_storage_handlers import handle_assets
from common.djangoapps.util.json_request import expect_json_in_class_view
from openedx.core.lib.api.parsers import TypedFileUploadParser
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, view_auth_classes

from ..serializers.assets import AssetSerializer
from .utils import validate_request_with_serializer

log = logging.getLogger(__name__)


@view_auth_classes()
class AssetsCreateRetrieveView(DeveloperErrorViewMixin, CreateAPIView, RetrieveAPIView):
    """
    public rest API endpoints for the CMS API Assets.
    course_key: required argument, needed to authorize course authors and identify the asset.
    asset_key_string: required argument, needed to identify the asset.
    """
    serializer_class = AssetSerializer
    parser_classes = (JSONParser, MultiPartParser, FormParser, TypedFileUploadParser)

    @csrf_exempt
    @course_author_access_required
    @validate_request_with_serializer
    def create(self, request, course_key):  # pylint: disable=arguments-differ
        return handle_assets(request, course_key.html_id())

    @course_author_access_required
    @expect_json_in_class_view
    def retrieve(self, request, course_key):  # pylint: disable=arguments-differ
        return handle_assets(request, course_key.html_id())


@view_auth_classes()
class AssetsUpdateDestroyView(DeveloperErrorViewMixin, UpdateAPIView, DestroyAPIView):
    """
    public rest API endpoints for the CMS API Assets.
    course_key: required argument, needed to authorize course authors and identify the asset.
    asset_key_string: required argument, needed to identify the asset.
    """
    serializer_class = AssetSerializer
    parser_classes = (JSONParser, MultiPartParser, FormParser, TypedFileUploadParser)

    @course_author_access_required
    @expect_json_in_class_view
    @validate_request_with_serializer
    def update(self, request, course_key, asset_key_string):  # pylint: disable=arguments-differ
        return handle_assets(request, course_key.html_id(), asset_key_string)

    @course_author_access_required
    @expect_json_in_class_view
    def destroy(self, request, course_key, asset_key_string):  # pylint: disable=arguments-differ
        return handle_assets(request, course_key.html_id(), asset_key_string)
