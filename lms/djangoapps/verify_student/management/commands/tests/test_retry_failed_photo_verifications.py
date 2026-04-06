"""
Tests for django admin commands in the verify_student module

Lots of imports from verify_student's model tests, since they cover similar ground
"""

from unittest.mock import ANY, call, patch

from django.conf import settings
from django.core.management import call_command
from django.test.utils import override_settings
from freezegun import freeze_time
from testfixtures import LogCapture

from common.test.utils import MockS3Boto3Mixin
from lms.djangoapps.verify_student.models import SoftwareSecurePhotoVerification, SSPVerificationRetryConfig
from lms.djangoapps.verify_student.tests import TestVerificationBase
from lms.djangoapps.verify_student.tests.test_models import (
    FAKE_SETTINGS,
    mock_software_secure_post,
    mock_software_secure_post_error,
)

LOGGER_NAME = 'retry_photo_verification'


# Lots of patching to stub in our own settings, and HTTP posting
@patch.dict(settings.VERIFY_STUDENT, FAKE_SETTINGS)
@patch('lms.djangoapps.verify_student.models.requests.post', new=mock_software_secure_post)
class TestRetryFailedPhotoVerifications(MockS3Boto3Mixin, TestVerificationBase):
    """
    Tests for django admin commands in the verify_student module
    """

    def test_command(self):
        """
        Tests that the task used to find "must_retry" SoftwareSecurePhotoVerifications
        and re-submit them executes successfully
        """
        # set up some fake data to use...
        self.create_upload_and_submit_attempt_for_user()
        with patch('lms.djangoapps.verify_student.models.requests.post', new=mock_software_secure_post_error):
            self.create_upload_and_submit_attempt_for_user()
            self.create_upload_and_submit_attempt_for_user()

        # check to make sure we had two successes and two failures; otherwise we've got problems elsewhere
        assert SoftwareSecurePhotoVerification.objects.filter(status='submitted').count() == 1
        assert SoftwareSecurePhotoVerification.objects.filter(status='must_retry').count() == 2

        with self.immediate_on_commit():
            call_command('retry_failed_photo_verifications')
        attempts_to_retry = SoftwareSecurePhotoVerification.objects.filter(status='must_retry')
        assert not attempts_to_retry

    def add_test_config_for_retry_verification(self):
        """Setups verification retry configuration."""
        config = SSPVerificationRetryConfig.current()
        config.arguments = ('--verification-ids 1 2 3')
        config.enabled = True
        config.save()

    def test_args_from_database(self):
        """Test management command arguments injected from config model."""
        # Nothing in the database, should default to disabled
        with LogCapture(LOGGER_NAME) as log:
            call_command('retry_failed_photo_verifications', '--args-from-database')
            log.check_present(
                (
                    LOGGER_NAME, 'ERROR',
                    'SSPVerificationRetryConfig is disabled or empty, but --args-from-database was requested.'
                ),
            )
        self.add_test_config_for_retry_verification()
        with patch('lms.djangoapps.verify_student.models.requests.post', new=mock_software_secure_post_error):
            self.create_upload_and_submit_attempt_for_user()
            with LogCapture(LOGGER_NAME) as log:
                with self.immediate_on_commit():
                    call_command('retry_failed_photo_verifications')
                    log.check_present(
                        (
                            LOGGER_NAME, 'INFO',
                            f'Attempting to re-submit {1} failed SoftwareSecurePhotoVerification submissions; '
                            f'\nwith status: must_retry'
                        ),
                    )

        with LogCapture(LOGGER_NAME) as log:
            with self.immediate_on_commit():
                call_command('retry_failed_photo_verifications', '--args-from-database')

                log.check_present(
                    (
                        LOGGER_NAME, 'INFO',
                        f'Attempting to re-submit {0} failed SoftwareSecurePhotoVerification submissions; '
                        f'with retry verification ids from config model'
                    ),
                )


@override_settings(VERIFY_STUDENT=FAKE_SETTINGS)
@patch.dict(settings.FEATURES, {'AUTOMATIC_VERIFY_STUDENT_IDENTITY_FOR_TESTING': True})
class TestRetryFailedPhotoVerificationsBetweenDates(MockS3Boto3Mixin, TestVerificationBase):
    """
    Tests that the command selects specific objects within a date range
    """

    def setUp(self):
        super().setUp()
        # Test that the correct attempts within a date range are called
        with patch('lms.djangoapps.verify_student.models.requests.post'):
            with freeze_time("2023-02-28 23:59:59"):
                self._create_attempts(1)
            with freeze_time("2023-03-01 00:00:00"):
                self._create_attempts(4)
            with freeze_time("2023-03-28 23:59:59"):
                self._create_attempts(4)
            with freeze_time("2023-03-29 00:00:00"):
                self._create_attempts(1)

    def _create_attempts(self, num_attempts):
        for _ in range(num_attempts):
            self.create_upload_and_submit_attempt_for_user()

    @patch('lms.djangoapps.verify_student.signals.signals.idv_update_signal.send')
    def test_resubmit_in_date_range(self, send_idv_update_mock):
        call_command('retry_failed_photo_verifications',
                     status="submitted",
                     start_datetime="2023-03-01 00:00:00",
                     end_datetime="2023-03-28 23:59:59"
                     )

        expected_calls = [
            call(
                sender='idv_update', attempt_id=2, user_id=2, status='submitted',
                photo_id_name=ANY, full_name=ANY
            ),
            call(
                sender='idv_update', attempt_id=3, user_id=3, status='submitted',
                photo_id_name=ANY, full_name=ANY
            ),
            call(
                sender='idv_update', attempt_id=4, user_id=4, status='submitted',
                photo_id_name=ANY, full_name=ANY
            ),
            call(
                sender='idv_update', attempt_id=5, user_id=5, status='submitted',
                photo_id_name=ANY, full_name=ANY
            ),
            call(
                sender='idv_update', attempt_id=6, user_id=6, status='submitted',
                photo_id_name=ANY, full_name=ANY
            ),
            call(
                sender='idv_update', attempt_id=7, user_id=7, status='submitted',
                photo_id_name=ANY, full_name=ANY
            ),
            call(
                sender='idv_update', attempt_id=8, user_id=8, status='submitted',
                photo_id_name=ANY, full_name=ANY
            ),
            call(
                sender='idv_update', attempt_id=9, user_id=9, status='submitted',
                photo_id_name=ANY, full_name=ANY
            ),
        ]
        self.assertEqual(send_idv_update_mock.call_count, 8)
        send_idv_update_mock.assert_has_calls(expected_calls, any_order=True)
