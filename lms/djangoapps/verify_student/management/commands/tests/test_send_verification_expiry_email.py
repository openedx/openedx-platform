"""
Tests for django admin command `send_verification_expiry_email` in the verify_student module
"""


from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.sites.models import Site
from django.core import mail
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.test.utils import override_settings
from django.utils.timezone import now
from testfixtures import LogCapture

from common.djangoapps.student.tests.factories import UserFactory
from common.test.utils import MockS3Boto3Mixin
from lms.djangoapps.verify_student.models import ManualVerification, SoftwareSecurePhotoVerification, SSOVerification
from lms.djangoapps.verify_student.tests.test_models import FAKE_SETTINGS, mock_software_secure_post

LOGGER_NAME = 'lms.djangoapps.verify_student.management.commands.send_verification_expiry_email'


@patch.dict(settings.VERIFY_STUDENT, FAKE_SETTINGS)
@patch('lms.djangoapps.verify_student.models.requests.post', new=mock_software_secure_post)
class TestSendVerificationExpiryEmail(MockS3Boto3Mixin, TestCase):
    """ Tests for django admin command `send_verification_expiry_email` in the verify_student module """

    def setUp(self):
        """ Initial set up for tests """
        super().setUp()
        Site.objects.create(domain='edx.org', name='edx.org')
        self.resend_days = settings.VERIFICATION_EXPIRY_EMAIL['RESEND_DAYS']
        self.days = settings.VERIFICATION_EXPIRY_EMAIL['DAYS_RANGE']
        self.default_no_of_emails = settings.VERIFICATION_EXPIRY_EMAIL['DEFAULT_EMAILS']

    def create_and_submit(self, user):
        """ Helper method that lets us create new SoftwareSecurePhotoVerifications """
        attempt = SoftwareSecurePhotoVerification(user=user)
        attempt.upload_face_image("Fake Data")
        attempt.upload_photo_id_image("More Fake Data")
        attempt.mark_ready()
        attempt.submit()
        return attempt

    def create_expired_software_secure_photo_verification(self):
        """
        Helper method that creates an expired ssp verification
        """
        user = UserFactory.create()
        verification = self.create_and_submit(user)
        verification.status = 'approved'
        verification.expiration_date = now() - timedelta(days=self.days)
        verification.save()
        return verification

    def test_expiration_date_range(self):
        """
        Test that the verifications are filtered on the given range. Email is not sent for any verification with
        expiry date out of range
        """
        user = UserFactory.create()
        verification_in_range = self.create_and_submit(user)
        verification_in_range.status = 'approved'
        verification_in_range.expiration_date = now() - timedelta(days=self.days)
        verification_in_range.save()

        user = UserFactory.create()
        verification = self.create_and_submit(user)
        verification.status = 'approved'
        verification.expiration_date = now() - timedelta(days=self.days + 1)
        verification.save()

        call_command('send_verification_expiry_email')

        # Check that only one email is sent
        assert len(mail.outbox) == 1

        # Verify that the email is not sent to the out of range verification
        expiry_email_date = SoftwareSecurePhotoVerification.objects.get(pk=verification.pk).expiry_email_date
        assert expiry_email_date is None

    def test_expiry_email_date_range(self):
        """
        Test that the verifications are filtered if the expiry_email_date has reached the time specified for
        resending email
        """
        user = UserFactory.create()
        today = now().replace(hour=0, minute=0, second=0, microsecond=0)
        verification_in_range = self.create_and_submit(user)
        verification_in_range.status = 'approved'
        verification_in_range.expiration_date = today - timedelta(days=self.days + 1)
        verification_in_range.expiry_email_date = today - timedelta(days=self.resend_days)
        verification_in_range.save()

        call_command('send_verification_expiry_email')

        # Check that email is sent even if the verification is not in expiration_date range but matches
        # the criteria to resend email
        assert len(mail.outbox) == 1

    def test_most_recent_verification(self):
        """
        Test that the SoftwareSecurePhotoVerification object is not filtered if it is outdated. A verification is
        outdated if its expiry_email_date is set NULL
        """
        # For outdated verification the expiry_email_date is set NULL verify_student/views.py:1164
        user = UserFactory.create()
        outdated_verification = self.create_and_submit(user)
        outdated_verification.status = 'approved'
        outdated_verification.save()

        call_command('send_verification_expiry_email')

        # Check that the expiry_email_date is not set for the outdated verification
        expiry_email_date = SoftwareSecurePhotoVerification.objects.get(pk=outdated_verification.pk).expiry_email_date
        assert expiry_email_date is None

    def test_send_verification_expiry_email(self):
        """
        Test that checks for valid criteria the email is sent and expiry_email_date is set
        """
        user = UserFactory.create()
        verification = self.create_and_submit(user)
        verification.status = 'approved'
        verification.expiration_date = now() - timedelta(days=self.days)
        verification.save()

        call_command('send_verification_expiry_email')

        expected_date = now()
        attempt = SoftwareSecurePhotoVerification.objects.get(user_id=verification.user_id)
        assert attempt.expiry_email_date.date() == expected_date.date()
        assert len(mail.outbox) == 1

    def test_verification_expiry_email_not_sent_valid_ssov(self):
        """
        Test that user has an expired software secure verification but a valid sso verification
        so an email is not sent to the user
        """
        expired_ssp_verification = self.create_expired_software_secure_photo_verification()

        SSOVerification.objects.create(user=expired_ssp_verification.user, status='approved')

        call_command('send_verification_expiry_email')
        assert len(mail.outbox) == 0

    def test_verification_expiry_email_not_sent_valid_manual_verification(self):
        """
        Test that user has an expired software secure verification but a valid manual verification
        so an email is not sent to the user
        """
        expired_ssp_verification = self.create_expired_software_secure_photo_verification()

        ManualVerification.objects.create(user=expired_ssp_verification.user, status='approved')

        call_command('send_verification_expiry_email')
        assert len(mail.outbox) == 0

    def test_email_already_sent(self):
        """
        Test that if email is already sent as indicated by expiry_email_date then don't send again if it has been less
        than resend_days
        """
        user = UserFactory.create()
        verification = self.create_and_submit(user)
        verification.status = 'approved'
        verification.expiration_date = now() - timedelta(days=self.days)
        verification.expiry_email_date = now()
        verification.save()

        call_command('send_verification_expiry_email')

        assert len(mail.outbox) == 0

    def test_no_verification_found(self):
        """
        Test that if no approved and expired verifications are found the management command terminates gracefully
        """
        start_date = now() - timedelta(days=self.days)  # using default days
        with LogCapture(LOGGER_NAME) as logger:
            call_command('send_verification_expiry_email')
            logger.check(
                (LOGGER_NAME,
                 'INFO', "No approved expired entries found in SoftwareSecurePhotoVerification for the "
                         "date range {} - {}".format(start_date.date(), now().date()))
            )

    def test_dry_run_flag(self):
        """
        Test that the dry run flags sends no email and only logs the the number of email sent in each batch
        """
        user = UserFactory.create()
        verification = self.create_and_submit(user)
        verification.status = 'approved'
        verification.expiration_date = now() - timedelta(days=self.days)
        verification.save()

        start_date = now() - timedelta(days=self.days)  # using default days
        count = 1

        with LogCapture(LOGGER_NAME) as logger:
            call_command('send_verification_expiry_email', '--dry-run')
            logger.check(
                (LOGGER_NAME,
                 'INFO',
                 "For the date range {} - {}, total Software Secure Photo verification filtered are {}"
                 .format(start_date.date(), now().date(), count)
                 ),
                (LOGGER_NAME,
                 'INFO',
                 "This was a dry run, no email was sent. For the actual run email would have been sent "
                 "to {} learner(s)".format(count)
                 ))
        assert len(mail.outbox) == 0

    def test_not_enrolled_in_verified_course(self):
        """
        Test that if the user is not enrolled in verified track, then after sending the default no of
        emails, `expiry_email_date` is updated to None so that it's not filtered in the future for
        sending emails
        """
        user = UserFactory.create()
        today = now().replace(hour=0, minute=0, second=0, microsecond=0)
        verification = self.create_and_submit(user)
        verification.status = 'approved'
        verification.expiration_date = now() - timedelta(days=self.resend_days * (self.default_no_of_emails - 1))
        verification.expiry_email_date = today - timedelta(days=self.resend_days)
        verification.save()

        call_command('send_verification_expiry_email')

        # check that after sending the default number of emails, the expiry_email_date is set to none for a
        # user who is not enrolled in verified track
        attempt = SoftwareSecurePhotoVerification.objects.get(pk=verification.id)
        assert len(mail.outbox) == 1
        assert attempt.expiry_email_date is None

    def test_number_of_emails_sent(self):
        """
        Tests that the number of emails sent in case the user is only enrolled in audit track are same
        as DEFAULT_EMAILS set in the settings
        """
        user = UserFactory.create()
        verification = self.create_and_submit(user)
        verification.status = 'approved'

        verification.expiration_date = now() - timedelta(days=1)
        verification.save()
        call_command('send_verification_expiry_email')

        # running the loop one extra time to verify that after sending DEFAULT_EMAILS no extra emails are sent and
        # for this reason expiry_email_date is set to None
        for i in range(1, self.default_no_of_emails + 1):
            if SoftwareSecurePhotoVerification.objects.get(pk=verification.id).expiry_email_date:
                today = now().replace(hour=0, minute=0, second=0, microsecond=0)
                verification.expiration_date = today - timedelta(days=self.resend_days * i + 1)
                verification.expiry_email_date = today - timedelta(days=self.resend_days)
                verification.save()
                call_command('send_verification_expiry_email')
            else:
                break

        # expiry_email_date set to None means it no longer will be filtered hence no emails will be sent in future
        assert SoftwareSecurePhotoVerification.objects.get(pk=verification.id).expiry_email_date is None
        assert len(mail.outbox) == self.default_no_of_emails

    @override_settings(VERIFICATION_EXPIRY_EMAIL={'RESEND_DAYS': 15, 'DAYS_RANGE': 1, 'DEFAULT_EMAILS': 0})
    def test_command_error(self):
        err_string = "DEFAULT_EMAILS must be a positive integer. If you do not wish to send " \
                     "emails use --dry-run flag instead."
        with self.assertRaisesRegex(CommandError, err_string):
            call_command('send_verification_expiry_email')

    def test_one_failed_but_others_succeeded(self):
        """
        Test that if the first verification fails to send, the rest still do.
        """
        verifications = []
        for _i in range(2):
            user = UserFactory.create()
            verification = self.create_and_submit(user)
            verification.status = 'approved'
            verification.expiration_date = now() - timedelta(days=self.days)
            verification.save()
            verifications.append(verification)

        with patch('lms.djangoapps.verify_student.management.commands.send_verification_expiry_email.ace') as mock_ace:
            mock_ace.send.side_effect = (Exception('Aw shucks'), None)
            with self.assertRaisesRegex(CommandError, 'One or more email attempts failed.*'):
                with LogCapture(LOGGER_NAME) as logger:
                    call_command('send_verification_expiry_email')

        logger.check_present(
            (LOGGER_NAME, 'ERROR', f'Could not send email for verification id {verifications[0].id}'),
        )

        for verification in verifications:
            verification.refresh_from_db()
        assert verifications[0].expiry_email_date is None
        assert verifications[1].expiry_email_date is not None
        assert mock_ace.send.call_count == 2

    def _run_cmd_and_get_verification_link(self, get_value_side_effect, *, fallback=None):
        """
        Create one expired, approved verification; run the command with patches;
        return the lms_verification_link computed in the message context.
        """
        self.create_expired_software_secure_photo_verification()

        captured_contexts = []

        class FakeVerificationExpiry:
            """Mock message class capturing context for verification expiry tests."""
            def __init__(self, context):
                captured_contexts.append(context)

            def personalize(self, recipient, language=None, user_context=None):
                return object()

        settings_ctx = (
            override_settings(ACCOUNT_MICROFRONTEND_URL=fallback)
            if fallback is not None else
            nullcontext()
        )

        module = (
            "lms.djangoapps.verify_student.management.commands."
            "send_verification_expiry_email"
        )

        with (
            settings_ctx,
            patch(f"{module}.VerificationExpiry", new=FakeVerificationExpiry),
            patch(f"{module}.ace.send") as mock_send,
            patch(
                f"{module}.configuration_helpers.get_value",
                side_effect=get_value_side_effect,
            ),
        ):
            call_command("send_verification_expiry_email")
            assert mock_send.called, "Expected at least one email to be sent"
            assert captured_contexts, ("VerificationExpiry(context=...) should have been called")

        return captured_contexts[0]["lms_verification_link"]

    def test_account_mfe_link_uses_site_config_value(self):
        """Test verification link uses site-configured ACCOUNT_MICROFRONTEND_URL."""
        siteconf_url = "https://accounts.siteconf.example/"

        link = self._run_cmd_and_get_verification_link(
            get_value_side_effect=lambda key, default=None, *a, **k:
                siteconf_url if key == "ACCOUNT_MICROFRONTEND_URL" else default
        )

        assert link == "https://accounts.siteconf.example/id-verification"

    def test_account_mfe_link_falls_back_to_settings(self):
        """Test verification link falls back to settings when site config is absent."""
        fallback = "https://accounts.settings.example/"

        link = self._run_cmd_and_get_verification_link(
            get_value_side_effect=lambda key, default=None, *a, **k: default,
            fallback=fallback,
        )

        assert link == "https://accounts.settings.example/id-verification"
