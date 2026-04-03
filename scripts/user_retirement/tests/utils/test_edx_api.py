"""
Tests for edX API calls.
"""
import unittest
from unittest.mock import DEFAULT, patch
from urllib.parse import urljoin

import requests
import responses
from ddt import data, ddt, unpack
from requests.exceptions import ConnectionError, HTTPError
from responses import GET, PATCH, POST, matchers
from responses.registries import OrderedRegistry

from scripts.user_retirement.tests.mixins import OAuth2Mixin
from scripts.user_retirement.tests.retirement_helpers import (
    FAKE_DATETIME_OBJECT,
    FAKE_ORIGINAL_USERNAME,
    FAKE_RESPONSE_MESSAGE,
    FAKE_USERNAME_MAPPING,
    FAKE_USERNAMES,
    TEST_RETIREMENT_QUEUE_STATES,
    TEST_RETIREMENT_STATE,
    get_fake_user_retirement,
)
from scripts.user_retirement.utils import edx_api


class BackoffTriedException(Exception):
    """
    Raise this from a backoff handler to indicate that backoff was tried.
    """


@ddt
class TestLmsApi(OAuth2Mixin, unittest.TestCase):
    """
    Test the edX LMS API client.
    """

    @responses.activate(registry=OrderedRegistry)
    def setUp(self):
        super().setUp()
        self.mock_access_token_response()
        self.lms_base_url = 'http://localhost:18000/'
        self.lms_api = edx_api.LmsApi(
            self.lms_base_url,
            self.lms_base_url,
            'the_client_id',
            'the_client_secret'
        )

    def tearDown(self):
        super().tearDown()
        responses.reset()

    @patch.object(edx_api.LmsApi, 'learners_to_retire')
    def test_learners_to_retire(self, mock_method):
        params = {
            'states': TEST_RETIREMENT_QUEUE_STATES,
            'cool_off_days': 365,
        }
        responses.add(
            GET,
            urljoin(self.lms_base_url, 'api/user/v1/accounts/retirement_queue/'),
            match=[matchers.query_param_matcher(params)],
        )
        self.lms_api.learners_to_retire(
            TEST_RETIREMENT_QUEUE_STATES, cool_off_days=365)
        mock_method.assert_called_once_with(
            TEST_RETIREMENT_QUEUE_STATES, cool_off_days=365)

    @patch.object(edx_api.LmsApi, 'get_learners_by_date_and_status')
    def test_get_learners_by_date_and_status(self, mock_method):
        query_params = {
            'start_date': FAKE_DATETIME_OBJECT.strftime('%Y-%m-%d'),
            'end_date': FAKE_DATETIME_OBJECT.strftime('%Y-%m-%d'),
            'state': TEST_RETIREMENT_STATE,
        }
        responses.add(
            GET,
            urljoin(self.lms_base_url, 'api/user/v1/accounts/retirements_by_status_and_date/'),
            match=[matchers.query_param_matcher(query_params)]
        )
        self.lms_api.get_learners_by_date_and_status(
            state_to_request=TEST_RETIREMENT_STATE,
            start_date=FAKE_DATETIME_OBJECT,
            end_date=FAKE_DATETIME_OBJECT
        )
        mock_method.assert_called_once_with(
            state_to_request=TEST_RETIREMENT_STATE,
            start_date=FAKE_DATETIME_OBJECT,
            end_date=FAKE_DATETIME_OBJECT
        )

    @patch.object(edx_api.LmsApi, 'get_learner_retirement_state')
    def test_get_learner_retirement_state(self, mock_method):
        responses.add(
            GET,
            urljoin(self.lms_base_url, f'api/user/v1/accounts/{FAKE_ORIGINAL_USERNAME}/retirement_status/'),
        )
        self.lms_api.get_learner_retirement_state(
            username=FAKE_ORIGINAL_USERNAME
        )
        mock_method.assert_called_once_with(
            username=FAKE_ORIGINAL_USERNAME
        )

    @patch.object(edx_api.LmsApi, 'update_learner_retirement_state')
    def test_update_leaner_retirement_state(self, mock_method):
        json_data = {
            'username': FAKE_ORIGINAL_USERNAME,
            'new_state': TEST_RETIREMENT_STATE,
            'response': FAKE_RESPONSE_MESSAGE,
        }
        responses.add(
            PATCH,
            urljoin(self.lms_base_url, 'api/user/v1/accounts/update_retirement_status/'),
            match=[matchers.json_params_matcher(json_data)]
        )
        self.lms_api.update_learner_retirement_state(
            username=FAKE_ORIGINAL_USERNAME,
            new_state_name=TEST_RETIREMENT_STATE,
            message=FAKE_RESPONSE_MESSAGE
        )
        mock_method.assert_called_once_with(
            username=FAKE_ORIGINAL_USERNAME,
            new_state_name=TEST_RETIREMENT_STATE,
            message=FAKE_RESPONSE_MESSAGE
        )

    @data(
        {
            'api_url': 'api/user/v1/accounts/deactivate_logout/',
            'mock_method': 'retirement_deactivate_logout',
            'method': 'POST',
        },
        {
            'api_url': 'api/discussion/v1/accounts/retire_forum/',
            'mock_method': 'retirement_retire_forum',
            'method': 'POST',
        },
        {
            'api_url': 'api/user/v1/accounts/retire_mailings/',
            'mock_method': 'retirement_retire_mailings',
            'method': 'POST',
        },
        {
            'api_url': 'api/enrollment/v1/unenroll/',
            'mock_method': 'retirement_unenroll',
            'method': 'POST',
        },
        {
            'api_url': 'api/edxnotes/v1/retire_user/',
            'mock_method': 'retirement_retire_notes',
            'method': 'POST',
        },
        {
            'api_url': 'api/user/v1/accounts/retire_misc/',
            'mock_method': 'retirement_lms_retire_misc',
            'method': 'POST',
        },
        {
            'api_url': 'api/user/v1/accounts/retire/',
            'mock_method': 'retirement_lms_retire',
            'method': 'POST',
        },
        {
            'api_url': 'api/user/v1/accounts/retirement_partner_report/',
            'mock_method': 'retirement_partner_queue',
            'method': 'PUT',
        },
    )
    @unpack
    @patch.multiple(
        'scripts.user_retirement.utils.edx_api.LmsApi',
        retirement_deactivate_logout=DEFAULT,
        retirement_retire_forum=DEFAULT,
        retirement_retire_mailings=DEFAULT,
        retirement_unenroll=DEFAULT,
        retirement_retire_notes=DEFAULT,
        retirement_lms_retire_misc=DEFAULT,
        retirement_lms_retire=DEFAULT,
        retirement_partner_queue=DEFAULT,
    )
    def test_learner_retirement(self, api_url, mock_method, method, **kwargs):
        json_data = {
            'username': FAKE_ORIGINAL_USERNAME,
        }
        responses.add(
            method,
            urljoin(self.lms_base_url, api_url),
            match=[matchers.json_params_matcher(json_data)]
        )
        getattr(self.lms_api, mock_method)(get_fake_user_retirement(original_username=FAKE_ORIGINAL_USERNAME))
        kwargs[mock_method].assert_called_once_with(get_fake_user_retirement(original_username=FAKE_ORIGINAL_USERNAME))

    @patch.object(edx_api.LmsApi, 'retirement_partner_report')
    def test_retirement_partner_report(self, mock_method):
        responses.add(
            POST,
            urljoin(self.lms_base_url, 'api/user/v1/accounts/retirement_partner_report/')
        )
        self.lms_api.retirement_partner_report(
            learner=get_fake_user_retirement(
                original_username=FAKE_ORIGINAL_USERNAME
            )
        )
        mock_method.assert_called_once_with(
            learner=get_fake_user_retirement(
                original_username=FAKE_ORIGINAL_USERNAME
            )
        )

    @patch.object(edx_api.LmsApi, 'retirement_partner_cleanup')
    def test_retirement_partner_cleanup(self, mock_method):
        json_data = FAKE_USERNAMES
        responses.add(
            POST,
            urljoin(self.lms_base_url, 'api/user/v1/accounts/retirement_partner_report_cleanup/'),
            match=[matchers.json_params_matcher(json_data)]
        )
        self.lms_api.retirement_partner_cleanup(
            usernames=FAKE_USERNAMES
        )
        mock_method.assert_called_once_with(
            usernames=FAKE_USERNAMES
        )

    @patch.object(edx_api.LmsApi, 'retirement_retire_proctoring_data')
    def test_retirement_retire_proctoring_data(self, mock_method):
        learner = get_fake_user_retirement()
        responses.add(
            POST,
            urljoin(self.lms_base_url, f"api/edx_proctoring/v1/retire_user/{learner['user']['id']}/"),
        )
        self.lms_api.retirement_retire_proctoring_data()
        mock_method.assert_called_once()

    @patch.object(edx_api.LmsApi, 'retirement_retire_proctoring_backend_data')
    def test_retirement_retire_proctoring_backend_data(self, mock_method):
        learner = get_fake_user_retirement()
        responses.add(
            POST,
            urljoin(self.lms_base_url, f"api/edx_proctoring/v1/retire_backend_user/{learner['user']['id']}/"),
        )
        self.lms_api.retirement_retire_proctoring_backend_data()
        mock_method.assert_called_once()

    @patch.object(edx_api.LmsApi, 'replace_lms_usernames')
    def test_replace_lms_usernames(self, mock_method):
        json_data = {
            'username_mappings': FAKE_USERNAME_MAPPING
        }
        responses.add(
            POST,
            urljoin(self.lms_base_url, 'api/user/v1/accounts/replace_usernames/'),
            match=[matchers.json_params_matcher(json_data)]
        )
        self.lms_api.replace_lms_usernames(
            username_mappings=FAKE_USERNAME_MAPPING
        )
        mock_method.assert_called_once_with(
            username_mappings=FAKE_USERNAME_MAPPING
        )

    @patch.object(edx_api.LmsApi, 'replace_forums_usernames')
    def test_replace_forums_usernames(self, mock_method):
        json_data = {
            'username_mappings': FAKE_USERNAME_MAPPING
        }
        responses.add(
            POST,
            urljoin(self.lms_base_url, 'api/discussion/v1/accounts/replace_usernames/'),
            match=[matchers.json_params_matcher(json_data)]
        )
        self.lms_api.replace_forums_usernames(
            username_mappings=FAKE_USERNAME_MAPPING
        )
        mock_method.assert_called_once_with(
            username_mappings=FAKE_USERNAME_MAPPING
        )

    @data(504, 500)
    @patch('scripts.user_retirement.utils.edx_api._backoff_handler')
    @patch.object(edx_api.LmsApi, 'learners_to_retire')
    def test_retrieve_learner_queue_backoff(
        self,
        svr_status_code,
        mock_backoff_handler,
        mock_learners_to_retire
    ):
        mock_backoff_handler.side_effect = BackoffTriedException
        params = {
            'states': TEST_RETIREMENT_QUEUE_STATES,
            'cool_off_days': 365,
        }
        response = requests.Response()
        response.status_code = svr_status_code
        responses.add(
            GET,
            urljoin(self.lms_base_url, 'api/user/v1/accounts/retirement_queue/'),
            status=200,
            match=[matchers.query_param_matcher(params)],
        )

        mock_learners_to_retire.side_effect = HTTPError(response=response)
        with self.assertRaises(BackoffTriedException):
            self.lms_api.learners_to_retire(
                TEST_RETIREMENT_QUEUE_STATES, cool_off_days=365)

    @data(104)
    @responses.activate
    @patch('scripts.user_retirement.utils.edx_api._backoff_handler')
    @patch.object(edx_api.LmsApi, 'retirement_partner_cleanup')
    def test_retirement_partner_cleanup_backoff_on_connection_error(
        self,
        svr_status_code,
        mock_backoff_handler,
        mock_retirement_partner_cleanup
    ):
        mock_backoff_handler.side_effect = BackoffTriedException
        response = requests.Response()
        response.status_code = svr_status_code
        mock_retirement_partner_cleanup.retirement_partner_cleanup.side_effect = ConnectionError(
            response=response
        )
        with self.assertRaises(BackoffTriedException):
            self.lms_api.retirement_partner_cleanup([{'original_username': 'test'}])


