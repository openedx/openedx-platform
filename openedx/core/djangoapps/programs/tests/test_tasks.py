"""
Tests for programs celery tasks.
"""

import json
import logging
from datetime import datetime, timedelta
from unittest import mock

import ddt
import httpretty
import pytest
from zoneinfo import ZoneInfo
import requests
from celery.exceptions import MaxRetriesExceededError
from django.conf import settings
from django.test import TestCase, override_settings
from edx_rest_api_client.auth import SuppliedJwtAuth
from requests.exceptions import HTTPError
from testfixtures import LogCapture

from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.certificates.tests.factories import CertificateDateOverrideFactory, GeneratedCertificateFactory
from openedx.core.djangoapps.catalog.tests.mixins import CatalogIntegrationMixin
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.credentials.tests.mixins import CredentialsApiConfigMixin
from openedx.core.djangoapps.oauth_dispatch.tests.factories import ApplicationFactory
from openedx.core.djangoapps.programs import tasks
from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory, SiteFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.data import CertificatesDisplayBehaviors

log = logging.getLogger(__name__)

CREDENTIALS_INTERNAL_SERVICE_URL = "https://credentials.example.com"
TASKS_MODULE = "openedx.core.djangoapps.programs.tasks"


@skip_unless_lms
class GetAwardedCertificateProgramsTestCase(TestCase):
    """
    Test the get_certified_programs function
    """

    def make_credential_result(self, **kwargs):
        """
        Helper to make dummy results from the credentials API
        """
        result = {
            "id": 1,
            "username": "dummy-username",
            "credential": {
                "credential_id": None,
                "program_uuid": None,
            },
            "status": "dummy-status",
            "uuid": "dummy-uuid",
            "certificate_url": "http://credentials.edx.org/credentials/dummy-uuid/",
        }
        result.update(**kwargs)
        return result

    @mock.patch(TASKS_MODULE + ".get_credentials")
    def test_get_certified_programs(self, mock_get_credentials):
        """
        Ensure the API is called and results handled correctly.
        """
        student = UserFactory(username="test-username")
        mock_get_credentials.return_value = [
            self.make_credential_result(status="awarded", credential={"program_uuid": 1}),
        ]

        result = tasks.get_certified_programs(student)
        assert mock_get_credentials.call_args[0] == (student,)
        assert mock_get_credentials.call_args[1].get("credential_type", None) == "program"
        assert result == [1]


@skip_unless_lms
class AwardProgramCertificateTestCase(TestCase):
    """
    Test the award_program_certificate function
    """

    @httpretty.activate
    @mock.patch("openedx.core.djangoapps.programs.tasks.get_credentials_api_base_url")
    def test_award_program_certificate(self, mock_get_api_base_url):
        """
        Ensure the correct API call gets made
        """
        mock_get_api_base_url.return_value = "http://test-server/"
        student = UserFactory(username="test-username", email="test-email@email.com")

        test_client = requests.Session()
        test_client.auth = SuppliedJwtAuth("test-token")

        httpretty.register_uri(
            httpretty.POST,
            "http://test-server/credentials/",
        )

        tasks.award_program_certificate(test_client, student, 123)

        expected_body = {
            "username": student.username,
            "lms_user_id": student.id,
            "credential": {
                "program_uuid": 123,
                "type": tasks.PROGRAM_CERTIFICATE,
            },
        }
        last_request_body = httpretty.last_request().body.decode("utf-8")
        assert json.loads(last_request_body) == expected_body


@skip_unless_lms
@ddt.ddt
@override_settings(CREDENTIALS_SERVICE_USERNAME="test-service-username")
class AwardProgramCertificatesUtilitiesTestCase(CatalogIntegrationMixin, CredentialsApiConfigMixin, TestCase):
    """
    Tests for the utility methods for the 'award_program_certificates' celery task.
    """

    def setUp(self):
        super().setUp()
        self.create_credentials_config()
        self.student = UserFactory.create(username="test-student")
        self.site = SiteFactory()
        self.site_configuration = SiteConfigurationFactory(site=self.site)
        self.catalog_integration = self.create_catalog_integration()
        ApplicationFactory.create(name="credentials")
        UserFactory.create(username=settings.CREDENTIALS_SERVICE_USERNAME)

    def test_get_completed_programs(self):
        """get_completed_programs returns result of ProgramProgressMeter.completed_programs_with_available_dates"""
        expected = {1: 1, 2: 2, 3: 3}
        with mock.patch(
            TASKS_MODULE + ".ProgramProgressMeter.completed_programs_with_available_dates",
            new_callable=mock.PropertyMock,
        ) as mock_completed_programs_with_available_dates:
            mock_completed_programs_with_available_dates.return_value = expected
            completed_programs = tasks.get_completed_programs(self.site, self.student)
            assert expected == completed_programs


