"""API Views for the video assets of a course."""

import logging

from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiRequest,
    OpenApiResponse,
    PolymorphicProxySerializer,
    extend_schema,
)
from edx_rest_framework_extensions.errors import ErrorResponseSerializer, error_type_uri
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.paginators import DefaultPagination, IterablePaginationMixin
from edx_rest_framework_extensions.shaping import MinimalViewMixin
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v1.serializers.video_uploads import (
    MINIMAL_VIDEO_FIELDS,
    CourseVideoListQuerySerializer,
    CourseVideoMinimalSerializer,
    CourseVideoQuerySerializer,
    CourseVideoSerializer,
    VideoUploadRequestSerializer,
    VideoUploadResponseSerializer,
)
from cms.djangoapps.contentstore.rest_api.v1.video_uploads_service import (
    DEFAULT_ORDERING,
    VIDEO_ORDERING_CHOICES,
    VIDEO_UPLOADS_NOT_CONFIGURED_SLUG,
    create_video_uploads,
    delete_course_video,
    enrich_course_videos,
    get_course_for_uploads,
    get_course_video,
    list_course_videos,
)
from cms.djangoapps.contentstore.rest_api.v1.views.permissions import HasCourseAuthorAccess

log = logging.getLogger(__name__)


class CourseVideoPagination(DefaultPagination):
    """
    The platform's page envelope, described in full to schema consumers.

    ``DefaultPagination`` answers with seven members, but the response schema it
    inherits names only four of them, so a generated client would not know that
    ``num_pages``, ``current_page`` and ``start`` are there.
    """

    def get_paginated_response_schema(self, schema):
        """Describe every member of the page envelope this paginator returns."""
        inherited = super().get_paginated_response_schema(schema)["properties"]
        return {
            "type": "object",
            "required": ["count", "num_pages", "current_page", "start", "results"],
            "properties": {
                "count": inherited["count"],
                "num_pages": {
                    "type": "integer",
                    "description": "Number of pages the results are divided into.",
                    "example": 13,
                },
                "current_page": {
                    "type": "integer",
                    "description": "Number of the page returned.",
                    "example": 4,
                },
                "start": {
                    "type": "integer",
                    "description": "Position of this page's first result within the whole list.",
                    "example": 30,
                },
                "next": inherited["next"],
                "previous": inherited["previous"],
                "results": inherited["results"],
            },
        }


_COURSE_KEY_PARAMETER = OpenApiParameter(
    name="course_key",
    description="Key of the course the videos belong to, for example course-v1:edX+DemoX+Demo_Course.",
    required=True,
    type=str,
    location=OpenApiParameter.PATH,
)

_EDX_VIDEO_ID_PARAMETER = OpenApiParameter(
    name="edx_video_id",
    description="Identifier of the video, as returned when its upload slot was created.",
    required=True,
    type=str,
    location=OpenApiParameter.PATH,
)

_VIEW_PARAMETER = OpenApiParameter(
    name="view",
    description=(
        "Response preset. 'minimal' reduces each video to "
        f"{', '.join(MINIMAL_VIDEO_FIELDS)}, omitting every other field. "
        "Omit the parameter to receive the full representation."
    ),
    required=False,
    type=str,
    location=OpenApiParameter.QUERY,
    enum=[MinimalViewMixin.minimal_view_value],
)

_ORDERING_PARAMETER = OpenApiParameter(
    name="ordering",
    description=(
        "Field to sort the videos by. Prefix the field name with '-' to sort descending. "
        f"Defaults to {DEFAULT_ORDERING}."
    ),
    required=False,
    type=str,
    location=OpenApiParameter.QUERY,
    enum=list(VIDEO_ORDERING_CHOICES),
)

_PAGE_PARAMETER = OpenApiParameter(
    name="page",
    description="Number of the page to return. Defaults to the first page.",
    required=False,
    type=int,
    location=OpenApiParameter.QUERY,
)

_PAGE_SIZE_PARAMETER = OpenApiParameter(
    name="page_size",
    description=(
        f"Number of videos per page. Defaults to {CourseVideoPagination.page_size}; a larger "
        f"value is reduced to the maximum of {CourseVideoPagination.max_page_size}."
    ),
    required=False,
    type=int,
    location=OpenApiParameter.QUERY,
)

