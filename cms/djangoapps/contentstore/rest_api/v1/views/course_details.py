""" API Views for course details """

import edx_api_doc_tools as apidocs
from django.core.exceptions import ValidationError
from opaque_keys.edx.keys import CourseKey
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from common.djangoapps.student.auth import has_studio_read_access
from common.djangoapps.util.json_request import JsonResponseBadRequest
from openedx.core.djangoapps.models.course_details import CourseDetails
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, verify_course_exists, view_auth_classes
from xmodule.modulestore.django import modulestore

from ....utils import update_course_details
from ..serializers import CourseDetailsSerializer


@view_auth_classes(is_authenticated=True)
class CourseDetailsView(DeveloperErrorViewMixin, APIView):
    """
    View for getting and setting the course details.
    """
    @apidocs.schema(
        parameters=[
            apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID"),
        ],
        responses={
            200: CourseDetailsSerializer,
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists()
    def get(self, request: Request, course_id: str):
        """
        Get an object containing all the course details.

        **Example Request**

            GET /api/contentstore/v1/course_details/{course_id}

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a single dict that contains keys that
        are the course's details.

        **Example Response**

        ```json
        {
            "about_sidebar_html": "",
            "banner_image_name": "images_course_image.jpg",
            "banner_image_asset_path": "/asset-v1:edX+E2E-101+course+type@asset+block@images_course_image.jpg",
            "certificate_available_date": "2029-01-02T00:00:00Z",
            "certificates_display_behavior": "end",
            "course_id": "E2E-101",
            "course_image_asset_path": "/static/studio/images/pencils.jpg",
            "course_image_name": "",
            "description": "",
            "duration": "",
            "effort": null,
            "end_date": "2023-08-01T01:30:00Z",
            "enrollment_end": "2023-05-30T01:00:00Z",
            "enrollment_start": "2023-05-29T01:00:00Z",
            "entrance_exam_enabled": "",
            "entrance_exam_id": "",
            "entrance_exam_minimum_score_pct": "50",
            "intro_video": null,
            "language": "creative-commons: ver=4.0 BY NC ND",
            "learning_info": [],
            "license": "creative-commons: ver=4.0 BY NC ND",
            "org": "edX",
            "overview": "<section class='about'></section>",
            "pre_requisite_courses": [],
            "run": "course",
            "self_paced": false,
            "short_description": "",
            "start_date": "2023-06-01T01:30:00Z",
            "subtitle": "",
            "syllabus": null,
            "title": "",
            "video_thumbnail_image_asset_path": "/asset-v1:edX+E2E-101+course+type@asset+block@images_course_image.jpg",
            "video_thumbnail_image_name": "images_course_image.jpg",
            "instructor_info": {
                "instructors": [{
                    "name": "foo bar",
                    "title": "title",
                    "organization": "org",
                    "image": "image",
                    "bio": ""
                }]
            }
        }
        ```
        """
        course_key = CourseKey.from_string(course_id)
        if not has_studio_read_access(request.user, course_key):
            self.permission_denied(request)

        course_details = CourseDetails.fetch(course_key)
        serializer = CourseDetailsSerializer(course_details)
        return Response(serializer.data)

    @apidocs.schema(
        body=CourseDetailsSerializer,
        parameters=[
            apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID"),
        ],
        responses={
            200: CourseDetailsSerializer,
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists()
    def put(self, request: Request, course_id: str):
        """
        Update a course's details.

        **Example Request**

            PUT /api/contentstore/v1/course_details/{course_id}

        **PUT Parameters**

        The data sent for a put request should follow a similar format as
        is returned by a ``GET`` request. Multiple details can be updated in
        a single request, however only the ``value`` field can be updated
        any other fields, if included, will be ignored.

        Example request data that updates the ``course_details`` the same as in GET method

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned,
        along with all the course's details similar to a ``GET`` request.
        """
        course_key = CourseKey.from_string(course_id)
        if not has_studio_read_access(request.user, course_key):
            self.permission_denied(request)

        course_block = modulestore().get_course(course_key)

        try:
            updated_data = update_course_details(request, course_key, request.data, course_block)
        except ValidationError as err:
            return JsonResponseBadRequest({"error": err.message})

        serializer = CourseDetailsSerializer(updated_data)
        return Response(serializer.data)