@skip_unless_lms
@ddt.ddt
@mock.patch(TASKS_MODULE + ".award_program_certificate")
@mock.patch(TASKS_MODULE + ".get_certified_programs")
@mock.patch(TASKS_MODULE + ".get_completed_programs")
@override_settings(CREDENTIALS_SERVICE_USERNAME="test-service-username")
class AwardProgramCertificatesTestCase(CatalogIntegrationMixin, CredentialsApiConfigMixin, TestCase):
    """
    Tests for the 'award_program_certificates' celery task.
    """

    def setUp(self):
        super().setUp()
        self.create_credentials_config()
        self.student = UserFactory.create(username="test-student")
        self.site = SiteFactory()
        self.site_configuration = SiteConfigurationFactory(site=self.site)
        self.catalog_integration = self.create_catalog_integration()
        ApplicationFactory.create(name="credentials")
        UserFactory.create(username=settings.CREDENTIALS_SERVICE_USERNAME)

    def test_completion_check(
        self,
        mock_get_completed_programs,
        mock_get_certified_programs,  # pylint: disable=unused-argument
        mock_award_program_certificate,  # pylint: disable=unused-argument
    ):
        """
        Checks that the Programs API is used correctly to determine completed
        programs.
        """
        tasks.award_program_certificates.delay(self.student.username).get()
        mock_get_completed_programs.assert_any_call(self.site, self.student)

    @ddt.data(
        ([1], [2, 3]),
        ([], [1, 2, 3]),
        ([1, 2, 3], []),
    )
    @ddt.unpack
    def test_awarding_certs(
        self,
        already_awarded_program_uuids,
        expected_awarded_program_uuids,
        mock_get_completed_programs,
        mock_get_certified_programs,
        mock_award_program_certificate,
    ):
        """
        Checks that the Credentials API is used to award certificates for
        the proper programs.
        """
        mock_get_completed_programs.return_value = {1: 1, 2: 2, 3: 3}
        mock_get_certified_programs.return_value = already_awarded_program_uuids

        tasks.award_program_certificates.delay(self.student.username).get()

        actual_program_uuids = [call[0][2] for call in mock_award_program_certificate.call_args_list]
        assert actual_program_uuids == expected_awarded_program_uuids

    @mock.patch("openedx.core.djangoapps.site_configuration.helpers.get_current_site_configuration")
    def test_awarding_certs_with_skip_program_certificate(
        self,
        mocked_get_current_site_configuration,
        mock_get_completed_programs,
        mock_get_certified_programs,
        mock_award_program_certificate,
    ):
        """
        Checks that the Credentials API is used to award certificates for
        the proper programs and those program will be skipped which are provided
        by 'programs_without_certificates' list in site configuration.
        """
        # all completed programs
        mock_get_completed_programs.return_value = {1: 1, 2: 2, 3: 3, 4: 4}

        # already awarded programs
        mock_get_certified_programs.return_value = [1]

        # programs to be skipped
        self.site_configuration.site_values = {"programs_without_certificates": [2]}
        self.site_configuration.save()
        mocked_get_current_site_configuration.return_value = self.site_configuration

        # programs which are expected to be awarded.
        # (completed_programs - (already_awarded+programs + to_be_skipped_programs)
        expected_awarded_program_uuids = [3, 4]

        tasks.award_program_certificates.delay(self.student.username).get()
        actual_program_uuids = [call[0][2] for call in mock_award_program_certificate.call_args_list]
        assert actual_program_uuids == expected_awarded_program_uuids

    @ddt.data(
        ("credentials", "enable_learner_issuance"),
    )
    @ddt.unpack
    def test_retry_if_config_disabled(self, disabled_config_type, disabled_config_attribute, *mock_helpers):
        """
        Checks that the task is aborted if any relevant api configs are
        disabled.
        """
        getattr(self, f"create_{disabled_config_type}_config")(**{disabled_config_attribute: False})
        with mock.patch(TASKS_MODULE + ".LOGGER.warning") as mock_warning:
            with pytest.raises(MaxRetriesExceededError):
                tasks.award_program_certificates.delay(self.student.username).get()
            assert mock_warning.called
        for mock_helper in mock_helpers:
            assert not mock_helper.called

    def test_abort_if_invalid_username(self, *mock_helpers):
        """
        Checks that the task will be aborted and not retried if the username
        passed was not found, and that an exception is logged.
        """
        with mock.patch(TASKS_MODULE + ".LOGGER.warning") as mock_exception:
            tasks.award_program_certificates.delay("nonexistent-username").get()
            assert mock_exception.called
        for mock_helper in mock_helpers:
            assert not mock_helper.called

    def test_abort_if_no_completed_programs(
        self,
        mock_get_completed_programs,
        mock_get_certified_programs,
        mock_award_program_certificate,
    ):
        """
        Checks that the task will be aborted without further action if there
        are no programs for which to award a certificate.
        """
        mock_get_completed_programs.return_value = {}
        tasks.award_program_certificates.delay(self.student.username).get()
        assert mock_get_completed_programs.called
        assert not mock_get_certified_programs.called
        assert not mock_award_program_certificate.called

    @mock.patch("openedx.core.djangoapps.site_configuration.helpers.get_value")
    def test_programs_without_certificates(
        self,
        mock_get_value,
        mock_get_completed_programs,
        mock_get_certified_programs,
        mock_award_program_certificate,
    ):
        """
        Checks that the task will be aborted without further action if there exists a list
        programs_without_certificates with ["ALL"] value in site configuration.
        """
        mock_get_value.return_value = ["ALL"]
        mock_get_completed_programs.return_value = {1: 1, 2: 2}
        tasks.award_program_certificates.delay(self.student.username).get()
        assert not mock_get_completed_programs.called
        assert not mock_get_certified_programs.called
        assert not mock_award_program_certificate.called

    @mock.patch(TASKS_MODULE + ".get_credentials_api_client")
    def test_failure_to_create_api_client_retries(
        self,
        mock_get_api_client,
        mock_get_completed_programs,
        mock_get_certified_programs,
        mock_award_program_certificate,
    ):
        """
        Checks that we log an exception and retry if the API client isn't creating.
        """
        mock_get_api_client.side_effect = Exception("boom")
        mock_get_completed_programs.return_value = {1: 1, 2: 2}
        mock_get_certified_programs.return_value = [2]

        with mock.patch(TASKS_MODULE + ".LOGGER.exception") as mock_exception:
            with pytest.raises(MaxRetriesExceededError):
                tasks.award_program_certificates.delay(self.student.username).get()

        assert mock_exception.called
        assert mock_get_api_client.call_count == (tasks.MAX_RETRIES)
        assert not mock_award_program_certificate.called

    def _make_side_effect(self, side_effects):
        """
        DRY helper.  Returns a side effect function for use with mocks that
        will be called multiple times, permitting Exceptions to be raised
        (or not) in a specified order.

        See Also:
            http://www.voidspace.org.uk/python/mock/examples.html#multiple-calls-with-different-effects
            http://www.voidspace.org.uk/python/mock/mock.html#mock.Mock.side_effect

        """

        def side_effect(*_a):  # pylint: disable=missing-docstring
            if side_effects:
                exc = side_effects.pop(0)
                if exc:
                    raise exc
            return mock.DEFAULT

        return side_effect

    def test_continue_awarding_certs_if_error(
        self,
        mock_get_completed_programs,
        mock_get_certified_programs,
        mock_award_program_certificate,
    ):
        """
        Checks that a single failure to award one of several certificates
        does not cause the entire task to fail.  Also ensures that
        successfully awarded certs are logged as INFO and warning is logged
        for failed requests if there are retries available.
        """
        mock_get_completed_programs.return_value = {1: 1, 2: 2}
        mock_get_certified_programs.side_effect = [[], [2]]
        mock_award_program_certificate.side_effect = self._make_side_effect([Exception("boom"), None])

        with mock.patch(TASKS_MODULE + ".LOGGER.info") as mock_info, mock.patch(
            TASKS_MODULE + ".LOGGER.exception"
        ) as mock_warning:
            tasks.award_program_certificates.delay(self.student.username).get()
        assert mock_award_program_certificate.call_count == 3
        mock_warning.assert_called_once_with(
            f"Failed to award program certificate to user {self.student.id} in program 1: boom"
        )
        mock_info.assert_any_call(f"Awarded program certificate to user {self.student.id} in program 1")
        mock_info.assert_any_call(f"Awarded program certificate to user {self.student.id} in program 2")

    def test_retry_on_programs_api_errors(self, mock_get_completed_programs, *_mock_helpers):
        """
        Ensures that any otherwise-unhandled errors that arise while trying
        to get completed programs (e.g. network issues or other
        transient API errors) will cause the task to be failed and queued for
        retry.
        """
        mock_get_completed_programs.side_effect = self._make_side_effect([Exception("boom"), None])
        tasks.award_program_certificates.delay(self.student.username).get()
        assert mock_get_completed_programs.call_count == 3

    def test_retry_on_credentials_api_errors(
        self,
        mock_get_completed_programs,
        mock_get_certified_programs,
        mock_award_program_certificate,
    ):
        """
        Ensures that any otherwise-unhandled errors that arise while trying
        to get existing program credentials (e.g. network issues or other
        transient API errors) will cause the task to be failed and queued for
        retry.
        """
        mock_get_completed_programs.return_value = {1: 1, 2: 2}
        mock_get_certified_programs.return_value = [1]
        mock_get_certified_programs.side_effect = self._make_side_effect([Exception("boom"), None])
        tasks.award_program_certificates.delay(self.student.username).get()
        assert mock_get_certified_programs.call_count == 2
        assert mock_award_program_certificate.call_count == 1

    def test_retry_on_credentials_api_429_error(
        self,
        mock_get_completed_programs,
        mock_get_certified_programs,  # pylint: disable=unused-argument
        mock_award_program_certificate,
    ):
        """
        Verify that a 429 error causes the task to fail and then retry.
        """
        exception = HTTPError()
        exception.response = mock.Mock(status_code=429)
        mock_get_completed_programs.return_value = {1: 1, 2: 2}
        mock_award_program_certificate.side_effect = self._make_side_effect([exception, None])

        tasks.award_program_certificates.delay(self.student.username).get()

        assert mock_award_program_certificate.call_count == 3

    def test_no_retry_on_credentials_api_404_error(
        self,
        mock_get_completed_programs,
        mock_get_certified_programs,  # pylint: disable=unused-argument
        mock_award_program_certificate,
    ):
        """
        Verify that a 404 error causes the task to fail but there is no retry.
        """
        exception = HTTPError()
        exception.response = mock.Mock(status_code=404)
        mock_get_completed_programs.return_value = {1: 1, 2: 2}
        mock_award_program_certificate.side_effect = self._make_side_effect([exception, None])

        tasks.award_program_certificates.delay(self.student.username).get()

        assert mock_award_program_certificate.call_count == 2

    def test_no_retry_on_credentials_api_4XX_error(
        self,
        mock_get_completed_programs,
        mock_get_certified_programs,  # pylint: disable=unused-argument
        mock_award_program_certificate,
    ):
        """
        Verify that other 4XX errors cause task to fail but there is no retry.
        """
        exception = HTTPError()
        exception.response = mock.Mock(status_code=418)
        mock_get_completed_programs.return_value = {1: 1, 2: 2}
        mock_award_program_certificate.side_effect = self._make_side_effect([exception, None])

        tasks.award_program_certificates.delay(self.student.username).get()

        assert mock_award_program_certificate.call_count == 2


