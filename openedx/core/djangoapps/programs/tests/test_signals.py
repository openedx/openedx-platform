"""
This module contains tests for programs-related signals and signal handlers.
"""

import datetime
from unittest import mock

from django.test import TestCase
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.programs.signals import (
    handle_course_cert_awarded,
    handle_course_cert_changed,
    handle_course_cert_date_change,
    handle_course_cert_revoked,
    handle_course_pacing_change,
)
from openedx.core.djangoapps.signals.signals import (
    COURSE_CERT_AWARDED,
    COURSE_CERT_CHANGED,
    COURSE_CERT_DATE_CHANGE,
    COURSE_CERT_REVOKED,
)
from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms

TEST_USERNAME = "test-user"
TEST_COURSE_KEY = CourseKey.from_string("course-v1:edX+test_course+1")


# The credentials app isn't installed for the CMS.
@skip_unless_lms
@mock.patch("openedx.core.djangoapps.programs.tasks.award_program_certificates.delay")
@mock.patch(
    "openedx.core.djangoapps.credentials.models.CredentialsApiConfig.is_learner_issuance_enabled",
    new_callable=mock.PropertyMock,
    return_value=False,
)
class CertAwardedReceiverTest(TestCase):
    """
    Tests for the `handle_course_cert_awarded` signal handler function.
    """

    @property
    def signal_kwargs(self):
        """
        DRY helper.
        """
        return dict(
            sender=self.__class__,
            user=UserFactory.create(username=TEST_USERNAME),
            course_key=TEST_COURSE_KEY,
            mode="test-mode",
            status="test-status",
        )

    def test_signal_received(self, mock_is_learner_issuance_enabled, mock_task):  # pylint: disable=unused-argument
        """
        Ensures the receiver function is invoked when COURSE_CERT_AWARDED is
        sent.

        Suboptimal: because we cannot mock the receiver function itself (due
        to the way django signals work), we mock a configuration call that is
        known to take place inside the function.
        """
        COURSE_CERT_AWARDED.send(**self.signal_kwargs)
        assert mock_is_learner_issuance_enabled.call_count == 1

    def test_programs_disabled(self, mock_is_learner_issuance_enabled, mock_task):
        """
        Ensures that the receiver function does nothing when the credentials API
        configuration is not enabled.
        """
        handle_course_cert_awarded(**self.signal_kwargs)
        assert mock_is_learner_issuance_enabled.call_count == 1
        assert mock_task.call_count == 0

    def test_programs_enabled(self, mock_is_learner_issuance_enabled, mock_task):
        """
        Ensures that the receiver function invokes the expected celery task
        when the credentials API configuration is enabled.
        """
        mock_is_learner_issuance_enabled.return_value = True

        handle_course_cert_awarded(**self.signal_kwargs)

        assert mock_is_learner_issuance_enabled.call_count == 1
        assert mock_task.call_count == 1
        assert mock_task.call_args[0] == (TEST_USERNAME,)


