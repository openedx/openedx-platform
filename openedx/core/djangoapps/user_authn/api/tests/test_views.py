"""
Logistration API View Tests
"""
import socket
from unittest.mock import patch
from urllib.parse import urlencode

import ddt
from django.conf import settings
from django.test.utils import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.models import Registration
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth import pipeline
from common.djangoapps.third_party_auth.tests.testutil import ThirdPartyAuthTestMixin, simulate_running_pipeline
from openedx.core.djangoapps.geoinfo.api import country_code_from_ip
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration
from openedx.core.djangoapps.user_api.tests.test_views import UserAPITestCase
from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
@ddt.ddt
class MFEContextViewTest(ThirdPartyAuthTestMixin, APITestCase):
    """
    MFE context tests
    """

    def setUp(self):  # pylint: disable=arguments-differ
        """
        Test Setup
        """
        super().setUp()

        self.user = UserFactory.create(username='test_user', password='password123')
        self.url = reverse('mfe_context')
        self.query_params = {'next': '/dashboard'}

        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
        self.country_code = country_code_from_ip(ip_address)
        self.pipeline_user_details = {}

        # Several third party auth providers are created for these tests:
        self.configure_google_provider(enabled=True, visible=True)
        self.configure_facebook_provider(enabled=True, visible=True)

        self.hidden_enabled_provider = self.configure_linkedin_provider(
            visible=False,
            enabled=True,
        )

    def _third_party_login_url(self, backend_name, auth_entry, params):
        """
        Construct the login URL to start third party authentication
        """
        return '{url}?auth_entry={auth_entry}&{param_str}'.format(
            url=reverse('social:begin', kwargs={'backend': backend_name}),
            auth_entry=auth_entry,
            param_str=urlencode(params)
        )

    def get_provider_data(self, params):
        """
        Returns the expected provider data based on providers enabled in test setup
        """
        return [
            {
                'id': 'oa2-facebook',
                'name': 'Facebook',
                'iconClass': 'fa-facebook',
                'iconImage': None,
                'skipHintedLogin': False,
                'skipRegistrationForm': False,
                'loginUrl': self._third_party_login_url('facebook', 'login', params),
                'registerUrl': self._third_party_login_url('facebook', 'register', params)
            },
            {
                'id': 'oa2-google-oauth2',
                'name': 'Google',
                'iconClass': 'fa-google-plus',
                'iconImage': None,
                'skipHintedLogin': False,
                "skipRegistrationForm": False,
                'loginUrl': self._third_party_login_url('google-oauth2', 'login', params),
                'registerUrl': self._third_party_login_url('google-oauth2', 'register', params)
            },
        ]

    def get_context(self, params=None, current_provider=None, backend_name=None, add_user_details=False):
        """
        Returns the MFE context
        """

        if add_user_details:
            self.pipeline_user_details.update(
                {
                    'username': None,
                    'email': 'test@test.com',
                    'name': None,
                    'firstName': None,
                    'lastName': None
                }
            )

        return {
            'contextData': {
                'currentProvider': current_provider,
                'platformName': settings.PLATFORM_NAME,
                'providers': self.get_provider_data(params) if params else [],
                'secondaryProviders': [],
                'finishAuthUrl': pipeline.get_complete_url(backend_name) if backend_name else None,
                'errorMessage': None,
                'registerFormSubmitButtonText': 'Create Account',
                'autoSubmitRegForm': False,
                'syncLearnerProfileData': False,
                'countryCode': self.country_code,
                'welcomePageRedirectUrl': None,
                'pipelineUserDetails': self.pipeline_user_details,
            },
            'registrationFields': {
                'fields': {},
            },
            'optionalFields': {
                'fields': {},
                'extended_profile': [],
            },
        }

    @patch.dict(settings.FEATURES, {'ENABLE_THIRD_PARTY_AUTH': False})
    def test_no_third_party_auth_providers(self):
        """
        Test that if third party auth is enabled, context returned by API contains
        the provider information
        """
        response = self.client.get(self.url, self.query_params)
        assert response.status_code == 200
        assert response.data == self.get_context()

    def test_third_party_auth_providers(self):
        """
        Test that api returns details of currently enabled third party auth providers
        """
        response = self.client.get(self.url, self.query_params)
        params = {
            'next': self.query_params['next']
        }

        assert response.status_code == 200
        assert response.data == self.get_context(params)

    @ddt.data(
        ('google-oauth2', 'Google', False),
        ('facebook', 'Facebook', False),
        ('google-oauth2', 'Google', True)
    )
    @ddt.unpack
    def test_running_pipeline(self, current_backend, current_provider, add_user_details):
        """
        Test that when third party pipeline is running, the api returns details
        of current provider
        """
        email = 'test@test.com' if add_user_details else None
        params = {
            'next': self.query_params['next']
        }

        # Simulate a running pipeline
        pipeline_target = 'openedx.core.djangoapps.user_authn.views.login_form.third_party_auth.pipeline'
        with simulate_running_pipeline(pipeline_target, current_backend, email=email):
            response = self.client.get(self.url, self.query_params)

        assert response.status_code == 200
        assert response.data == self.get_context(params, current_provider, current_backend, add_user_details)

    def test_tpa_hint(self):
        """
        Test that if tpa_hint is provided, the context returns the third party auth provider
        even if it is not visible on the login page
        """
        params = {
            'next': self.query_params['next']
        }
        tpa_hint = self.hidden_enabled_provider.provider_id
        self.query_params.update({'tpa_hint': tpa_hint})

        provider_data = self.get_provider_data(params)
        provider_data.append({
            'id': self.hidden_enabled_provider.provider_id,
            'name': 'LinkedIn',
            'iconClass': 'fa-linkedin',
            'iconImage': None,
            'skipHintedLogin': False,
            'skipRegistrationForm': False,
            'loginUrl': self._third_party_login_url('linkedin-oauth2', 'login', params),
            'registerUrl': self._third_party_login_url('linkedin-oauth2', 'register', params)
        })

        response = self.client.get(self.url, self.query_params)
        assert response.data['contextData']['providers'] == provider_data

    def test_user_country_code(self):
        """
        Test api that returns country code of user
        """
        response = self.client.get(self.url, self.query_params)

        assert response.status_code == 200
        assert response.data['contextData']['countryCode'] == self.country_code

    @override_settings(
        ENABLE_DYNAMIC_REGISTRATION_FIELDS=True,
        REGISTRATION_EXTRA_FIELDS={"first_name": "optional", "city": "optional"}
    )
    def test_required_fields_not_configured(self):
        """
        Test that when no required fields are configured in REGISTRATION_EXTRA_FIELDS
        settings, then API returns proper response.
        """
        self.query_params.update({'is_register_page': True})
        response = self.client.get(self.url, self.query_params)
        assert response.status_code == status.HTTP_200_OK
        assert response.data['registrationFields']['fields'] == {}

    @with_site_configuration(
        configuration={
            'extended_profile_fields': ['first_name', 'last_name']
        }
    )
    @override_settings(
        ENABLE_DYNAMIC_REGISTRATION_FIELDS=True,
        REGISTRATION_EXTRA_FIELDS={'state': 'required', 'last_name': 'required', 'first_name': 'required'},
        REGISTRATION_FIELD_ORDER=['first_name', 'last_name', 'state'],
    )
    def test_required_field_order(self):
        """
        Test that order of required fields
        """
        self.query_params.update({'is_register_page': True})
        response = self.client.get(self.url, self.query_params)

        assert response.status_code == status.HTTP_200_OK
        assert list(response.data['registrationFields']['fields'].keys()) == ['first_name', 'last_name', 'state']

    @override_settings(
        ENABLE_DYNAMIC_REGISTRATION_FIELDS=True,
        REGISTRATION_EXTRA_FIELDS={"new_field_with_no_description": "optional", "goals": "optional"}
    )
    def test_optional_field_has_no_description(self):
        """
        Test that if a new optional field is added to REGISTRATION_EXTRA_FIELDS without
        adding field description then that field is omitted from the final response.
        """
        expected_response = {
            'goals': {
                'name': 'goals',
                'type': 'textarea',
                'label': "Tell us why you're interested in {platform_name}".format(
                    platform_name=settings.PLATFORM_NAME
                ),
                'error_message': '',
            }
        }
        self.query_params.update({'is_register_page': True})
        response = self.client.get(self.url, self.query_params)
        assert response.status_code == status.HTTP_200_OK
        assert response.data['optionalFields']['fields'] == expected_response

    @with_site_configuration(
        configuration={
            'EXTRA_FIELD_OPTIONS': {'profession': ['Software Engineer', 'Teacher', 'Other']},
            'extended_profile_fields': ['profession', 'specialty']
        }
    )
    @override_settings(
        ENABLE_DYNAMIC_REGISTRATION_FIELDS=True,
        REGISTRATION_EXTRA_FIELDS={'profession': 'optional', 'specialty': 'optional'}
    )
    def test_configurable_select_option_fields(self):
        """
        Test that if optional fields have configurable options present in EXTRA_FIELD_OPTIONS,
        they are returned in response as "select" fields otherwise as "text" field.
        """
        expected_response = {
            'profession': {
                'name': 'profession',
                'label': 'Profession',
                'error_message': '',
                'type': 'select',
                'options': [('software engineer', 'Software Engineer'), ('teacher', 'Teacher'), ('other', 'Other')],
            },
            'specialty': {
                'name': 'specialty',
                'label': 'Specialty',
                'error_message': '',
                'type': 'text',
            }
        }
        self.query_params.update({'is_register_page': True})
        response = self.client.get(self.url, self.query_params)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['optionalFields']['fields'] == expected_response

    @with_site_configuration(
        configuration={
            'extended_profile_fields': ['specialty']
        }
    )
    @override_settings(
        ENABLE_DYNAMIC_REGISTRATION_FIELDS=True,
        REGISTRATION_EXTRA_FIELDS={'goals': 'optional', 'specialty': 'optional'},
        REGISTRATION_FIELD_ORDER=['specialty', 'goals'],
    )
    def test_optional_field_order(self):
        """
        Test that order of optional fields
        """
        self.query_params.update({'is_register_page': True})
        response = self.client.get(self.url, self.query_params)
        assert response.status_code == status.HTTP_200_OK
        assert list(response.data['optionalFields']['fields'].keys()) == ['specialty', 'goals']

    @with_site_configuration(
        configuration={
            'extended_profile_fields': ['specialty']
        }
    )
    @override_settings(
        ENABLE_DYNAMIC_REGISTRATION_FIELDS=True,
        REGISTRATION_EXTRA_FIELDS={'profession': 'required', 'specialty': 'required'},
        REGISTRATION_FIELD_ORDER=['specialty', 'profession'],
    )
    def test_field_not_available_in_extended_profile_config(self):
        """
        Test that if the field is not available in extended_profile configuration then the field
        will not be sent in response.
        """
        self.query_params.update({'is_register_page': True})
        response = self.client.get(self.url, self.query_params)
        assert response.status_code == status.HTTP_200_OK
        assert list(response.data['registrationFields']['fields'].keys()) == ['specialty']

    @override_settings(
        ENABLE_DYNAMIC_REGISTRATION_FIELDS=True,
    )
    @patch.dict(settings.FEATURES, {'ENABLE_THIRD_PARTY_AUTH': False})
    def test_response_structure(self):
        """
        Test that API return valid response dictionary with both required and optional fields
        """
        response = self.client.get(self.url, self.query_params)
        assert response.data == self.get_context()

    def test_mfe_context_api_serialized_response(self):
        """
        Test MFE Context API serialized response
        """
        response = self.client.get(self.url, self.query_params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        params = {
            'next': self.query_params['next']
        }

        self.assertEqual(
            response.data,
            self.get_context(params)
        )

    def test_mfe_context_api_response_keys(self):
        """
        Test MFE Context API response keys
        """
        response = self.client.get(self.url, self.query_params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response_keys = set(response.data.keys())
        self.assertSetEqual(
            response_keys,
            {
                'contextData',
                'registrationFields',
                'optionalFields'
            }
        )

    @with_site_configuration(
        configuration={
            'extended_profile_fields': ['specialty']
        }
    )
    @override_settings(
        ENABLE_DYNAMIC_REGISTRATION_FIELDS=True,
        REGISTRATION_EXTRA_FIELDS={'specialty': 'optional', 'goals': 'optional'},
        LOGIN_REDIRECT_WHITELIST=['openedx.service'],
    )
    def test_welcome_page_context(self):
        """
        Test MFE Context API response for welcome page
        """
        redirect_url = 'https://openedx.service/coolpage'
        self.query_params.update({'is_welcome_page': True, 'next': redirect_url})
        response = self.client.get(self.url, self.query_params, HTTP_ACCEPT='*/*')
        assert response.status_code == status.HTTP_200_OK
        assert list(response.data['optionalFields']['fields'].keys()) == ['specialty', 'goals']
        assert list(response.data['optionalFields']['extended_profile']) == ['specialty']
        assert response.data['contextData']['welcomePageRedirectUrl'] == redirect_url


@skip_unless_lms
class SendAccountActivationEmail(UserAPITestCase):
    """
    Test for send activation email view
    """

    def setUp(self):
        """
        Create a user, then log in.
        """
        super().setUp()
        self.user = UserFactory(password=self.TEST_PASSWORD)
        Registration().register(self.user)
        result = self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        assert result, 'Could not log in'
        self.path = reverse('send_account_activation_email')

    @patch('common.djangoapps.student.views.management.compose_activation_email')
    def test_send_email_to_inactive_user_via_cta_dialog(self, email):
        """
        Tests when user clicks on resend activation email on CTA dialog box, system
        sends an activation email to the user.
        """
        self.user.is_active = False
        self.user.save()
        self.client.post(self.path)
        assert email.called is True, 'method should have been called'
