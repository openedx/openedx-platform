"""API views for a course's video upload and transcript settings."""

from drf_spectacular.utils import OpenApiResponse, PolymorphicProxySerializer, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v2.serializers.video_settings import (
    CourseVideoSettingsFullSerializer,
    CourseVideoSettingsQuerySerializer,
    CourseVideoSettingsSerializer,
)
from cms.djangoapps.contentstore.rest_api.v2.video_settings_service import get_course_video_settings
from cms.djangoapps.contentstore.views.permissions import HasStudioReadAccess

FULL_VIEW = "full"


@extend_schema(tags=["openedx-platform-sdk"])
class CourseVideoSettingsViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    The video upload and transcript settings of one course.

    By default only the settings are returned. ``?view=full`` adds every video
    uploaded to the course, which grows with the course and costs queries per
    video, so it is never part of the default representation.

    Callable by anyone with Studio read access to the course. Permission is
    checked before the course is looked up, so a caller without access is
    refused whether or not the course exists.

    A plain ``ViewSet``: the settings come from the modulestore, Django
    settings, feature toggles and the video pipeline's own API.
    """

    # Studio authors reach this endpoint from the authoring UI while their
    # account is still inactive, which the default session class rejects.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, HasStudioReadAccess)
    serializer_class = CourseVideoSettingsSerializer

    def get_serializer(self, *args, **kwargs):
        """Instantiate the settings serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="Retrieve a course's video settings",
        description=(
            "Returns the course's video upload limits, thumbnail image limits, transcript settings "
            "and the Studio addresses that act on them. Add `view=full` to also receive every video "
            "uploaded to the course under `previous_uploads`, newest first."
        ),
        parameters=[CourseVideoSettingsQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=PolymorphicProxySerializer(
                    component_name="CourseVideoSettingsRepresentation",
                    serializers=[CourseVideoSettingsSerializer, CourseVideoSettingsFullSerializer],
                    resource_type_field_name=None,
                ),
                description="The course's video settings, with its uploaded videos when `view=full` is given.",
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="The `view` parameter has a value other than `full`.",
            ),
            401: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="The requester is not authenticated.",
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="The requester does not have Studio read access to this course.",
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="The course does not exist.",
            ),
        },
    )
    def retrieve(self, request, course_key):
        """Return the course's video settings, with its uploads under ``view=full``."""
        query = CourseVideoSettingsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        full = query.validated_data.get("view") == FULL_VIEW
        video_settings = get_course_video_settings(course_key, include_uploads=full)
        serializer_class = CourseVideoSettingsFullSerializer if full else CourseVideoSettingsSerializer
        return Response(serializer_class(video_settings).data)
