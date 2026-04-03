"""
Public rest API endpoints for the CMS API video assets.
"""
import logging

from django.views.decorators.csrf import csrf_exempt
from rest_framework.generics import CreateAPIView, DestroyAPIView, RetrieveAPIView
from rest_framework.parsers import FormParser, MultiPartParser

from cms.djangoapps.contentstore.api import course_author_access_required
from cms.djangoapps.contentstore.rest_api.v0.views.utils import validate_request_with_serializer
from cms.djangoapps.contentstore.transcript_storage_handlers import (
    delete_video_transcript_or_404,
    handle_transcript_download,
    upload_transcript
)
from cms.djangoapps.contentstore.views.transcripts_ajax import check_transcripts, replace_transcripts
from common.djangoapps.util.json_request import expect_json_in_class_view
from openedx.core.lib.api.parsers import TypedFileUploadParser
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, view_auth_classes

from ..serializers import TranscriptSerializer, YoutubeTranscriptCheckSerializer, YoutubeTranscriptUploadSerializer

log = logging.getLogger(__name__)


@view_auth_classes()
class TranscriptView(DeveloperErrorViewMixin, CreateAPIView, RetrieveAPIView, DestroyAPIView):
    """
    public rest API endpoints for the CMS API video transcripts.
    course_key: required argument, needed to authorize course authors and identify the video.
    edx_video_id: optional query parameter, needed to identify the transcript.
    language_code: optional query parameter, needed to identify the transcript.
    """
    serializer_class = TranscriptSerializer
    parser_classes = (MultiPartParser, FormParser, TypedFileUploadParser)

    @csrf_exempt
    @course_author_access_required
    @expect_json_in_class_view
    @validate_request_with_serializer
    def create(self, request, course_key_string):  # pylint: disable=arguments-differ
        return upload_transcript(request)

    @course_author_access_required
    def retrieve(self, request, course_key_string):  # pylint: disable=arguments-differ
        """
        Get a video transcript. edx_video_id and language_code query parameters are required.
        """
        return handle_transcript_download(request)

    @course_author_access_required
    def destroy(self, request, course_key_string):  # pylint: disable=arguments-differ
        """
        Delete a video transcript. edx_video_id and language_code query parameters are required.
        """

        return delete_video_transcript_or_404(request)


@view_auth_classes()
class YoutubeTranscriptCheckView(DeveloperErrorViewMixin, RetrieveAPIView):
    """
    public rest API endpoints for the CMS API YouTube transcripts.
    youtube_id: required argument, needed to authorize course authors and identify the video.
    edx_video_id: required argument, needed to identify the transcript.
    xblock_id: required argument, needed to identify the transcript.
    """
    serializer_class = YoutubeTranscriptCheckSerializer
    parser_classes = (MultiPartParser, FormParser, TypedFileUploadParser)

    @course_author_access_required
    def retrieve(self, request, course_key_string):  # pylint: disable=arguments-differ
        """
        Get the status of youtube transcripts for a given youtube video
        """
        return check_transcripts(request)


@view_auth_classes()
class YoutubeTranscriptUploadView(DeveloperErrorViewMixin, RetrieveAPIView):
    """
    public rest API endpoints for the CMS API YouTube transcripts.
    youtube_id: required argument, needed to authorize course authors and identify the video.
    xblock_id: required argument, needed to identify the transcript.
    """
    serializer_class = YoutubeTranscriptUploadSerializer
    parser_classes = (MultiPartParser, FormParser, TypedFileUploadParser)

    @course_author_access_required
    def retrieve(self, request, course_key_string):  # pylint: disable=arguments-differ
        """
        Get the  youtube transcripts for a give youtube video and add them to video block
        """
        return replace_transcripts(request)
