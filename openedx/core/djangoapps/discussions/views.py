"""
Handle view-logic for the discussions app.
"""
from typing import Dict  # noqa: UP035

import edx_api_doc_tools as apidocs
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.view_utils import validate_course_key

from .config.waffle import ENABLE_NEW_STRUCTURE_DISCUSSIONS
from .models import AVAILABLE_PROVIDER_MAP, DiscussionsConfiguration, Features, Provider
from .permissions import HasPagesAndResourcesAccess, check_course_permissions
from .serializers import DiscussionsConfigurationSerializer, DiscussionsProvidersSerializer
from .tasks import update_discussions_settings_from_course_task


class DiscussionsConfigurationSettingsView(APIView):
    """
    View for configuring discussion settings.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser
    )
    permission_classes = (HasPagesAndResourcesAccess,)

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'course_id',
                apidocs.ParameterLocation.PATH,
                description="The course for which to get provider list",
            ),
            apidocs.string_parameter(
                'provider_id',
                apidocs.ParameterLocation.QUERY,
                description="The provider_id to fetch data for"
            )
        ],
        responses={
            200: DiscussionsConfigurationSerializer,
            400: "Invalid provider ID",
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    def get(self, request: Request, course_key_string: str, **_kwargs) -> Response:
        """
        Handle HTTP/GET requests
        """
        data = self.get_configuration_data(request, course_key_string)
        return Response(data)

    @staticmethod
    def get_configuration_data(request: Request, course_key_string: str) -> Dict:  # noqa: UP006
        """
        Get discussions configuration data for the course
        Args:
            request (Request): a DRF request
            course_key_string (str): a course key string

        Returns:
            Dict: Discussion configuration data for the course
        """
        course_key = validate_course_key(course_key_string)
        configuration = DiscussionsConfiguration.get(course_key)
        provider_type = request.query_params.get('provider_id', None)
        if provider_type and provider_type not in AVAILABLE_PROVIDER_MAP:
            raise ValidationError("Unsupported provider type")
        serializer = DiscussionsConfigurationSerializer(
            configuration,
            context={
                'user_id': request.user.id,
                'provider_type': provider_type,
            }
        )
        return serializer.data

    def post(self, request, course_key_string: str, **_kwargs) -> Response:
        """
        Handle HTTP/POST requests
        """
        data = self.update_configuration_data(request, course_key_string)
        return Response(data)

    @staticmethod
    def update_configuration_data(request, course_key_string):
        """
        Update discussion configuration for the course based on data in the request.
        Args:
            request (Request): a DRF request
            course_key_string (str): a course key string

        Returns:
            Dict: modified course configuration data
        """
        course_key = validate_course_key(course_key_string)
        configuration = DiscussionsConfiguration.get(course_key)
        course = CourseOverview.get_from_id(course_key)
        serializer = DiscussionsConfigurationSerializer(
            configuration,
            context={
                'user_id': request.user.id,
            },
            data=request.data,
            partial=True,
        )
        if serializer.is_valid(raise_exception=True):
            new_provider_type = serializer.validated_data.get('provider_type', None)
            if new_provider_type is not None and new_provider_type != configuration.provider_type:
                check_course_permissions(course, request.user, 'change_provider')

            serializer.save()
        return serializer.data


class DiscussionsProvidersView(APIView):
    """
    Read only view that lists details of discussion providers available for a course.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser
    )
    permission_classes = (HasPagesAndResourcesAccess,)

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'course_id',
                apidocs.ParameterLocation.PATH,
                description="The course for which to get provider list",
            )
        ],
        responses={
            200: DiscussionsProvidersSerializer,
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    def get(self, request, course_key_string: str, **_kwargs) -> Response:
        """
        Handle HTTP/GET requests
        """
        # Return all providers always, if the user is staff
        data = self.get_provider_data(course_key_string, show_all=False)
        return Response(data)

    @staticmethod
    def get_provider_data(course_key_string: str, show_all: bool = False) -> Dict:  # noqa: UP006
        """
        Get provider data for specified course
        Args:
            course_key_string (str): course key string
            show_all (bool): don't hide any providers

        Returns:
            Dict: course discussion providers
        """
        course_key = validate_course_key(course_key_string)
        configuration = DiscussionsConfiguration.get(course_key)
        hidden_providers = []

        if not show_all:
            # If the new style discussions are enabled, then hide the legacy provider unless it's already in use.
            if ENABLE_NEW_STRUCTURE_DISCUSSIONS.is_enabled(course_key):
                if configuration.provider_type != Provider.LEGACY:
                    hidden_providers.append(Provider.LEGACY)
            # If new discussions is not enabled, hide the new provider
            else:
                if configuration.provider_type != Provider.OPEN_EDX:
                    hidden_providers.append(Provider.OPEN_EDX)
        else:
            # if new discussions is not enabled, hide the new provider in case it is not already in use
            if not ENABLE_NEW_STRUCTURE_DISCUSSIONS.is_enabled(course_key):
                if configuration.provider_type != Provider.OPEN_EDX:
                    hidden_providers.append(Provider.OPEN_EDX)

        if configuration.provider_type != Provider.LEGACY:
            hidden_providers.append(Provider.LEGACY)
        else:
            hidden_providers.append(Provider.OPEN_EDX)

        serializer = DiscussionsProvidersSerializer(
            {
                'features': [
                    {'id': feature.value, 'feature_support_type': feature.feature_support_type}
                    for feature in Features
                ],
                'active': configuration.provider_type,
                'available': {
                    key: value
                    for key, value in AVAILABLE_PROVIDER_MAP.items()
                    if key not in hidden_providers
                },
            }
        )
        return serializer.data


class CombinedDiscussionsConfigurationView(DiscussionsConfigurationSettingsView):
    """
    Combined view that includes both provider data and discussion configuration.

    Note:
        This is temporary code for backwards-compatibility and will be removed soon
        after the frontend supports the new split APIs.
    """

    def get(self, request: Request, course_key_string: str, **_kwargs) -> Response:
        """
        Handle HTTP/GET requests
        """
        config_data = self.get_configuration_data(request, course_key_string)
        provider_data = DiscussionsProvidersView.get_provider_data(course_key_string, show_all=request.user.is_staff)
        return Response({
            **config_data,
            "features": provider_data["features"],
            "providers": {
                "active": provider_data["active"],
                "available": provider_data["available"],
            },
        })

    def post(self, request, course_key_string: str, **_kwargs) -> Response:
        """
        Handle HTTP/POST requests
        """
        config_data = self.update_configuration_data(request, course_key_string)
        provider_data = DiscussionsProvidersView.get_provider_data(course_key_string, request.user.is_staff)
        return Response(
            {
                **config_data,
                "features": provider_data["features"],
                "providers": {
                    "active": provider_data["active"],
                    "available": provider_data["available"],
                },
            }
        )


class SyncDiscussionTopicsView(APIView):
    """
    View for syncing discussion topics for a course.
    """
    authentication_classes = (BearerAuthenticationAllowInactiveUser, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, HasPagesAndResourcesAccess)

    def post(self, request, course_key_string):
        """
        Sync discussion topics for the course based on data in the request.

        Args:
            request (Request): a DRF request
            course_key_string (str): a course key string

        Returns:
            Response: modified course configuration data
        """
        update_discussions_settings_from_course_task(course_key_string)
        return Response({
            "status": "success",
            "message": "Discussion topics synced successfully."
        })