class TestEcommerceApi(OAuth2Mixin, unittest.TestCase):
    """
    Test the edX Ecommerce API client.
    """

    @responses.activate(registry=OrderedRegistry)
    def setUp(self):
        super().setUp()
        self.mock_access_token_response()
        self.lms_base_url = 'http://localhost:18000/'
        self.ecommerce_base_url = 'http://localhost:18130/'
        self.ecommerce_api = edx_api.EcommerceApi(
            self.lms_base_url,
            self.ecommerce_base_url,
            'the_client_id',
            'the_client_secret'
        )

    def tearDown(self):
        super().tearDown()
        responses.reset()

    @patch.object(edx_api.EcommerceApi, 'retire_learner')
    def test_retirement_partner_report(self, mock_method):
        json_data = {
            'username': FAKE_ORIGINAL_USERNAME,
        }
        responses.add(
            POST,
            urljoin(self.lms_base_url, 'api/v2/user/retire/'),
            match=[matchers.json_params_matcher(json_data)]
        )
        self.ecommerce_api.retire_learner(
            learner=get_fake_user_retirement(original_username=FAKE_ORIGINAL_USERNAME)
        )
        mock_method.assert_called_once_with(
            learner=get_fake_user_retirement(original_username=FAKE_ORIGINAL_USERNAME)
        )

    @patch.object(edx_api.EcommerceApi, 'retire_learner')
    def get_tracking_key(self, mock_method):
        original_username = {
            'original_username': get_fake_user_retirement(original_username=FAKE_ORIGINAL_USERNAME)
        }
        responses.add(
            GET,
            urljoin(self.lms_base_url, f"api/v2/retirement/tracking_id/{original_username}/"),
        )
        self.ecommerce_api.get_tracking_key(
            learner=get_fake_user_retirement(original_username=FAKE_ORIGINAL_USERNAME)
        )
        mock_method.assert_called_once_with(
            learner=get_fake_user_retirement(original_username=FAKE_ORIGINAL_USERNAME)
        )

    @patch.object(edx_api.EcommerceApi, 'replace_usernames')
    def test_replace_usernames(self, mock_method):
        json_data = {
            "username_mappings": FAKE_USERNAME_MAPPING
        }
        responses.add(
            POST,
            urljoin(self.lms_base_url, 'api/v2/user_management/replace_usernames/'),
            match=[matchers.json_params_matcher(json_data)]
        )
        self.ecommerce_api.replace_usernames(
            username_mappings=FAKE_USERNAME_MAPPING
        )
        mock_method.assert_called_once_with(
            username_mappings=FAKE_USERNAME_MAPPING
        )


