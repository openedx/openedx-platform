""" API Views for course grading """

import edx_api_doc_tools as apidocs
from django.conf import settings
from opaque_keys.edx.keys import CourseKey
from openedx_authz.constants.permissions import COURSES_VIEW_GRADING_SETTINGS, COURSES_EDIT_GRADING_SETTINGS
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.djangoapps.models.settings.course_grading import CourseGradingModel
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import authz_permission_required
from openedx.core.djangoapps.credit.api import is_credit_course
from openedx.core.djangoapps.credit.tasks import update_credit_course_requirements
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, verify_course_exists, view_auth_classes
from xmodule.modulestore.django import modulestore

from ..serializers import CourseGradingModelSerializer, CourseGradingSerializer
from ....utils import get_course_grading


@view_auth_classes(is_authenticated=True)
class CourseGradingView(DeveloperErrorViewMixin, APIView):
    """
    View for Course Grading policy configuration.
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID"),
        ],
        responses={
            200: CourseGradingSerializer,
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists()
    @authz_permission_required(COURSES_VIEW_GRADING_SETTINGS.identifier, LegacyAuthoringPermission.READ)
    def get(self, request: Request, course_key: CourseKey):
        """
        Get an object containing course grading settings with model.

        **Example Request**

            GET /api/contentstore/v1/course_grading/{course_id}

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a single dict that contains keys that
        are the course's grading.

        **Example Response**

        ```json
        {
            "mfe_proctored_exam_settings_url": "",
            "course_assignment_lists": {
                "Homework": [
                "Section :754c5e889ac3489e9947ba62b916bdab - Subsection :56c1bc20d270414b877e9c178954b6ed"
                ]
            },
            "course_details": {
                "graders": [
                {
                    "type": "Homework",
                    "min_count": 1,
                    "drop_count": 0,
                    "short_label": "",
                    "weight": 100,
                    "id": 0
                }
                ],
                "grade_cutoffs": {
                    "A": 0.75,
                    "B": 0.63,
                    "C": 0.57,
                    "D": 0.5
                },
                "grace_period": {
                    "hours": 12,
                    "minutes": 0
                },
                "minimum_grade_credit": 0.7
            },
            "show_credit_eligibility": false,
            "is_credit_course": true,
            "default_grade_designations": ["A","B","C","D"]
        }
        ```
        """
        with modulestore().bulk_operations(course_key):
            credit_eligibility_enabled = settings.FEATURES.get("ENABLE_CREDIT_ELIGIBILITY", False)
            show_credit_eligibility = is_credit_course(course_key) and credit_eligibility_enabled

            grading_context = get_course_grading(course_key)
            grading_context['show_credit_eligibility'] = show_credit_eligibility

            serializer = CourseGradingSerializer(grading_context)
            return Response(serializer.data)

    @apidocs.schema(
        body=CourseGradingModelSerializer,
        parameters=[
            apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID"),
        ],
        responses={
            200: CourseGradingModelSerializer,
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists()
    # Please note: previous legacy permisison was checking for has_studio_read_access
    # So we are using LegacyAuthoringPermission.READ to keep compatibility
    @authz_permission_required(COURSES_EDIT_GRADING_SETTINGS.identifier, LegacyAuthoringPermission.READ)
    def post(self, request: Request, course_key: CourseKey):
        """
        Update a course's grading.

        **Example Request**

            POST /api/contentstore/v1/course_grading/{course_id}

        **POST Parameters**

        The data sent for a post request should follow next object.
        Here is an example request data that updates the ``course_grading``

        ```json
        {
            "graders": [
                {
                    "type": "Homework",
                    "min_count": 1,
                    "drop_count": 0,
                    "short_label": "",
                    "weight": 100,
                    "id": 0
                }
            ],
            "grade_cutoffs": {
                "A": 0.75,
                "B": 0.63,
                "C": 0.57,
                "D": 0.5
            },
            "grace_period": {
                "hours": 12,
                "minutes": 0
            },
            "minimum_grade_credit": 0.7,
            "is_credit_course": true
        }
        ```

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned,
        """
        if 'minimum_grade_credit' in request.data:
            update_credit_course_requirements.delay(str(course_key))

        updated_data = CourseGradingModel.update_from_json(course_key, request.data, request.user)
        serializer = CourseGradingModelSerializer(updated_data)
        return Response(serializer.data)
