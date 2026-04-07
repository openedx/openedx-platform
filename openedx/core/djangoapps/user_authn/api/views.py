"""
Authn API Views
"""

from django.conf import settings
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from common.djangoapps.student.helpers import get_next_url_for_login_page
from common.djangoapps.student.views import compose_and_send_activation_email
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_authn.api.helper import RegistrationFieldsContext
from openedx.core.djangoapps.user_authn.serializers import MFEContextSerializer
from openedx.core.djangoapps.user_authn.views.utils import get_mfe_context


class MFEContextThrottle(AnonRateThrottle):
    """
    Setting rate limit for MFEContextView API
    """
    rate = settings.LOGISTRATION_API_RATELIMIT


class MFEContextView(APIView):
    """
    API to get third party auth providers, optional fields, required fields,
    user country code and the currently running pipeline.
    """
    throttle_classes = [MFEContextThrottle]

    def _get_optional_fields_context(self):
        """
        Fetch optional fields data for registration form
        """
        optional_fields_context = {}
        optional_fields = RegistrationFieldsContext('optional').get_fields()
        if optional_fields:
            extended_profile_fields = configuration_helpers.get_value('extended_profile_fields')
            optional_fields_context = {
                'fields': optional_fields,
                'extended_profile': extended_profile_fields if extended_profile_fields else []
            }

        return optional_fields_context

    def get(self, request, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
        """
        Returns
        - dynamic registration fields
        - dynamic optional fields
        - the context for third party auth providers
        - user country code
        - the currently running pipeline.

        Arguments:
            request (HttpRequest): The request, used to determine if a pipeline
                is currently running.
            kwargs (dict):
                tpa_hint (string): An override flag that will return a matching provider
                    as long as its configuration has been enabled
                is_register_page (bool): Determine the call is from register or login page
                is_welcome_page (bool): Checks if the call is from the Welcome Page
        """
        request_params = request.GET
        redirect_to = get_next_url_for_login_page(request)
        third_party_auth_hint = request_params.get('tpa_hint')

        context = {
            'context_data': get_mfe_context(request, redirect_to, third_party_auth_hint),
            'registration_fields': {
                'fields': {},
            },
            'optional_fields': {
                'fields': {},
                'extended_profile': []
            },
        }

        if settings.ENABLE_DYNAMIC_REGISTRATION_FIELDS:
            if request_params.get('is_welcome_page'):
                optional_fields = self._get_optional_fields_context()
                context = {
                    'context_data': {
                        'welcomePageRedirectUrl': redirect_to if redirect_to == request_params.get('next') else None,
                    },
                    'optional_fields': optional_fields,
                }
                return Response(
                    status=status.HTTP_200_OK,
                    data=MFEContextSerializer(
                        context if optional_fields else {}
                    ).data
                )

            if request_params.get('is_register_page'):
                registration_fields = RegistrationFieldsContext().get_fields()
                context['registration_fields'].update({
                    'fields': registration_fields,
                })

                optional_fields = self._get_optional_fields_context()
                if optional_fields:
                    context['optional_fields'].update(optional_fields)

        return Response(
            status=status.HTTP_200_OK,
            data=MFEContextSerializer(
                context
            ).data
        )


class SendAccountActivationEmail(APIView):
    """
    API to to send the account activation email using account activation cta.
    """
    authentication_classes = (SessionAuthenticationAllowInactiveUser,)
    permission_classes = (IsAuthenticated,)

    def post(self, request, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
        """
        Returns status code.
        Arguments:
            request (HttpRequest): The request, used to get the user
        """
        try:
            user = request.user
            if not user.is_active:
                compose_and_send_activation_email(user, user.profile)
            return Response(
                status=status.HTTP_200_OK
            )
        except Exception:  # pylint: disable=broad-except
            return Response(
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
