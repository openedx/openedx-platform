"""API Views for YouTube transcripts of course videos."""

import json

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v1.serializers import (
    YoutubeTranscriptCheckRequestSerializer,
    YoutubeTranscriptCheckResultSerializer,
    YoutubeTranscriptImportRequestSerializer,
    YoutubeTranscriptImportResultSerializer,
)
from cms.djangoapps.contentstore.rest_api.v1.views.permissions import HasCourseAuthorAccess
from cms.djangoapps.contentstore.rest_api.v1.youtube_transcripts_service import (
    get_transcript_check_result,
    import_transcripts,
)

_ERROR_RESPONSES = {
    400: OpenApiResponse(response=ErrorResponseSerializer, description="The request body is invalid."),
    401: OpenApiResponse(response=ErrorResponseSerializer, description="The requester is not authenticated."),
    403: OpenApiResponse(
        response=ErrorResponseSerializer,
        description="The requester cannot author the course or the addressed video.",
    ),
    404: OpenApiResponse(
        response=ErrorResponseSerializer,
        description="The course or the addressed video block does not exist.",
    ),
}

_DATA_PARAMETER = OpenApiParameter(
    name="data",
    description=(
        "JSON object naming the video to inspect. It carries a `locator` holding the "
        "usage key of the video block, and a `videos` list of `{type, video, mode}` "
        "entries describing the block's declared sources."
    ),
    required=True,
    type=str,
    location=OpenApiParameter.QUERY,
)


class _CourseScopedMixin:
    """Shared plumbing for the course-scoped YouTube transcript collections."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.course_key = None

    def initial(self, request, *args, **kwargs):
        """Expose the course key from the path before permission checks run."""
        self.course_key = kwargs.get("course_key")
        super().initial(request, *args, **kwargs)

    def get_serializer(self, *args, **kwargs):
        """Instantiate the response serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @staticmethod
    def _validated(raw, request_serializer_class):
        """Return ``raw`` validated against ``request_serializer_class``."""
        serializer = request_serializer_class(data=raw)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def _payload_from_query(self, request, request_serializer_class):
        """Return the validated payload carried by the ``data`` query parameter."""
        raw = request.query_params.get("data", "")
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise ValidationError({"data": ["Must be a JSON object."]}) from exc
        if not isinstance(parsed, dict):
            raise ValidationError({"data": ["Must be a JSON object."]})
        return self._validated(parsed, request_serializer_class)

    def _payload_from_body(self, request, request_serializer_class):
        """Return the validated payload carried by the request body."""
        return self._validated(request.data, request_serializer_class)


@extend_schema(tags=["openedx-platform-sdk"])
class YoutubeTranscriptChecksViewSet(StandardizedErrorMixin, _CourseScopedMixin, viewsets.ViewSet):
    """
    Transcript availability reports for the videos of one course.

    A report describes which transcripts already exist for a video block and
    whether the one published on YouTube differs from the stored one, so a
    caller can decide whether an import is worth making. Producing a report
    changes nothing.

    Callable by anyone with authoring rights on both the course in the path and
    the course the addressed video belongs to.
    """

    # Studio authors reach this endpoint from the authoring UI while their
    # account is still inactive, which the default session class rejects.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, HasCourseAuthorAccess)
    serializer_class = YoutubeTranscriptCheckResultSerializer

    @extend_schema(
        summary="Report the transcript availability of a video",
        description=(
            "Reports which transcripts exist for the video named by the `data` parameter, "
            "and whether the transcript published on YouTube differs from the stored one."
        ),
        parameters=[_DATA_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=YoutubeTranscriptCheckResultSerializer,
                description="The transcript availability of the requested video.",
            ),
            **_ERROR_RESPONSES,
        },
    )
    def list(self, request, course_key):
        """Return the transcript availability of the requested video."""
        payload = self._payload_from_query(request, YoutubeTranscriptCheckRequestSerializer)
        result = get_transcript_check_result(request, payload, course_key)
        serializer = self.get_serializer(data=result)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data)


@extend_schema(tags=["openedx-platform-sdk"])
class YoutubeTranscriptImportsViewSet(StandardizedErrorMixin, _CourseScopedMixin, viewsets.ViewSet):
    """
    Imports of YouTube transcripts into the videos of one course.

    Creating an import downloads the transcript YouTube publishes for a video
    and stores it against that video, replacing whatever was stored before.

    Callable by anyone with authoring rights on both the course in the path and
    the course the addressed video belongs to.
    """

    # Studio authors reach this endpoint from the authoring UI while their
    # account is still inactive, which the default session class rejects.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, HasCourseAuthorAccess)
    serializer_class = YoutubeTranscriptImportResultSerializer

    @extend_schema(
        summary="Import a video's transcript from YouTube",
        description=(
            "Downloads the transcript YouTube publishes for the video named in the request "
            "body and stores it against that video, replacing any transcript already stored."
        ),
        request=YoutubeTranscriptImportRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=YoutubeTranscriptImportResultSerializer,
                description="The transcript was downloaded and stored.",
            ),
            **_ERROR_RESPONSES,
        },
    )
    def create(self, request, course_key):
        """Import the video's YouTube transcript and return the resulting record."""
        payload = self._payload_from_body(request, YoutubeTranscriptImportRequestSerializer)
        result = import_transcripts(request, payload, course_key)
        serializer = self.get_serializer(data=result)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data)
