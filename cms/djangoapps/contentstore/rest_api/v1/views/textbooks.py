""" API Views for course textbooks """

import edx_api_doc_tools as apidocs
from opaque_keys.edx.keys import CourseKey
from openedx_authz.constants.permissions import COURSES_VIEW_PAGES_AND_RESOURCES
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.djangoapps.contentstore.rest_api.v1.serializers import CourseTextbooksSerializer
from cms.djangoapps.contentstore.utils import get_textbooks_context
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import user_has_course_permission
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, verify_course_exists, view_auth_classes
from xmodule.modulestore.django import modulestore


@view_auth_classes(is_authenticated=True)
class CourseTextbooksView(DeveloperErrorViewMixin, APIView):
    """
    View for course textbooks page.
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                "course_id", apidocs.ParameterLocation.PATH, description="Course ID"
            ),
        ],
        responses={
            200: CourseTextbooksSerializer,
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists()
    def get(self, request: Request, course_id: str):
        """
        Get an object containing course's textbooks.

        **Example Request**

            GET /api/contentstore/v1/textbooks/{course_id}

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a single dict that contains keys that
        are the course's textbooks.

        **Example Response**

        ```json
        {
            "textbooks": [
                {
                    "tab_title": "Textbook Name",
                    "chapters": [
                        {
                            "title": "Chapter 1",
                            "url": "/static/Present_Perfect.pdf"
                        },
                        {
                            "title": "Chapter 2",
                            "url": "/static/Lear.pdf"
                        }
                    ],
                    "id": "Textbook_Name"
                }
            ]
        }
        ```
        """
        course_key = CourseKey.from_string(course_id)
        store = modulestore()

        if not user_has_course_permission(
            request.user,
            COURSES_VIEW_PAGES_AND_RESOURCES.identifier,
            course_key,
            LegacyAuthoringPermission.READ,
        ):
            self.permission_denied(request)

        with store.bulk_operations(course_key):
            course = modulestore().get_course(course_key)
            textbooks_context = get_textbooks_context(course)
            serializer = CourseTextbooksSerializer(textbooks_context)
            return Response(serializer.data)