@skip_unless_lms
class PostCourseCertificateTestCase(TestCase):
    """
    Test the award_program_certificate function
    """

    def setUp(self):  # lint-amnesty, pylint: disable=super-method-not-called
        self.student = UserFactory.create(username="test-student")
        self.course = CourseOverviewFactory.create(
            self_paced=True  # Any option to allow the certificate to be viewable for the course
        )
        self.certificate = GeneratedCertificateFactory(
            user=self.student,
            mode="verified",
            course_id=self.course.id,
            status="downloadable",
        )

    @httpretty.activate
    @mock.patch("openedx.core.djangoapps.programs.tasks.get_credentials_api_base_url")
    def test_post_course_certificate(self, mock_get_api_base_url):
        """
        Ensure the correct API call gets made
        """
        mock_get_api_base_url.return_value = "http://test-server/"
        test_client = requests.Session()
        test_client.auth = SuppliedJwtAuth("test-token")

        httpretty.register_uri(
            httpretty.POST,
            "http://test-server/credentials/",
        )

        tasks.post_course_certificate(test_client, self.student.username, self.certificate)

        expected_body = {
            "username": self.student.username,
            "status": "awarded",
            "credential": {
                "course_run_key": str(self.certificate.course_id),
                "mode": self.certificate.mode,
                "type": tasks.COURSE_CERTIFICATE,
            },
            "date_override": None,
        }
        last_request_body = httpretty.last_request().body.decode("utf-8")
        assert json.loads(last_request_body) == expected_body


