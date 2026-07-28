""" API Views for course certificates """

import edx_api_doc_tools as apidocs
from opaque_keys.edx.keys import CourseKey
from openedx_authz.constants.permissions import COURSES_MANAGE_CERTIFICATES
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.djangoapps.contentstore.rest_api.v1.serializers import CourseCertificatesSerializer
from cms.djangoapps.contentstore.utils import get_certificates_context
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import user_has_course_permission
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, verify_course_exists, view_auth_classes
from xmodule.modulestore.django import modulestore


@view_auth_classes(is_authenticated=True)
class CourseCertificatesView(DeveloperErrorViewMixin, APIView):
    """
    View for course certificate page.
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                "course_id", apidocs.ParameterLocation.PATH, description="Course ID"
            ),
        ],
        responses={
            200: CourseCertificatesSerializer,
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists()
    def get(self, request: Request, course_id: str):
        """
        Get an object containing course's certificates.

        **Example Request**

            GET /api/contentstore/v1/certificates/{course_id}

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a single dict that contains keys that
        are the course's certificates.

        **Example Response**

        ```json
        {
            "certificate_activation_handler_url": "/certificates/activation/course-v1:org+101+101/",
            "certificate_web_view_url": "///certificates/course/course-v1:org+101+101?preview=honor",
            "certificates": [
                {
                    "course_title": "Course title",
                    "description": "Description of the certificate",
                    "editing": false,
                    "id": 1622146085,
                    "is_active": false,
                    "name": "Name of the certificate",
                    "signatories": [
                        {
                            "id": 268550145,
                            "name": "name_sign",
                            "organization": "org",
                            "signature_image_path": "/asset-v1:org+101+101+type@asset+block@camera.png",
                            "title": "title_sign"
                        }
                    ],
                    "version": 1
                },
            ],
            "course_modes": [
                "honor"
            ],
            "has_certificate_modes": true,
            "is_active": false,
            "is_global_staff": true,
            "mfe_proctored_exam_settings_url": "",
            "course_number": "DemoX",
            "course_title": "Demonstration Course",
            "course_number_override": "Course Number Display String"
        }
        ```
        """
        course_key = CourseKey.from_string(course_id)
        store = modulestore()

        if not user_has_course_permission(
            request.user,
            COURSES_MANAGE_CERTIFICATES.identifier,
            course_key,
            LegacyAuthoringPermission.WRITE
        ):
            self.permission_denied(request)

        with store.bulk_operations(course_key):
            course = modulestore().get_course(course_key)
            certificates_context = get_certificates_context(course, request.user)
            serializer = CourseCertificatesSerializer(certificates_context)
            return Response(serializer.data)
