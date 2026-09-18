"""
Public rest API endpoints for the Authoring API video assets.

.. deprecated::
    The video upload endpoints in this module are superseded by
    ``CourseVideoUploadsViewSet`` in
    ``cms.djangoapps.contentstore.rest_api.v1.views.video_uploads``.
    Use ``/api/authoring/v1/courses/{course_key}/videos/`` going forward.
    These v0 endpoints will be removed in a future release.
"""
import logging

from django.views.decorators.csrf import csrf_exempt
from rest_framework.generics import CreateAPIView, DestroyAPIView, RetrieveAPIView
from rest_framework.parsers import FormParser, MultiPartParser

from cms.djangoapps.contentstore.rest_api.v1.serializers import VideoImageSerializer, VideoUploadSerializer
from cms.djangoapps.contentstore.video_storage_handlers import (
    enabled_video_features,
    get_video_encodings_download,
    handle_video_images,
    handle_videos,
)
from common.djangoapps.util.json_request import expect_json_in_class_view
from openedx.core.lib.api.parsers import TypedFileUploadParser
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, view_auth_classes

from ....api import course_author_access_required
from .utils import validate_request_with_serializer

log = logging.getLogger(__name__)


@view_auth_classes()
class VideosUploadsView(DeveloperErrorViewMixin, RetrieveAPIView, DestroyAPIView):
    """
    **DEPRECATED** — use ``GET /api/authoring/v1/courses/{course_key}/videos/`` for the listing
    this GET returns, and ``DELETE /api/authoring/v1/courses/{course_key}/videos/{edx_video_id}/``
    to remove one video from the course. These v0 endpoints will be removed in a future release.

    public rest API endpoints for the CMS API video assets.
    course_key: required argument, needed to authorize course authors and identify the video.
    video_id: required argument, needed to identify the video.
    """
    serializer_class = VideoUploadSerializer

    @course_author_access_required
    def retrieve(self, request, course_key, edx_video_id=None):  # pylint: disable=arguments-differ
        """
        **DEPRECATED** — use ``GET /api/authoring/v1/courses/{course_key}/videos/`` instead.

        This GET ignores ``edx_video_id`` and returns the whole course listing;
        ``/api/authoring/v1/courses/{course_key}/videos/`` is the successor for that listing.
        It will be removed in a future release.
        """
        return handle_videos(request, course_key.html_id(), edx_video_id)

    @course_author_access_required
    @expect_json_in_class_view
    def destroy(self, request, course_key, edx_video_id):  # pylint: disable=arguments-differ
        """
        **DEPRECATED** — use
        ``DELETE /api/authoring/v1/courses/{course_key}/videos/{edx_video_id}/`` instead.

        It will be removed in a future release.
        """
        return handle_videos(request, course_key.html_id(), edx_video_id)


@view_auth_classes()
class VideosCreateUploadView(DeveloperErrorViewMixin, CreateAPIView):
    """
    **DEPRECATED** — use ``POST /api/authoring/v1/courses/{course_key}/videos/`` instead.

    These v0 endpoints will be removed in a future release.

    public rest API endpoints for the CMS API video assets.
    course_key: required argument, needed to authorize course authors and identify the video.
    """
    serializer_class = VideoUploadSerializer

    @csrf_exempt
    @course_author_access_required
    @expect_json_in_class_view
    @validate_request_with_serializer
    def create(self, request, course_key):  # pylint: disable=arguments-differ
        return handle_videos(request, course_key.html_id())


@view_auth_classes()
class VideoImagesView(DeveloperErrorViewMixin, CreateAPIView):
    """
    public rest API endpoint for uploading a video image.
    course_key: required argument, needed to authorize course authors and identify the video.
    video_id: required argument, needed to identify the video.
    """
    serializer_class = VideoImageSerializer
    parser_classes = (MultiPartParser, FormParser, TypedFileUploadParser)

    @csrf_exempt
    @course_author_access_required
    @expect_json_in_class_view
    @validate_request_with_serializer
    def create(self, request, course_key, edx_video_id=None):  # pylint: disable=arguments-differ
        return handle_video_images(request, course_key.html_id(), edx_video_id)


@view_auth_classes()
class VideoEncodingsDownloadView(DeveloperErrorViewMixin, RetrieveAPIView):
    """
    public rest API endpoint providing a CSV report containing the encoded video URLs for video uploads.
    course_key: required argument, needed to authorize course authors and identify relevant videos.
    """

    # TODO: ARCH-91
    # This view is excluded from Swagger doc generation because it
    # does not specify a serializer class.
    swagger_schema = None

    @csrf_exempt
    @course_author_access_required
    def retrieve(self, request, course_key):  # pylint: disable=arguments-differ
        return get_video_encodings_download(request, course_key.html_id())


@view_auth_classes()
class VideoFeaturesView(DeveloperErrorViewMixin, RetrieveAPIView):
    """
    public rest API endpoint providing a list of enabled video features.
    """

    # TODO: ARCH-91
    # This view is excluded from Swagger doc generation because it
    # does not specify a serializer class.
    swagger_schema = None

    @csrf_exempt
    def retrieve(self, request):  # pylint: disable=arguments-differ
        return enabled_video_features(request)