# The credentials app isn't installed for the CMS.
@skip_unless_lms
@mock.patch("openedx.core.djangoapps.programs.tasks.award_course_certificate.delay")
@mock.patch(
    "openedx.core.djangoapps.credentials.models.CredentialsApiConfig.is_learner_issuance_enabled",
    new_callable=mock.PropertyMock,
    return_value=False,
)
class CertChangedReceiverTest(TestCase):
    """
    Tests for the `handle_course_cert_changed` signal handler function.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(username=TEST_USERNAME)

    @property
    def signal_kwargs(self):
        """
        DRY helper.
        """
        return dict(
            sender=self.__class__,
            user=self.user,
            course_key=TEST_COURSE_KEY,
            mode="test-mode",
            status="test-status",
        )

    def test_signal_received(self, mock_is_learner_issuance_enabled, mock_task):  # pylint: disable=unused-argument
        """
        Ensures the receiver function is invoked when COURSE_CERT_CHANGED is
        sent.

        Suboptimal: because we cannot mock the receiver function itself (due
        to the way django signals work), we mock a configuration call that is
        known to take place inside the function.
        """
        COURSE_CERT_CHANGED.send(**self.signal_kwargs)
        assert mock_is_learner_issuance_enabled.call_count == 2

    def test_credentials_disabled(self, mock_is_learner_issuance_enabled, mock_task):
        """
        Ensures that the receiver function does nothing when the credentials API
        configuration is not enabled.
        """
        handle_course_cert_changed(**self.signal_kwargs)
        assert mock_is_learner_issuance_enabled.call_count == 1
        assert mock_task.call_count == 0

    def test_credentials_enabled(self, mock_is_learner_issuance_enabled, mock_task):
        """
        Ensures that the receiver function invokes the expected celery task
        when the credentials API configuration is enabled.
        """
        mock_is_learner_issuance_enabled.return_value = True

        handle_course_cert_changed(**self.signal_kwargs)

        assert mock_is_learner_issuance_enabled.call_count == 1
        assert mock_task.call_count == 1
        assert mock_task.call_args[0] == (TEST_USERNAME, str(TEST_COURSE_KEY))

    def test_records_enabled(self, mock_is_learner_issuance_enabled, mock_task):
        mock_is_learner_issuance_enabled.return_value = True

        site_config = SiteConfigurationFactory.create(site_values={"course_org_filter": ["edX"]})

        # Correctly sent
        handle_course_cert_changed(**self.signal_kwargs)
        assert mock_task.called
        mock_task.reset_mock()

        # Correctly not sent
        site_config.site_values["ENABLE_LEARNER_RECORDS"] = False
        site_config.save()
        handle_course_cert_changed(**self.signal_kwargs)
        assert not mock_task.called


# The credentials app isn't installed for the CMS.
@skip_unless_lms
@mock.patch("openedx.core.djangoapps.programs.tasks.revoke_program_certificates.delay")
@mock.patch(
    "openedx.core.djangoapps.credentials.models.CredentialsApiConfig.is_learner_issuance_enabled",
    new_callable=mock.PropertyMock,
    return_value=False,
)
class CertRevokedReceiverTest(TestCase):
    """
    Tests for the `handle_course_cert_revoked` signal handler function.
    """

    @property
    def signal_kwargs(self):
        """
        DRY helper.
        """
        return dict(
            sender=self.__class__,
            user=UserFactory.create(username=TEST_USERNAME),
            course_key=TEST_COURSE_KEY,
            mode="test-mode",
            status="test-status",
        )

    def test_signal_received(self, mock_is_learner_issuance_enabled, mock_task):  # pylint: disable=unused-argument
        """
        Ensures the receiver function is invoked when COURSE_CERT_REVOKED is
        sent.

        Suboptimal: because we cannot mock the receiver function itself (due
        to the way django signals work), we mock a configuration call that is
        known to take place inside the function.
        """
        COURSE_CERT_REVOKED.send(**self.signal_kwargs)
        assert mock_is_learner_issuance_enabled.call_count == 1

    def test_programs_disabled(self, mock_is_learner_issuance_enabled, mock_task):
        """
        Ensures that the receiver function does nothing when the credentials API
        configuration is not enabled.
        """
        handle_course_cert_revoked(**self.signal_kwargs)
        assert mock_is_learner_issuance_enabled.call_count == 1
        assert mock_task.call_count == 0

    def test_programs_enabled(self, mock_is_learner_issuance_enabled, mock_task):
        """
        Ensures that the receiver function invokes the expected celery task
        when the credentials API configuration is enabled.
        """
        mock_is_learner_issuance_enabled.return_value = True

        handle_course_cert_revoked(**self.signal_kwargs)

        assert mock_is_learner_issuance_enabled.call_count == 1
        assert mock_task.call_count == 1
        assert mock_task.call_args[0] == (TEST_USERNAME, str(TEST_COURSE_KEY))


@skip_unless_lms
@mock.patch("openedx.core.djangoapps.programs.tasks.update_certificate_available_date_on_course_update.delay")
@mock.patch(
    "openedx.core.djangoapps.credentials.models.CredentialsApiConfig.is_learner_issuance_enabled",
    new_callable=mock.PropertyMock,
    return_value=False,
)
class CourseCertAvailableDateChangedReceiverTest(TestCase):
    """
    Tests for the `handle_course_cert_date_change` signal handler function.
    """

    @property
    def signal_kwargs(self):
        """
        DRY helper.
        """
        return {"sender": self.__class__, "course_key": TEST_COURSE_KEY, "available_date": datetime.datetime.now()}

    def test_signal_received(self, mock_is_learner_issuance_enabled, mock_cad_task):
        """
        Ensures the receiver function is invoked when COURSE_CERT_DATE_CHANGE is sent.
        """
        COURSE_CERT_DATE_CHANGE.send(**self.signal_kwargs)
        assert mock_is_learner_issuance_enabled.call_count == 1
        assert mock_cad_task.call_count == 0

    def test_programs_disabled(self, mock_is_learner_issuance_enabled, mock_cad_task):
        """
        Ensures that the receiver function does not queue any celery tasks if the system is not configured to use the
        Credentials service.
        """
        handle_course_cert_date_change(**self.signal_kwargs)
        assert mock_is_learner_issuance_enabled.call_count == 1
        assert mock_cad_task.call_count == 0

    def test_programs_enabled(self, mock_is_learner_issuance_enabled, mock_cad_task):
        """
        Ensures that the receiver function enqueues the expected celery tasks when the system is configured to use the
        Credentials IDA.
        """
        mock_is_learner_issuance_enabled.return_value = True

        handle_course_cert_date_change(**self.signal_kwargs)
        assert mock_is_learner_issuance_enabled.call_count == 1
        assert mock_cad_task.call_count == 1


@skip_unless_lms
@mock.patch("openedx.core.djangoapps.programs.tasks.update_certificate_available_date_on_course_update.delay")
@mock.patch("openedx.core.djangoapps.programs.signals.is_credentials_enabled")
class CoursePacingChangedReceiverTest(TestCase):
    """
    Tests for the `handle_course_pacing_change` signal handler function.
    """

    def setUp(self):
        super().setUp()
        self.course_overview = CourseOverviewFactory.create(
            self_paced=False,
        )

    @property
    def signal_kwargs(self):
        """
        DRY helper.
        """
        return {
            "sender": self.__class__,
            "updated_course_overview": self.course_overview,
        }

    def test_handle_course_pacing_change_credentials_disabled(
        self,
        mock_is_creds_enabled,
        mock_cad_task,
    ):
        """
        Test the verifies the behavior of the `handle_course_pacing_change` signal receiver when use of the Credentials
        IDA is disabled by configuration.
        """
        mock_is_creds_enabled.return_value = False

        handle_course_pacing_change(**self.signal_kwargs)
        assert mock_is_creds_enabled.call_count == 1
        assert mock_cad_task.call_count == 0

    def test_handle_course_pacing_change_credentials_enabled(
        self,
        mock_is_creds_enabled,
        mock_cad_task,
    ):
        """
        Test that verifies the behavior of the `handle_course_pacing_change` signal receiver when use of the Credentials
        IDA is enabled by configuration.
        """
        mock_is_creds_enabled.return_value = True
        self.course_overview.self_paced = True

        handle_course_pacing_change(**self.signal_kwargs)
        assert mock_is_creds_enabled.call_count == 1
        assert mock_cad_task.call_count == 1
