"""
Tests for the Sending activation email celery tasks
"""


from unittest import mock  # pylint: disable=wrong-import-order

from django.conf import settings
from django.test import TestCase
from edx_ace.errors import ChannelError, RecoverableChannelDeliveryError

from common.djangoapps.student.models import Registration
from common.djangoapps.student.views.management import compose_activation_email, compose_and_send_activation_email
from lms.djangoapps.courseware.tests.factories import UserFactory
from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory, SiteFactory
from openedx.core.djangoapps.user_authn.tasks import send_activation_email


class SendActivationEmailTestCase(TestCase):
    """
    Test for send activation email to user
    """

    def setUp(self):
        """ Setup components used by each test."""
        super().setUp()
        self.student = UserFactory()

        registration = Registration()
        registration.register(self.student)

        self.msg = compose_activation_email(self.student, registration)

    def test_ComposeEmail(self):
        """
        Tests that attributes of the message are being filled correctly in compose_activation_email
        """
        # Check that variables used by the base template are present in generated context
        assert 'platform_name' in self.msg.context
        assert 'contact_mailing_address' in self.msg.context
        # Verify the presence of the activation-email specific attributes
        assert self.msg.recipient.lms_user_id == self.student.id
        assert self.msg.recipient.email_address == self.student.email
        assert self.msg.context['routed_user'] == self.student.username
        assert self.msg.context['routed_user_email'] == self.student.email
        assert self.msg.context['routed_profile_name'] == ''
        assert self.msg.context['registration_flow'] is False
        assert self.msg.context['is_enterprise_learner'] is False
        assert self.msg.context['is_first_purchase_discount_overridden'] is False

    @mock.patch('time.sleep', mock.Mock(return_value=None))
    @mock.patch('openedx.core.djangoapps.user_authn.tasks.log')
    @mock.patch('openedx.core.djangoapps.user_authn.tasks.ace.send', mock.Mock(side_effect=RecoverableChannelDeliveryError(None, None)))  # pylint: disable=line-too-long
    def test_RetrySendUntilFail(self, mock_log):
        """
        Tests retries when the activation email doesn't send
        """
        from_address = 'task_testing@example.com'
        email_max_attempts = settings.RETRY_ACTIVATION_EMAIL_MAX_ATTEMPTS

        send_activation_email.delay(str(self.msg), from_address=from_address)

        # Asserts sending email retry logging.
        for attempt in range(email_max_attempts):
            mock_log.info.assert_any_call(
                'Retrying sending email to user {dest_addr}, attempt # {attempt} of {max_attempts}'.format(  # noqa: UP032  # pylint: disable=line-too-long
                    dest_addr=self.student.email,
                    attempt=attempt,
                    max_attempts=email_max_attempts
                ))
        assert mock_log.info.call_count == 6

        # Asserts that the error was logged on crossing max retry attempts.
        mock_log.error.assert_called_with(
            'Unable to send activation email to user from "%s" to "%s"',
            from_address,
            self.student.email,
            exc_info=True
        )
        assert mock_log.error.call_count == 1

    @mock.patch('openedx.core.djangoapps.user_authn.tasks.log')
    @mock.patch('openedx.core.djangoapps.user_authn.tasks.ace.send', mock.Mock(side_effect=ChannelError))
    def test_UnrecoverableSendError(self, mock_log):
        """
        Tests that a major failure of the send is logged
        """
        from_address = 'task_testing@example.com'

        send_activation_email.delay(str(self.msg), from_address=from_address)

        # Asserts that the error was logged
        mock_log.exception.assert_called_with(
            'Unable to send activation email to user from "%s" to "%s"',
            from_address,
            self.student.email,
        )

        # Assert that nothing else was logged
        assert mock_log.info.call_count == 0
        assert mock_log.error.call_count == 0
        assert mock_log.exception.call_count == 1

    @mock.patch('openedx.core.djangoapps.user_authn.tasks.log')
    @mock.patch('openedx.core.djangoapps.user_authn.tasks.ace.send', mock.Mock(side_effect=ChannelError))
    @mock.patch('common.djangoapps.student.views.management.theming_helpers.get_current_site')
    @mock.patch('openedx.core.djangoapps.site_configuration.helpers.get_current_site_configuration')
    def test_from_address_in_send_email(self, mock_site_configuration, mock_get_current_site, mock_log):
        """
        Tests that the "from_address" is pulled from the site configuration.
        """
        site = SiteFactory.create()
        mock_get_current_site.return_value = site
        expected_from_email_address = 'test-no-reply@example.com'
        site_config = SiteConfigurationFactory.create(site=site, site_values={
            'ACTIVATION_EMAIL_FROM_ADDRESS': expected_from_email_address
        })
        mock_site_configuration.return_value = site_config

        compose_and_send_activation_email(self.student, self.student.profile, self.student.registration)
        mock_log.exception.assert_called_with(
            'Unable to send activation email to user from "%s" to "%s"',
            expected_from_email_address,
            self.student.email,
        )
