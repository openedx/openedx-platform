""" API Views for course advanced settings """

from django import forms
import edx_api_doc_tools as apidocs
from opaque_keys.edx.keys import CourseKey
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from xmodule.modulestore.django import modulestore

from cms.djangoapps.models.settings.course_metadata import CourseMetadata
from cms.djangoapps.contentstore.api.views.utils import get_bool_param
from common.djangoapps.student.auth import check_course_advanced_settings_access
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, verify_course_exists, view_auth_classes
from ..serializers import CourseAdvancedSettingsSerializer
from ....views.course import update_course_advanced_settings


@view_auth_classes(is_authenticated=True)
class AdvancedCourseSettingsView(DeveloperErrorViewMixin, APIView):
    """
    View for getting and setting the advanced settings for a course.
    """

    class FilterQuery(forms.Form):
        """
        Form for validating query parameters passed to advanced course settings view
        to filter the data it returns.
        """
        filter_fields = forms.CharField(strip=True, empty_value=None, required=False)

        def clean_filter_fields(self):
            if 'filter_fields' in self.data and self.cleaned_data['filter_fields']:
                return set(self.cleaned_data['filter_fields'].split(','))
            return None

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID"),
            apidocs.string_parameter(
                "filter_fields",
                apidocs.ParameterLocation.QUERY,
                description="Comma separated list of fields to filter",
            ),
            apidocs.string_parameter(
                "fetch_all",
                apidocs.ParameterLocation.QUERY,
                description="Specifies whether to fetch all settings or only enabled ones",
            ),
        ],
        responses={
            200: CourseAdvancedSettingsSerializer,
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists()
    def get(self, request: Request, course_id: str):
        """
        Get an object containing all the advanced settings in a course.

        **Example Request**

            GET /api/contentstore/v0/advanced_settings/{course_id}

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a single dict that contains keys that
        are the course's advanced settings. For each setting a dictionary is
        returned that contains the following fields:

        * **deprecated**: This is true for settings that are deprecated.
        * **display_name**: This is a friendly name for the setting.
        * **help**: Contains help text that explains how the setting works.
        * **value**: Contains the value of the setting. The exact format
          depends on the setting and is often explained in the ``help`` field
          above.

        There may be other fields returned by the response.

        **Example Response**

        ```json
        {
            "display_name": {
                "value": "Demonstration Course",
                "display_name": "Course Display Name",
                "help": "Enter the name of the course as it should appear in the course list.",
                "deprecated": false,
                "hide_on_enabled_publisher": false
            },
            "course_edit_method": {
                "value": "Studio",
                "display_name": "Course Editor",
                "help": "Enter the method by which this course is edited (\"XML\" or \"Studio\").",
                "deprecated": true,
                "hide_on_enabled_publisher": false
            },
            "days_early_for_beta": {
                "value": null,
                "display_name": "Days Early for Beta Users",
                "help": "Enter the number of days before the start date that beta users can access the course.",
                "deprecated": false,
                "hide_on_enabled_publisher": false
            },
            ...
        }
        ```
        """
        filter_query_data = AdvancedCourseSettingsView.FilterQuery(request.query_params)
        if not filter_query_data.is_valid():
            raise ValidationError(filter_query_data.errors)
        course_key = CourseKey.from_string(course_id)
        if not check_course_advanced_settings_access(request.user, course_key, access_type='read'):
            self.permission_denied(request)
        course_block = modulestore().get_course(course_key)
        fetch_all = get_bool_param(request, 'fetch_all', True)
        if fetch_all:
            return Response(CourseMetadata.fetch_all(
                course_block,
                filter_fields=filter_query_data.cleaned_data['filter_fields'],
            ))
        return Response(CourseMetadata.fetch(
            course_block,
            filter_fields=filter_query_data.cleaned_data['filter_fields'],
        ))

    @apidocs.schema(
        body=CourseAdvancedSettingsSerializer,
        parameters=[apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID")],
        responses={
            200: CourseAdvancedSettingsSerializer,
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists()
    def patch(self, request: Request, course_id: str):
        """
        Update a course's advanced settings.

        **Example Request**

            PATCH /api/contentstore/v0/advanced_settings/{course_id} {
                "{setting_name}": {
                    "value": {setting_value}
                }
            }

        **PATCH Parameters**

        The data sent for a patch request should follow a similar format as
        is returned by a ``GET`` request. Multiple settings can be updated in
        a single request, however only the ``value`` field can be updated
        any other fields, if included, will be ignored.

        Here is an example request that updates the ``advanced_modules``
        available for the course, and enables the calculator tool:

        ```json
        {
            "advanced_modules": {
                "value": [
                    "poll",
                    "survey",
                    "drag-and-drop-v2",
                    "lti_consumer"
                ]
            },
            "show_calculator": {
                "value": true
            }
        }
        ```

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned,
        along with all the course's settings similar to a ``GET`` request.
        """
        course_key = CourseKey.from_string(course_id)
        if not check_course_advanced_settings_access(request.user, course_key, access_type='write'):
            self.permission_denied(request)
        course_block = modulestore().get_course(course_key)
        updated_data = update_course_advanced_settings(course_block, request.data, request.user)
        return Response(updated_data)
