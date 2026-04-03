"""
View for course live app
"""
from typing import Dict

import edx_api_doc_tools as apidocs
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from lti_consumer.api import get_lti_pii_sharing_state_for_course
from opaque_keys.edx.keys import CourseKey
from rest_framework import permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import ValidationError
from rest_framework.views import APIView

from common.djangoapps.util.views import ensure_valid_course_key
from lms.djangoapps.courseware.courses import get_course_with_access
from openedx.core.djangoapps.course_live.permissions import IsEnrolledOrStaff, IsStaffOrInstructor
from openedx.core.djangoapps.course_live.tab import CourseLiveTab
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser

from ...lib.api.view_utils import verify_course_exists
from .models import CourseLiveConfiguration
from .providers import ProviderManager
from .serializers import CourseLiveConfigurationSerializer


class CourseLiveConfigurationView(APIView):
    """
    View for configuring CourseLive settings.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser
    )
    permission_classes = (IsStaffOrInstructor,)

    @apidocs.schema(
        parameters=[
            apidocs.path_parameter(
                'course_id',
                str,
                description="The course for which to get provider list",
            )
        ],
        responses={
            200: CourseLiveConfigurationSerializer,
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @ensure_valid_course_key
    @verify_course_exists()
    def get(self, request: Request, course_id: str) -> Response:
        """
        Handle HTTP/GET requests
        """
        configuration = CourseLiveConfiguration.get(course_id) or CourseLiveConfiguration()
        serializer = CourseLiveConfigurationSerializer(configuration, context={
            "pii_sharing_allowed": get_lti_pii_sharing_state_for_course(course_id),
            "course_id": course_id
        })

        return Response(serializer.data)

    @apidocs.schema(
        parameters=[
            apidocs.path_parameter(
                'course_id',
                str,
                description="The course for which to get provider list",
            ),
            apidocs.path_parameter(
                'lti_1p1_client_key',
                str,
                description="The LTI provider's client key",
            ),
            apidocs.path_parameter(
                'lti_1p1_client_secret',
                str,
                description="The LTI provider's client secretL",
            ),
            apidocs.path_parameter(
                'lti_1p1_launch_url',
                str,
                description="The LTI provider's launch URL",
            ),
            apidocs.path_parameter(
                'provider_type',
                str,
                description="The LTI provider's launch URL",
            ),
            apidocs.parameter(
                'lti_config',
                apidocs.ParameterLocation.QUERY,
                object,
                description="The lti_config object with required additional parameters ",
            ),
        ],
        responses={
            200: CourseLiveConfigurationSerializer,
            400: "Required parameters are missing.",
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @ensure_valid_course_key
    @verify_course_exists()
    def post(self, request, course_id: str) -> Response:
        """
        Handle HTTP/POST requests
        """
        pii_sharing_allowed = get_lti_pii_sharing_state_for_course(course_id)
        provider = ProviderManager().get_enabled_providers().get(request.data.get('provider_type', ''), None)
        if not pii_sharing_allowed and provider.requires_pii_sharing():
            return Response({
                "pii_sharing_allowed": pii_sharing_allowed,
                "message": "PII sharing is not allowed on this course"
            })
        if provider and not provider.additional_parameters and request.data.get('lti_configuration', False):
            # Add empty lti config if none is provided in case additional params are not required
            request.data['lti_configuration']['lti_config'] = {'additional_parameters': {}}
        configuration = CourseLiveConfiguration.get(course_id)
        serializer = CourseLiveConfigurationSerializer(
            configuration,
            data=request.data,
            context={
                "pii_sharing_allowed": pii_sharing_allowed,
                "course_id": course_id,
                "provider": provider
            }
        )
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)
        serializer.save()
        return Response(serializer.data)


class CourseLiveProvidersView(APIView):
    """
    Read only view that lists details of LIVE providers available for a course.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser
    )
    permission_classes = (IsStaffOrInstructor,)

    @ensure_valid_course_key
    @verify_course_exists()
    def get(self, request, course_id: str, **_kwargs) -> Response:
        """
            A view for retrieving Program live IFrame .

            Path: ``api/course_live/providers/{course_id}/``

            Accepts: [GET]

            ------------------------------------------------------------------------------------
            GET
            ------------------------------------------------------------------------------------

            **Returns**
                * 200: Returns list of providers with active provider,
                * 401: The requester is not authenticated.
                * 403: The requester cannot access the specified course.
                * 404: The requested course does not exist.
            **Response**

                In the case of a 200 response code, the response will be available live providers.

            **Example**

                {
                    "providers": {
                        "active": "zoom",
                        "available": {
                            'zoom': {
                                'name': 'Zoom LTI PRO',
                                'features': []
                            }
                        }
                    }
                }

            """
        data = self.get_provider_data(course_id)
        return Response(data)

    @staticmethod
    def get_provider_data(course_id: str) -> Dict:
        """
        Get provider data for specified course
        Args:
            course_id (str): course key string

        Returns:
            Dict: course Live providers
        """
        configuration = CourseLiveConfiguration.get(course_id)
        providers = ProviderManager().get_enabled_providers()
        selected_provider = providers.get(configuration.provider_type if configuration else None, None)
        return {
            "providers": {
                "active": selected_provider.id if selected_provider else "",
                "available": {key: provider.__dict__() for (key, provider) in providers.items()}
            }
        }


class CourseLiveIframeView(APIView):
    """
    A view for retrieving course live iFrame.

    Path: ``api/course_live/iframe/{course_id}/``

    Accepts: [GET]

    ------------------------------------------------------------------------------------
    GET
    ------------------------------------------------------------------------------------

    **Returns**

        * 200: OK - Contains a course live zoom iframe.
        * 401: The requesting user is not authenticated.
        * 403: The requesting user lacks access to the course.
        * 404: The requested course does not exist.

    **Response**

        In the case of a 200 response code, the response will be iframe HTML.

    **Example**

        {
            "iframe": "
                        <iframe
                            id='lti-tab-embed'
                            style='width: 100%; min-height: 800px; border: none'
                            srcdoc='{srcdoc}'
                            >
                        </iframe>
                        ",
        }

    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser
    )
    permission_classes = (permissions.IsAuthenticated, IsEnrolledOrStaff)

    @ensure_valid_course_key
    @verify_course_exists()
    def get(self, request, course_id: str, **_kwargs) -> Response:
        """
        Handle HTTP/GET requests
        """
        course_key = CourseKey.from_string(course_id)
        course_live_tab = CourseLiveTab({})
        course = get_course_with_access(request.user, 'load', course_key)

        if not course_live_tab.is_enabled(course, request.user):
            error_data = {
                "developer_message": "Course live is not enabled for this course."
            }
            return Response(error_data, status=status.HTTP_200_OK)

        iframe = course_live_tab.render_to_fragment(request, course)
        data = {
            "iframe": iframe.content
        }
        return Response(data, status=status.HTTP_200_OK)
