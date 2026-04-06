"""
Tests course_creators.admin.py.
"""


from unittest import mock

from django.contrib.admin.sites import AdminSite
from django.core import mail
from django.http import HttpRequest
from django.test import TestCase

from cms.djangoapps.course_creators.admin import CourseCreatorAdmin
from cms.djangoapps.course_creators.models import CourseCreator
from common.djangoapps.student import auth
from common.djangoapps.student.roles import CourseCreatorRole
from common.djangoapps.student.tests.factories import UserFactory


def mock_render_to_string(template_name, context):
    """Return a string that encodes template_name and context"""
    return str((template_name, context))


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
        self.enable_creator_group_patch = {
            "ENABLE_CREATOR_GROUP": True,
            "STUDIO_REQUEST_EMAIL": self.studio_request_email
        }

    @mock.patch(
        'cms.djangoapps.course_creators.admin.render_to_string',
        mock.Mock(side_effect=mock_render_to_string, autospec=True)
    )
    @mock.patch('django.contrib.auth.models.User.email_user')
    def test_change_status(self, email_user):
        """
        Tests that updates to state impact the creator group maintained in authz.py and that e-mails are sent.
        """

        def change_state_and_verify_email(state, is_creator):
            """ Changes user state, verifies creator status, and verifies e-mail is sent based on transition """
            self._change_state(state)
            self.assertEqual(is_creator, auth.user_has_role(self.user, CourseCreatorRole()))  # noqa: PT009

            context = {'studio_request_email': self.studio_request_email}
            if state == CourseCreator.GRANTED:
                template = 'emails/course_creator_granted.txt'
            elif state == CourseCreator.DENIED:
                template = 'emails/course_creator_denied.txt'
            else:
                template = 'emails/course_creator_revoked.txt'
            email_user.assert_called_with(
                mock_render_to_string('emails/course_creator_subject.txt', context),
                mock_render_to_string(template, context),
                self.studio_request_email
            )

        with mock.patch.dict('django.conf.settings.FEATURES', self.enable_creator_group_patch):

            # User is initially unrequested.
            self.assertFalse(auth.user_has_role(self.user, CourseCreatorRole()))  # noqa: PT009

            change_state_and_verify_email(CourseCreator.GRANTED, True)

            change_state_and_verify_email(CourseCreator.DENIED, False)

            change_state_and_verify_email(CourseCreator.GRANTED, True)

            change_state_and_verify_email(CourseCreator.PENDING, False)

            change_state_and_verify_email(CourseCreator.GRANTED, True)

            change_state_and_verify_email(CourseCreator.UNREQUESTED, False)

            change_state_and_verify_email(CourseCreator.DENIED, False)

    @mock.patch(
        'cms.djangoapps.course_creators.admin.render_to_string',
        mock.Mock(side_effect=mock_render_to_string, autospec=True)
    )
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
                context = {'user_name': 'test_user', 'user_email': 'test_user+courses@edx.org'}

                self.assertEqual(base_num_emails + 1, len(mail.outbox), 'Expected admin message to be sent')  # noqa: PT009
                sent_mail = mail.outbox[base_num_emails]
                self.assertEqual(  # noqa: PT009
                    mock_render_to_string('emails/course_creator_admin_subject.txt', context),
                    sent_mail.subject
                )
                self.assertEqual(  # noqa: PT009
                    mock_render_to_string('emails/course_creator_admin_user_pending.txt', context),
                    sent_mail.body
                )
                self.assertEqual(self.studio_request_email, sent_mail.from_email)  # noqa: PT009
                self.assertEqual([self.studio_request_email], sent_mail.to)  # noqa: PT009
            else:
                self.assertEqual(base_num_emails, len(mail.outbox))  # noqa: PT009

        with mock.patch.dict('django.conf.settings.FEATURES', self.enable_creator_group_patch):
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