class TestCredentialApi(OAuth2Mixin, unittest.TestCase):
    """
    Test the edX Credential API client.
    """

    @responses.activate(registry=OrderedRegistry)
    def setUp(self):
        super().setUp()
        self.mock_access_token_response()
        self.lms_base_url = 'http://localhost:18000/'
        self.credentials_base_url = 'http://localhost:18150/'
        self.credentials_api = edx_api.CredentialsApi(
            self.lms_base_url,
            self.credentials_base_url,
            'the_client_id',
            'the_client_secret'
        )

    def tearDown(self):
        super().tearDown()
        responses.reset()

    @patch.object(edx_api.CredentialsApi, 'retire_learner')
    def test_retire_learner(self, mock_method):
        json_data = {
            'username': FAKE_ORIGINAL_USERNAME
        }
        responses.add(
            POST,
            urljoin(self.credentials_base_url, 'user/retire/'),
            match=[matchers.json_params_matcher(json_data)]
        )
        self.credentials_api.retire_learner(
            learner=get_fake_user_retirement(original_username=FAKE_ORIGINAL_USERNAME)
        )
        mock_method.assert_called_once_with(
            learner=get_fake_user_retirement(original_username=FAKE_ORIGINAL_USERNAME)
        )

    @patch.object(edx_api.CredentialsApi, 'replace_usernames')
    def test_replace_usernames(self, mock_method):
        json_data = {
            "username_mappings": FAKE_USERNAME_MAPPING
        }
        responses.add(
            POST,
            urljoin(self.credentials_base_url, 'api/v2/replace_usernames/'),
            match=[matchers.json_params_matcher(json_data)]
        )
        self.credentials_api.replace_usernames(
            username_mappings=FAKE_USERNAME_MAPPING
        )
        mock_method.assert_called_once_with(
            username_mappings=FAKE_USERNAME_MAPPING
        )


