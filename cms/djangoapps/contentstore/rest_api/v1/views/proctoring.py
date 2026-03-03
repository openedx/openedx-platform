""" API Views for proctored exam settings and proctoring error """
import copy

from django.conf import settings
import edx_api_doc_tools as apidocs
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.djangoapps.contentstore.views.course import get_course_and_check_access
from cms.djangoapps.contentstore.utils import get_proctored_exam_settings_url
from cms.djangoapps.models.settings.course_metadata import CourseMetadata
from common.djangoapps.student.auth import check_course_advanced_settings_access
from xmodule.course_block import (
    get_available_providers,
    get_requires_escalation_email_providers,
)  # lint-amnesty, pylint: disable=wrong-import-order
from openedx.core.djangoapps.course_apps.toggles import exams_ida_enabled
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, verify_course_exists, view_auth_classes
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from ..serializers import (
    LimitedProctoredExamSettingsSerializer,
    ProctoredExamConfigurationSerializer,
    ProctoredExamSettingsSerializer,
    ProctoringErrorsSerializer,
)


@view_auth_classes()
class ProctoredExamSettingsView(APIView):
    """
    A view for retrieving information about proctored exam settings for a course.

    Path: ``/api/contentstore/v1/proctored_exam_settings/{course_id}``

    Accepts: [GET, POST]

    ------------------------------------------------------------------------------------
    GET
    ------------------------------------------------------------------------------------

    **Returns**

        * 200: OK - Contains a set of course proctored exam settings.
        * 401: The requesting user is not authenticated.
        * 403: The requesting user lacks access to the course.
        * 404: The requested course does not exist.

    **Response**

        In the case of a 200 response code, the response will proctored exam settings data,
        list of proctoring backends that have 'requires_escalation_email' set to 'True'
        as well as other metadata about the course or the requesting user that are necessary
        for rendering the settings page.

    **Example**

        {
            "proctored_exam_settings": {
                "enable_proctored_exams": true,
                "allow_proctoring_opt_out": true,
                "proctoring_provider": "mockprock",
                "proctoring_escalation_email": null,
                "create_zendesk_tickets": true
            },
            "available_proctoring_providers": [
                "mockprock",
                "software_secure",
            ],
            "requires_escalation_email_providers": [
                "software_secure"
            ],
            "course_start_date": "2013-02-05T05:00:00Z",
        }

    ------------------------------------------------------------------------------------
    POST
    ------------------------------------------------------------------------------------

    **Returns**

        * 200: OK - Proctored exam settings saved.
        * 400: Bad Request - Unable to save requested settings.
        * 401: The requesting user is not authenticated.
        * 403: The requesting user lacks access to the course.
        * 404: The requested course does not exist.

    **Response**

        In the case of a 200 response code, the response will echo the updated proctored
        exam settings data.
    """
    PROCTORED_EXAM_SETTINGS_KEYS = [
        'enable_proctored_exams',
        'allow_proctoring_opt_out',
        'proctoring_provider',
        'proctoring_escalation_email',
        'create_zendesk_tickets',
    ]

    def get(self, request, course_id):
        """ GET handler """
        with modulestore().bulk_operations(CourseKey.from_string(course_id)):
            course_block = self._get_and_validate_course_access(request.user, course_id)
            course_metadata = CourseMetadata().fetch_all(course_block)
            proctored_exam_settings = self._get_proctored_exam_setting_values(course_metadata)

            data = {}

            data['proctored_exam_settings'] = proctored_exam_settings
            data['course_start_date'] = course_metadata['start'].get('value')

            available_providers = get_available_providers()
            requires_escalation_email_providers = (
                get_requires_escalation_email_providers()
            )
            if not exams_ida_enabled(CourseKey.from_string(course_id)):
                available_providers.remove('lti_external')
                requires_escalation_email_providers.remove('lti_external')

            data['available_proctoring_providers'] = available_providers
            data['requires_escalation_email_providers'] = requires_escalation_email_providers

            serializer = ProctoredExamConfigurationSerializer(data)

            return Response(serializer.data)

    def post(self, request, course_id):
        """ POST handler """
        serializer = ProctoredExamSettingsSerializer if request.user.is_staff \
            else LimitedProctoredExamSettingsSerializer
        exam_config = serializer(data=request.data.get('proctored_exam_settings', {}))
        valid_request = exam_config.is_valid()
        if not request.user.is_staff and valid_request and ProctoredExamSettingsSerializer(
            data=request.data.get('proctored_exam_settings', {})
        ).is_valid():
            return Response(status=status.HTTP_403_FORBIDDEN)

        with modulestore().bulk_operations(CourseKey.from_string(course_id)):
            course_block = self._get_and_validate_course_access(request.user, course_id)
            course_metadata = CourseMetadata().fetch_all(course_block)

            models_to_update = {}
            for setting_key, value in exam_config.data.items():
                model = course_metadata.get(setting_key)
                if model:
                    models_to_update[setting_key] = copy.deepcopy(model)
                    models_to_update[setting_key]['value'] = value

            # validate data formats and update the course block object
            is_valid, errors, updated_data = CourseMetadata.validate_and_update_from_json(
                course_block,
                models_to_update,
                user=request.user,
            )

            if not is_valid:
                error_messages = [{error.get('key'): error.get('message')} for error in errors]
                return Response(
                    {'detail': error_messages},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # save to mongo
            modulestore().update_item(course_block, request.user.id)

            # merge updated settings with all existing settings.
            # do this because fields that could not be modified are excluded from the result
            course_metadata = {**course_metadata, **updated_data}
            updated_settings = self._get_proctored_exam_setting_values(course_metadata)
            serializer = ProctoredExamSettingsSerializer(updated_settings)
            return Response({
                'proctored_exam_settings': serializer.data
            })

    @classmethod
    def _get_proctored_exam_setting_values(cls, course_metadata):
        return {
            setting_key: course_metadata[setting_key].get('value')
            for setting_key in cls.PROCTORED_EXAM_SETTINGS_KEYS
        }

    @staticmethod
    def _get_and_validate_course_access(user, course_id):
        """
        Check if course_id exists and is accessible by the user.

        Returns a course_block object
        """
        course_key = CourseKey.from_string(course_id)
        course_block = get_course_and_check_access(course_key, user)

        if not course_block:
            raise NotFound(
                f'Course with course_id {course_id} does not exist.'
            )

        return course_block


@view_auth_classes(is_authenticated=True)
class ProctoringErrorsView(DeveloperErrorViewMixin, APIView):
    """
    View for getting the proctoring errors for a course with url to proctored exam settings.
    """
    @apidocs.schema(
        parameters=[
            apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID"),
        ],
        responses={
            200: ProctoringErrorsSerializer,
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists()
    def get(self, request: Request, course_id: str) -> Response:
        """
        Get an object containing proctoring errors in a course.

        **Example Request**

            GET /api/contentstore/v1/proctoring_errors/{course_id}

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a list of object proctoring errors.
        Also response contains mfe proctored exam settings url.
        For each item returned an object that contains the following fields:

        * **key**: This is proctoring settings key.
        * **message**: This is a description for proctoring error.
        * **model**: This is proctoring provider model object.

        **Example Response**

        ```json
        {
            "mfe_proctored_exam_settings_url": "http://course-authoring-mfe/course/course_key/proctored-exam-settings",
            "proctoring_errors": [
                {
                "key": "proctoring_provider",
                "message": "The proctoring provider cannot be modified after a course has started.",
                "model": {
                    "value": "null",
                    "display_name": "Proctoring Provider",
                    "help": "Enter the proctoring provider you want to use for this course run.",
                    "deprecated": false,
                    "hide_on_enabled_publisher": false
                }}
            ],
        }
        ```
        """
        course_key = CourseKey.from_string(course_id)
        if not check_course_advanced_settings_access(
            request.user, course_key, access_type='feature_restricted'
        ):
            self.permission_denied(request)

        course_block = modulestore().get_course(course_key)
        advanced_dict = CourseMetadata.fetch(course_block)
        if settings.FEATURES.get('DISABLE_MOBILE_COURSE_AVAILABLE', False):
            advanced_dict.get('mobile_available')['deprecated'] = True

        proctoring_errors = CourseMetadata.validate_proctoring_settings(course_block, advanced_dict, request.user)
        proctoring_context = {
            'mfe_proctored_exam_settings_url': get_proctored_exam_settings_url(course_key),
            'proctoring_errors': proctoring_errors,
        }

        serializer = ProctoringErrorsSerializer(data=proctoring_context)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)
