"""  # lint-amnesty, pylint: disable=cyclic-import
Mixins for the EnterpriseApiClient.
"""


import json
from unittest import mock

import httpretty
from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse

from openedx.features.enterprise_support.tests import FAKE_ENTERPRISE_CUSTOMER


class EnterpriseServiceMockMixin:
    """
    Mocks for the Enterprise service responses.
    """

    consent_url = '{}{}'.format(settings.ENTERPRISE_CONSENT_API_URL, 'data_sharing_consent')

    def setUp(self):
        super().setUp()
        cache.clear()

    @staticmethod
    def get_enterprise_url(path):
        """Return a URL to the configured Enterprise API. """
        return f'{settings.ENTERPRISE_API_URL}{path}/'

    def mock_get_enterprise_customer(self, uuid, response, status):
        """
        Helper to mock the HTTP call to the /enterprise-customer/uuid endpoint
        """
        body = json.dumps(response)
        httpretty.register_uri(
            method=httpretty.GET,
            uri=(self.get_enterprise_url('enterprise-customer') + uuid + '/'),
            body=body,
            content_type='application/json',
            status=status,
        )

    def mock_enterprise_course_enrollment_post_api(  # pylint: disable=invalid-name
            self,
            username='test_user',
            course_id='course-v1:edX+DemoX+Demo_Course',
            consent_granted=True
    ):
        """
        Helper method to register the enterprise course enrollment API POST endpoint.
        """
        api_response = {
            username: username,
            course_id: course_id,
            consent_granted: consent_granted,
        }
        api_response_json = json.dumps(api_response)
        httpretty.register_uri(
            method=httpretty.POST,
            uri=self.get_enterprise_url('enterprise-course-enrollment'),
            body=api_response_json,
            content_type='application/json'
        )

    def mock_enterprise_course_enrollment_post_api_failure(self):  # pylint: disable=invalid-name
        """
        Helper method to register the enterprise course enrollment API endpoint for a failure.
        """
        httpretty.register_uri(
            method=httpretty.POST,
            uri=self.get_enterprise_url('enterprise-course-enrollment'),
            body='{}',
            content_type='application/json',
            status=500
        )

    def mock_consent_response(  # lint-amnesty, pylint: disable=missing-function-docstring
            self,
            username,
            course_id,
            ec_uuid,
            method=httpretty.GET,
            granted=True,
            required=False,
            exists=True,
            response_code=None
    ):
        response_body = {
            'username': username,
            'course_id': course_id,
            'enterprise_customer_uuid': ec_uuid,
            'consent_provided': granted,
            'consent_required': required,
            'exists': exists,
        }
        httpretty.register_uri(
            method=method,
            uri=self.consent_url,
            content_type='application/json',
            body=json.dumps(response_body),
            status=response_code or 200,
        )

    def mock_consent_post(self, username, course_id, ec_uuid):
        self.mock_consent_response(
            username,
            course_id,
            ec_uuid,
            method=httpretty.POST,
            granted=True,
            exists=True,
        )

    def mock_consent_get(self, username, course_id, ec_uuid):
        self.mock_consent_response(
            username,
            course_id,
            ec_uuid
        )

    def mock_consent_missing(self, username, course_id, ec_uuid):
        self.mock_consent_response(
            username,
            course_id,
            ec_uuid,
            exists=False,
            granted=False,
            required=True,
        )

    def mock_consent_not_required(self, username, course_id, ec_uuid):
        self.mock_consent_response(
            username,
            course_id,
            ec_uuid,
            exists=False,
            granted=False,
            required=False,
        )

    def get_mock_active_enterprise_learner_details(
            self,
            learner_id=1,
            enterprise_customer_uuid='cf246b88-d5f6-4908-a522-fc307e0b0c59',
            enable_audit_enrollment=False,
            enable_data_sharing_consent=True,
            slug='test-shib',
            data_sharing_consent_records=None,
            site_domain='example.com',
    ):
        """
        Helper function to format enterprise learner API response.
        """
        mock_result = {
            'id': learner_id,
            'enterprise_customer': {
                'uuid': enterprise_customer_uuid,
                'name': 'TestShib',
                'slug': slug,
                'active': True,
                'site': {
                    'domain': site_domain,
                    'name': 'example.com'
                },
                'enable_data_sharing_consent': enable_data_sharing_consent,
                'enforce_data_sharing_consent': 'at_login',
                'enable_audit_enrollment': enable_audit_enrollment,
            },
            'active': True,
            'user_id': learner_id,
            'user': {
                'id': learner_id,
                'username': 'verified',
                'first_name': '',
                'last_name': '',
                'email': 'verified@example.com',
                'is_staff': True,
                'is_active': True,
                'date_joined': '2016-09-01T19:18:26.026495Z'
            },
            'data_sharing_consent_records': data_sharing_consent_records or [],
            'groups': [],
            'created': '2016-09-01T19:18:26.026495Z',
            'invite_key': '',
            'role_assignments': [
                'enterprise_learner'
            ]
        }
        return mock_result

    def get_mock_enterprise_learner_results(
            self,
            entitlement_id=1,
            learner_id=1,
            enterprise_customer_uuid='cf246b88-d5f6-4908-a522-fc307e0b0c59',
            enable_audit_enrollment=False,
    ):
        """
        Helper function to format enterprise learner API response.
        """
        mock_results = [
            {
                'id': learner_id,
                'enterprise_customer': {
                    'uuid': enterprise_customer_uuid,
                    'name': 'TestShib',
                    'active': True,
                    'site': {
                        'domain': 'example.com',
                        'name': 'example.com'
                    },
                    'enable_data_sharing_consent': True,
                    'enforce_data_sharing_consent': 'at_login',
                    'enable_audit_enrollment': enable_audit_enrollment,
                    'branding_configuration': {
                        'enterprise_customer': enterprise_customer_uuid,
                        'logo': 'https://open.edx.org/sites/all/themes/edx_open/logo.png'
                    },
                    'enterprise_customer_entitlements': [
                        {
                            'enterprise_customer': enterprise_customer_uuid,
                            'entitlement_id': entitlement_id
                        }
                    ],
                    'replace_sensitive_sso_username': True,
                },
                'user_id': 5,
                'user': {
                    'username': 'verified',
                    'first_name': '',
                    'last_name': '',
                    'email': 'verified@example.com',
                    'is_staff': True,
                    'is_active': True,
                    'date_joined': '2016-09-01T19:18:26.026495Z'
                },
                'data_sharing_consent': [
                    {
                        "username": "verified",
                        "enterprise_customer_uuid": enterprise_customer_uuid,
                        "exists": True,
                        "course_id": "course-v1:edX DemoX Demo_Course",
                        "consent_provided": True,
                        "consent_required": False
                    }
                ]
            }
        ]
        return mock_results

    def mock_enterprise_learner_api(
            self,
            entitlement_id=1,
            learner_id=1,
            enterprise_customer_uuid='cf246b88-d5f6-4908-a522-fc307e0b0c59',
            enable_audit_enrollment=False,
    ):
        """
        Helper function to register enterprise learner API endpoint.
        """
        results = self.get_mock_enterprise_learner_results(
            entitlement_id, learner_id, enterprise_customer_uuid, enable_audit_enrollment
        )
        enterprise_learner_api_response = {
            'count': 1,
            'num_pages': 1,
            'current_page': 1,
            'results': results,
            'next': None,
            'start': 0,
            'previous': None
        }
        enterprise_learner_api_response_json = json.dumps(enterprise_learner_api_response)

        httpretty.register_uri(
            method=httpretty.GET,
            uri=self.get_enterprise_url('enterprise-learner'),
            body=enterprise_learner_api_response_json,
            content_type='application/json'
        )


