""" API Views for course settings """

import edx_api_doc_tools as apidocs
from django.conf import settings
from opaque_keys.edx.keys import CourseKey
from openedx_authz.constants.permissions import COURSES_VIEW_COURSE
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from lms.djangoapps.certificates.api import can_show_certificate_available_date_field
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import user_has_course_permission
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, verify_course_exists, view_auth_classes
from xmodule.modulestore.django import modulestore

from ....utils import get_course_settings
from ..serializers import CourseSettingsSerializer


@view_auth_classes(is_authenticated=True)
class CourseSettingsView(DeveloperErrorViewMixin, APIView):
    """
    View for getting the settings for a course.
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID"),
        ],
        responses={
            200: CourseSettingsSerializer,
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists()
    def get(self, request: Request, course_id: str):
        """
        Get an object containing all the course settings.

        **Example Request**

            GET /api/contentstore/v1/course_settings/{course_id}

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a single dict that contains keys that
        are the course's settings.

        **Example Response**

        ```json
        {
            "about_page_editable": false,
            "can_show_certificate_available_date_field": false,
            "course_display_name": "E2E Test Course",
            "course_display_name_with_default": "E2E Test Course",
            "credit_eligibility_enabled": true,
            "enable_extended_course_details": true,
            "enrollment_end_editable": true,
            "is_credit_course": false,
            "is_entrance_exams_enabled": true,
            "is_prerequisite_courses_enabled": true,
            "language_options": [
                [
                "aa",
                "Afar"
                ],
                [
                "uk",
                "Ukrainian"
                ],
                ...
            ],
            "licensing_enabled": false,
            "lms_link_for_about_page": "http://localhost:18000/courses/course-v1:edX+E2E-101+course/about",
            "marketing_enabled": true,
            "mfe_proctored_exam_settings_url": "",
            "platform_name": "edX",
            "possible_pre_requisite_courses": [
                {
                "course_key": "course-v1:edX+M12+2T2023",
                "display_name": "Differential Equations",
                "lms_link": "//localhost:18000/courses/course-v1:edX+M1...",
                "number": "M12",
                "org": "edX",
                "rerun_link": "/course_rerun/course-v1:edX+M12+2T2023",
                "run": "2T2023",
                "url": "/course/course-v1:edX+M12+2T2023"
                },
            ],
            "short_description_editable": true,
            "show_min_grade_warning": false,
            "sidebar_html_enabled": true,
            "upgrade_deadline": null,
            }
        ```
        """
        course_key = CourseKey.from_string(course_id)
        if not user_has_course_permission(
            request.user,
            COURSES_VIEW_COURSE.identifier,
            course_key,
            LegacyAuthoringPermission.READ
        ):
            self.permission_denied(request)

        with modulestore().bulk_operations(course_key):
            course_block = modulestore().get_course(course_key)
            settings_context = get_course_settings(request, course_key, course_block)
            settings_context.update({
                'can_show_certificate_available_date_field': can_show_certificate_available_date_field(course_block),
                'course_display_name': course_block.display_name,
                'course_display_name_with_default': course_block.display_name_with_default,
                'platform_name': settings.PLATFORM_NAME,
                'licensing_enabled': settings.FEATURES.get("LICENSING", False),
            })

            serializer = CourseSettingsSerializer(settings_context)
            return Response(serializer.data)
