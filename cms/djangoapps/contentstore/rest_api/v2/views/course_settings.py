"""API views for a course's Studio settings."""

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.url_converters import CourseKeyConverter
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v2.course_settings_service import get_course_settings_data
from cms.djangoapps.contentstore.rest_api.v2.serializers.course_settings import (
    FULL_VIEW,
    MINIMAL_VIEW,
    VIEW_HELP_TEXT,
    CourseSettingsQuerySerializer,
    CourseSettingsSerializer,
)
from cms.djangoapps.contentstore.views.permissions import CanViewCourse

_COURSE_KEY_PARAMETER = OpenApiParameter(
    name="course_key",
    description="Key of the course, such as course-v1:edX+DemoX+2026.",
    location=OpenApiParameter.PATH,
    type=str,
    pattern=f"^{CourseKeyConverter.regex}$",
)

_VIEW_PARAMETER = OpenApiParameter(
    name="view",
    description=VIEW_HELP_TEXT,
    required=False,
    location=OpenApiParameter.QUERY,
    type=str,
    enum=[MINIMAL_VIEW, FULL_VIEW],
    default=MINIMAL_VIEW,
)


def _error(description):
    return OpenApiResponse(response=ErrorResponseSerializer, description=description)


_SETTINGS = {
    "about_page_editable": True,
    "can_show_certificate_available_date_field": False,
    "content_experiments_enabled": False,
    "course_display_name": "Demonstration Course",
    "course_display_name_with_default": "Demonstration Course",
    "credit_eligibility_enabled": True,
    "enable_extended_course_details": False,
    "enrollment_end_editable": True,
    "is_credit_course": False,
    "is_entrance_exams_enabled": True,
    "is_prerequisite_courses_enabled": True,
    "licensing_enabled": False,
    "lms_link_for_about_page": "https://lms.example.com/courses/course-v1:edX+DemoX+2026/about",
    "marketing_enabled": True,
    "mfe_proctored_exam_settings_url": "",
    "platform_name": "Example Site",
    "short_description_editable": True,
    "show_min_grade_warning": False,
    "sidebar_html_enabled": False,
    "upgrade_deadline": None,
}

_MINIMAL_EXAMPLE = OpenApiExample(
    "Course settings",
    summary="The default view",
    response_only=True,
    status_codes=["200"],
    value=_SETTINGS,
)

_FULL_EXAMPLE = OpenApiExample(
    "Course settings with lists",
    summary="view=full, the language list cut to two entries",
    response_only=True,
    status_codes=["200"],
    value={
        **_SETTINGS,
        "language_options": [["en", "English"], ["fr", "French"]],
        "possible_pre_requisite_courses": [
            {
                "course_key": "course-v1:edX+Intro+2025",
                "display_name": "Introduction",
                "lms_link": "https://lms.example.com/courses/course-v1:edX+Intro+2025/jump_to/"
                            "block-v1:edX+Intro+2025+type@course+block@course",
                "number": "Intro",
                "org": "edX",
                "rerun_link": "/course_rerun/course-v1:edX+Intro+2025",
                "run": "2025",
                "url": "/course/course-v1:edX+Intro+2025",
            },
        ],
    },
)


@extend_schema(tags=["openedx-platform-sdk"])
class CourseSettingsViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    What Studio's course settings pages need to know about one course and the
    site: which settings may be edited, which features are on, and the links
    and names shown beside them.

    By default (``view=minimal``) the switches and values are returned; with
    ``view=full`` the language list, the courses that may be set as
    prerequisites and the course's credit requirements are added. The
    prerequisite list does not depend on any query parameter.

    Callable by anyone who may view the course in Studio. The course is looked
    up first, so a signed-in caller learns that a course does not exist (404)
    before being refused (403).

    A plain ``ViewSet``: the settings come from the course block in the
    modulestore, site configuration and several feature modules, not from
    database rows of one model.
    """

    # An inactive account's browser session stays authenticated and the course permission decides:
    # staff status admits it; its roles count only on courses where policy-based permissions are on.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, CanViewCourse)
    serializer_class = CourseSettingsSerializer

    def get_serializer(self, *args, **kwargs):
        """Instantiate the settings serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="Get a course's Studio settings",
        description=(
            "Returns what the Studio settings pages need: whether the about page, enrollment end date and "
            "short description are edited in Studio, which site features are on (credit, entrance exams, "
            "prerequisite courses, licensing, content experiments), the course's name, about page address, "
            "proctored exam settings path and upgrade deadline. view=full adds the language list, the "
            "courses the caller may set as prerequisites (while prerequisite courses are on) and the "
            "course's credit requirements (for a credit course while credit eligibility is on)."
        ),
        parameters=[_COURSE_KEY_PARAMETER, _VIEW_PARAMETER],
        responses={
            200: OpenApiResponse(response=CourseSettingsSerializer, description="The course's Studio settings."),
            400: _error("A query parameter has an invalid value."),
            401: _error("The requester is not authenticated."),
            403: _error("The requester may not view this course in Studio."),
            404: _error("The course does not exist."),
        },
        examples=[_MINIMAL_EXAMPLE, _FULL_EXAMPLE],
    )
    def retrieve(self, request, course_key):
        """Return the course's settings, with the lists and credit requirements when ``view=full``."""
        query = CourseSettingsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        full = query.validated_data["view"] == FULL_VIEW
        return Response(self.get_serializer(get_course_settings_data(request, course_key, full=full)).data)
