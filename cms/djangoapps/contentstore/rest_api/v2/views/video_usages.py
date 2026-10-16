"""API Views for where a video is used in a course."""

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v2.serializers.video_usages import CourseVideoUsageSerializer
from cms.djangoapps.contentstore.rest_api.v2.video_management_service import get_course_video_usages
from cms.djangoapps.contentstore.rest_api.v2.views.video_schema import (
    COURSE_KEY_PARAMETER,
    COURSE_NOT_FOUND_RESPONSE,
    forbidden_response,
    unauthenticated_response,
)
from cms.djangoapps.contentstore.views.permissions import HasFullCourseKey, HasStudioReadAccess

_EDX_VIDEO_ID_PARAMETER = OpenApiParameter(
    name="edx_video_id",
    description="Identifier of the video in the video store, as set on the video components that play it.",
    required=True,
    type=str,
    location=OpenApiParameter.PATH,
)

_USAGE_EXAMPLE = OpenApiExample(
    "A video placed in two units",
    value={
        "usage_locations": [
            {
                "display_location": "Week 1 - Welcome / Introduction video",
                "url": (
                    "/container/block-v1:edX+DemoX+Demo_Course+type@vertical+block@welcome"
                    "#block-v1:edX+DemoX+Demo_Course+type@video+block@intro"
                ),
            },
            {
                "display_location": "Week 3 - Recap / Introduction video",
                "url": (
                    "/container/block-v1:edX+DemoX+Demo_Course+type@vertical+block@recap"
                    "#block-v1:edX+DemoX+Demo_Course+type@video+block@intro_again"
                ),
            },
        ]
    },
    response_only=True,
)


@extend_schema(tags=["openedx-platform-sdk"])
class CourseVideoUsageViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    Where one video of a course is used.

    Answers with every video component in the course that references the video,
    including components in unpublished units. Callers need Studio read access
    to the course; a caller without it is refused before the course is looked
    up, so the refusal does not reveal whether the course exists.

    The usages come from walking the course's video components in the content
    store, not from a database query, so this is a plain ViewSet with no
    queryset. An identifier that no component references answers with an empty
    list rather than 404, because components may reference videos that the
    video store does not hold.
    """

    permission_classes = (IsAuthenticated, HasFullCourseKey, HasStudioReadAccess)
    serializer_class = CourseVideoUsageSerializer

    def get_serializer(self, *args, **kwargs):
        """Instantiate and return the configured serializer class."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="Get where a video is used in a course",
        description=(
            "Returns one entry for every video component in the course that plays the video, "
            "including components in units that are not published yet. Each entry names the "
            "subsection, unit and component, and gives the Studio address of the unit."
        ),
        parameters=[COURSE_KEY_PARAMETER, _EDX_VIDEO_ID_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=CourseVideoUsageSerializer,
                description="The places the video is used; an empty list when it is used nowhere.",
                examples=[_USAGE_EXAMPLE],
            ),
            401: unauthenticated_response(requires_csrf_token=False),
            403: forbidden_response(requires_csrf_token=False),
            404: COURSE_NOT_FOUND_RESPONSE,
        },
    )
    def retrieve(self, request, course_key, edx_video_id):
        """Return the places in the course where the video is used."""
        usages = get_course_video_usages(course_key, edx_video_id)
        return Response(self.get_serializer(usages).data)