@skip_unless_lms
@ddt.ddt
@mock.patch(
    "lms.djangoapps.certificates.api.auto_certificate_generation_enabled",
    mock.Mock(return_value=True),
)
@mock.patch(TASKS_MODULE + ".post_course_certificate")
@override_settings(CREDENTIALS_SERVICE_USERNAME="test-service-username")
class AwardCourseCertificatesTestCase(CredentialsApiConfigMixin, TestCase):
    """
    Test the award_course_certificate celery task
    """

    def setUp(self):
        super().setUp()

        self.available_date = datetime.now(ZoneInfo("UTC")) + timedelta(days=1)
        self.course = CourseOverviewFactory.create(
            self_paced=True,  # Any option to allow the certificate to be viewable for the course
            certificate_available_date=self.available_date,
            certificates_display_behavior=CertificatesDisplayBehaviors.END_WITH_DATE,
        )
        self.student = UserFactory.create(username="test-student")
        # Instantiate the Certificate first so that the config doesn't execute issuance
        self.certificate = GeneratedCertificateFactory.create(
            user=self.student,
            mode="verified",
            course_id=self.course.id,
            status="downloadable",
        )

        self.create_credentials_config()
        self.site = SiteFactory()

        ApplicationFactory.create(name="credentials")
        UserFactory.create(username=settings.CREDENTIALS_SERVICE_USERNAME)

    def _add_certificate_date_override(self):
        """
        Creates a mock CertificateDateOverride and adds it to the certificate
        """
        self.certificate.date_override = CertificateDateOverrideFactory.create(
            generated_certificate=self.certificate,
            overridden_by=UserFactory.create(username="test-admin"),
        )

    @ddt.data(
        "verified",
        "no-id-professional",
    )
    def test_award_course_certificates(self, mode, mock_post_course_certificate):
        """
        Tests the API POST method is called with appropriate params when configured properly
        """
        self.certificate.mode = mode
        self.certificate.save()
        tasks.award_course_certificate.delay(self.student.username, str(self.course.id)).get()
        call_args, _ = mock_post_course_certificate.call_args
        assert call_args[1] == self.student.username
        assert call_args[2] == self.certificate

    def test_award_course_certificates_available_date(self, mock_post_course_certificate):
        """
        Tests the API POST method is called with available date when the course is not self paced
        """
        self.course.self_paced = False
        self.course.save()
        tasks.award_course_certificate.delay(self.student.username, str(self.course.id)).get()
        call_args, _ = mock_post_course_certificate.call_args
        assert call_args[1] == self.student.username
        assert call_args[2] == self.certificate

    def test_award_course_certificates_override_date(self, mock_post_course_certificate):
        """
        Tests the API POST method is called with date override when present
        """
        self._add_certificate_date_override()
        tasks.award_course_certificate.delay(self.student.username, str(self.course.id)).get()
        call_args, _ = mock_post_course_certificate.call_args
        assert call_args[1] == self.student.username
        assert call_args[2] == self.certificate
        assert call_args[3] == self.certificate.date_override.date

    def test_award_course_cert_not_called_if_disabled(self, mock_post_course_certificate):
        """
        Test that the post method is never called if the config is disabled
        """
        self.create_credentials_config(enabled=False)
        with mock.patch(TASKS_MODULE + ".LOGGER.warning") as mock_warning:
            with pytest.raises(MaxRetriesExceededError):
                tasks.award_course_certificate.delay(self.student.username, str(self.course.id)).get()
        assert mock_warning.called
        assert not mock_post_course_certificate.called

    def test_award_course_cert_not_called_if_user_not_found(self, mock_post_course_certificate):
        """
        Test that the post method is never called if the user isn't found by username
        """
        with mock.patch(TASKS_MODULE + ".LOGGER.warning") as mock_exception:
            # Use a random username here since this user won't be found in the DB
            tasks.award_course_certificate.delay("random_username", str(self.course.id)).get()
        assert mock_exception.called
        assert not mock_post_course_certificate.called

    def test_award_course_cert_not_called_if_course_overview_not_found(self, mock_post_course_certificate):
        """
        Test that the post method is never called if the CourseOverview isn't found
        """
        self.course.delete()
        with mock.patch(TASKS_MODULE + ".LOGGER.warning") as mock_exception:
            # Use the certificate course id here since the course will be deleted
            tasks.award_course_certificate.delay(self.student.username, str(self.certificate.course_id)).get()
        assert mock_exception.called
        assert not mock_post_course_certificate.called

    def test_award_course_cert_not_called_if_certificate_not_found(self, mock_post_course_certificate):
        """
        Test that the post method is never called if the certificate doesn't exist for the user and course
        """
        self.certificate.delete()
        with mock.patch(TASKS_MODULE + ".LOGGER.warning") as mock_exception:
            tasks.award_course_certificate.delay(self.student.username, str(self.course.id)).get()
        assert mock_exception.called
        assert not mock_post_course_certificate.called

    def test_award_course_cert_not_called_if_course_run_key_is_bad(self, mock_post_course_certificate):
        """
        Test that the post method is never called if the course run key is invalid
        """
        bad_course_run_key = "I/Am/The/Keymaster"
        expected_message = (
            f"Failed to award course certificate for user {self.student.id} for course "
            f"{bad_course_run_key}. Reason: Failed to determine course key"
        )
        with LogCapture(level=logging.WARNING) as log_capture:
            tasks.award_course_certificate.delay(self.student.username, bad_course_run_key).get()
            assert not mock_post_course_certificate.called
            log_capture.check_present(
                ("openedx.core.djangoapps.programs.tasks", "WARNING", expected_message),
            )

    def test_award_course_cert_not_called_if_certificated_not_verified_mode(self, mock_post_course_certificate):
        """
        Test that the post method is never called if the GeneratedCertificate is an 'audit' cert
        """
        # Temporarily disable the config so the signal isn't handled from .save
        self.create_credentials_config(enabled=False)
        self.certificate.mode = "audit"
        self.certificate.save()
        self.create_credentials_config()

        tasks.award_course_certificate.delay(self.student.username, str(self.certificate.course_id)).get()
        assert not mock_post_course_certificate.called


