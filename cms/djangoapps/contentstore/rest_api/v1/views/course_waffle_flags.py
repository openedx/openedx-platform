""" API Views for course waffle flags """

from opaque_keys.edx.keys import CourseKey
from rest_framework.decorators import APIView
from rest_framework.response import Response

from openedx.core.lib.api.view_utils import view_auth_classes

from ..serializers import CourseWaffleFlagsSerializer


@view_auth_classes(is_authenticated=True)
class CourseWaffleFlagsView(APIView):
    """
    API view to retrieve course waffle flag settings for a specific course.

    This view provides a GET endpoint that returns the status of various waffle
    flags for a given course. It requires the user to be authenticated.
    """

    def get(self, request, course_id=None):
        """
        Retrieve the waffle flag settings for the specified course.

        Args:
            request (HttpRequest): The HTTP request object.
            course_id (str, optional): The ID of the course for which to retrieve
                                       the waffle flag settings. If not provided,
                                       defaults to None.

        Returns:
            Response: A JSON response containing the status of various waffle flags
                      for the specified course.

        **Example Request**

            GET /api/contentstore/v1/course_waffle_flags
            GET /api/contentstore/v1/course_waffle_flags/course-v1:test+test+test

        **Response Values**

            A JSON response containing the status of various waffle flags
            for the specified course.

        **Example Response**

        ```json
        {
            "use_new_home_page": true,
            "use_new_custom_pages": true,
            "use_new_schedule_details_page": true,
            "use_new_advanced_settings_page": true,
            "use_new_grading_page": true,
            "use_new_updates_page": true,
            "use_new_import_page": true,
            "use_new_export_page": true,
            "use_new_files_uploads_page": true,
            "use_new_video_uploads_page": false,
            "use_new_course_outline_page": true,
            "use_new_unit_page": true,
            "use_new_course_team_page": true,
            "use_new_certificates_page": true,
            "use_new_textbooks_page": true,
            "use_new_group_configurations_page": true,
            "use_react_markdown_editor": true,
            "use_video_gallery_flow": true
        }
        ```
        """
        course_key = CourseKey.from_string(course_id) if course_id else None
        serializer = CourseWaffleFlagsSerializer(
            context={"course_key": course_key}, data={}
        )
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)
