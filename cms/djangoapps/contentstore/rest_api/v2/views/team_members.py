"""API views for the members of a course's team."""

from django.core.exceptions import ImproperlyConfigured
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.paginators import IterablePaginationMixin
from edx_rest_framework_extensions.url_converters import CourseKeyConverter
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.pagination import DocumentedDefaultPagination
from cms.djangoapps.contentstore.rest_api.v2.serializers.team_members import (
    CourseTeamCallerSerializer,
    CourseTeamMemberSerializer,
)
from cms.djangoapps.contentstore.rest_api.v2.team_members_service import describe_caller, list_team_members
from cms.djangoapps.contentstore.views.permissions import CanViewCourseTeam, IsExistingCourse

_COURSE_KEY_PARAMETER = OpenApiParameter(
    name="course_key",
    description="Key of the course, such as course-v1:edX+DemoX+2026.",
    location=OpenApiParameter.PATH,
    type=str,
    pattern=f"^{CourseKeyConverter.regex}$",
)


def _error(description):
    return OpenApiResponse(response=ErrorResponseSerializer, description=description)


_MEMBER_EXAMPLE = OpenApiExample(
    "Team member",
    summary="One member of a course's team",
    response_only=True,
    status_codes=["200"],
    value={"id": 3, "username": "edx", "email": "edx@example.com", "role": "instructor"},
)

_CALLER_EXAMPLE = OpenApiExample(
    "Only instructor",
    summary="A caller who is the course's only instructor",
    response_only=True,
    status_codes=["200"],
    value={
        "username": "edx",
        "email": "edx@example.com",
        "role": "instructor",
        "can_manage_team": True,
        "is_only_instructor": True,
    },
)


@extend_schema(tags=["openedx-platform-sdk"])
class CourseTeamMembersViewSet(StandardizedErrorMixin, IterablePaginationMixin, viewsets.ViewSet):
    """
    The team of one course: the users who hold the instructor or staff role on
    it, and the caller as the team sees them (``me``).

    Members are listed instructors first, then staff, each in order of
    username. ``me`` says which role the caller holds, if any, whether they may
    change the team, and whether they are its only instructor.

    Anyone who may see the course's team may call both. The course is looked up
    first, so a signed-in caller learns that a course does not exist (404)
    before being refused (403). An action this viewset was not written for is
    refused with a server error instead of being served with these checks.

    A plain ``ViewSet``: the team is read through Studio's course role helpers,
    not from a queryset this view owns.
    """

    # An inactive account's browser session stays authenticated, so the course permission decides:
    # inactive global staff are admitted, inactive course staff are refused (403, not 401).
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, IsExistingCourse, CanViewCourseTeam)
    # The actions the permissions above were written for. A method with no
    # route (answered 405) has no action; OPTIONS is ``metadata``.
    checked_actions = (None, "list", "retrieve", "metadata")
    serializer_class = CourseTeamMemberSerializer
    pagination_class = DocumentedDefaultPagination

    def get_permissions(self):
        """Return the viewset's permissions, or refuse an action they were not written for."""
        if self.action not in self.checked_actions:
            raise ImproperlyConfigured(f"No permission is declared for the {self.action!r} action.")
        return super().get_permissions()

    def get_serializer(self, *args, **kwargs):
        """Instantiate the team member serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="List the members of a course's team",
        description=(
            "Returns one page of the users who hold the instructor or staff role on the course: instructors "
            "first, then staff, each in order of username. A user who holds both roles is listed once, as "
            "an instructor. Global staff are not listed unless they hold a role on the course."
        ),
        parameters=[_COURSE_KEY_PARAMETER],
        responses={
            200: OpenApiResponse(response=CourseTeamMemberSerializer, description="A page of the team's members."),
            401: _error("The requester is not authenticated."),
            403: _error("The requester may not see this course's team."),
            404: _error("The course does not exist, or the requested page is out of range."),
        },
        examples=[_MEMBER_EXAMPLE],
    )
    def list(self, request, course_key):
        """Return one page of the course's team members."""
        return self.paginate_iterable(request, list_team_members(course_key, request.user))

    @extend_schema(
        summary="Get the caller's place on a course's team",
        description=(
            "Returns the caller's username and email, the role they hold on the course's team (null when they "
            "hold none, as for global staff), whether they may add, remove and change the roles of members, "
            "and whether they are the course's only instructor."
        ),
        parameters=[_COURSE_KEY_PARAMETER],
        responses={
            200: OpenApiResponse(response=CourseTeamCallerSerializer, description="The caller, as the team sees them."),
            401: _error("The requester is not authenticated."),
            403: _error("The requester may not see this course's team."),
            404: _error("The course does not exist."),
        },
        examples=[_CALLER_EXAMPLE],
    )
    def retrieve(self, request, course_key):
        """Return the caller as the course's team sees them."""
        return Response(CourseTeamCallerSerializer(describe_caller(course_key, request.user)).data)