@skip_unless_lms
class RevokeProgramCertificateTestCase(TestCase):
    """
    Test the revoke_program_certificate function
    """

    @httpretty.activate
    @mock.patch("openedx.core.djangoapps.programs.tasks.get_credentials_api_base_url")
    def test_revoke_program_certificate(self, mock_get_api_base_url):
        """
        Ensure the correct API call gets made
        """
        mock_get_api_base_url.return_value = "http://test-server/"
        test_username = "test-username"
        test_client = requests.Session()
        test_client.auth = SuppliedJwtAuth("test-token")

        httpretty.register_uri(
            httpretty.POST,
            "http://test-server/credentials/",
        )

        tasks.revoke_program_certificate(test_client, test_username, 123)

        expected_body = {
            "username": test_username,
            "status": "revoked",
            "credential": {
                "program_uuid": 123,
                "type": tasks.PROGRAM_CERTIFICATE,
            },
        }
        last_request_body = httpretty.last_request().body.decode("utf-8")
        assert json.loads(last_request_body) == expected_body


@skip_unless_lms
@ddt.ddt
@mock.patch(TASKS_MODULE + ".revoke_program_certificate")
@mock.patch(TASKS_MODULE + ".get_certified_programs")
@mock.patch(TASKS_MODULE + ".get_inverted_programs")
@override_settings(CREDENTIALS_SERVICE_USERNAME="test-service-username")
class RevokeProgramCertificatesTestCase(CatalogIntegrationMixin, CredentialsApiConfigMixin, TestCase):
    """
    Tests for the 'revoke_program_certificates' celery task.
    """

    def setUp(self):
        super().setUp()

        self.student = UserFactory.create(username="test-student")
        self.course_key = "course-v1:testX+test101+2T2020"
        self.site = SiteFactory()
        self.site_configuration = SiteConfigurationFactory(site=self.site)
        ApplicationFactory.create(name="credentials")
        UserFactory.create(username=settings.CREDENTIALS_SERVICE_USERNAME)
        self.create_credentials_config()

        self.inverted_programs = {self.course_key: [{"uuid": 1}, {"uuid": 2}]}

    def _make_side_effect(self, side_effects, *args, **kwargs):
        """
        DRY helper.  Returns a side effect function for use with mocks that
        will be called multiple times, permitting Exceptions to be raised
        (or not) in a specified order.
        """

        def side_effect(*args, **kwargs):
            if side_effects:
                exc = side_effects.pop(0)
                if exc:
                    raise exc
            return mock.DEFAULT

        return side_effect

    def test_inverted_programs(
        self,
        mock_get_inverted_programs,
        mock_get_certified_programs,  # pylint: disable=unused-argument
        mock_revoke_program_certificate,  # pylint: disable=unused-argument
    ):
        """
        Checks that the Programs API is used correctly to determine completed
        programs.
        """
        tasks.revoke_program_certificates.delay(self.student.username, self.course_key).get()
        mock_get_inverted_programs.assert_any_call(self.student)

    def test_revoke_program_certificate(
        self,
        mock_get_inverted_programs,
        mock_get_certified_programs,
        mock_revoke_program_certificate,
    ):
        """
        Checks that the Credentials API is used to revoke certificates for
        the proper programs.
        """
        expected_program_uuid = 1
        mock_get_inverted_programs.return_value = {self.course_key: [{"uuid": expected_program_uuid}]}
        mock_get_certified_programs.return_value = [expected_program_uuid]

        tasks.revoke_program_certificates.delay(self.student.username, self.course_key).get()

        call_args, _ = mock_revoke_program_certificate.call_args
        assert call_args[1] == self.student.username
        assert call_args[2] == expected_program_uuid

    @ddt.data(
        ("credentials", "enable_learner_issuance"),
    )
    @ddt.unpack
    def test_retry_if_config_disabled(self, disabled_config_type, disabled_config_attribute, *mock_helpers):
        """
        Checks that the task is aborted if any relevant api configs are
        disabled.
        """
        getattr(self, f"create_{disabled_config_type}_config")(**{disabled_config_attribute: False})
        with mock.patch(TASKS_MODULE + ".LOGGER.warning") as mock_warning:
            with pytest.raises(MaxRetriesExceededError):
                tasks.revoke_program_certificates.delay(self.student.username, self.course_key).get()
            assert mock_warning.called
        for mock_helper in mock_helpers:
            assert not mock_helper.called

    def test_abort_if_invalid_username(self, *mock_helpers):
        """
        Checks that the task will be aborted and not retried if the username
        passed was not found, and that an exception is logged.
        """
        with mock.patch(TASKS_MODULE + ".LOGGER.warning") as mock_exception:
            tasks.revoke_program_certificates.delay("nonexistent-username", self.course_key).get()
            assert mock_exception.called
        for mock_helper in mock_helpers:
            assert not mock_helper.called

    def test_abort_if_no_program(
        self,
        mock_get_inverted_programs,
        mock_get_certified_programs,
        mock_revoke_program_certificate,
    ):
        """
        Checks that the task will be aborted without further action if course is
        not part of any program.
        """
        mock_get_inverted_programs.return_value = {}
        tasks.revoke_program_certificates.delay(self.student.username, self.course_key).get()
        assert mock_get_inverted_programs.called
        assert not mock_get_certified_programs.called
        assert not mock_revoke_program_certificate.called

    def test_continue_revoking_certs_if_error(
        self,
        mock_get_inverted_programs,
        mock_get_certified_programs,
        mock_revoke_program_certificate,
    ):
        """
        Checks that a single failure to revoke one of several certificates
        does not cause the entire task to fail.  Also ensures that
        successfully revoked certs are logged as INFO and warning is logged
        for failed requests if there are retries available.
        """
        mock_get_inverted_programs.return_value = self.inverted_programs
        mock_get_certified_programs.side_effect = [[1], [1, 2]]
        mock_revoke_program_certificate.side_effect = self._make_side_effect([Exception("boom"), None])

        with mock.patch(TASKS_MODULE + ".LOGGER.info") as mock_info, mock.patch(
            TASKS_MODULE + ".LOGGER.exception"
        ) as mock_warning:
            tasks.revoke_program_certificates.delay(self.student.username, self.course_key).get()

        assert mock_revoke_program_certificate.call_count == 3
        mock_warning.assert_called_once_with(
            f"Failed to revoke program certificate from user {self.student.id} in program 1: boom"
        )
        mock_info.assert_any_call(f"Revoked program certificate from user {self.student.id} in program 1")
        mock_info.assert_any_call(f"Revoked program certificate from user {self.student.id} in program 2")

    def test_retry_on_credentials_api_errors(
        self,
        mock_get_inverted_programs,
        mock_get_certified_programs,
        mock_revoke_program_certificate,
    ):
        """
        Ensures that any otherwise-unhandled errors that arise while trying
        to get existing program credentials (e.g. network issues or other
        transient API errors) will cause the task to be failed and queued for
        retry.
        """
        mock_get_inverted_programs.return_value = self.inverted_programs
        mock_get_certified_programs.return_value = [1]
        mock_get_certified_programs.side_effect = self._make_side_effect([Exception("boom"), None])
        tasks.revoke_program_certificates.delay(self.student.username, self.course_key).get()
        assert mock_get_certified_programs.call_count == 2
        assert mock_revoke_program_certificate.call_count == 1

    def test_retry_on_credentials_api_429_error(
        self,
        mock_get_inverted_programs,
        mock_get_certified_programs,
        mock_revoke_program_certificate,
    ):
        """
        Verify that a 429 error causes the task to fail and then retry.
        """
        exception = HTTPError()
        exception.response = mock.Mock(status_code=429)
        mock_get_inverted_programs.return_value = self.inverted_programs
        mock_get_certified_programs.return_value = [1, 2]
        mock_revoke_program_certificate.side_effect = self._make_side_effect([exception, None])

        tasks.revoke_program_certificates.delay(self.student.username, self.course_key).get()

        assert mock_revoke_program_certificate.call_count == 3

    def test_no_retry_on_credentials_api_404_error(
        self,
        mock_get_inverted_programs,
        mock_get_certified_programs,
        mock_revoke_program_certificate,
    ):
        """
        Verify that a 404 error causes the task to fail but there is no retry.
        """
        exception = HTTPError()
        exception.response = mock.Mock(status_code=404)
        mock_get_inverted_programs.return_value = self.inverted_programs
        mock_get_certified_programs.return_value = [1, 2]
        mock_revoke_program_certificate.side_effect = self._make_side_effect([exception, None])

        tasks.revoke_program_certificates.delay(self.student.username, self.course_key).get()

        assert mock_revoke_program_certificate.call_count == 2

    def test_no_retry_on_credentials_api_4XX_error(
        self,
        mock_get_inverted_programs,
        mock_get_certified_programs,
        mock_revoke_program_certificate,
    ):
        """
        Verify that other 4XX errors cause task to fail but there is no retry.
        """
        exception = HTTPError()
        exception.response = mock.Mock(status_code=418)
        mock_get_inverted_programs.return_value = self.inverted_programs
        mock_get_certified_programs.return_value = [1, 2]
        mock_revoke_program_certificate.side_effect = self._make_side_effect([exception, None])

        tasks.revoke_program_certificates.delay(self.student.username, self.course_key).get()

        assert mock_revoke_program_certificate.call_count == 2

    def test_get_api_client_failure_retries(
        self,
        mock_get_inverted_programs,
        mock_get_certified_programs,
        mock_revoke_program_certificate,
    ):
        """
        Verify that a 404 error causes the task to fail but there is no retry.
        """
        mock_get_inverted_programs.return_value = self.inverted_programs
        mock_get_certified_programs.return_value = [1, 2]

        with mock.patch(TASKS_MODULE + ".get_credentials_api_client") as mock_get_api_client, mock.patch(
            TASKS_MODULE + ".LOGGER.exception"
        ) as mock_exception:
            mock_get_api_client.side_effect = Exception("boom")
            with pytest.raises(MaxRetriesExceededError):
                tasks.revoke_program_certificates.delay(self.student.username, self.course_key).get()
        assert mock_exception.called
        assert mock_get_api_client.call_count == (tasks.MAX_RETRIES)
        assert not mock_revoke_program_certificate.called