class TestDiscoveryApi(OAuth2Mixin, unittest.TestCase):
    """
    Test the edX Discovery API client.
    """

    @responses.activate(registry=OrderedRegistry)
    def setUp(self):
        super().setUp()
        self.mock_access_token_response()
        self.lms_base_url = 'http://localhost:18000/'
        self.discovery_base_url = 'http://localhost:18150/'
        self.discovery_api = edx_api.DiscoveryApi(
            self.lms_base_url,
            self.discovery_base_url,
            'the_client_id',
            'the_client_secret'
        )

    def tearDown(self):
        super().tearDown()
        responses.reset()

    @patch.object(edx_api.DiscoveryApi, 'replace_usernames')
    def test_replace_usernames(self, mock_method):
        json_data = {
            "username_mappings": FAKE_USERNAME_MAPPING
        }
        responses.add(
            POST,
            urljoin(self.discovery_base_url, 'api/v1/replace_usernames/'),
            match=[matchers.json_params_matcher(json_data)]
        )
        self.discovery_api.replace_usernames(
            username_mappings=FAKE_USERNAME_MAPPING
        )
        mock_method.assert_called_once_with(
            username_mappings=FAKE_USERNAME_MAPPING
        )


class TestLicenseManagerApi(OAuth2Mixin, unittest.TestCase):
    """
    Test the edX License Manager API client.
    """

    @responses.activate(registry=OrderedRegistry)
    def setUp(self):
        super().setUp()
        self.mock_access_token_response()
        self.lms_base_url = 'http://localhost:18000/'
        self.license_manager_base_url = 'http://localhost:18170/'
        self.license_manager_api = edx_api.LicenseManagerApi(
            self.lms_base_url,
            self.license_manager_base_url,
            'the_client_id',
            'the_client_secret'
        )

    def tearDown(self):
        super().tearDown()
        responses.reset()

    @patch.object(edx_api.LicenseManagerApi, 'retire_learner')
    def test_retire_learner(self, mock_method):
        json_data = {
            'lms_user_id': get_fake_user_retirement()['user']['id'],
            'original_username': FAKE_ORIGINAL_USERNAME,
        }
        responses.add(
            POST,
            urljoin(self.license_manager_base_url, 'api/v1/retire_user/'),
            match=[matchers.json_params_matcher(json_data)]
        )
        self.license_manager_api.retire_learner(
            learner=get_fake_user_retirement(
                original_username=FAKE_ORIGINAL_USERNAME
            )
        )
        mock_method.assert_called_once_with(
            learner=get_fake_user_retirement(
                original_username=FAKE_ORIGINAL_USERNAME
            )
        )