_RESPONSE_BAD_REQUEST = OpenApiResponse(
    response=ErrorResponseSerializer,
    description="The request body or a query parameter is invalid.",
)
_RESPONSE_UNAUTHENTICATED = OpenApiResponse(
    response=ErrorResponseSerializer,
    description="The requester is not authenticated.",
)
_RESPONSE_FORBIDDEN = OpenApiResponse(
    response=ErrorResponseSerializer,
    description="The requester does not have authoring access to the course.",
)
_EXAMPLE_UPLOADS_NOT_CONFIGURED = OpenApiExample(
    "The course does not accept video uploads",
    value={
        "type": error_type_uri(VIDEO_UPLOADS_NOT_CONFIGURED_SLUG),
        "title": "Video Uploads Not Configured",
        "status": 404,
        "detail": "Video uploads are not configured for this course.",
        "instance": "/api/authoring/v1/courses/course-v1:edX+DemoX+Demo_Course/videos/",
    },
    response_only=True,
    status_codes=["404"],
)

_EXAMPLE_VIDEO_NOT_FOUND = OpenApiExample(
    "No such video in this course",
    value={
        "type": error_type_uri("not-found"),
        "title": "Not Found",
        "status": 404,
        "detail": "Not found.",
        "instance": (
            "/api/authoring/v1/courses/course-v1:edX+DemoX+Demo_Course/videos/"
            "8f2a6d9c-2f2e-4a7e-9d6b-1f0c9e2a4b31/"
        ),
    },
    response_only=True,
    status_codes=["404"],
)

_RESPONSE_NOT_FOUND = OpenApiResponse(
    response=ErrorResponseSerializer,
    description="The course does not accept video uploads, or the video is not attached to it.",
    examples=[_EXAMPLE_UPLOADS_NOT_CONFIGURED, _EXAMPLE_VIDEO_NOT_FOUND],
)
_RESPONSE_CREATE_NOT_FOUND = OpenApiResponse(
    response=ErrorResponseSerializer,
    description="The course does not accept video uploads.",
    examples=[_EXAMPLE_UPLOADS_NOT_CONFIGURED],
)
_RESPONSE_LIST_NOT_FOUND = OpenApiResponse(
    response=ErrorResponseSerializer,
    description=(
        "The course does not accept video uploads, or the requested page is past the end of "
        "the list."
    ),
    examples=[_EXAMPLE_UPLOADS_NOT_CONFIGURED],
)

_CREATE_EXAMPLE_REQUEST = OpenApiExample(
    "Two files",
    value={
        "files": [
            {"file_name": "lecture-01.mp4", "content_type": "video/mp4"},
            {"file_name": "lecture-02.mov", "content_type": "video/quicktime"},
        ]
    },
    request_only=True,
)

_CREATE_EXAMPLE_RESPONSE = OpenApiExample(
    "Upload slots to PUT the files to",
    value={
        "files": [
            {
                "file_name": "lecture-01.mp4",
                "upload_url": "https://video-uploads.example.com/videos/8f2a...?X-Amz-Signature=...",
                "edx_video_id": "8f2a6d9c-2f2e-4a7e-9d6b-1f0c9e2a4b31",
            },
            {
                "file_name": "lecture-02.mov",
                "upload_url": "https://video-uploads.example.com/videos/1c7b...?X-Amz-Signature=...",
                "edx_video_id": "1c7b40f5-6b62-4a51-8f10-0b4d2a9f7c58",
            },
        ]
    },
    response_only=True,
)


def _video_response(description, many=False):
    """Describe a response carrying videos in whichever representation was asked for."""
    return OpenApiResponse(
        response=PolymorphicProxySerializer(
            component_name="CourseVideoRepresentation",
            serializers=[CourseVideoSerializer, CourseVideoMinimalSerializer],
            resource_type_field_name=None,
            many=many,
        ),
        description=description,
    )


