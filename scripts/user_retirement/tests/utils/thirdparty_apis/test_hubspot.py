"""
Tests for the Sailthru API functionality
"""
import logging
import os
import unittest
from unittest import mock

import requests_mock
from six.moves import reload_module

# This module is imported separately solely so it can be re-loaded below.
from scripts.user_retirement.utils.thirdparty_apis import hubspot_api

# This HubspotAPI class will be used without being re-loaded.
from scripts.user_retirement.utils.thirdparty_apis.hubspot_api import HubspotAPI

# Change the number of retries for Hubspot API's delete_user call to 1.
# Then reload hubspot_api so only a single retry is performed.
os.environ['RETRY_HUBSPOT_MAX_ATTEMPTS'] = "1"
reload_module(hubspot_api)  # pylint: disable=too-many-function-args


@requests_mock.Mocker()
@mock.patch.object(HubspotAPI, 'send_marketing_alert')
class TestHubspot(unittest.TestCase):
    """
    Class containing tests of all code interacting with Hubspot.
    """

    def setUp(self):
        super(TestHubspot, self).setUp()  # noqa: UP008
        self.test_learner = {'original_email': 'foo@bar.com'}
        self.api_key = 'example_key'
        self.test_vid = 12345
        self.test_region = 'test-east-1'
        self.from_address = 'no-reply@example.com'
        self.alert_email = 'marketing@example.com'

    def _mock_get_vid(self, req_mock, status_code):
        req_mock.get(
            hubspot_api.GET_VID_FROM_EMAIL_URL_TEMPLATE.format(
                email=self.test_learner['original_email']
            ),
            json={'vid': self.test_vid},
            status_code=status_code
        )

    def _mock_delete(self, req_mock, status_code):
        req_mock.delete(
            hubspot_api.DELETE_USER_FROM_VID_TEMPLATE.format(
                vid=self.test_vid
            ),
            json={},
            status_code=status_code
        )

    def test_delete_no_email(self, req_mock, mock_alert):  # pylint: disable=unused-argument
        with self.assertRaises(TypeError) as exc:  # noqa: PT027
            HubspotAPI(
                self.api_key,
                self.test_region,
                self.from_address,
                self.alert_email
            ).delete_user({})
            self.assertIn('Expected an email address for user to delete, but received None.', str(exc))  # noqa: PT009
            mock_alert.assert_not_called()

    def test_delete_success(self, req_mock, mock_alert):
        self._mock_get_vid(req_mock, 200)
        self._mock_delete(req_mock, 200)
        logger = logging.getLogger('scripts.user_retirement.utils.thirdparty_apis.hubspot_api')

        with mock.patch.object(logger, 'info') as mock_info:
            HubspotAPI(
                self.api_key,
                self.test_region,
                self.from_address,
                self.alert_email
            ).delete_user(self.test_learner)
            mock_info.assert_called_once_with("User successfully deleted from Hubspot")
            mock_alert.assert_called_once_with(12345)

    def test_delete_email_does_not_exist(self, req_mock, mock_alert):
        self._mock_get_vid(req_mock, 404)
        logger = logging.getLogger('scripts.user_retirement.utils.thirdparty_apis.hubspot_api')
        with mock.patch.object(logger, 'info') as mock_info:
            HubspotAPI(
                self.api_key,
                self.test_region,
                self.from_address,
                self.alert_email
            ).delete_user(self.test_learner)
            mock_info.assert_called_once_with("No action taken because no user was found in Hubspot.")
            mock_alert.assert_not_called()

    def test_delete_server_failure_on_user_retrieval(self, req_mock, mock_alert):
        self._mock_get_vid(req_mock, 500)
        with self.assertRaises(hubspot_api.HubspotException) as exc:  # noqa: PT027
            HubspotAPI(
                self.api_key,
                self.test_region,
                self.from_address,
                self.alert_email
            ).delete_user(self.test_learner)
            self.assertIn("Error attempted to get user_vid from Hubspot", str(exc))  # noqa: PT009
            mock_alert.assert_not_called()

    def test_delete_unauthorized_deletion(self, req_mock, mock_alert):
        self._mock_get_vid(req_mock, 200)
        self._mock_delete(req_mock, 401)
        with self.assertRaises(hubspot_api.HubspotException) as exc:  # noqa: PT027
            HubspotAPI(
                self.api_key,
                self.test_region,
                self.from_address,
                self.alert_email
            ).delete_user(self.test_learner)
            self.assertIn("Hubspot user deletion failed due to authorized API call", str(exc))  # noqa: PT009
            mock_alert.assert_not_called()

    def test_delete_vid_not_found(self, req_mock, mock_alert):
        self._mock_get_vid(req_mock, 200)
        self._mock_delete(req_mock, 404)
        with self.assertRaises(hubspot_api.HubspotException) as exc:  # noqa: PT027
            HubspotAPI(
                self.api_key,
                self.test_region,
                self.from_address,
                self.alert_email
            ).delete_user(self.test_learner)
            self.assertIn("Hubspot user deletion failed because vid doesn't match user", str(exc))  # noqa: PT009
            mock_alert.assert_not_called()

    def test_delete_server_failure_on_deletion(self, req_mock, mock_alert):
        self._mock_get_vid(req_mock, 200)
        self._mock_delete(req_mock, 500)
        with self.assertRaises(hubspot_api.HubspotException) as exc:  # noqa: PT027
            HubspotAPI(
                self.api_key,
                self.test_region,
                self.from_address,
                self.alert_email
            ).delete_user(self.test_learner)
            self.assertIn("Hubspot user deletion failed due to server-side (Hubspot) issues", str(exc))  # noqa: PT009
            mock_alert.assert_not_called()

    def test_delete_catch_all_on_deletion(self, req_mock, mock_alert):
        self._mock_get_vid(req_mock, 200)
        # Testing 403 as it's not a response type per the Hubspot API docs, so it doesn't have it's own error.
        self._mock_delete(req_mock, 403)
        with self.assertRaises(hubspot_api.HubspotException) as exc:  # noqa: PT027
            HubspotAPI(
                self.api_key,
                self.test_region,
                self.from_address,
                self.alert_email
            ).delete_user(self.test_learner)
            self.assertIn("Hubspot user deletion failed due to unknown reasons", str(exc))  # noqa: PT009
            mock_alert.assert_not_called()
