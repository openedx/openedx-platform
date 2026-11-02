"""
Tests for signal handling in commerce djangoapp.
"""


import base64
import json
from unittest import mock
from urllib.parse import urljoin

import pytest
import ddt
import httpretty
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test.utils import override_settings
from opaque_keys.edx.keys import CourseKey
from requests import Timeout
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.signals import REFUND_ORDER
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory

from ..models import CommerceConfiguration
from ..utils import _generate_refund_notification_body, _send_refund_notification, create_zendesk_ticket
from . import JSON
from .mocks import mock_create_refund, mock_process_refund
from common.test.utils import assert_dict_contains_subset

ZENDESK_URL = 'http://zendesk.example.com/'
ZENDESK_USER = 'test@example.com'
ZENDESK_API_KEY = 'abc123'


@ddt.ddt
@override_settings(ZENDESK_URL=ZENDESK_URL, ZENDESK_USER=ZENDESK_USER, ZENDESK_API_KEY=ZENDESK_API_KEY)
class TestRefundSignal(ModuleStoreTestCase):
    """
    Exercises logic triggered by the REFUND_ORDER signal.
    """

    def setUp(self):
        super().setUp()

        # Ensure the E-Commerce service user exists
        UserFactory(username=settings.ECOMMERCE_SERVICE_WORKER_USERNAME, is_staff=True)

        self.requester = UserFactory(username="test-requester")
        self.student = UserFactory(
            username="test-student",
            email="test-student@example.com",
        )
        self.course_enrollment = CourseEnrollmentFactory(
            user=self.student,
            course_id=CourseKey.from_string('course-v1:org+course+run'),
            mode=CourseMode.VERIFIED,
        )
        self.course_enrollment.refundable = mock.Mock(return_value=True)

        self.config = CommerceConfiguration.current()
        self.config.enable_automatic_refund_approval = True
        self.config.save()

    def send_signal(self):
        """
        DRY helper: emit the REFUND_ORDER signal, as is done in
        common.djangoapps.student.models after a successful unenrollment.
        """
        REFUND_ORDER.send(sender=None, course_enrollment=self.course_enrollment)

    @override_settings(
        ECOMMERCE_PUBLIC_URL_ROOT=None,
        ECOMMERCE_API_URL=None,
    )
    def test_no_service(self):
        """
        Ensure that the receiver quietly bypasses attempts to initiate
        refunds when there is no external service configured.
        """
        with mock.patch('lms.djangoapps.commerce.signals.refund_seat') as mock_refund_seat:
            self.send_signal()
            assert not mock_refund_seat.called

    @mock.patch('lms.djangoapps.commerce.signals.refund_seat')
    def test_receiver(self, mock_refund_seat):
        """
        Ensure that the REFUND_ORDER signal triggers correct calls to
        refund_seat(), when it is appropriate to do so.

        TODO (jsa): ideally we would assert that the signal receiver got wired
        up independently of the import statement in this module.  I'm not aware
        of any reliable / sane way to do this.
        """
        self.send_signal()
        assert mock_refund_seat.called
        assert mock_refund_seat.call_args[0] == (self.course_enrollment,)

        # if the course_enrollment is not refundable, we should not try to initiate a refund.
        mock_refund_seat.reset_mock()
        self.course_enrollment.refundable = mock.Mock(return_value=False)
        self.send_signal()
        assert not mock_refund_seat.called

    @mock.patch('lms.djangoapps.commerce.signals.refund_seat')
    @mock.patch('lms.djangoapps.commerce.signals.get_request_user', return_value=None)
    def test_requester(self, mock_get_request_user, mock_refund_seat):
        """
        Ensure the right requester is specified when initiating refunds.
        """
        # no HTTP request/user: auth to commerce service as the unenrolled student.
        self.send_signal()
        assert mock_refund_seat.called
        assert mock_refund_seat.call_args[0] == (self.course_enrollment,)

        # HTTP user is the student: auth to commerce service as the unenrolled student.
        mock_get_request_user.return_value = self.student
        mock_refund_seat.reset_mock()
        self.send_signal()
        assert mock_refund_seat.called
        assert mock_refund_seat.call_args[0] == (self.course_enrollment,)

        # HTTP user is another user: auth to commerce service as the requester.
        mock_get_request_user.return_value = self.requester
        mock_refund_seat.reset_mock()
        self.send_signal()
        assert mock_refund_seat.called
        assert mock_refund_seat.call_args[0] == (self.course_enrollment,)

        # HTTP user is another server (AnonymousUser): do not try to initiate a refund at all.
        mock_get_request_user.return_value = AnonymousUser()
        mock_refund_seat.reset_mock()
        self.send_signal()
        assert not mock_refund_seat.called

    @mock.patch('lms.djangoapps.commerce.signals.log.exception')
    def test_error_logging(self, mock_log_exception):
        """
        Ensure that unexpected Exceptions are logged as errors (but do not
        break program flow).
        """
        with mock_create_refund(status=500):
            self.send_signal()
            assert mock_log_exception.called

    @mock.patch('lms.djangoapps.commerce.utils._send_refund_notification')
    def test_notification_when_approval_fails(self, mock_send_notification):
        """
        Ensure the notification function is triggered when refunds are initiated, and cannot be automatically approved.
        """
        refund_id = 1
        failed_refund_id = 2

        with mock_create_refund(status=201, response=[refund_id, failed_refund_id]):
            with mock_process_refund(refund_id, reset_on_exit=False):
                with mock_process_refund(failed_refund_id, status=500, reset_on_exit=False):
                    self.send_signal()
                    assert mock_send_notification.called
                    mock_send_notification.assert_called_with(self.course_enrollment.user, [failed_refund_id])

    @mock.patch('lms.djangoapps.commerce.utils._send_refund_notification')
    def test_notification_if_automatic_approval_disabled(self, mock_send_notification):
        """
        Ensure the notification is always sent if the automatic approval functionality is disabled.
        """
        refund_id = 1
        self.config.enable_automatic_refund_approval = False
        self.config.save()

        with mock_create_refund(status=201, response=[refund_id]):
            self.send_signal()
            assert mock_send_notification.called
            mock_send_notification.assert_called_with(self.course_enrollment.user, [refund_id])

    @mock.patch('lms.djangoapps.commerce.utils._send_refund_notification')
    def test_no_notification_after_approval(self, mock_send_notification):
        """
        Ensure the notification function is triggered when refunds are initiated, and cannot be automatically approved.
        """
        refund_id = 1

        with mock_create_refund(status=201, response=[refund_id]):
            with mock_process_refund(refund_id, reset_on_exit=False):
                self.send_signal()
                assert not mock_send_notification.called

                last_request = httpretty.last_request()
                self.assertDictEqual(json.loads(last_request.body.decode('utf8')), {'action': 'approve_payment_only'})

    @mock.patch('lms.djangoapps.commerce.utils._send_refund_notification')
    def test_notification_no_refund(self, mock_send_notification):
        """
        Ensure the notification function is NOT triggered when no refunds are
        initiated
        """
        with mock_create_refund(status=200, response=[]):
            self.send_signal()
            assert not mock_send_notification.called

    @mock.patch('lms.djangoapps.commerce.utils._send_refund_notification')
    @ddt.data(
        CourseMode.HONOR,
        CourseMode.PROFESSIONAL,
        CourseMode.AUDIT,
        CourseMode.NO_ID_PROFESSIONAL_MODE,
        CourseMode.CREDIT_MODE,
    )
    def test_notification_not_verified(self, mode, mock_send_notification):
        """
        Ensure the notification function is NOT triggered when the
        unenrollment is for any mode other than verified (i.e. any mode other
        than one for which refunds are presently supported).  See the
        TODO associated with XCOM-371 in the signals module in the commerce
        package for more information.
        """
        self.course_enrollment.mode = mode
        with mock_create_refund(status=200, response=[1, 2, 3]):
            self.send_signal()
            assert not mock_send_notification.called

    @mock.patch('lms.djangoapps.commerce.utils._send_refund_notification', side_effect=Exception("Splat!"))
    @mock.patch('lms.djangoapps.commerce.utils.log.warning')
    def test_notification_error(self, mock_log_warning, mock_send_notification):
        """
        Ensure an error occuring during notification does not break program
        flow, but a warning is logged.
        """
        with mock_create_refund(status=200, response=[1, 2, 3]):
            self.send_signal()
            assert mock_send_notification.called
            assert mock_log_warning.called

    @mock.patch('openedx.core.djangoapps.theming.helpers.is_request_in_themed_site', return_value=True)
    def test_notification_themed_site(self, mock_is_request_in_themed_site):  # pylint: disable=unused-argument
        """
        Ensure the notification function raises an Exception if used in the
        context of themed site.
        """
        with pytest.raises(NotImplementedError):
            _send_refund_notification(self.course_enrollment.user, [1, 2, 3])

    @ddt.data('email@example.com', 'üñîcode.email@example.com')
    @mock.patch('lms.djangoapps.commerce.utils.create_zendesk_ticket')
    def test_send_refund_notification(self, student_email, mock_zendesk):
        """ Verify the support team is notified of the refund request. """
        refund_ids = [1, 2, 3]

        # pass a student with unicode and ascii email to ensure that
        # generate_refund_notification_body can handle formatting a unicode
        # message
        self.student.email = student_email
        _send_refund_notification(self.course_enrollment.user, refund_ids)
        body = _generate_refund_notification_body(self.student, refund_ids)
        mock_zendesk.assert_called_with(
            self.student.profile.name,
            self.student.email,
            "[Refund] User-Requested Refund",
            body,
            ['auto_refund']
        )

    def _mock_zendesk_api(self, status=201):
        """ Mock Zendesk's ticket creation API. """
        httpretty.register_uri(httpretty.POST, urljoin(ZENDESK_URL, '/api/v2/tickets.json'), status=status,
                               body='{}', content_type=JSON)

    def call_create_zendesk_ticket(self, name='Test user', email='user@example.com', subject='Test Ticket',
                                   body='I want a refund!', tags=None):
        """ Call the create_zendesk_ticket function. """
        tags = tags or ['auto_refund']
        return create_zendesk_ticket(name, email, subject, body, tags)

    @override_settings(ZENDESK_URL=ZENDESK_URL, ZENDESK_USER=None, ZENDESK_API_KEY=None)
    def test_create_zendesk_ticket_no_settings(self):
        """ Verify the Zendesk API is not called if the settings are not all set. """
        with mock.patch('requests.post') as mock_post:
            success = self.call_create_zendesk_ticket()
            assert not success
            assert not mock_post.called

    def test_create_zendesk_ticket_request_error(self):
        """
        Verify exceptions are handled appropriately if the request to the Zendesk API fails.

        We simply need to ensure the exception is not raised beyond the function.
        """
        with mock.patch('requests.post', side_effect=Timeout) as mock_post:
            success = self.call_create_zendesk_ticket()
            assert not success
            assert mock_post.called

    @httpretty.activate
    def test_create_zendesk_ticket(self):
        """ Verify the Zendesk API is called. """
        self._mock_zendesk_api()

        name = 'Test user'
        email = 'user@example.com'
        subject = 'Test Ticket'
        body = 'I want a refund!'
        tags = ['auto_refund']
        ticket_created = self.call_create_zendesk_ticket(name, email, subject, body, tags)
        assert ticket_created
        last_request = httpretty.last_request()

        # Verify the headers
        expected = {
            'content-type': JSON,
            'Authorization': 'Basic {}'.format(base64.b64encode(
                f'{ZENDESK_USER}/token:{ZENDESK_API_KEY}'.encode('utf8')).decode('utf8')
            )
        }
        assert_dict_contains_subset(self, expected, last_request.headers)

        # Verify the content
        expected = {
            'ticket': {
                'requester': {
                    'name': name,
                    'email': email
                },
                'subject': subject,
                'comment': {'body': body},
                'tags': ['LMS'] + tags
            }
        }
        response_dict = json.loads(last_request.body.decode('utf8'))
        response_dict['ticket']['tags'].sort()
        expected['ticket']['tags'].sort()
        self.assertDictEqual(response_dict, expected)
