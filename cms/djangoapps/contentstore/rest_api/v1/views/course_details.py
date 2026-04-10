""" API Views for course details """

import edx_api_doc_tools as apidocs
from django.core.exceptions import ValidationError
from opaque_keys.edx.keys import CourseKey
from openedx_authz.constants.permissions import (
        COURSES_EDIT_DETAILS,
        COURSES_EDIT_SCHEDULE,
        COURSES_VIEW_SCHEDULE_AND_DETAILS,
)
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from common.djangoapps.util.json_request import JsonResponseBadRequest
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import user_has_course_permission
from openedx.core.djangoapps.models.course_details import CourseDetails
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, verify_course_exists, view_auth_classes
from xmodule.modulestore.django import modulestore

from ....utils import update_course_details
from ..serializers import CourseDetailsSerializer


def _classify_update(payload: dict, course_key: CourseKey) -> tuple[bool, bool]:
    """
    Determine whether the payload is updating schedule fields, detail fields, or both
    for the course identified by course_key.

    Returns:
        (is_schedule_update, is_details_update)
    """

    # Define which fields are considered schedule fields.
    # Any field not in this set that is being updated will be considered a details update.
    schedule_fields = frozenset(
        {"start_date", "end_date", "enrollment_start", "enrollment_end", "certificate_available_date"}
    )

    # Define which fields are date fields to ensure proper comparison after parsing.
    # At this time, all schedule fields are also date fields, but this is defined separately for clarity
    # and in case this changes in the future.
    date_fields = frozenset(
        {"start_date", "end_date", "enrollment_start", "enrollment_end", "certificate_available_date"}
    )

    course_details = CourseDetails.fetch(course_key)

    is_schedule_update = False
    is_details_update = False

    serializer = CourseDetailsSerializer()

    for field, payload_value in payload.items():
        # Early exit for efficiency
        if is_schedule_update and is_details_update:
            break

        # Ignore unknown fields if needed
        if field not in serializer.fields:
            continue

        current_value = getattr(course_details, field, None)

        if field in date_fields:
            # For date fields, we need to parse the payload value to compare it with the current value
            try:
                # Convert payload value to internal value for accurate comparison
                # on date fields
                if payload_value is not None:
                    payload_value = serializer.fields[field].to_internal_value(payload_value)
            except ValidationError as exc:
                raise ValidationError(
                    f"Invalid date format for field {field}: {payload_value}"
                ) from exc

        # Check schedule fields
        if field in schedule_fields:
            if is_schedule_update:
                # Already classified as schedule update, no need to check again
                continue
            if payload_value != current_value:
                is_schedule_update = True
        else:
            # Any non-schedule field counts as details update
            if is_details_update:
                # Already classified as details update, no need to check again
                continue
            if payload_value != current_value:
                is_details_update = True

    return is_schedule_update, is_details_update


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
        if not user_has_course_permission(
            request.user,
            COURSES_VIEW_SCHEDULE_AND_DETAILS.identifier,
            course_key,
            LegacyAuthoringPermission.READ
        ):
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
        is_schedule_update, is_details_update = _classify_update(request.data, course_key)

        if not is_schedule_update and not is_details_update:
            # No updatable fields provided in the request
            is_details_update = True  # To trigger permission check and return 403 if user cannot edit details

        if is_schedule_update and not user_has_course_permission(
            request.user,
            COURSES_EDIT_SCHEDULE.identifier,
            course_key,
            LegacyAuthoringPermission.READ
        ):
            self.permission_denied(request)

        if is_details_update and not user_has_course_permission(
            request.user,
            COURSES_EDIT_DETAILS.identifier,
            course_key,
            LegacyAuthoringPermission.READ
        ):
            self.permission_denied(request)

        course_block = modulestore().get_course(course_key)

        try:
            updated_data = update_course_details(request, course_key, request.data, course_block)
        except ValidationError as err:
            return JsonResponseBadRequest({"error": err.message})

        serializer = CourseDetailsSerializer(updated_data)
        return Response(serializer.data)
