""" API Views for course's settings group configurations """

import edx_api_doc_tools as apidocs
from opaque_keys.edx.keys import CourseKey
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from openedx_authz.constants.permissions import COURSES_MANAGE_GROUP_CONFIGURATIONS

from cms.djangoapps.contentstore.utils import get_group_configurations_context
from cms.djangoapps.contentstore.rest_api.v1.serializers import (
    CourseGroupConfigurationsSerializer,
)
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import authz_permission_required
from openedx.core.lib.api.view_utils import (
    DeveloperErrorViewMixin,
    verify_course_exists,
    view_auth_classes,
)
from xmodule.modulestore.django import modulestore


@view_auth_classes(is_authenticated=True)
class CourseGroupConfigurationsView(DeveloperErrorViewMixin, APIView):
    """
    View for course's settings group configurations.
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                "course_id", apidocs.ParameterLocation.PATH, description="Course ID"
            ),
        ],
        responses={
            200: CourseGroupConfigurationsSerializer,
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists()
    @authz_permission_required(
        authz_permission=COURSES_MANAGE_GROUP_CONFIGURATIONS.identifier,
        legacy_permission=LegacyAuthoringPermission.READ
    )
    def get(self, request: Request, course_key: CourseKey):
        """
        Get an object containing course's settings group configurations.

        **Example Request**

            GET /api/contentstore/v1/group_configurations/{course_id}

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a single dict that contains keys that
        are the course's settings group configurations.

        **Example Response**

        ```json
        {
            "all_group_configurations": [
                {
                    "active": true,
                    "description": "Partition for segmenting users by enrollment track",
                    "groups": [
                        {
                            "id": 2,
                            "name": "Enroll",
                            "usage": [
                                {
                                    "label": "Subsection / Unit",
                                    "url": "/container/block-v1:org+101+101+type@vertical+block@08772238547242848cef9"
                                }
                            ],
                            "version": 1
                        }
                    ],
                    "id": 50,
                    "usage": null,
                    "name": "Enrollment Track Groups",
                    "parameters": {
                        "course_id": "course-v1:org+101+101"
                    },
                    "read_only": true,
                    "scheme": "enrollment_track",
                    "version": 3
                },
                {
                    "active": true,
                    "description": "The groups in this configuration can be mapped to cohorts in the Instructor.",
                    "groups": [
                        {
                            "id": 593758473,
                            "name": "My Content Group",
                            "usage": [],
                            "version": 1
                        }
                    ],
                    "id": 1791848226,
                    "name": "Content Groups",
                    "parameters": {},
                    "read_only": false,
                    "scheme": "cohort",
                    "version": 3
                }
            ],
            "experiment_group_configurations": [
                {
                    "active": true,
                    "description": "desc",
                    "groups": [
                        {
                            "id": 276408623,
                            "name": "Group A",
                            "usage": null,
                            "version": 1
                        },
                        ...
                    ],
                    "id": 875961582,
                    "usage": [
                        {
                            "label": "Unit / Content Experiment",
                            "url": "/container/block-v1:org+101+101+type@split_test+block@90ccbbad0dac48b18c5c80",
                            "validation": null
                        },
                        ...
                    ],
                    "name": "Experiment Group Configurations 5",
                    "parameters": {},
                    "scheme": "random",
                    "version": 3
                },
                ...
            ],
            "mfe_proctored_exam_settings_url": "",
            "should_show_enrollment_track": true,
            "should_show_experiment_groups": true,
        }
        ```
        """
        store = modulestore()

        with store.bulk_operations(course_key):
            course = modulestore().get_course(course_key)
            group_configurations_context = get_group_configurations_context(course, store)
            serializer = CourseGroupConfigurationsSerializer(group_configurations_context)
            return Response(serializer.data)
