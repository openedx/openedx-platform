"""
Tests course_creators.admin.py.
"""


from smtplib import SMTPException
from unittest import mock

from django.contrib.admin.sites import AdminSite
from django.core import mail
from django.http import HttpRequest
from django.test import TestCase
from django.test.utils import override_settings

from cms.djangoapps.course_creators.admin import CourseCreatorAdmin
from cms.djangoapps.course_creators.models import CourseCreator
from common.djangoapps.edxmako.shortcuts import render_to_string
from common.djangoapps.student import auth
from common.djangoapps.student.roles import CourseCreatorRole
from common.djangoapps.student.tests.factories import UserFactory


class CourseCreatorAdminTest(TestCase):
    """
    Tests for course creator admin.
    """

    def setUp(self):
        """ Test case setup """
        super().setUp()
        self.user = UserFactory.create(
            username='test_user',
            email='test_user+courses@edx.org',
            password='foo',
        )
        self.table_entry = CourseCreator(user=self.user)
        self.table_entry.save()

        self.admin = UserFactory.create(
            username='Mark',
            email='admin+courses@edx.org',
            password='foo',
        )
        self.admin.is_staff = True

        self.request = HttpRequest()
        self.request.user = self.admin

        self.creator_admin = CourseCreatorAdmin(self.table_entry, AdminSite())

        self.studio_request_email = 'mark@marky.mark'
        self.context = {
            'studio_request_email': self.studio_request_email,
            'is_secure': False,
            'site': 'localhost:8001',
            'user_name': 'test_user',
            'user_email': 'test_user+courses@edx.org',
        }
        self.enable_creator_group_patch = {'ENABLE_CREATOR_GROUP': True}

    @override_settings(ENABLE_CREATOR_GROUP=True, STUDIO_REQUEST_EMAIL='mark@marky.mark')
    @mock.patch('django.contrib.auth.models.User.email_user')
    def test_change_status(self, email_user):
        """
        Tests that updates to state impact the creator group maintained in authz.py and that e-mails are sent.
        """

        def change_state_and_verify_email(state, is_creator):
            """ Changes user state, verifies creator status, and verifies e-mail is sent based on transition """
            self._change_state(state)
            self.assertEqual(is_creator, auth.user_has_role(self.user, CourseCreatorRole()))  # noqa: PT009

            if state == CourseCreator.GRANTED:
                template = 'emails/course_creator_granted.txt'
            elif state == CourseCreator.DENIED:
                template = 'emails/course_creator_denied.txt'
            else:
                template = 'emails/course_creator_revoked.txt'
            email_user.assert_called_with(
                render_to_string('emails/course_creator_subject.txt', self.context).strip(),
                render_to_string(template, self.context),
                self.studio_request_email
            )

        # User is initially unrequested.
        self.assertFalse(auth.user_has_role(self.user, CourseCreatorRole()))  # noqa: PT009

        change_state_and_verify_email(CourseCreator.GRANTED, True)

        change_state_and_verify_email(CourseCreator.DENIED, False)

        change_state_and_verify_email(CourseCreator.GRANTED, True)

        change_state_and_verify_email(CourseCreator.PENDING, False)

        change_state_and_verify_email(CourseCreator.GRANTED, True)

        change_state_and_verify_email(CourseCreator.UNREQUESTED, False)

        change_state_and_verify_email(CourseCreator.DENIED, False)

    @override_settings(ENABLE_CREATOR_GROUP=True, STUDIO_REQUEST_EMAIL='mark@marky.mark')
    def test_mail_admin_on_pending(self):
        """
        Tests that the admin account is notified when a user is in the 'pending' state.
        """

        def check_admin_message_state(state, expect_sent_to_admin, expect_sent_to_user):
            """ Changes user state and verifies e-mail sent to admin address only when pending. """
            mail.outbox = []
            self._change_state(state)

            # If a message is sent to the user about course creator status change, it will be the first
            # message sent. Admin message will follow.
            base_num_emails = 1 if expect_sent_to_user else 0
            if expect_sent_to_admin:

                self.assertEqual(base_num_emails + 1, len(mail.outbox), 'Expected admin message to be sent')  # noqa: PT009  # pylint: disable=line-too-long
                sent_mail = mail.outbox[base_num_emails]
                self.assertEqual(  # noqa: PT009
                    render_to_string('emails/course_creator_admin_subject.txt', self.context).strip(),
                    sent_mail.subject
                )
                self.assertEqual(  # noqa: PT009
                    render_to_string('emails/course_creator_admin_user_pending.txt', self.context),
                    sent_mail.body
                )
                self.assertEqual(self.studio_request_email, sent_mail.from_email)  # noqa: PT009
                self.assertEqual([self.studio_request_email], sent_mail.to)  # noqa: PT009
            else:
                self.assertEqual(base_num_emails, len(mail.outbox))  # noqa: PT009

        # E-mail message should be sent to admin only when new state is PENDING, regardless of what
        # previous state was (unless previous state was already PENDING).
        # E-mail message sent to user only on transition into and out of GRANTED state.
        check_admin_message_state(CourseCreator.UNREQUESTED, expect_sent_to_admin=False, expect_sent_to_user=False)
        check_admin_message_state(CourseCreator.PENDING, expect_sent_to_admin=True, expect_sent_to_user=False)
        check_admin_message_state(CourseCreator.GRANTED, expect_sent_to_admin=False, expect_sent_to_user=True)
        check_admin_message_state(CourseCreator.DENIED, expect_sent_to_admin=False, expect_sent_to_user=True)
        check_admin_message_state(CourseCreator.GRANTED, expect_sent_to_admin=False, expect_sent_to_user=True)
        check_admin_message_state(CourseCreator.PENDING, expect_sent_to_admin=True, expect_sent_to_user=True)
        check_admin_message_state(CourseCreator.PENDING, expect_sent_to_admin=False, expect_sent_to_user=False)
        check_admin_message_state(CourseCreator.DENIED, expect_sent_to_admin=False, expect_sent_to_user=True)

    def _change_state(self, state):
        """ Helper method for changing state """
        self.table_entry.state = state
        self.creator_admin.save_model(self.request, self.table_entry, None, True)

    def test_add_permission(self):
        """
        Tests that staff cannot add entries
        """
        self.assertFalse(self.creator_admin.has_add_permission(self.request))  # noqa: PT009

    def test_delete_permission(self):
        """
        Tests that staff cannot delete entries
        """
        self.assertFalse(self.creator_admin.has_delete_permission(self.request))  # noqa: PT009

    def test_change_permission(self):
        """
        Tests that only staff can change entries
        """
        self.assertTrue(self.creator_admin.has_change_permission(self.request))  # noqa: PT009

        self.request.user = self.user
        self.assertFalse(self.creator_admin.has_change_permission(self.request))  # noqa: PT009

    @override_settings(ENABLE_CREATOR_GROUP=True, STUDIO_REQUEST_EMAIL='mark@marky.mark')
    @mock.patch('cms.djangoapps.course_creators.admin.log')
    @mock.patch('django.contrib.auth.models.User.email_user')
    def test_send_user_notification_error_logging(self, mock_email_user, mock_log):
        """
        Test that email_user raising an exception logs the correct message based on SQUELCH_PII_IN_LOGS setting.
        """
        mock_email_user.side_effect = Exception("SMTP error")

        def test_and_assert_case(squelch_pii, states):
            mock_log.reset_mock()
            with self.settings(SQUELCH_PII_IN_LOGS=squelch_pii), mock.patch.dict(
                'django.conf.settings.FEATURES', self.enable_creator_group_patch
            ):
                for state in states:
                    self._change_state(state)
                expected_identifier = self.user.id if squelch_pii else self.user.email
                mock_log.warning.assert_any_call(
                    "Unable to send course creator status e-mail to %s",
                    expected_identifier
                )

        test_and_assert_case(True, [CourseCreator.GRANTED])
        # True case moved the object to GRANTED; False case needs DENIED -> GRANTED to retrigger.
        test_and_assert_case(False, [CourseCreator.DENIED, CourseCreator.GRANTED])

    @override_settings(ENABLE_CREATOR_GROUP=True, STUDIO_REQUEST_EMAIL='mark@marky.mark')
    @mock.patch('cms.djangoapps.course_creators.admin.log')
    @mock.patch('cms.djangoapps.course_creators.admin.send_mail')
    def test_send_admin_notification_error_logging(self, mock_send_mail, mock_log):
        """
        Test that send_mail raising SMTPException logs the correct message based on SQUELCH_PII_IN_LOGS setting.
        """
        mock_send_mail.side_effect = SMTPException("SMTP error")

        def test_and_assert_case(squelch_pii, states):
            mock_log.reset_mock()
            with self.settings(SQUELCH_PII_IN_LOGS=squelch_pii), mock.patch.dict(
                'django.conf.settings.FEATURES', self.enable_creator_group_patch
            ):
                for state in states:
                    self._change_state(state)
                expected_identifier = self.user.id if squelch_pii else self.user.email
                mock_log.warning.assert_any_call(
                    "Failure sending 'pending state' e-mail for %s to %s",
                    expected_identifier,
                    self.studio_request_email
                )

        test_and_assert_case(True, [CourseCreator.PENDING])
        # True case left the object at PENDING; False case needs UNREQUESTED -> PENDING to retrigger.
        test_and_assert_case(False, [CourseCreator.UNREQUESTED, CourseCreator.PENDING])