class EnterpriseTestConsentRequired(SimpleTestCase):
    """
    Mixin to help test the data_sharing_consent_required decorator.
    """

    @mock.patch('openedx.features.enterprise_support.utils.get_enterprise_learner_generic_name')
    @mock.patch('openedx.features.enterprise_support.api.enterprise_customer_from_api')
    @mock.patch('openedx.features.enterprise_support.api.enterprise_customer_uuid_for_request')
    @mock.patch('openedx.features.enterprise_support.api.reverse')
    @mock.patch('openedx.features.enterprise_support.api.enterprise_enabled')
    @mock.patch('openedx.features.enterprise_support.api.consent_needed_for_course')
    def verify_consent_required(
            self,
            client,
            url,
            mock_consent_necessary,
            mock_enterprise_enabled,
            mock_reverse,
            mock_enterprise_customer_uuid_for_request,
            mock_enterprise_customer_from_api,
            mock_get_enterprise_learner_generic_name,
            status_code=200,
    ):
        """
        Verify that the given URL redirects to the consent page when consent is required,
        and doesn't redirect to the consent page when consent is not required.
        """

        def mock_consent_reverse(*args, **kwargs):
            if args[0] == 'grant_data_sharing_permissions':
                return '/enterprise/grant_data_sharing_permissions'
            return reverse(*args, **kwargs)

        # ENT-924: Temporary solution to replace sensitive SSO usernames.
        mock_get_enterprise_learner_generic_name.return_value = ''

        mock_reverse.side_effect = mock_consent_reverse
        mock_enterprise_enabled.return_value = True
        mock_enterprise_customer_uuid_for_request.return_value = 'fake-uuid'
        mock_enterprise_customer_from_api.return_value = FAKE_ENTERPRISE_CUSTOMER
        # Ensure that when consent is necessary, the user is redirected to the consent page.
        mock_consent_necessary.return_value = True
        response = client.get(url)
        while(response.status_code == 302 and 'grant_data_sharing_permissions' not in response.url):
            response = client.get(response.url)
        assert response.status_code == 302
        assert 'grant_data_sharing_permissions' in response.url

        # Ensure that when consent is not necessary, the user continues through to the requested page.
        mock_consent_necessary.return_value = False
        response = client.get(url)
        assert response.status_code == status_code

        # If we were expecting a redirect, ensure it's not to the data sharing permission page
        if status_code == 302:
            assert 'grant_data_sharing_permissions' not in response.url
        return response
