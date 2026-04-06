"""
Tests for the Amplitude API functionality
"""
import logging
import os
import unittest
from unittest import mock

import ddt
import requests_mock

MAX_ATTEMPTS = int(os.environ.get("RETRY_MAX_ATTEMPTS", 5))
from scripts.user_retirement.utils.thirdparty_apis.amplitude_api import (
    AmplitudeApi,
    AmplitudeException,
    AmplitudeRecoverableException,
)


@ddt.ddt
@requests_mock.Mocker()
class TestAmplitude(unittest.TestCase):
    """
    Class containing tests of all code interacting with Amplitude.
    """

    def setUp(self):
        super().setUp()
        self.user = {"user": {"id": "1234"}}
        self.amplitude = AmplitudeApi("test-api-key", "test-secret-key")

    def _mock_delete(self, req_mock, status_code, message=None):
        """
        Send a mock request with dummy headers and status code.

        """
        req_mock.post(
            "https://amplitude.com/api/2/deletions/users",
            headers={"Content-Type": "application/json"},
            json={},
            status_code=status_code
        )

    def test_delete_happy_path(self, req_mock):
        """
        This test pass status_code 200 to mock_delete see how AmplitudeApi respond in happy path.

        """
        self._mock_delete(req_mock, 200)
        logger = logging.getLogger("scripts.user_retirement.utils.thirdparty_apis.amplitude_api")
        with mock.patch.object(logger, "info") as mock_info:
            self.amplitude.delete_user(self.user)

        self.assertEqual(mock_info.call_args, [("Amplitude user deletion succeeded",)])

        self.assertEqual(len(req_mock.request_history), 1)
        request = req_mock.request_history[0]
        self.assertEqual(request.json(),
                         {"user_ids": ["1234"], 'ignore_invalid_id': 'true', "requester": "user-retirement-pipeline"})

    def test_delete_fatal_error(self, req_mock):
        """
        This test pass status_code 404 to see how AmplitudeApi respond in fatal error case.

        """
        self._mock_delete(req_mock, 404)
        message = None
        logger = logging.getLogger("scripts.user_retirement.utils.thirdparty_apis.amplitude_api")
        with mock.patch.object(logger, "error") as mock_error:
            with self.assertRaises(AmplitudeException) as exc:
                self.amplitude.delete_user(self.user)
        error = "Amplitude user deletion failed due to {message}".format(message=message)
        self.assertEqual(mock_error.call_args, [(error,)])
        self.assertEqual(str(exc.exception), error)

    @ddt.data(429, 500)
    def test_delete_recoverable_error(self, status_code, req_mock):
        """
        This test pass status_code 429 and 500 to see how AmplitudeApi respond to recoverable cases.

        """
        self._mock_delete(req_mock, status_code)

        with self.assertRaises(AmplitudeRecoverableException):
            self.amplitude.delete_user(self.user)
        self.assertEqual(len(req_mock.request_history), MAX_ATTEMPTS)
