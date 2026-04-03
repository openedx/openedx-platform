"""
Tests for discussion moderation email notifications.
"""
from unittest import mock
from django.test import override_settings
from django.core import mail

from lms.djangoapps.discussion.rest_api.emails import send_ban_escalation_email
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.factories import CourseFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase


class BanEscalationEmailTest(ModuleStoreTestCase):
    """Tests for send_ban_escalation_email function."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(org='TestX', number='CS101', run='2024')
        self.course_key = str(self.course.id)
        self.banned_user = UserFactory.create(username='spammer', email='spammer@example.com')
        self.moderator = UserFactory.create(username='moderator', email='mod@example.com')

    @override_settings(DISCUSSION_MODERATION_BAN_EMAIL_ENABLED=False)
    def test_email_disabled_by_setting(self):
        """Test that email is not sent when DISCUSSION_MODERATION_BAN_EMAIL_ENABLED is False."""
        # Clear outbox
        mail.outbox = []

        # Try to send email
        send_ban_escalation_email(
            banned_user_id=self.banned_user.id,
            moderator_id=self.moderator.id,
            course_id=self.course_key,
            scope='course',
            reason='Spam',
            threads_deleted=5,
            comments_deleted=10
        )

        # No email should be sent
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(
        DISCUSSION_MODERATION_BAN_EMAIL_ENABLED=True,
        DISCUSSION_MODERATION_ESCALATION_EMAIL='partner-support@edx.org'
    )
    @mock.patch('lms.djangoapps.discussion.rest_api.emails.ace')
    def test_email_sent_via_ace(self, mock_ace_module):
        """Test that email is sent via ACE when available."""
        # Create mock ACE send function
        mock_send = mock.MagicMock()
        mock_ace_module.send = mock_send

        send_ban_escalation_email(
            banned_user_id=self.banned_user.id,
            moderator_id=self.moderator.id,
            course_id=self.course_key,
            scope='course',
            reason='Posting scam links',
            threads_deleted=3,
            comments_deleted=7
        )

        # ACE send should be called
        mock_send.assert_called_once()

        # Get the message argument
        call_args = mock_send.call_args
        message = call_args[0][0]

        # Verify message properties
        self.assertEqual(message.recipient.email_address, 'partner-support@edx.org')
        self.assertEqual(message.context['banned_username'], 'spammer')
        self.assertEqual(message.context['moderator_username'], 'moderator')
        self.assertEqual(message.context['scope'], 'course')
        self.assertEqual(message.context['reason'], 'Posting scam links')
        self.assertEqual(message.context['threads_deleted'], 3)
        self.assertEqual(message.context['comments_deleted'], 7)
        self.assertEqual(message.context['total_deleted'], 10)

    @override_settings(
        DISCUSSION_MODERATION_BAN_EMAIL_ENABLED=True,
        DISCUSSION_MODERATION_ESCALATION_EMAIL='custom-support@example.com',
        DEFAULT_FROM_EMAIL='noreply@edx.org'
    )
    @mock.patch('lms.djangoapps.discussion.rest_api.emails.ace', None)
    def test_email_fallback_to_django_mail(self):
        """Test that email falls back to Django mail when ACE is not available."""
        # Clear outbox
        mail.outbox = []

        # Simulate ACE not being importable by making the import fail
        import sys
        original_modules = sys.modules.copy()

        # Remove ace modules if present
        ace_modules = [key for key in sys.modules if key.startswith('edx_ace')]
        for mod in ace_modules:
            sys.modules.pop(mod, None)

        try:
            send_ban_escalation_email(
                banned_user_id=self.banned_user.id,
                moderator_id=self.moderator.id,
                course_id=self.course_key,
                scope='organization',
                reason='Multiple violations',
                threads_deleted=15,
                comments_deleted=25
            )
        finally:
            # Restore modules
            sys.modules.update(original_modules)

        # Email should be sent via Django
        self.assertEqual(len(mail.outbox), 1)

        email = mail.outbox[0]
        self.assertIn('custom-support@example.com', email.to)
        self.assertEqual(email.from_email, 'noreply@edx.org')
        self.assertIn('spammer', email.body)
        self.assertIn('moderator', email.body)
        self.assertIn('Multiple violations', email.body)
        self.assertIn('ORGANIZATION', email.body)
        self.assertIn('15', email.body)  # threads_deleted
        self.assertIn('25', email.body)  # comments_deleted

    @override_settings(
        DISCUSSION_MODERATION_BAN_EMAIL_ENABLED=True,
        DISCUSSION_MODERATION_ESCALATION_EMAIL='support@example.com'
    )
    @mock.patch('lms.djangoapps.discussion.rest_api.emails.ace', None)
    def test_email_handles_missing_reason(self):
        """Test that email handles empty/None reason gracefully."""
        mail.outbox = []

        # Send with empty reason (will use Django mail since ace is None)
        send_ban_escalation_email(
            banned_user_id=self.banned_user.id,
            moderator_id=self.moderator.id,
            course_id=self.course_key,
            scope='course',
            reason='',
            threads_deleted=1,
            comments_deleted=0
        )

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        # Should use default text
        self.assertIn('No reason provided', email.body)

    @override_settings(
        DISCUSSION_MODERATION_BAN_EMAIL_ENABLED=True,
        DISCUSSION_MODERATION_ESCALATION_EMAIL='support@example.com'
    )
    @mock.patch('lms.djangoapps.discussion.rest_api.emails.ace', None)
    def test_email_with_org_level_ban(self):
        """Test email for organization-level ban."""
        mail.outbox = []

        send_ban_escalation_email(
            banned_user_id=self.banned_user.id,
            moderator_id=self.moderator.id,
            course_id=self.course_key,
            scope='organization',
            reason='Org-wide spam campaign',
            threads_deleted=50,
            comments_deleted=100
        )

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn('ORGANIZATION', email.body)
        self.assertIn('Org-wide spam campaign', email.body)

    @override_settings(
        DISCUSSION_MODERATION_BAN_EMAIL_ENABLED=True,
        DISCUSSION_MODERATION_ESCALATION_EMAIL='support@example.com'
    )
    @mock.patch('lms.djangoapps.discussion.rest_api.emails.ace', None)
    def test_email_failure_logged(self):
        """Test that email failures are properly logged."""
        with mock.patch('django.core.mail.send_mail', side_effect=Exception("SMTP error")):
            with self.assertLogs('lms.djangoapps.discussion.rest_api.emails', level='ERROR') as logs:
                with self.assertRaises(Exception):
                    send_ban_escalation_email(
                        banned_user_id=self.banned_user.id,
                        moderator_id=self.moderator.id,
                        course_id=self.course_key,
                        scope='course',
                        reason='Test',
                        threads_deleted=1,
                        comments_deleted=1
                    )

                # Verify error was logged
                self.assertTrue(any('Failed to send ban escalation email' in log for log in logs.output))

    @override_settings(DISCUSSION_MODERATION_BAN_EMAIL_ENABLED=True)
    def test_email_with_invalid_user_id(self):
        """Test that email handles invalid user IDs gracefully."""
        with self.assertRaises(Exception):
            send_ban_escalation_email(
                banned_user_id=99999,  # Non-existent user
                moderator_id=self.moderator.id,
                course_id=self.course_key,
                scope='course',
                reason='Test',
                threads_deleted=0,
                comments_deleted=0
            )

    @override_settings(
        DISCUSSION_MODERATION_BAN_EMAIL_ENABLED=True,
        DISCUSSION_MODERATION_ESCALATION_EMAIL='test@example.com'
    )
    @mock.patch('lms.djangoapps.discussion.rest_api.emails.ace', None)
    def test_email_subject_format(self):
        """Test that email subject is properly formatted."""
        mail.outbox = []

        send_ban_escalation_email(
            banned_user_id=self.banned_user.id,
            moderator_id=self.moderator.id,
            course_id=self.course_key,
            scope='course',
            reason='Test ban',
            threads_deleted=1,
            comments_deleted=1
        )

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        # Subject should contain username and course
        self.assertIn('spammer', email.subject)
        self.assertIn(self.course_key, email.subject)
