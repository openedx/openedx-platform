"""
Unit test for various Utility functions
"""

import json
from datetime import date, timedelta
from unittest.mock import patch

import ddt
from django.test import TestCase
from edx_rest_api_client.client import OAuthAPIClient
from oauth2_provider.models import Application
from requests.models import Response
from rest_framework import status

from common.djangoapps.student.tests.factories import GlobalStaffFactory, UserFactory
from lms.djangoapps.courseware.access_utils import adjust_start_date
from lms.djangoapps.courseware.constants import UNEXPECTED_ERROR_IS_ELIGIBLE
from lms.djangoapps.courseware.tests.factories import FinancialAssistanceConfigurationFactory
from lms.djangoapps.courseware.utils import (
    create_financial_assistance_application,
    get_financial_assistance_application_status,
    is_eligible_for_financial_aid,
)


@ddt.ddt
class TestAccessUtils(TestCase):
    """Tests for the access_utils functions."""

    @ddt.data(
        # days_early_for_beta, is_beta_user, expected_date
        (None, True, "2025-01-03"),
        (2, True, "2025-01-01"),
        (timedelta.max.days + 10, True, "2025-01-03"),
        (None, False, "2025-01-03"),
        (2, False, "2025-01-03"),
        (timedelta.max.days + 10, False, "2025-01-03"),
    )
    @ddt.unpack
    def test_adjust_start_date(self, days_early_for_beta, is_beta_user, expected_date):
        """Tests adjust_start_date

        Should only modify the date if the user is beta for the course,
        and `days_early_for_beta` is sensible number."""
        start = date(2025, 1, 3)
        expected = date.fromisoformat(expected_date)
        user = "princessofpower"
        course_key = "edx1+8675"
        with patch("lms.djangoapps.courseware.access_utils.CourseBetaTesterRole") as role_mock:
            instance = role_mock.return_value
            instance.has_user.return_value = is_beta_user
            adjusted_date = adjust_start_date(user, days_early_for_beta, start, course_key)
            self.assertEqual(expected, adjusted_date)


@ddt.ddt
class TestFinancialAssistanceViews(TestCase):
    """
    Tests new financial assistance views that communicate with edx-financial-assistance backend.
    """

    def setUp(self) -> None:
        super().setUp()
        self.test_course_id = "course-v1:edX+Test+1"
        self.user = UserFactory()
        self.global_staff = GlobalStaffFactory.create()
        _ = FinancialAssistanceConfigurationFactory(
            api_base_url="http://financial.assistance.test:1234",
            service_username=self.global_staff.username,
            fa_backend_enabled_courses_percentage=100,
            enabled=True,
        )
        _ = Application.objects.create(
            name="Test Application",
            user=self.global_staff,
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
        )

    def _mock_response(self, status_code, content=None):
        """
        Generates a python core response which is used as a default response in edx-rest-api-client.
        """
        mock_response = Response()
        mock_response.status_code = status_code
        mock_response._content = json.dumps(content).encode("utf-8")  # pylint: disable=protected-access
        return mock_response

    @ddt.data(
        {"is_eligible": True, "reason": None},
        {"is_eligible": False, "reason": "This course is not eligible for financial aid"},
    )
    def test_is_eligible_for_financial_aid(self, response_data):
        """
        Tests the functionality of is_eligible_for_financial_aid which calls edx-financial-assistance backend
        to return eligibility status for financial assistance for a given course.
        """
        with patch.object(OAuthAPIClient, "request") as oauth_mock:
            oauth_mock.return_value = self._mock_response(status.HTTP_200_OK, response_data)
            is_eligible, reason = is_eligible_for_financial_aid(self.test_course_id)
            assert is_eligible is response_data.get("is_eligible")
            assert reason == response_data.get("reason")

    def test_is_eligible_for_financial_aid_invalid_course_id(self):
        """
        Tests the functionality of is_eligible_for_financial_aid for an invalid course id.
        """
        error_message = f"Invalid course id {self.test_course_id} provided"
        with patch.object(OAuthAPIClient, "request") as oauth_mock:
            oauth_mock.return_value = self._mock_response(status.HTTP_400_BAD_REQUEST, {"message": error_message})
            is_eligible, reason = is_eligible_for_financial_aid(self.test_course_id)
            assert is_eligible is False
            assert reason == error_message

    def test_is_eligible_for_financial_aid_invalid_unexpected_error(self):
        """
        Tests the functionality of is_eligible_for_financial_aid for an unexpected error
        """
        with patch.object(OAuthAPIClient, "request") as oauth_mock:
            oauth_mock.return_value = self._mock_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR, {"error": "unexpected error occurred"}
            )
            is_eligible, reason = is_eligible_for_financial_aid(self.test_course_id)
            assert is_eligible is False
            assert reason == UNEXPECTED_ERROR_IS_ELIGIBLE

    def test_get_financial_assistance_application_status(self):
        """
        Tests the functionality of get_financial_assistance_application_status against a user id and a course id
        edx-financial-assistance backend to return status of a financial assistance application.
        """
        test_response = {"id": 123, "status": "ACCEPTED", "coupon_code": "ABCD.."}
        with patch.object(OAuthAPIClient, "request") as oauth_mock:
            oauth_mock.return_value = self._mock_response(status.HTTP_200_OK, test_response)
            has_application, reason = get_financial_assistance_application_status(self.user.id, self.test_course_id)
            assert has_application is True
            assert reason == test_response

    @ddt.data(
        {"status": status.HTTP_400_BAD_REQUEST, "content": {"message": "Invalid course id provided"}},
        {"status": status.HTTP_404_NOT_FOUND, "content": {"message": "Application details not found"}},
    )
    def test_get_financial_assistance_application_status_unsuccessful(self, response_data):
        """
        Tests unsuccessful scenarios of get_financial_assistance_application_status
        against a user id and a course id edx-financial-assistance backend.
        """
        with patch.object(OAuthAPIClient, "request") as oauth_mock:
            oauth_mock.return_value = self._mock_response(response_data.get("status"), response_data.get("content"))
            has_application, reason = get_financial_assistance_application_status(self.user.id, self.test_course_id)
            assert has_application is False
            assert reason == response_data.get("content").get("message")

    def test_create_financial_assistance_application(self):
        """
        Tests the functionality of create_financial_assistance_application which calls edx-financial-assistance backend
        to create a new financial assistance application given a form data.
        """
        test_form_data = {
            "lms_user_id": self.user.id,
            "course_id": self.test_course_id,
        }
        with patch.object(OAuthAPIClient, "request") as oauth_mock:
            oauth_mock.return_value = self._mock_response(status.HTTP_200_OK, {"success": True})
            response = create_financial_assistance_application(form_data=test_form_data)
            assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_create_financial_assistance_application_bad_request(self):
        """
        Tests the functionality of create_financial_assistance_application which calls edx-financial-assistance backend
        to create a new financial assistance application given a form data.
        """
        test_form_data = {
            "lms_user_id": self.user.id,
            "course_id": "invalid_course_id",
        }
        error_response = {"message": "Invalid course id provided"}
        with patch.object(OAuthAPIClient, "request") as oauth_mock:
            oauth_mock.return_value = self._mock_response(status.HTTP_400_BAD_REQUEST, error_response)
            response = create_financial_assistance_application(form_data=test_form_data)
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert json.loads(response.content.decode("utf-8")) == error_response