@skip_unless_lms
@override_settings(CREDENTIALS_SERVICE_USERNAME="test-service-username")
class UpdateCredentialsCourseCertificateConfigurationAvailableDateTestCase(TestCase):
    """
    Tests for the update_credentials_course_certificate_configuration_available_date function
    """

    def setUp(self):
        super().setUp()
        self.course = CourseOverviewFactory.create(
            certificate_available_date=datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        CourseModeFactory.create(course_id=self.course.id, mode_slug="verified")
        CourseModeFactory.create(course_id=self.course.id, mode_slug="audit")
        self.available_date = self.course.certificate_available_date
        self.course_id = self.course.id
        self.credentials_worker = UserFactory(username="test-service-username")

    def test_update_course_cert_available_date(self):
        with mock.patch(TASKS_MODULE + ".post_course_certificate_configuration") as update_posted:
            tasks.update_credentials_course_certificate_configuration_available_date(
                self.course_id, self.available_date
            )
            update_posted.assert_called_once()

    def test_course_with_two_paid_modes(self):
        CourseModeFactory.create(course_id=self.course.id, mode_slug="professional")
        with mock.patch(TASKS_MODULE + ".post_course_certificate_configuration") as update_posted:
            tasks.update_credentials_course_certificate_configuration_available_date(
                self.course_id, self.available_date
            )
            update_posted.assert_not_called()


@skip_unless_lms
class PostCourseCertificateConfigurationTestCase(TestCase):
    """
    Test the post_course_certificate_configuration function
    """

    def setUp(self):
        super().setUp()
        self.certificate = {
            "mode": "verified",
            "course_id": "testCourse",
        }

    @httpretty.activate
    @mock.patch("openedx.core.djangoapps.programs.tasks.get_credentials_api_base_url")
    def test_post_course_certificate_configuration(self, mock_get_api_base_url):
        """
        Ensure the correct API call gets made
        """
        mock_get_api_base_url.return_value = "http://test-server/"
        test_client = requests.Session()
        test_client.auth = SuppliedJwtAuth("test-token")

        httpretty.register_uri(
            httpretty.POST,
            "http://test-server/course_certificates/",
        )

        available_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

        tasks.post_course_certificate_configuration(test_client, self.certificate, available_date)

        expected_body = {
            "course_id": "testCourse",
            "certificate_type": "verified",
            "certificate_available_date": available_date,
            "is_active": True,
        }
        last_request_body = httpretty.last_request().body.decode("utf-8")
        assert json.loads(last_request_body) == expected_body


@skip_unless_lms
class UpdateCertificateAvailableDateOnCourseUpdateTestCase(CredentialsApiConfigMixin, TestCase):
    """
    Tests for the `update_certificate_available_date_on_course_update` task.
    """

    def setUp(self):
        super().setUp()
        self.end_date = datetime.now(ZoneInfo("UTC")) + timedelta(days=90)
        self.credentials_api_config = self.create_credentials_config(enabled=False)

    def tearDown(self):
        super().tearDown()
        self.credentials_api_config = self.create_credentials_config(enabled=False)

    def _create_course_overview(self, self_paced, display_behavior, available_date, end):
        """
        Utility function to generate a CourseOverview with required settings for functions under test.
        """
        return CourseOverviewFactory.create(
            self_paced=self_paced,
            end=end,
            certificate_available_date=available_date,
            certificates_display_behavior=display_behavior,
        )

    def _update_credentials_api_config(self, is_enabled):
        """
        Utility function to enable or disable use of the Credentials IDA in our test environment for the functions
        under test.
        """
        self.credentials_api_config.enabled = True
        self.credentials_api_config.enable_learner_issuance = True

    def test_update_certificate_available_date_credentials_config_disabled(self):
        """
        A test that verifies we do not queue any subtasks to update a certificate available date if use of the
        Credentials is disabled in config.
        """
        course_overview = self._create_course_overview(
            False,
            CertificatesDisplayBehaviors.EARLY_NO_INFO,
            None,
            self.end_date,
        )

        with pytest.raises(MaxRetriesExceededError):
            # pylint: disable=no-value-for-parameter
            tasks.update_certificate_available_date_on_course_update(course_overview.id)

    @mock.patch(f"{TASKS_MODULE}.update_credentials_course_certificate_configuration_available_date.delay")
    def test_update_certificate_available_date_instructor_paced_cdb_early_no_info(self, mock_update):
        """
        This test checks that we enqueue an `update_credentials_course_certificate_configuration_available_date` celery
        task with values we would expect.

        In this scenario, we have a course overview that...
            - is instructor-paced
            - has a certificates display behavior of "EARLY NO INFO" (certificates are visible immediately after
              generation)
            - has no certificate available date

        We expect that the task enqueued has a certificate available date of `None`, as the certificates should have no
        visibility restrictions.
        """
        self._update_credentials_api_config(True)

        course_overview = self._create_course_overview(
            False,
            CertificatesDisplayBehaviors.EARLY_NO_INFO,
            None,
            self.end_date,
        )

        # pylint: disable=no-value-for-parameter
        tasks.update_certificate_available_date_on_course_update(course_overview.id)
        mock_update.assert_called_once_with(str(course_overview.id), None)

    @mock.patch(f"{TASKS_MODULE}.update_credentials_course_certificate_configuration_available_date.delay")
    def test_update_certificate_available_date_instructor_paced_cdb_end(self, mock_update):
        """
        This test checks that we enqueue an `update_credentials_course_certificate_configuration_available_date` celery
        task with values we would expect.

        In this scenario, we have a course overview that...
            - is instructor-paced
            - has a certificates display behavior of "END" ("End of the course")
            - has no certificate available date

        We expect that the task enqueued has a certificate available date that matches the end date of the course.
        """
        self._update_credentials_api_config(True)

        course_overview = self._create_course_overview(
            False,
            CertificatesDisplayBehaviors.END,
            None,
            self.end_date,
        )

        # pylint: disable=no-value-for-parameter
        tasks.update_certificate_available_date_on_course_update(course_overview.id)
        mock_update.assert_called_once_with(str(course_overview.id), str(self.end_date))

    @mock.patch(f"{TASKS_MODULE}.update_credentials_course_certificate_configuration_available_date.delay")
    def test_update_certificate_available_date_instructor_paced_cdb_end_with_date(self, mock_update):
        """
        This test checks that we enqueue an `update_credentials_course_certificate_configuration_available_date` celery
        task with values we would expect.

        In this scenario, we have a course overview that...
            - is instructor-paced
            - has a certificates display behavior of "END WITH DATE" ("A date after the course ends")
            - has an end date set to 90 days from today
            - has a certificate available date set to 120 days from today

        We expect that the task enqueued has a certificate available date that matches the certificate available date
        explicitly set as part of the course overview.
        """
        self._update_credentials_api_config(True)
        certificate_available_date = datetime.now(ZoneInfo("UTC")) + timedelta(days=120)

        course_overview = self._create_course_overview(
            False,
            CertificatesDisplayBehaviors.END_WITH_DATE,
            certificate_available_date,
            self.end_date,
        )

        # pylint: disable=no-value-for-parameter
        tasks.update_certificate_available_date_on_course_update(course_overview.id)
        mock_update.assert_called_once_with(str(course_overview.id), str(certificate_available_date))

    @mock.patch(f"{TASKS_MODULE}.update_credentials_course_certificate_configuration_available_date.delay")
    def test_update_certificate_available_date_self_paced(self, mock_update):
        """
        This test checks that we enqueue an `update_credentials_course_certificate_configuration_available_date` celery
        task with values we would expect.

        In this scenario, we have a course overview that...
            - is self-paced
            - has a certificates display behavior of "END WITH DATE" ("A date after the course ends")
            - has an end date set to 90 days from today
            - has a cerificate available date set 120 days from today

        We expect that the task enqueued has a certificate available date that matches the certificate available date
        explicitly set as part of the course overview.

        This test case also verifies a change in recent behavior. There is a product defect that allows a self-paced
        course to sometimes pass a certificate available date to Credentials. This test case also verifies that, if
        invalid data is set in a course overview, we don't pass it to Credentials.
        """
        self._update_credentials_api_config(True)
        certificate_available_date = datetime.now(ZoneInfo("UTC")) + timedelta(days=120)

        course_overview = self._create_course_overview(
            True,
            None,
            certificate_available_date,
            self.end_date,
        )

        # pylint: disable=no-value-for-parameter
        tasks.update_certificate_available_date_on_course_update(course_overview.id)
        mock_update.assert_called_once_with(str(course_overview.id), None)

    def test_update_certificate_available_date_no_course_overview(self):
        """
        A test case that verifies some logging if the
        `update_credentials_course_certificate_configuration_available_date` task is queued with an invalid course run
        id.
        """
        bad_course_run_key = "course-v1:OpenEdx+MtG101x+1T2024"
        expected_message = (
            f"Unable to send the updated certificate available date of course run [{bad_course_run_key}] to "
            "Credentials. A course overview for this course run could not be found"
        )

        self._update_credentials_api_config(True)

        with LogCapture(level=logging.WARNING) as log_capture:
            # pylint: disable=no-value-for-parameter
            tasks.update_certificate_available_date_on_course_update(bad_course_run_key)

        log_capture.check_present(
            ("openedx.core.djangoapps.programs.tasks", "WARNING", expected_message),
        )
