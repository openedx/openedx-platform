"""
Support tool for changing course enrollments.
"""

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db.models import Q
from django.utils.decorators import method_decorator
from django.views.generic import View
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from social_django.models import UserSocialAuth

from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.third_party_auth.models import SAMLProviderConfig
from lms.djangoapps.program_enrollments.api import (
    fetch_program_enrollments_by_student,
    get_users_by_external_keys_and_org_key,
)
from lms.djangoapps.program_enrollments.exceptions import (
    BadOrganizationShortNameException,
    ProviderDoesNotExistException,
)
from lms.djangoapps.support.decorators import require_support_permission
from lms.djangoapps.support.serializers import ProgramEnrollmentSerializer, serialize_user_info
from lms.djangoapps.support.views.utils import validate_and_link_program_enrollments
from lms.djangoapps.verify_student.services import IDVerificationService

TEMPLATE_PATH = 'support/link_program_enrollments.html'
DATETIME_FORMAT = '%Y-%m-%dT%H:%M:%S'


class LinkProgramEnrollmentSupportView(View):
    """
    Allows viewing and changing learner enrollments by support
    staff.
    """
    # TODO: ARCH-91
    # This view is excluded from Swagger doc generation because it
    # does not specify a serializer class.
    exclude_from_schema = True

    @method_decorator(require_support_permission)
    def get(self, request):
        return render_to_response(
            TEMPLATE_PATH,
            {
                'successes': [],
                'errors': [],
                'program_uuid': '',
                'text': '',
            }
        )

    @method_decorator(require_support_permission)
    def post(self, request):
        """
        Link the given program enrollments and lms users
        """
        program_uuid = request.POST.get('program_uuid', '').strip()
        text = request.POST.get('text', '')
        successes, errors = validate_and_link_program_enrollments(program_uuid, text)
        return render_to_response(
            TEMPLATE_PATH,
            {
                'successes': successes,
                'errors': errors,
                'program_uuid': program_uuid,
                'text': text,
            }
        )


class LinkProgramEnrollmentSupportAPIView(APIView):
    """
    Support-only API View for linking learner enrollments by support staff.
    """
    permission_classes = (
        IsAuthenticated,
    )

    @method_decorator(require_support_permission)
    def post(self, request):
        """
        Links learner enrollments by support staff
        * Example Request:
            - POST / support / link_program_enrollments_details/
            * Sample Payload
            {
                program_uuid: < program_uuid > ,
                username_pair_text: 'external_user_key,lms_username'
            }
        * Example Response:
            {
                program_uuid: < program_uuid>,
                username_pair_text: 'external_user_key,lms_username'
                successes: 'Success messages if Linkages are created',
                errors: 'Error messages if there is no linkages'
            }
        """

        program_uuid = request.POST.get('program_uuid', '').strip()
        username_pair_text = request.POST.get('username_pair_text', '')
        successes, errors = validate_and_link_program_enrollments(program_uuid, username_pair_text)
        data = {
            'successes': successes,
            'errors': errors,
            'program_uuid': program_uuid,
            'username_pair_text': username_pair_text,
        }
        return Response(data)


