""" API Views for course rerun """

import edx_api_doc_tools as apidocs
from opaque_keys.edx.keys import CourseKey
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.djangoapps.contentstore.rest_api.v1.serializers import CourseRerunSerializer
from cms.djangoapps.contentstore.utils import get_course_rerun_context
from common.djangoapps.student.roles import GlobalStaff
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, verify_course_exists, view_auth_classes
from xmodule.modulestore.django import modulestore


@view_auth_classes(is_authenticated=True)
class CourseRerunView(DeveloperErrorViewMixin, APIView):
    """
    View for course rerun.
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID"),
        ],
        responses={
            200: CourseRerunSerializer,
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists()
    def get(self, request: Request, course_id: str):
        """
        Get an object containing course rerun.

        **Example Request**

            GET /api/contentstore/v1/course_rerun/{course_id}

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a single dict that contains keys that
        are the course's rerun.

        **Example Response**

        ```json
        {
            "allow_unicode_course_id": False,
            "course_creator_status": "granted",
            "number": "101",
            "display_name": "new edx course",
            "org": "edx",
            "run": "2023",
        }
        ```
        """

        if not GlobalStaff().has_user(request.user):
            self.permission_denied(request)

        course_key = CourseKey.from_string(course_id)
        with modulestore().bulk_operations(course_key):
            course_block = modulestore().get_course(course_key)
            course_rerun_context = get_course_rerun_context(course_key, course_block, request.user)
            course_rerun_context.update({
                'org': course_key.org,
                'number': course_key.course,
                'run': course_key.run,
            })
            serializer = CourseRerunSerializer(course_rerun_context)
            return Response(serializer.data)
