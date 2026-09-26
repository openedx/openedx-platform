""" API Views for course team """

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from opaque_keys.edx.keys import CourseKey
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.djangoapps.contentstore.utils import get_course_team
from common.djangoapps.student.auth import STUDIO_VIEW_USERS, get_user_permissions
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, verify_course_exists, view_auth_classes

from ..serializers import CourseTeamSerializer


@view_auth_classes(is_authenticated=True)
class CourseTeamView(DeveloperErrorViewMixin, APIView):
    """
    View for getting data for course team.
    """
    @extend_schema(
        parameters=[
            OpenApiParameter("course_id", OpenApiTypes.STR, OpenApiParameter.PATH, description="Course ID"),
        ],
        responses={
            200: CourseTeamSerializer,
            401: OpenApiResponse(description="The requester is not authenticated."),
            403: OpenApiResponse(description="The requester cannot access the specified course."),
            404: OpenApiResponse(description="The requested course does not exist."),
        },
    )
    @verify_course_exists()
    def get(self, request: Request, course_id: str):
        """
        Get all CMS users who are editors for the specified course.

        **Example Request**

            GET /api/contentstore/v1/course_team/{course_id}

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a single dict that contains keys that
        are the course's team info.

        **Example Response**

        ```json
        {
            "show_transfer_ownership_hint": true,
            "users": [
                {
                    "email": "edx@example.com",
                    "id": "3",
                    "role": "instructor",
                    "username": "edx"
                },
            ],
            "allow_actions": true
        }
        ```
        """
        user = request.user
        course_key = CourseKey.from_string(course_id)

        user_perms = get_user_permissions(user, course_key)
        if not user_perms & STUDIO_VIEW_USERS:
            self.permission_denied(request)

        course_team_context = get_course_team(user, course_key, user_perms)
        serializer = CourseTeamSerializer(course_team_context)
        return Response(serializer.data)