class ProgramEnrollmentInspector:
    """
    A common class to provide functionality of search and display the program enrollments
    information of a learner for Program Inspector Views and APIViews.
    """

    def _get_org_keys_and_idps(self):
        """
        From our Third_party_auth models, return a dictionary of
        of organizations keys and their correspondingactive and configured SAMLProviders
        """
        saml_providers = SAMLProviderConfig.objects.current_set().filter(
            enabled=True,
            organization__isnull=False
        ).select_related('organization')

        return {
            saml_provider.organization.short_name: saml_provider for saml_provider in saml_providers
        }

    def _get_account_info(self, username_or_email, idp_provider=None):
        """
        Provided the edx account username or email, and the SAML provider selected,
        return edx account info and program_enrollments_info.
        If we cannot identify the user, return empty object and error.
        """
        try:
            user = User.objects.get(Q(username=username_or_email) | Q(email=username_or_email))
            user_social_auths = None
            user_social_auths = UserSocialAuth.objects.filter(user=user)
            if idp_provider:
                user_social_auths = user_social_auths.filter(provider=idp_provider.backend_name)
            user_info = serialize_user_info(user, user_social_auths)
            enrollments = self._get_enrollments(user=user)
            result = {'user': user_info}
            if enrollments:
                result['enrollments'] = enrollments

            result['id_verification'] = IDVerificationService.user_status(user)
            return result, ''
        except User.DoesNotExist:
            return {}, f'Could not find edx account with {username_or_email}'

    def _get_external_user_info(self, external_user_key, org_key, idp_provider=None):
        """
        Provided the external_user_key and org_key, return edx account info
        and program_enrollments_info if any. If we cannot identify the data,
        return empty object.
        """
        found_user = None
        result = {}
        try:
            users_by_key = get_users_by_external_keys_and_org_key(
                [external_user_key],
                org_key
            )
            # Remove entries with no corresponding user and convert keys to lowercase
            users_by_key_lower = {key.lower(): value for key, value in users_by_key.items() if value}
            found_user = users_by_key_lower.get(external_user_key.lower())
        except (
            BadOrganizationShortNameException,
            ProviderDoesNotExistException
        ):
            # We cannot identify edX user from external_user_key and org_key pair
            pass

        enrollments = self._get_enrollments(external_user_key=external_user_key)
        if enrollments:
            result['enrollments'] = enrollments
        if found_user:
            user_social_auths = UserSocialAuth.objects.filter(user=found_user)
            if idp_provider:
                user_social_auths = user_social_auths.filter(provider=idp_provider.backend_name)
            user_info = serialize_user_info(found_user, user_social_auths)
            result['user'] = user_info
            result['id_verification'] = IDVerificationService.user_status(found_user)
        elif 'enrollments' in result:
            result['user'] = {'external_user_key': external_user_key}

        return result

    def _get_enrollments(self, user=None, external_user_key=None):
        """
        With the user or external_user_key passed in,
        return an array of dictionariers with corresponding ProgramEnrollments
        and ProgramCourseEnrollments all serialized for view
        """
        program_enrollments = fetch_program_enrollments_by_student(
            user=user,
            external_user_key=external_user_key
        ).prefetch_related('program_course_enrollments')
        serialized = ProgramEnrollmentSerializer(program_enrollments, many=True)
        return serialized.data


class SAMLProvidersWithOrg(APIView):
    """
    Support-only API View for fetching a list of all
    organizations names which will be utilized as keys.
    """
    @method_decorator(require_support_permission)
    def get(self, request):
        """
        The get request returns a list of all
        organizations names which will be utilized as keys.
        * Example Request:
            - GET /support/get_saml_providers/
        * Example Response:
            [
                'test_org',
                'donut_org',
                'tri_org'
            ]
        """
        org_key_names = self._get_org_key_names()
        return Response(data=org_key_names)

    def _get_org_key_names(self):
        """
        From our Third_party_auth models, return a list of
        of organizations names which will be utilized as keys.
        """
        saml_providers = SAMLProviderConfig.objects.current_set().filter(
            enabled=True,
            organization__isnull=False
        ).select_related('organization')

        return [saml_provider.organization.short_name for saml_provider in saml_providers]


