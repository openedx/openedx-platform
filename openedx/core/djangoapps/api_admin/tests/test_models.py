# pylint: disable=missing-docstring


from smtplib import SMTPException
from unittest import mock

import ddt
import pytest
from django.db import IntegrityError
from django.test import TestCase

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.api_admin.models import ApiAccessConfig, ApiAccessRequest
from openedx.core.djangoapps.api_admin.models import log as model_log
from openedx.core.djangoapps.api_admin.tests.factories import ApiAccessRequestFactory
from openedx.core.djangoapps.site_configuration.tests.factories import SiteFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms


@ddt.ddt
@skip_unless_lms
class ApiAccessRequestTests(TestCase):

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.request = ApiAccessRequestFactory(user=self.user)

    def test_default_status(self):
        assert self.request.status == ApiAccessRequest.PENDING
        assert not ApiAccessRequest.has_api_access(self.user)

    def test_approve(self):
        self.request.approve()
        assert self.request.status == ApiAccessRequest.APPROVED

    def test_deny(self):
        self.request.deny()
        assert self.request.status == ApiAccessRequest.DENIED

    def test_nonexistent_request(self):
        """Test that users who have not requested API access do not get it."""
        other_user = UserFactory()
        assert not ApiAccessRequest.has_api_access(other_user)

    @ddt.data(
        (ApiAccessRequest.PENDING, False),
        (ApiAccessRequest.DENIED, False),
        (ApiAccessRequest.APPROVED, True),
    )
    @ddt.unpack
    def test_has_access(self, status, should_have_access):
        self.request.status = status
        self.request.save()
        assert ApiAccessRequest.has_api_access(self.user) == should_have_access

    def test_unique_per_user(self):
        with pytest.raises(IntegrityError):
            ApiAccessRequestFactory(user=self.user)

    def test_no_access(self):
        self.request.delete()
        assert ApiAccessRequest.api_access_status(self.user) is None

    def test_unicode(self):
        request_unicode = str(self.request)
        assert self.request.website in request_unicode
        assert self.request.status in request_unicode

    def test_retire_user_success(self):
        retire_result = self.request.retire_user(self.user)
        assert retire_result
        assert self.request.company_address == ''
        assert self.request.company_name == ''
        assert self.request.website == ''
        assert self.request.reason == ''

    def test_retire_user_do_not_exist(self):
        user2 = UserFactory()
        retire_result = self.request.retire_user(user2)
        assert not retire_result


class ApiAccessConfigTests(TestCase):

    def test_unicode(self):
        assert str(ApiAccessConfig(enabled=True)) == 'ApiAccessConfig [enabled=True]'
        assert str(ApiAccessConfig(enabled=False)) == 'ApiAccessConfig [enabled=False]'


@skip_unless_lms
class ApiAccessRequestSignalTests(TestCase):
    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.api_access_request = ApiAccessRequest(user=self.user, site=SiteFactory())
        self.send_new_pending_email_function = 'openedx.core.djangoapps.api_admin.models._send_new_pending_email'
        self.send_decision_email_function = 'openedx.core.djangoapps.api_admin.models._send_decision_email'

    def test_save_signal_success_new_email(self):
        """ Verify that initial save sends new email and no decision email. """
        with mock.patch(self.send_new_pending_email_function) as mock_new_email:
            with mock.patch(self.send_decision_email_function) as mock_decision_email:
                self.api_access_request.save()

        mock_new_email.assert_called_once_with(self.api_access_request)
        assert not mock_decision_email.called

    def test_save_signal_success_decision_email(self):
        """ Verify that updating request status sends decision email and no new email. """
        self.api_access_request.save()

        with mock.patch(self.send_new_pending_email_function) as mock_new_email:
            with mock.patch(self.send_decision_email_function) as mock_decision_email:
                self.api_access_request.approve()

        mock_decision_email.assert_called_once_with(self.api_access_request)
        assert not mock_new_email.called

    def test_save_signal_success_no_emails(self):
        """ Verify that updating request status again sends no emails. """
        self.api_access_request.save()
        self.api_access_request.approve()

        with mock.patch(self.send_new_pending_email_function) as mock_new_email:
            with mock.patch(self.send_decision_email_function) as mock_decision_email:
                self.api_access_request.deny()

        assert not mock_decision_email.called
        assert not mock_new_email.called

    def test_save_signal_failure_email(self):
        """ Verify that saving still functions even on email errors. """
        assert self.api_access_request.id is None

        mail_function = 'openedx.core.djangoapps.api_admin.models.send_mail'
        with mock.patch(mail_function, side_effect=SMTPException):
            with mock.patch.object(model_log, 'exception') as mock_model_log_exception:
                self.api_access_request.save()

        # Verify that initial save logs email errors properly
        mock_model_log_exception.assert_called_once_with(
            'Error sending API user notification email for request [%s].', self.api_access_request.id
        )
        # Verify object saved
        assert self.api_access_request.id is not None

        with mock.patch(mail_function, side_effect=SMTPException):
            with mock.patch.object(model_log, 'exception') as mock_model_log_exception:
                self.api_access_request.approve()
        # Verify that updating request status logs email errors properly
        mock_model_log_exception.assert_called_once_with(
            'Error sending API user notification email for request [%s].', self.api_access_request.id
        )
        # Verify object saved
        assert self.api_access_request.status == ApiAccessRequest.APPROVED
