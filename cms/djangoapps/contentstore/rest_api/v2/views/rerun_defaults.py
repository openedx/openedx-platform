"""API views for the values a rerun of a course starts from."""

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.url_converters import CourseKeyConverter
from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v2.rerun_defaults_service import get_rerun_defaults
from cms.djangoapps.contentstore.rest_api.v2.serializers.rerun_defaults import CourseRerunDefaultsSerializer
from cms.djangoapps.contentstore.views.permissions import IsExistingCourse

_COURSE_KEY_PARAMETER = OpenApiParameter(
    name="course_key",
    description="Key of the course, such as course-v1:edX+DemoX+2026.",
    location=OpenApiParameter.PATH,
    type=str,
    pattern=f"^{CourseKeyConverter.regex}$",
)


def _error(description):
    return OpenApiResponse(response=ErrorResponseSerializer, description=description)


_DEFAULTS_EXAMPLE = OpenApiExample(
    "Rerun defaults",
    summary="The values a rerun of a course starts from",
    response_only=True,
    status_codes=["200"],
    value={
        "allow_unicode_course_id": False,
        "course_creator_status": "granted",
        "display_name": "Demonstration Course",
        "number": "DemoX",
        "org": "edX",
        "run": "2026",
    },
)


@extend_schema(tags=["openedx-platform-sdk"])
class CourseRerunDefaultsViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    The values Studio fills in when an author starts a rerun of one course:
    the course's organization, number, run and name, and whether the site and
    the caller allow a course to be created.

    Only accounts with staff status may read them, which every saved superuser
    has; course and organization roles do not count. The course is looked up
    first, so a signed-in caller learns that a course does not exist (404)
    before being refused (403).

    A plain ``ViewSet``: the values come from the course block in the
    modulestore and from site settings, not from database rows.
    """

    # An inactive account's browser session stays authenticated, so global staff whose account is
    # inactive may still read the values.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, IsExistingCourse, IsAdminUser)
    serializer_class = CourseRerunDefaultsSerializer

    def get_serializer(self, *args, **kwargs):
        """Instantiate the rerun defaults serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="Get the values a rerun of a course starts from",
        description=(
            "Returns the organization, number, run and name of the course, which a rerun starts from, "
            "with whether this site accepts characters outside ASCII in a course key and whether the "
            "caller may create courses. Only global staff may call it."
        ),
        parameters=[_COURSE_KEY_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=CourseRerunDefaultsSerializer,
                description="The values a rerun of the course starts from.",
            ),
            401: _error("The requester is not authenticated."),
            403: _error("The requester is not global staff."),
            404: _error("The course does not exist."),
        },
        examples=[_DEFAULTS_EXAMPLE],
    )
    def retrieve(self, request, course_key):
        """Return the values a rerun of the course starts from."""
        return Response(self.get_serializer(get_rerun_defaults(course_key, request.user)).data)