class ProgramEnrollmentsInspectorView(ProgramEnrollmentInspector, View):
    """
    The view to search and display the program enrollments
    information of a learner.
    """
    exclude_from_schema = True
    CONSOLE_TEMPLATE_PATH = 'support/program_enrollments_inspector.html'

    @method_decorator(require_support_permission)
    def get(self, request):
        """
        Based on the query string parameters passed through the GET request
        Search the data store for information about ProgramEnrollment and
        SSO linkage with the user.
        """
        search_error = ''
        edx_username_or_email = request.GET.get('edx_user', '').strip()
        org_key = request.GET.get('org_key', '').strip()
        external_user_key = request.GET.get('external_user_key', '').strip()
        learner_program_enrollments = {}
        saml_providers_with_org_key = self._get_org_keys_and_idps()
        selected_provider = None
        if org_key:
            selected_provider = saml_providers_with_org_key.get(org_key)
        if edx_username_or_email:
            learner_program_enrollments, search_error = self._get_account_info(
                edx_username_or_email,
                selected_provider,
            )
        elif org_key and external_user_key:
            learner_program_enrollments = self._get_external_user_info(
                external_user_key,
                org_key,
                selected_provider,
            )
            if not learner_program_enrollments:
                search_error = 'No user found for external key {} for institution {}'.format(  # noqa: UP032
                    external_user_key, org_key
                )
        elif not org_key and not external_user_key:
            # This is initial rendering state.
            pass
        else:
            search_error = (
                "To perform a search, you must provide either the student's "
                "(a) edX username, "
                "(b) email address associated with their edX account, or "
                "(c) Identity-providing institution and external key!"
            )

        return render_to_response(
            self.CONSOLE_TEMPLATE_PATH,
            {
                'error': search_error,
                'learner_program_enrollments': learner_program_enrollments,
                'org_keys': sorted(saml_providers_with_org_key.keys()),
            }
        )


class ProgramEnrollmentsInspectorAPIView(ProgramEnrollmentInspector, APIView):
    """
    The APIview to search and display the program enrollments
    information of a learner.
    """

    permission_classes = (
        IsAuthenticated,
    )

    @method_decorator(require_support_permission)
    def get(self, request):
        """
        Based on the query string parameters passed through the GET request
        Search the data store for information about ProgramEnrollment and
        SSO linkage with the user.
        * Example Request:
            - GET / support/program_enrollments_inspector_details?
                    edx_user=<edx_user>&org_key=<org_key>&external_user_key=<external_user_key>
        * Example Response:
            {
                learner_program_enrollments: {
                    "user": {
                        "username": "edx",
                        "email": "edx@example.com"
                    },
                    "id_verification": {
                        "status": "none",
                        "error": <error>,
                        "should_display": true,
                        "status_date": <status_date>,
                        "verification_expiry": <verification_expiry>
                    },
                    "enrollments": [
                        {
                            "created": "2021-11-25T04:56:25",
                            "modified": "2021-12-19T22:27:34",
                            "external_user_key": "testuser",
                            "status": "enrolled",
                            "program_uuid": <program_uuid>,
                            "program_course_enrollments": [],
                            "program_name": <program_name>
                        }
                    ],
                    "user": {
                        "external_user_key": "testuser"
                    }
                },
                org_key: < org_key >
                errors: 'Error messages for invalid query'
            }
        """
        search_error = ''
        edx_username_or_email = request.query_params.get('edx_user', '').strip()
        org_key = request.query_params.get('org_key', '').strip()
        external_user_key = request.query_params.get('external_user_key', '').strip()
        learner_program_enrollments = {}
        saml_providers_with_org_key = self._get_org_keys_and_idps()
        selected_provider = None
        if org_key:
            selected_provider = saml_providers_with_org_key.get(org_key)
        if edx_username_or_email:
            learner_program_enrollments, search_error = self._get_account_info(
                edx_username_or_email,
                selected_provider,
            )
        elif org_key and external_user_key:
            learner_program_enrollments = self._get_external_user_info(
                external_user_key,
                org_key,
                selected_provider,
            )
            if not learner_program_enrollments:
                search_error = 'No user found for external key {} for institution {}'.format(  # noqa: UP032
                    external_user_key, org_key
                )
        else:
            search_error = (
                "To perform a search, you must provide either the student's "
                "(a) edX username, "
                "(b) email address associated with their edX account, or "
                "(c) Identity-providing institution and external key!"
            )
        return Response(data={
            'error': search_error,
            'learner_program_enrollments': learner_program_enrollments,
            'org_keys': org_key,
        })
