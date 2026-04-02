""" API Views for course advanced settings """

import edx_api_doc_tools as apidocs
from opaque_keys.edx.keys import CourseKey
from openedx_authz.constants.permissions import COURSES_EDIT_GRADING_SETTINGS
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.djangoapps.models.settings.course_grading import CourseGradingModel
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import authz_permission_required
from openedx.core.djangoapps.credit.tasks import update_credit_course_requirements
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, verify_course_exists, view_auth_classes
from ..serializers import CourseGradingModelSerializer


@view_auth_classes(is_authenticated=True)
class AuthoringGradingView(DeveloperErrorViewMixin, APIView):
    """
    View for getting and setting the advanced settings for a course.
    """
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

            POST /api/contentstore/v0/course_grading/{course_id}

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
