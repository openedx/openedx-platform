"""
Test cases for recover account management command
"""
import re
from tempfile import NamedTemporaryFile

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import RequestFactory, TestCase, override_settings
from testfixtures import LogCapture

from common.djangoapps.student.models import AccountRecoveryConfiguration
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms

LOGGER_NAME = 'common.djangoapps.student.management.commands.recover_account'
FEATURES_WITH_AUTHN_MFE_ENABLED = settings.FEATURES.copy()
FEATURES_WITH_AUTHN_MFE_ENABLED['ENABLE_AUTHN_MICROFRONTEND'] = True


class RecoverAccountTests(TestCase):
    """
    Test account recovery and exception handling
    """

    request_factory = RequestFactory()

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(username='amy', email='amy@edx.com', password='password')

    def _write_test_csv(self, csv, lines):
        """Write a test csv file with the lines provided"""
        csv.write(b"username,current_email,desired_email\n")
        for line in lines:
            csv.write(line.encode())
        csv.seek(0)
        return csv

    def test_account_recovery(self):
        """
        Test account is recovered. Send email to learner and then reset password. After
        reset password login to make sure account is recovered
        :return:
        """

        with NamedTemporaryFile() as csv:
            csv = self._write_test_csv(csv, lines=['amy,amy@edx.com,amy@newemail.com\n'])
            call_command("recover_account", f"--csv_file_path={csv.name}")

            assert len(mail.outbox) == 1

        reset_link = re.findall("(http.+pwreset)", mail.outbox[0].body)[0]
        request_params = {'new_password1': 'password1', 'new_password2': 'password1'}
        self.client.get(reset_link)
        resp = self.client.post(reset_link, data=request_params)

        # Verify the response status code is: 302 with password reset because 302 means success
        assert resp.status_code == 302

        assert self.client.login(username=self.user.username, password='password1')

        # try to login with previous password
        assert not self.client.login(username=self.user.username, password='password')

    @override_settings(FEATURES=FEATURES_WITH_AUTHN_MFE_ENABLED)
    @skip_unless_lms
    def test_authn_mfe_url_in_reset_link(self):
        """
        send password reset link to learner with authn mfe.
        :return:
        """

        with NamedTemporaryFile() as csv:
            csv = self._write_test_csv(csv, lines=['amy,amy@edx.com,amy@newemail.com\n'])
            call_command("recover_account", f"--csv_file_path={csv.name}")

            assert len(mail.outbox) == 1

        authn_mfe_url = re.findall(settings.AUTHN_MICROFRONTEND_URL, mail.outbox[0].body)[0]
        self.assertEqual(authn_mfe_url, settings.AUTHN_MICROFRONTEND_URL)

    def test_file_not_found_error(self):
        """
        Test command error raised when csv path is invalid
        :return:
        """
        with pytest.raises(CommandError):
            call_command("recover_account", "--csv_file_path={}".format('test'))

    def test_exception_raised(self):
        """
        Test user matching query does not exist exception raised
        :return:
        """
        with NamedTemporaryFile() as csv:
            csv = self._write_test_csv(csv, lines=['amm,amy@myedx.com,amy@newemail.com\n'])

            expected_message = 'Unable to send email to amy@newemail.com and ' \
                               'exception was User matching query does not exist.'

            with LogCapture(LOGGER_NAME) as log:
                call_command("recover_account", f"--csv_file_path={csv.name}")

                log.check_present(
                    (LOGGER_NAME, 'ERROR', expected_message)
                )

    def test_successfull_users_logged(self):
        """
        Test accumulative logs for all successfull and failed learners.
        """
        with NamedTemporaryFile() as csv:
            csv = self._write_test_csv(csv, lines=['amy,amy@edx.com,amy@newemail.com\n'])

            expected_message = "Successfully updated ['amy@newemail.com'] accounts. Failed to update [] accounts"

            with LogCapture(LOGGER_NAME) as log:
                call_command("recover_account", f"--csv_file_path={csv.name}")

                log.check_present(
                    (LOGGER_NAME, 'INFO', expected_message)

                )

    def test_account_recovery_from_config_model(self):
        """Verify learners account recovery using config model."""
        lines = 'username,current_email,desired_email\namy,amy@edx.com,amy@newemail.com\n'

        csv_file = SimpleUploadedFile(name='test.csv', content=lines.encode('utf-8'), content_type='text/csv')
        AccountRecoveryConfiguration.objects.create(enabled=True, csv_file=csv_file)

        call_command("recover_account")

        email = get_user_model().objects.get(pk=self.user.pk).email
        assert email == 'amy@newemail.com'
