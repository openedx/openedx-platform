"""API Views for zip archives of a course's videos."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from cms.djangoapps.contentstore.rest_api.v1.views.videos import VideoDownloadThrottle
from cms.djangoapps.contentstore.rest_api.v2.serializers.video_archives import VideoArchiveRequestSerializer
from cms.djangoapps.contentstore.rest_api.v2.video_management_service import (
    create_course_video_archive,
    ensure_course_exists,
)
from cms.djangoapps.contentstore.rest_api.v2.views.video_schema import (
    COURSE_KEY_PARAMETER,
    COURSE_NOT_FOUND_RESPONSE,
    forbidden_response,
    unauthenticated_response,
)
from cms.djangoapps.contentstore.views.permissions import HasFullCourseKey, HasStudioReadAccess

_REQUEST_EXAMPLE = OpenApiExample(
    "Two videos",
    value={
        "files": [
            {"url": "https://video-store.example.com/course-videos/lecture-01.mp4", "name": "Lecture 1.mp4"},
            {"url": "https://video-store.example.com/course-videos/lecture-02.mp4", "name": "Lecture 2"},
        ]
    },
    request_only=True,
)

_RESPONSE_ARCHIVE = OpenApiResponse(
    response=OpenApiTypes.BINARY,
    description=(
        "A zip archive holding one entry per requested video, in request order, sent as an "
        "attachment named '<course key>_videos.zip'. The archive is streamed while the videos "
        "are fetched; if fetching a video fails part way, the transfer stops and the archive "
        "received is incomplete."
    ),
)
_RESPONSE_BAD_REQUEST = OpenApiResponse(
    response=ErrorResponseSerializer,
    description=(
        "The request body is malformed, or names an address that is not one of the course's "
        "video files. Nothing is fetched."
    ),
)
_RESPONSE_NOT_ACCEPTABLE = OpenApiResponse(
    response=ErrorResponseSerializer,
    description=(
        "The Accept header does not admit application/json. Errors are sent as JSON, so a request "
        "for the archive sends 'Accept: application/zip, application/json', or no Accept header. "
        "Nothing is fetched."
    ),
)
_RESPONSE_UNSUPPORTED_MEDIA_TYPE = OpenApiResponse(
    response=ErrorResponseSerializer,
    description="The request body is not JSON. Nothing is fetched.",
)
_RESPONSE_THROTTLED = OpenApiResponse(
    response=ErrorResponseSerializer,
    description=(
        "The requester has made too many archive requests recently. The Retry-After header gives "
        "the number of seconds to wait."
    ),
)


@extend_schema(tags=["openedx-platform-sdk"])
class CourseVideoArchiveView(StandardizedErrorMixin, APIView):
    """
    Build a zip archive of chosen videos of a course.

    The archive is assembled while it is sent and is never stored, so there is
    no collection to list and no member to fetch or delete afterwards; that is
    why this is a single POST handler rather than a ViewSet. Each requested
    address must be one of the course's own encoded-video URLs and is checked
    before any video is fetched, so the server only ever fetches addresses the
    course already holds.

    The body is accepted as JSON only: a list of objects has no form encoding
    that both the published schema and the parser agree on.

    Callers need Studio read access to the course. Requests are rate-limited
    per user with the throttle of the v1 video download endpoint, so both
    endpoints draw on one budget.
    """

    permission_classes = (IsAuthenticated, HasFullCourseKey, HasStudioReadAccess)
    parser_classes = (JSONParser,)
    throttle_classes = (VideoDownloadThrottle,)

    @extend_schema(
        summary="Download chosen videos of a course as one zip archive",
        description=(
            "Fetches the requested videos of the course and streams them back as a single zip "
            "archive. The archive is built for this request only and is not stored. Every "
            "address must be one of the course's encoded video files, as the course's video "
            "listing reports them; a request naming any other address is refused as a whole "
            "before anything is fetched. Requests are rate-limited per user."
        ),
        parameters=[COURSE_KEY_PARAMETER],
        request=VideoArchiveRequestSerializer,
        examples=[_REQUEST_EXAMPLE],
        responses={
            (200, "application/zip"): _RESPONSE_ARCHIVE,
            400: _RESPONSE_BAD_REQUEST,
            401: unauthenticated_response(requires_csrf_token=True),
            403: forbidden_response(requires_csrf_token=True),
            404: COURSE_NOT_FOUND_RESPONSE,
            406: _RESPONSE_NOT_ACCEPTABLE,
            415: _RESPONSE_UNSUPPORTED_MEDIA_TYPE,
            429: _RESPONSE_THROTTLED,
        },
    )
    def post(self, request, course_key):
        """Stream an archive of the requested videos of the course."""
        ensure_course_exists(course_key)
        # The view declares no serializer_class: documentation generators read
        # it as the response body as well, and the response is a binary archive.
        body = VideoArchiveRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return create_course_video_archive(course_key, body.validated_data["files"])