@extend_schema(tags=["openedx-platform-sdk"])
class CourseVideoUploadsViewSet(
    StandardizedErrorMixin, MinimalViewMixin, IterablePaginationMixin, viewsets.ViewSet
):
    """
    The video assets of a single course.

    Lists the videos attached to a course, returns one of them, creates upload
    slots for new ones, and detaches a video from the course. Reads and writes
    go through the video store rather than the database directly, so this is a
    plain ViewSet with no queryset of its own.

    Listing a video reports an upload that has been stuck past the upload window
    as failed without changing the stored record; a stuck upload is reconciled
    by the upload pipeline, not by reading the list.
    """

    permission_classes = (IsAuthenticated, HasCourseAuthorAccess)
    serializer_class = CourseVideoSerializer
    pagination_class = CourseVideoPagination
    minimal_fields = MINIMAL_VIDEO_FIELDS

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.course_key = None

    def initial(self, request, *args, **kwargs):
        """Expose the course key for the permission check that runs next."""
        self.course_key = kwargs.get("course_key")
        super().initial(request, *args, **kwargs)

    def get_serializer_context(self):
        """Return the context every serializer of this view is built with."""
        return {"request": self.request, "view": self, "format": self.format_kwarg}

    def get_serializer(self, *args, **kwargs):
        """Instantiate and return the configured serializer class."""
        kwargs.setdefault("context", self.get_serializer_context())
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="List the videos of a course",
        description=(
            "Returns a paginated list of the videos attached to the course, newest first by "
            "default. Transcript details are resolved only for the videos on the requested page."
        ),
        parameters=[
            _COURSE_KEY_PARAMETER,
            _ORDERING_PARAMETER,
            _PAGE_PARAMETER,
            _PAGE_SIZE_PARAMETER,
            _VIEW_PARAMETER,
        ],
        responses={
            200: _video_response(
                "A page of videos, in the representation the view parameter selected.",
                many=True,
            ),
            400: _RESPONSE_BAD_REQUEST,
            401: _RESPONSE_UNAUTHENTICATED,
            403: _RESPONSE_FORBIDDEN,
            404: _RESPONSE_LIST_NOT_FOUND,
        },
    )
    def list(self, request, course_key):
        """Return a page of the course's videos."""
        query = CourseVideoListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        course = get_course_for_uploads(course_key, request.user)
        videos = list_course_videos(course, query.validated_data.get("ordering", DEFAULT_ORDERING))
        return self.paginate_iterable(
            request,
            videos,
            serialize=lambda page: self.shape_minimal(
                self.get_serializer(enrich_course_videos(course, page), many=True).data,
                request,
            ),
        )

    @extend_schema(
        summary="Get one video of a course",
        description="Returns the video attached to the course under the given identifier.",
        parameters=[_COURSE_KEY_PARAMETER, _EDX_VIDEO_ID_PARAMETER, _VIEW_PARAMETER],
        responses={
            200: _video_response("The video, in the representation the view parameter selected."),
            400: _RESPONSE_BAD_REQUEST,
            401: _RESPONSE_UNAUTHENTICATED,
            403: _RESPONSE_FORBIDDEN,
            404: _RESPONSE_NOT_FOUND,
        },
    )
    def retrieve(self, request, course_key, edx_video_id):
        """Return one of the course's videos."""
        CourseVideoQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        course = get_course_for_uploads(course_key, request.user)
        video = get_course_video(course, edx_video_id)
        enriched, = enrich_course_videos(course, [video])
        data = self.get_serializer(enriched).data
        return Response(self.shape_minimal(data, request))

    @extend_schema(
        summary="Create upload slots for new course videos",
        description=(
            "Registers one video per requested file and returns a short-lived pre-signed URL for "
            "each. Upload the file itself with a PUT to that URL; this endpoint does not receive "
            "file content. The returned identifiers address the videos from then on."
        ),
        parameters=[_COURSE_KEY_PARAMETER],
        request=OpenApiRequest(request=VideoUploadRequestSerializer),
        responses={
            201: OpenApiResponse(
                response=VideoUploadResponseSerializer,
                description="Upload slots created, one per requested file, in request order.",
            ),
            400: _RESPONSE_BAD_REQUEST,
            401: _RESPONSE_UNAUTHENTICATED,
            403: _RESPONSE_FORBIDDEN,
            404: _RESPONSE_CREATE_NOT_FOUND,
        },
        examples=[_CREATE_EXAMPLE_REQUEST, _CREATE_EXAMPLE_RESPONSE],
    )
    def create(self, request, course_key):
        """Create an upload slot for each requested file."""
        request_serializer = VideoUploadRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        course = get_course_for_uploads(course_key, request.user)
        created = create_video_uploads(course, request_serializer.validated_data["files"])
        return Response(
            VideoUploadResponseSerializer(created).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="Remove a video from a course",
        description=(
            "Detaches the video from the course. The video itself is kept, so it stays available "
            "in any other course it is attached to."
        ),
        parameters=[_COURSE_KEY_PARAMETER, _EDX_VIDEO_ID_PARAMETER],
        responses={
            204: OpenApiResponse(description="The video is no longer attached to the course."),
            401: _RESPONSE_UNAUTHENTICATED,
            403: _RESPONSE_FORBIDDEN,
            404: _RESPONSE_NOT_FOUND,
        },
    )
    def destroy(self, request, course_key, edx_video_id):
        """Detach one video from the course."""
        get_course_for_uploads(course_key, request.user)
        delete_course_video(course_key, edx_video_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
