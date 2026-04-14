"""
Test cases for notifications/email/tasks.py
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import ddt
import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from edx_toggles.toggles.testutils import override_waffle_flag
from freezegun import freeze_time

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.notifications.email.tasks import (
    _claim_digest_schedule,
    add_to_existing_buffer,
    decide_email_action,
    get_next_digest_delivery_time,
    is_digest_already_scheduled,
    is_digest_already_sent_in_window,
    schedule_bulk_digest_emails,
    schedule_digest_buffer,
    send_buffered_digest,
    send_digest_email_to_user,
    send_immediate_cadence_email,
    send_immediate_email,
    send_user_digest_email_task,
)
from openedx.core.djangoapps.notifications.email.utils import get_start_end_date
from openedx.core.djangoapps.notifications.email_notifications import EmailCadence
from openedx.core.djangoapps.notifications.models import (
    DigestSchedule,
    Notification,
    NotificationPreference,
)
from openedx.core.djangoapps.notifications.tasks import send_notifications
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from ...config.waffle import DISABLE_EMAIL_NOTIFICATIONS
from .utils import create_notification

User = get_user_model()


@ddt.ddt
class TestEmailDigestForUser(ModuleStoreTestCase):
    """
    Tests email notification for a specific user
    """

    def setUp(self):
        """
        Setup
        """
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create(display_name='test course', run="Testing_course")

    @patch('edx_ace.ace.send')
    def test_email_is_not_sent_if_no_notifications(self, mock_func):
        """
        Tests that no email is sent when there are no notifications for the user
        """
        start_date, end_date = get_start_end_date(EmailCadence.DAILY)
        send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert not mock_func.called

    @ddt.data(True, False)
    @patch('edx_ace.ace.send')
    def test_email_is_sent_if_flag_disabled(self, flag_value, mock_func):
        """
        Tests email is sent if waffle flag is disabled
        """
        created_date = datetime.now() - timedelta(days=1)
        create_notification(self.user, self.course.id, created=created_date)
        start_date, end_date = get_start_end_date(EmailCadence.DAILY)
        with override_waffle_flag(DISABLE_EMAIL_NOTIFICATIONS, flag_value):
            send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert mock_func.called is not flag_value

    @patch('edx_ace.ace.send')
    def test_notification_not_send_if_created_on_next_day(self, mock_func):
        """
        Tests email is not sent if notification is created on next day
        """
        start_date, end_date = get_start_end_date(EmailCadence.DAILY)
        create_notification(self.user, self.course.id, created=end_date + timedelta(minutes=2))
        send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert not mock_func.called

    @ddt.data(True, False)
    @patch('edx_ace.ace.send')
    def test_email_not_send_to_disable_user(self, value, mock_func):
        """
        Tests email is not sent to disabled user
        """
        created_date = datetime.now() - timedelta(days=1)
        create_notification(self.user, self.course.id, created=created_date)
        start_date, end_date = get_start_end_date(EmailCadence.DAILY)
        if value:
            self.user.set_password("12345678")
        else:
            self.user.set_unusable_password()
        send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert mock_func.called is value

    @patch('edx_ace.ace.send')
    def test_notification_not_send_if_created_day_before_yesterday(self, mock_func):
        """
        Tests email is not sent if notification is created day before yesterday
        """
        start_date, end_date = get_start_end_date(EmailCadence.DAILY)
        created_date = datetime.now() - timedelta(days=1, minutes=18)
        create_notification(self.user, self.course.id, created=created_date)
        send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert not mock_func.called

    @ddt.data(
        (EmailCadence.DAILY, datetime.now() - timedelta(days=1, minutes=30), False),
        (EmailCadence.DAILY, datetime.now() - timedelta(minutes=10), True),
        (EmailCadence.DAILY, datetime.now() - timedelta(days=1), True),
        (EmailCadence.DAILY, datetime.now() + timedelta(minutes=20), False),
        (EmailCadence.WEEKLY, datetime.now() - timedelta(days=7, minutes=30), False),
        (EmailCadence.WEEKLY, datetime.now() - timedelta(days=7), True),
        (EmailCadence.WEEKLY, datetime.now() - timedelta(minutes=20), True),
        (EmailCadence.WEEKLY, datetime.now() + timedelta(minutes=20), False),
    )
    @ddt.unpack
    @patch('edx_ace.ace.send')
    def test_notification_content(self, cadence_type, created_time, notification_created, mock_func):
        """
        Tests email only contains notification created within date
        """
        start_date, end_date = get_start_end_date(cadence_type)
        create_notification(self.user, self.course.id, created=created_time)
        send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert mock_func.called is notification_created


@ddt.ddt
class TestEmailDigestForUserWithAccountPreferences(ModuleStoreTestCase):
    """
    Tests email notification for a specific user
    """

    def setUp(self):
        """
        Setup
        """
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create(display_name='test course', run="Testing_course")

    @patch('edx_ace.ace.send')
    def test_email_is_not_sent_if_no_notifications(self, mock_func):
        """
        Tests email is sent iff waffle flag is enabled
        """
        start_date, end_date = get_start_end_date(EmailCadence.DAILY)
        send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert not mock_func.called

    @ddt.data(True, False)
    @patch('edx_ace.ace.send')
    def test_email_is_sent_if_flag_disabled(self, flag_value, mock_func):
        """
        Tests email is sent iff waffle flag is disabled
        """
        created_date = datetime.now() - timedelta(days=1)
        create_notification(self.user, self.course.id, created=created_date)
        start_date, end_date = get_start_end_date(EmailCadence.DAILY)
        with override_waffle_flag(DISABLE_EMAIL_NOTIFICATIONS, flag_value):
            send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert mock_func.called is not flag_value

    @patch('edx_ace.ace.send')
    def test_notification_not_send_if_created_on_next_day(self, mock_func):
        """
        Tests email is not sent if notification is created on next day
        """
        start_date, end_date = get_start_end_date(EmailCadence.DAILY)
        create_notification(self.user, self.course.id, created=end_date + timedelta(minutes=2))
        send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert not mock_func.called

    @ddt.data(True, False)
    @patch('edx_ace.ace.send')
    def test_email_not_send_to_disable_user(self, value, mock_func):
        """
        Tests email is not sent to disabled user
        """
        created_date = datetime.now() - timedelta(days=1)
        create_notification(self.user, self.course.id, created=created_date)
        start_date, end_date = get_start_end_date(EmailCadence.DAILY)
        if value:
            self.user.set_password("12345678")
        else:
            self.user.set_unusable_password()
        send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert mock_func.called is value

    @patch('edx_ace.ace.send')
    def test_notification_not_send_if_created_day_before_yesterday(self, mock_func):
        """
        Tests email is not sent if notification is created day before yesterday
        """
        start_date, end_date = get_start_end_date(EmailCadence.DAILY)
        created_date = datetime.now() - timedelta(days=1, minutes=18)
        create_notification(self.user, self.course.id, created=created_date)
        send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert not mock_func.called

    @ddt.data(
        (EmailCadence.DAILY, datetime.now() - timedelta(days=1, minutes=30), False),
        (EmailCadence.DAILY, datetime.now() - timedelta(minutes=10), True),
        (EmailCadence.DAILY, datetime.now() - timedelta(days=1), True),
        (EmailCadence.DAILY, datetime.now() + timedelta(minutes=20), False),
        (EmailCadence.WEEKLY, datetime.now() - timedelta(days=7, minutes=30), False),
        (EmailCadence.WEEKLY, datetime.now() - timedelta(days=7), True),
        (EmailCadence.WEEKLY, datetime.now() - timedelta(minutes=20), True),
        (EmailCadence.WEEKLY, datetime.now() + timedelta(minutes=20), False),
    )
    @ddt.unpack
    @patch('edx_ace.ace.send')
    def test_notification_content(self, cadence_type, created_time, notification_created, mock_func):
        """
        Tests email only contains notification created within date
        """
        start_date, end_date = get_start_end_date(cadence_type)
        create_notification(self.user, self.course.id, created=created_time)
        send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert mock_func.called is notification_created


@ddt.ddt
class TestAccountPreferences(ModuleStoreTestCase):
    """
    Tests preferences
    """

    def setUp(self):
        """
        Setup
        """
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create(display_name='test course', run="Testing_course")
        self.preference, _ = NotificationPreference.objects.get_or_create(user=self.user, app="discussion",
                                                                          type="new_discussion_post")
        created_date = datetime.now() - timedelta(hours=23)
        create_notification(self.user, self.course.id, notification_type='new_discussion_post', created=created_date)

    @patch('edx_ace.ace.send')
    def test_email_send_for_digest_preference(self, mock_func):
        """
        Tests email is send for digest notification preference
        """
        start_date, end_date = get_start_end_date(EmailCadence.DAILY)
        self.preference.email = True
        self.preference.email_cadence = EmailCadence.DAILY
        self.preference.save()
        send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert mock_func.called

    @ddt.data(True, False)
    @patch('edx_ace.ace.send')
    def test_email_send_for_email_preference_value(self, pref_value, mock_func):
        """
        Tests email is sent iff preference value is True
        """
        start_date, end_date = get_start_end_date(EmailCadence.DAILY)
        self.preference.email = pref_value
        self.preference.email_cadence = EmailCadence.DAILY
        self.preference.save()
        send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert mock_func.called is pref_value

    @patch('edx_ace.ace.send')
    def test_email_not_send_if_different_digest_preference(self, mock_func):
        """
        Tests email is not send if digest notification preference doesnot match
        """
        start_date, end_date = get_start_end_date(EmailCadence.DAILY)
        self.preference.email = True
        self.preference.email_cadence = EmailCadence.WEEKLY
        self.preference.save()
        send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert not mock_func.called


class TestImmediateEmailNotifications(ModuleStoreTestCase):
    """
    Tests for immediate email notifications functionality.
    Covers both high-level notification triggering and specific task execution logic.
    """

    def setUp(self):
        """
        Shared setup for user, course, and default preferences.
        """
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create(display_name='test course', run="Testing_course")

        # Ensure a clean slate for this user
        NotificationPreference.objects.filter(user=self.user).delete()

        # Create a default preference object that can be modified by individual tests
        self.preference, _ = NotificationPreference.objects.get_or_create(
            user=self.user,
            type='new_discussion_post',
            app='discussion',
            defaults={
                'web': True,
                'push': True,
                'email': True,
                'email_cadence': EmailCadence.IMMEDIATELY
            }
        )

    @patch('edx_ace.ace.send')
    def test_email_sent_when_cadence_is_immediate(self, mock_ace_send):
        """
        Tests that an email is sent via send_notifications when cadence is set to IMMEDIATE.
        """
        # Ensure preference matches test case
        self.preference.email = True
        self.preference.email_cadence = EmailCadence.IMMEDIATELY
        self.preference.save()

        context = {
            'username': 'User',
            'post_title': 'title'
        }

        send_notifications(
            [self.user.id],
            str(self.course.id),
            'discussion',
            'new_discussion_post',
            context,
            'http://test.url'
        )

        assert mock_ace_send.call_count == 1

    @patch('openedx.core.djangoapps.notifications.email.tasks.send_user_digest_email_task.apply_async')
    @patch('edx_ace.ace.send')
    def test_email_not_sent_when_cadence_is_not_immediate(self, mock_ace_send, mock_apply_async):
        """
        Tests that an email is NOT sent via send_notifications when cadence is DAILY.
        The digest is scheduled for later delivery — ace.send must not be called immediately.
        """
        # Modify preference for this test case
        self.preference.email = True
        self.preference.email_cadence = EmailCadence.DAILY
        self.preference.save()

        context = {
            'replier_name': 'User',
            'post_title': 'title'
        }
        send_notifications(
            [self.user.id],
            str(self.course.id),
            'discussion',
            'new_response',
            context,
            'http://test.url'
        )

        assert mock_ace_send.call_count == 0


@ddt.ddt
class TestDecideEmailAction(ModuleStoreTestCase):
    """Test the core decision logic for email buffering."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create()
        self.course_key = str(self.course.id)

    def _create_notification(self, **kwargs):
        """Helper to create notification with defaults."""
        defaults = {
            'user': self.user,
            'course_id': self.course_key,
            'app_name': 'discussion',
            'notification_type': 'new_discussion_post',
            'content_url': 'http://example.com',
            'email': True,
        }
        defaults.update(kwargs)
        return Notification.objects.create(**defaults)

    @freeze_time("2025-12-15 10:00:00")
    def test_first_notification_sends_immediate(self):
        """Test that first notification triggers immediate send."""
        notification = self._create_notification()

        decision = decide_email_action(self.user, self.course_key, notification)

        assert decision == 'send_immediate'

    @freeze_time("2025-12-15 10:00:00")
    def test_second_notification_schedules_buffer(self):
        """Test that second notification within buffer schedules digest."""
        # First notification - sent 5 minutes ago
        self._create_notification(
            email_sent_on=timezone.now() - timedelta(minutes=5)
        )

        # Second notification - should schedule buffer
        notification = self._create_notification()

        decision = decide_email_action(self.user, self.course_key, notification)

        assert decision == 'schedule_buffer'

    @freeze_time("2025-12-15 10:00:00")
    def test_third_notification_adds_to_buffer(self):
        """Test that third notification just marks as scheduled."""
        # First notification - sent 5 minutes ago
        self._create_notification(
            email_sent_on=timezone.now() - timedelta(minutes=5)
        )

        # Second notification - scheduled
        self._create_notification(email_scheduled=True)

        # Third notification - should add to existing buffer
        notification = self._create_notification()

        decision = decide_email_action(self.user, self.course_key, notification)

        assert decision == 'add_to_buffer'

    @freeze_time("2025-12-15 10:00:00")
    @override_settings(NOTIFICATION_IMMEDIATE_EMAIL_BUFFER_MINUTES=15)
    def test_old_email_triggers_new_immediate_send(self):
        """Test that email sent outside buffer period triggers new immediate send."""
        # Email sent 20 minutes ago (outside 15-minute buffer)
        self._create_notification(
            email_sent_on=timezone.now() - timedelta(minutes=20)
        )

        notification = self._create_notification()

        decision = decide_email_action(self.user, self.course_key, notification)

        assert decision == 'send_immediate'

    @freeze_time("2025-12-15 10:00:00")
    def test_different_course_doesnt_affect_decision(self):
        """Test that notifications from different courses are independent."""
        other_course = CourseFactory.create()

        # Notification from different course
        self._create_notification(
            course_id=str(other_course.id),
            email_sent_on=timezone.now() - timedelta(minutes=5)
        )

        # This course should still send immediate
        notification = self._create_notification()

        decision = decide_email_action(self.user, self.course_key, notification)

        assert decision == 'send_immediate'

    @freeze_time("2025-12-15 10:00:00")
    def test_race_condition_protection(self):
        """Test that select_for_update prevents race conditions."""
        # Simulate concurrent notifications
        notification1 = self._create_notification()
        notification2 = self._create_notification()

        # Both should see no recent email initially
        with patch('openedx.core.djangoapps.notifications.email.tasks.logger') as mock_logger:  # noqa: F841
            decision1 = decide_email_action(self.user, self.course_key, notification1)

            # Mark first as sent to simulate race
            notification1.email_sent_on = timezone.now()
            notification1.save()

            decision2 = decide_email_action(self.user, self.course_key, notification2)

            assert decision1 == 'send_immediate'
            assert decision2 == 'schedule_buffer'


@ddt.ddt
class TestSendImmediateEmail(ModuleStoreTestCase):
    """Test immediate email sending logic."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create(display_name='Test Course')
        self.course_key = str(self.course.id)

        self.notification = Notification.objects.create(
            user=self.user,
            course_id=self.course_key,
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            content_context=get_new_post_notification_content_context(),
            email=True,
        )

    @freeze_time("2025-12-15 10:00:00")
    @patch('openedx.core.djangoapps.notifications.email.tasks.ace.send')
    def test_immediate_email_sent_successfully(self, mock_ace_send):
        """Test that immediate email is sent and notification marked."""
        send_immediate_email(
            user=self.user,
            notification=self.notification,
            course_key=self.course_key,
            course_name='Test Course',
            user_language='en'
        )

        # Verify email was sent
        assert mock_ace_send.called

        # Verify notification marked with sent time
        self.notification.refresh_from_db()
        assert self.notification.email_sent_on is not None
        assert self.notification.email_sent_on == timezone.now()

    @patch('openedx.core.djangoapps.notifications.email.tasks.ace.send')
    def test_email_content_includes_notification_data(self, mock_ace_send):
        """Test that email contains all required notification data."""
        send_immediate_email(
            user=self.user,
            notification=self.notification,
            course_key=self.course_key,
            course_name='Test Course',
            user_language='en'
        )

        # Get the message that was sent
        call_args = mock_ace_send.call_args
        message = call_args[0][0]

        # Verify message context
        assert 'Test Course' in str(message.context)
        assert 'Email content' in str(message.context.get('content', ''))


@ddt.ddt
class TestScheduleDigestBuffer(ModuleStoreTestCase):
    """Test digest buffer scheduling logic."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create()
        self.course_key = str(self.course.id)

    @freeze_time("2025-12-15 10:00:00", tz_offset=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.send_buffered_digest.apply_async')
    @override_settings(NOTIFICATION_IMMEDIATE_EMAIL_BUFFER_MINUTES=15)
    def test_buffer_scheduled_with_correct_delay(self, mock_apply_async):
        """Test that buffer task is scheduled with correct countdown."""
        # Create notification that was sent 5 minutes ago
        Notification.objects.create(
            user=self.user,
            course_id=self.course_key,
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            email=True,
            email_sent_on=timezone.now() - timedelta(minutes=5)
        )

        new_notification = Notification.objects.create(
            user=self.user,
            course_id=self.course_key,
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            email=True,
        )

        schedule_digest_buffer(
            user=self.user,
            notification=new_notification,
            course_key=self.course_key,
            user_language='en'
        )

        # Verify task was scheduled
        assert mock_apply_async.called

        # Verify notification marked as scheduled
        new_notification.refresh_from_db()
        assert new_notification.email_scheduled is True

        # Verify scheduled time (should be 15 minutes from now)
        call_kwargs = mock_apply_async.call_args[1]
        eta = call_kwargs['eta']
        expected_eta = timezone.now() + timedelta(minutes=15)
        if timezone.is_naive(eta) and timezone.is_aware(expected_eta):
            expected_eta = timezone.make_naive(expected_eta)
        elif timezone.is_aware(eta) and timezone.is_naive(expected_eta):
            expected_eta = timezone.make_aware(expected_eta)
        # --- FIX END ---
        # Allow 1 second tolerance
        assert abs((eta - expected_eta).total_seconds()) < 1

    @patch('openedx.core.djangoapps.notifications.email.tasks.send_buffered_digest.apply_async')
    def test_schedule_includes_start_date(self, mock_apply_async):
        """Test that scheduled task includes correct start date."""
        sent_time = timezone.now() - timedelta(minutes=10)

        Notification.objects.create(
            user=self.user,
            course_id=self.course_key,
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            email=True,
            email_sent_on=sent_time
        )

        new_notification = Notification.objects.create(
            user=self.user,
            course_id=self.course_key,
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            email=True,
        )

        schedule_digest_buffer(
            user=self.user,
            notification=new_notification,
            course_key=self.course_key,
            user_language='en'
        )

        # Verify start_date in task kwargs
        call_kwargs = mock_apply_async.call_args[1]['kwargs']
        assert call_kwargs['start_date'] == sent_time


class TestAddToExistingBuffer(ModuleStoreTestCase):
    """Test adding notifications to existing buffer."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create()

    def test_notification_marked_as_scheduled(self):
        """Test that notification is marked as scheduled."""
        notification = Notification.objects.create(
            user=self.user,
            course_id=str(self.course.id),
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            email=True,
            email_scheduled=False
        )

        add_to_existing_buffer(notification)

        notification.refresh_from_db()
        assert notification.email_scheduled is True

    def test_only_scheduled_field_updated(self):
        """Test that only email_scheduled field is updated, other fields remain unchanged."""
        notification = Notification.objects.create(
            user=self.user,
            course_id=str(self.course.id),
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            email=True,
            content_context=get_new_post_notification_content_context()
        )
        original_content_url = notification.content_url
        original_email_sent_on = notification.email_sent_on

        add_to_existing_buffer(notification)

        notification.refresh_from_db()
        assert notification.email_scheduled is True
        assert notification.content_url == original_content_url
        assert notification.email_sent_on == original_email_sent_on
        assert notification.email is True


@ddt.ddt
class TestSendBufferedDigest(ModuleStoreTestCase):
    """Test buffered digest email sending."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create(display_name='Test Course')
        self.course_key = str(self.course.id)

        # Create preference
        NotificationPreference.objects.all().delete()
        NotificationPreference.objects.create(
            user=self.user,
            app='discussion',
            type='new_discussion_post',
            email=True,
            email_cadence=EmailCadence.IMMEDIATELY
        )

    @freeze_time("2025-12-15 10:15:00")
    @patch('openedx.core.djangoapps.notifications.email.tasks.ace.send')
    def test_digest_collects_all_scheduled_notifications(self, mock_ace_send):
        """Test that digest email includes all scheduled notifications."""
        start_time = timezone.now() - timedelta(minutes=15)

        # Create 3 scheduled notifications
        for i in range(3):
            Notification.objects.create(
                user=self.user,
                course_id=self.course_key,
                app_name='discussion',
                notification_type='new_discussion_post',
                content_url='http://example.com',
                content_context=get_new_post_notification_content_context(),
                email=True,
                email_scheduled=True,
                created=start_time + timedelta(minutes=i * 5)
            )

            send_buffered_digest(  # pylint: disable=no-value-for-parameter
                user_id=self.user.id,
                course_key=self.course_key,
                start_date=start_time,
                user_language='en'
            )

        # Verify email was sent
        assert mock_ace_send.called

        # Verify all notifications marked as sent and unscheduled
        notifications = Notification.objects.filter(
            user=self.user,
            course_id=self.course_key
        )

        for notif in notifications:
            assert notif.email_sent_on is not None
            assert notif.email_scheduled is False

    @patch('openedx.core.djangoapps.notifications.email.tasks.ace.send')
    def test_digest_skips_non_scheduled_notifications(self, mock_ace_send):
        """Test that digest only includes scheduled notifications."""
        start_time = timezone.now() - timedelta(minutes=15)

        # Scheduled notification
        Notification.objects.create(
            user=self.user,
            course_id=self.course_key,
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            content_context=get_new_post_notification_content_context(),
            email=True,
            email_scheduled=True,
            created=start_time + timedelta(minutes=5)
        )

        # Non-scheduled notification (should be ignored)
        Notification.objects.create(
            user=self.user,
            course_id=self.course_key,
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            content_context=get_new_post_notification_content_context(),
            email=True,
            email_scheduled=False,
            created=start_time + timedelta(minutes=10)
        )

        send_buffered_digest(  # pylint: disable=no-value-for-parameter
            user_id=self.user.id,
            course_key=self.course_key,
            start_date=start_time,
            user_language='en'
        )

        # Only 1 notification should be marked as sent
        sent_count = Notification.objects.filter(
            user=self.user,
            email_sent_on__isnull=False
        ).count()

        assert sent_count == 1

    @patch('openedx.core.djangoapps.notifications.email.tasks.ace.send')
    def test_digest_respects_user_preferences(self, mock_ace_send):
        """Test that digest filters based on user preferences."""
        start_time = timezone.now() - timedelta(minutes=15)
        NotificationPreference.objects.all().delete()

        # Create notification for type that user has disabled
        NotificationPreference.objects.create(
            user=self.user,
            app='discussion',
            type='new_comment',
            email=False,  # Disabled
            email_cadence=EmailCadence.IMMEDIATELY
        )

        Notification.objects.create(
            user=self.user,
            course_id=self.course_key,
            app_name='discussion',
            notification_type='new_comment',
            content_context=get_new_post_notification_content_context(),
            content_url='http://example.com',
            email=True,
            email_scheduled=True,
            created=start_time + timedelta(minutes=5)
        )

        send_buffered_digest(  # pylint: disable=no-value-for-parameter
            user_id=self.user.id,
            course_key=self.course_key,
            start_date=start_time,
            user_language='en'
        )

        # Email should not be sent
        assert not mock_ace_send.called

        # Notification should still be marked as scheduled=False
        notif = Notification.objects.get(
            user=self.user,
            notification_type='new_comment'
        )
        assert notif.email_scheduled is False

    def test_digest_handles_missing_user(self):
        """Test that digest handles non-existent user gracefully."""
        start_time = timezone.now() - timedelta(minutes=15)

        # Should not raise exception
        send_buffered_digest(  # pylint: disable=no-value-for-parameter
            user_id=99999,  # Non-existent
            course_key=self.course_key,
            start_date=start_time,
            user_language='en'
        )

    @patch('openedx.core.djangoapps.notifications.email.tasks.ace.send', side_effect=Exception('Email failed'))
    def test_digest_retries_on_failure(self, mock_ace_send):
        """Test that digest task retries on failure."""
        start_time = timezone.now() - timedelta(minutes=15)

        Notification.objects.create(
            user=self.user,
            course_id=self.course_key,
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            content_context={'email_content': '<p>Email</p>'},
            email=True,
            email_scheduled=True,
            created=start_time + timedelta(minutes=5)
        )

        # Create a mock task instance
        mock_task = Mock()
        mock_task.request.retries = 0

        with self.assertRaises(Exception):  # noqa: B017, PT027
            send_buffered_digest.bind(mock_task)(
                user_id=self.user.id,
                course_key=self.course_key,
                start_date=start_time,
                user_language='en'
            )


@ddt.ddt
class TestIntegrationScenarios(ModuleStoreTestCase):
    """Integration tests for complete notification flow."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create(display_name='Test Course')
        NotificationPreference.objects.all().delete()

        NotificationPreference.objects.create(
            user=self.user,
            app='discussion',
            type='new_discussion_post',
            email=True,
            email_cadence=EmailCadence.IMMEDIATELY
        )

    @freeze_time("2025-12-15 10:00:00")
    @patch('openedx.core.djangoapps.notifications.email.tasks.ace.send')
    @patch('openedx.core.djangoapps.notifications.email.tasks.send_buffered_digest.apply_async')
    @override_settings(NOTIFICATION_IMMEDIATE_EMAIL_BUFFER_MINUTES=15)
    def test_complete_three_notification_flow(self, mock_digest_async, mock_ace_send):
        """Test complete flow: immediate → buffer → add to buffer."""
        email_mapping = {}

        # FIRST NOTIFICATION - should send immediately
        notif1 = Notification.objects.create(
            user=self.user,
            course_id=self.course.id,
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            content_context=get_new_post_notification_content_context(),
            email=True,
        )
        email_mapping[self.user.id] = notif1

        send_immediate_cadence_email(email_mapping, self.course.id)

        # Verify immediate email sent
        assert mock_ace_send.call_count == 1
        assert mock_digest_async.call_count == 0

        notif1.refresh_from_db()
        assert notif1.email_sent_on is not None
        assert notif1.email_scheduled is False

        # SECOND NOTIFICATION - should schedule buffer (5 minutes later)
        with freeze_time("2025-12-15 10:05:00"):
            notif2 = Notification.objects.create(
                user=self.user,
                course_id=self.course.id,
                app_name='discussion',
                notification_type='new_discussion_post',
                content_url='http://example.com',
                content_context=get_new_post_notification_content_context(),
                email=True,
            )
            email_mapping = {self.user.id: notif2}

            send_immediate_cadence_email(email_mapping, self.course.id)

            # Verify buffer scheduled
            assert mock_ace_send.call_count == 1  # Still just 1 immediate email
            assert mock_digest_async.call_count == 1  # Buffer scheduled

            notif2.refresh_from_db()
            assert notif2.email_sent_on is None
            assert notif2.email_scheduled is True

        # THIRD NOTIFICATION - should just mark as scheduled (10 minutes later)
        with freeze_time("2025-12-15 10:10:00"):
            notif3 = Notification.objects.create(
                user=self.user,
                course_id=self.course.id,
                app_name='discussion',
                notification_type='new_discussion_post',
                content_url='http://example.com',
                content_context=get_new_post_notification_content_context(),
                email=True,
            )
            email_mapping = {self.user.id: notif3}

            send_immediate_cadence_email(email_mapping, self.course.id)

            # Verify no new tasks scheduled
            assert mock_ace_send.call_count == 1
            assert mock_digest_async.call_count == 1  # Still just 1 buffer task

            notif3.refresh_from_db()
            assert notif3.email_sent_on is None
            assert notif3.email_scheduled is True

        # BUFFER FIRES - should send digest with notif2 and notif3
        with freeze_time("2025-12-15 10:15:00"):
            send_buffered_digest(  # pylint: disable=no-value-for-parameter
                user_id=self.user.id,
                course_key=str(self.course.id),
                start_date=notif1.email_sent_on,
                user_language='en'
            )

            # Verify digest email sent
            assert mock_ace_send.call_count == 2  # 1 immediate + 1 digest

            # Verify both buffered notifications marked as sent
            notif2.refresh_from_db()
            notif3.refresh_from_db()

            assert notif2.email_sent_on is not None
            assert notif2.email_scheduled is False
            assert notif3.email_sent_on is not None
            assert notif3.email_scheduled is False

    @freeze_time("2025-12-15 10:00:00")
    @patch('openedx.core.djangoapps.notifications.email.tasks.ace.send')
    @override_settings(NOTIFICATION_IMMEDIATE_EMAIL_BUFFER_MINUTES=15)
    def test_notification_after_buffer_expires_sends_immediate(self, mock_ace_send):
        """Test that notification after buffer period sends immediately again."""
        # First notification
        notif1 = Notification.objects.create(
            user=self.user,
            course_id=self.course.id,
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            content_context=get_new_post_notification_content_context(),
            email=True,
        )
        email_mapping = {self.user.id: notif1}

        send_immediate_cadence_email(email_mapping, self.course.id)

        assert mock_ace_send.call_count == 1

        # New notification 20 minutes later (after 15-minute buffer)
        with freeze_time("2025-12-15 10:20:00"):
            notif2 = Notification.objects.create(
                user=self.user,
                course_id=self.course.id,
                app_name='discussion',
                notification_type='new_discussion_post',
                content_url='http://example.com',
                content_context=get_new_post_notification_content_context(),
                email=True,
            )
            email_mapping = {self.user.id: notif2}

            send_immediate_cadence_email(email_mapping, self.course.id)

            # Should send immediate again (buffer expired)
            assert mock_ace_send.call_count == 2

            notif2.refresh_from_db()
            assert notif2.email_sent_on is not None
            assert notif2.email_scheduled is False

    def test_multiple_courses_independent_buffers(self):
        """Test that different courses maintain independent buffers."""
        course2 = CourseFactory.create()

        # Notifications in course 1
        notif1 = Notification.objects.create(  # noqa: F841
            user=self.user,
            course_id=self.course.id,
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            email=True,
            email_sent_on=timezone.now() - timedelta(minutes=5)
        )

        # Notification in course 2 should be independent
        notif2 = Notification.objects.create(
            user=self.user,
            course_id=str(course2.id),
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            email=True,
        )

        decision = decide_email_action(self.user, str(course2.id), notif2)
        assert decision == 'send_immediate'


def get_new_post_notification_content_context(**kwargs):
    """Helper to generate notification content for a new post."""
    return {
        "topic_id": "i4x-edx-eiorguegnru-course-foobarbaz",
        "username": "verified",
        "thread_id": "693fbf23ee2b892eaed49239",
        "comment_id": None,
        "post_title": "Hello world",
        "course_name": "Demonstration Course",
        "response_id": None,
        "replier_name": "verified",
        "email_content": "<p style=\"margin: 0\">Email content</p>",
        **kwargs
    }


@ddt.ddt
class TestGetNextDigestDeliveryTime(ModuleStoreTestCase):
    """Tests for get_next_digest_delivery_time function."""

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)  # Friday 10 AM UTC
    @override_settings(NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR=17, NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE=0)
    def test_daily_delivery_time_later_today(self):
        """Test daily delivery is scheduled for later today if time hasn't passed."""
        delivery_time = get_next_digest_delivery_time(EmailCadence.DAILY)
        assert delivery_time.hour == 17
        assert delivery_time.minute == 0
        assert delivery_time.day == 6  # Today

    @freeze_time("2026-03-06 18:00:00", tz_offset=0)  # Friday 6 PM UTC
    @override_settings(NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR=17, NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE=0)
    def test_daily_delivery_time_tomorrow_if_passed(self):
        """Test daily delivery is scheduled for tomorrow if today's time has passed."""
        delivery_time = get_next_digest_delivery_time(EmailCadence.DAILY)
        assert delivery_time.hour == 17
        assert delivery_time.day == 7  # Tomorrow

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)  # Friday
    @override_settings(
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_DAY=0,  # Monday
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_HOUR=17,
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_MINUTE=0
    )
    def test_weekly_delivery_time_next_monday(self):
        """Test weekly delivery scheduled for next Monday."""
        delivery_time = get_next_digest_delivery_time(EmailCadence.WEEKLY)
        assert delivery_time.weekday() == 0  # Monday
        assert delivery_time.hour == 17
        assert delivery_time.day == 9  # Next Monday (March 9)

    @freeze_time("2026-03-09 10:00:00", tz_offset=0)  # Monday 10 AM UTC
    @override_settings(
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_DAY=0,  # Monday
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_HOUR=17,
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_MINUTE=0
    )
    def test_weekly_delivery_time_today_if_not_passed(self):
        """Test weekly delivery scheduled for today if it's the right day and time hasn't passed."""
        delivery_time = get_next_digest_delivery_time(EmailCadence.WEEKLY)
        assert delivery_time.weekday() == 0  # Monday
        assert delivery_time.day == 9  # Today
        assert delivery_time.hour == 17

    @freeze_time("2026-03-09 18:00:00", tz_offset=0)  # Monday 6 PM UTC
    @override_settings(
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_DAY=0,  # Monday
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_HOUR=17,
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_MINUTE=0
    )
    def test_weekly_delivery_time_next_week_if_passed(self):
        """Test weekly delivery scheduled for next week if today's time has passed."""
        delivery_time = get_next_digest_delivery_time(EmailCadence.WEEKLY)
        assert delivery_time.weekday() == 0  # Monday
        assert delivery_time.day == 16  # Next Monday

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    @override_settings(
        NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR=9,
        NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE=30
    )
    def test_daily_custom_delivery_time(self):
        """Test custom delivery hour and minute from settings."""
        delivery_time = get_next_digest_delivery_time(EmailCadence.DAILY)
        # 9:30 has passed (it's 10:00), so should be tomorrow
        assert delivery_time.day == 7
        assert delivery_time.hour == 9
        assert delivery_time.minute == 30

    def test_invalid_cadence_raises_error(self):
        """Test that invalid cadence type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid cadence_type for digest scheduling"):
            get_next_digest_delivery_time(EmailCadence.IMMEDIATELY)

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    @override_settings(
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_DAY=4,  # Friday
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_HOUR=17,
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_MINUTE=0
    )
    def test_weekly_delivery_same_day_future_time(self):
        """Test weekly delivery on same weekday but later time."""
        delivery_time = get_next_digest_delivery_time(EmailCadence.WEEKLY)
        assert delivery_time.weekday() == 4  # Friday
        assert delivery_time.day == 6  # Today (Friday)
        assert delivery_time.hour == 17


@ddt.ddt
class TestIsDigestAlreadyScheduled(ModuleStoreTestCase):
    """Tests for is_digest_already_scheduled function."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create()

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    def test_no_scheduled_notifications(self):
        """Test returns False when no DigestSchedule record exists."""
        delivery_time = datetime(2026, 3, 6, 17, 0, tzinfo=UTC)
        assert is_digest_already_scheduled(self.user.id, EmailCadence.DAILY, delivery_time) is False

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    def test_has_scheduled_notification(self):
        """Test returns True when a DigestSchedule record exists for the exact delivery_time."""
        delivery_time = datetime(2026, 3, 6, 17, 0, tzinfo=UTC)
        DigestSchedule.objects.create(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=delivery_time,
            task_id='test-task-id',
        )
        assert is_digest_already_scheduled(self.user.id, EmailCadence.DAILY, delivery_time) is True

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    def test_scheduled_notification_outside_window(self):
        """Test returns False when DigestSchedule record has a different delivery_time."""
        delivery_time = datetime(2026, 3, 6, 17, 0, tzinfo=UTC)
        different_delivery_time = datetime(2026, 3, 5, 17, 0, tzinfo=UTC)  # Yesterday
        DigestSchedule.objects.create(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=different_delivery_time,
            task_id='test-task-id',
        )
        assert is_digest_already_scheduled(self.user.id, EmailCadence.DAILY, delivery_time) is False


@ddt.ddt
class TestIsDigestAlreadySentInWindow(ModuleStoreTestCase):
    """Tests for is_digest_already_sent_in_window function."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create()

    def test_no_sent_notifications(self):
        """Test returns False when no digest has been sent."""
        delivery_time = datetime(2026, 3, 6, 17, 0, tzinfo=UTC)
        assert is_digest_already_sent_in_window(self.user.id, EmailCadence.DAILY, delivery_time) is False

    def test_has_sent_notification_in_window(self):
        """Test returns True when digest was already sent in window."""
        delivery_time = datetime(2026, 3, 6, 17, 0, tzinfo=UTC)
        Notification.objects.create(
            user=self.user,
            course_id=str(self.course.id),
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            email=True,
            email_sent_on=datetime(2026, 3, 6, 10, 0, tzinfo=UTC),
        )
        assert is_digest_already_sent_in_window(self.user.id, EmailCadence.DAILY, delivery_time) is True


@ddt.ddt
class TestScheduleBulkDigestEmails(ModuleStoreTestCase):
    """Tests for schedule_bulk_digest_emails function."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create()
        # Patch transaction.on_commit to execute callbacks immediately in tests
        self.on_commit_patcher = patch('django.db.transaction.on_commit', side_effect=lambda func: func())
        self.on_commit_patcher.start()

    def tearDown(self):
        self.on_commit_patcher.stop()
        super().tearDown()

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    @override_settings(NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR=17, NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.send_user_digest_email_task.apply_async')
    def test_schedules_daily_digest_task(self, mock_apply_async):
        """Test that a daily digest task is scheduled when notification exists."""
        Notification.objects.create(
            user=self.user,
            course_id=str(self.course.id),
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            email=True,
            email_scheduled=False,
            email_sent_on=None,
        )

        schedule_bulk_digest_emails({self.user.id: EmailCadence.DAILY})

        assert mock_apply_async.called
        call_kwargs = mock_apply_async.call_args[1]
        assert call_kwargs['kwargs']['user_id'] == self.user.id
        assert call_kwargs['kwargs']['cadence_type'] == EmailCadence.DAILY
        # Should be scheduled for 5 PM UTC today
        assert call_kwargs['eta'].hour == 17
        assert call_kwargs['eta'].day == 6

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    @override_settings(NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR=17, NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.send_user_digest_email_task.apply_async')
    def test_does_not_schedule_if_already_scheduled(self, mock_apply_async):
        """Test that no duplicate task is scheduled when a DigestSchedule record already exists."""
        delivery_time = datetime(2026, 3, 6, 17, 0, tzinfo=UTC)
        DigestSchedule.objects.create(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=delivery_time,
            task_id='existing-task-id',
        )

        schedule_bulk_digest_emails({self.user.id: EmailCadence.DAILY})

        assert not mock_apply_async.called

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.send_user_digest_email_task.apply_async')
    def test_invalid_cadence_does_not_schedule(self, mock_apply_async):
        """Test that IMMEDIATELY cadence does not schedule a digest."""
        schedule_bulk_digest_emails({self.user.id: EmailCadence.IMMEDIATELY})
        assert not mock_apply_async.called

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    @override_settings(
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_DAY=0,
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_HOUR=17,
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_MINUTE=0
    )
    @patch('openedx.core.djangoapps.notifications.email.tasks.send_user_digest_email_task.apply_async')
    def test_schedules_weekly_digest_task(self, mock_apply_async):
        """Test that a weekly digest task is scheduled correctly."""
        Notification.objects.create(
            user=self.user,
            course_id=str(self.course.id),
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            email=True,
            email_scheduled=False,
            email_sent_on=None,
        )

        schedule_bulk_digest_emails({self.user.id: EmailCadence.WEEKLY})

        assert mock_apply_async.called
        call_kwargs = mock_apply_async.call_args[1]
        assert call_kwargs['kwargs']['cadence_type'] == EmailCadence.WEEKLY
        # Should be scheduled for next Monday 5 PM
        assert call_kwargs['eta'].weekday() == 0
        assert call_kwargs['eta'].hour == 17

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    @override_settings(NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR=17, NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.send_user_digest_email_task.apply_async')
    def test_marks_notifications_as_scheduled(self, mock_apply_async):
        """Test that notifications are marked as email_scheduled=True."""
        notif = Notification.objects.create(
            user=self.user,
            course_id=str(self.course.id),
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            email=True,
            email_scheduled=False,
            email_sent_on=None,
        )

        schedule_bulk_digest_emails({self.user.id: EmailCadence.DAILY})

        notif.refresh_from_db()
        assert notif.email_scheduled is True

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    @override_settings(NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR=17, NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.send_user_digest_email_task.apply_async')
    def test_creates_digest_schedule_record(self, mock_apply_async):
        """Test that a DigestSchedule record is created after scheduling."""
        Notification.objects.create(
            user=self.user,
            course_id=str(self.course.id),
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            email=True,
            email_scheduled=False,
            email_sent_on=None,
        )

        schedule_bulk_digest_emails({self.user.id: EmailCadence.DAILY})

        delivery_time = datetime(2026, 3, 6, 17, 0, tzinfo=UTC)
        assert DigestSchedule.objects.filter(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=delivery_time,
        ).exists()

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    @override_settings(NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR=17, NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.send_user_digest_email_task.apply_async')
    def test_empty_map_does_nothing(self, mock_apply_async):
        """Test that an empty user_cadence_map does nothing."""
        schedule_bulk_digest_emails({})
        assert not mock_apply_async.called

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    @override_settings(NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR=17, NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.send_user_digest_email_task.apply_async')
    def test_schedules_for_multiple_users(self, mock_apply_async):
        """Test that digest tasks are scheduled for multiple users in one call."""
        user2 = UserFactory()
        for user in [self.user, user2]:
            Notification.objects.create(
                user=user,
                course_id=str(self.course.id),
                app_name='discussion',
                notification_type='new_discussion_post',
                content_url='http://example.com',
                email=True,
                email_scheduled=False,
                email_sent_on=None,
            )

        schedule_bulk_digest_emails({
            self.user.id: EmailCadence.DAILY,
            user2.id: EmailCadence.DAILY,
        })

        assert mock_apply_async.call_count == 2
        assert DigestSchedule.objects.count() == 2


@ddt.ddt
class TestSendUserDigestEmailTask(ModuleStoreTestCase):
    """Tests for the send_user_digest_email_task celery task."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create(display_name='Test Course')

        NotificationPreference.objects.filter(user=self.user).delete()
        NotificationPreference.objects.create(
            user=self.user,
            app='discussion',
            type='new_discussion_post',
            email=True,
            email_cadence=EmailCadence.DAILY,
        )

    @freeze_time("2026-03-06 17:00:00", tz_offset=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.ace.send')
    def test_sends_digest_email(self, mock_ace_send):
        """Test that digest email is sent successfully."""
        created_time = datetime(2026, 3, 6, 10, 0, tzinfo=UTC)
        Notification.objects.create(
            user=self.user,
            course_id=str(self.course.id),
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            content_context=get_new_post_notification_content_context(),
            email=True,
            email_scheduled=True,
            created=created_time,
        )
        DigestSchedule.objects.create(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=datetime(2026, 3, 6, 17, 0, tzinfo=UTC),
            task_id='test-task-id',
        )

        send_user_digest_email_task(  # pylint: disable=no-value-for-parameter
            user_id=self.user.id,
            cadence_type=EmailCadence.DAILY,
        )

        assert mock_ace_send.called

    @freeze_time("2026-03-06 17:00:00", tz_offset=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.ace.send')
    def test_skips_if_already_sent_by_cron(self, mock_ace_send):
        """Test that digest is skipped if cron already sent it."""
        created_time = datetime(2026, 3, 6, 10, 0, tzinfo=UTC)
        Notification.objects.create(
            user=self.user,
            course_id=str(self.course.id),
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            content_context=get_new_post_notification_content_context(),
            email=True,
            email_scheduled=True,
            email_sent_on=datetime(2026, 3, 6, 15, 0, tzinfo=UTC),  # Already sent by cron
            created=created_time,
        )
        DigestSchedule.objects.create(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=datetime(2026, 3, 6, 17, 0, tzinfo=UTC),
            task_id='test-task-id',
        )

        send_user_digest_email_task(  # pylint: disable=no-value-for-parameter
            user_id=self.user.id,
            cadence_type=EmailCadence.DAILY,
        )

        assert not mock_ace_send.called

    @freeze_time("2026-03-06 17:00:00", tz_offset=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.ace.send')
    def test_clears_scheduled_flags_after_send(self, mock_ace_send):
        """Test that email_scheduled flags are cleared after successful send."""
        created_time = datetime(2026, 3, 6, 10, 0, tzinfo=UTC)
        notif = Notification.objects.create(
            user=self.user,
            course_id=str(self.course.id),
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            content_context=get_new_post_notification_content_context(),
            email=True,
            email_scheduled=True,
            created=created_time,
        )
        DigestSchedule.objects.create(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=datetime(2026, 3, 6, 17, 0, tzinfo=UTC),
            task_id='test-task-id',
        )

        send_user_digest_email_task(  # pylint: disable=no-value-for-parameter
            user_id=self.user.id,
            cadence_type=EmailCadence.DAILY,
        )

        notif.refresh_from_db()
        assert notif.email_scheduled is False
        assert not DigestSchedule.objects.filter(user=self.user, cadence_type=EmailCadence.DAILY).exists()

    @freeze_time("2026-03-06 17:00:00", tz_offset=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.ace.send')
    def test_skips_disabled_user(self, mock_ace_send):
        """Test that digest is not sent to disabled user and DigestSchedule is cleaned up."""
        self.user.set_unusable_password()
        self.user.save()

        created_time = datetime(2026, 3, 6, 10, 0, tzinfo=UTC)
        Notification.objects.create(
            user=self.user,
            course_id=str(self.course.id),
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            content_context=get_new_post_notification_content_context(),
            email=True,
            email_scheduled=True,
            created=created_time,
        )
        DigestSchedule.objects.create(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=datetime(2026, 3, 6, 17, 0, tzinfo=UTC),
            task_id='test-task-id',
        )
        send_user_digest_email_task(  # pylint: disable=no-value-for-parameter
            user_id=self.user.id,
            cadence_type=EmailCadence.DAILY,
        )

        assert not mock_ace_send.called
        # Verify DigestSchedule was cleaned up even though user is disabled
        assert not DigestSchedule.objects.filter(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=datetime(2026, 3, 6, 17, 0, tzinfo=UTC),
        ).exists()

    @freeze_time("2026-03-06 17:00:00", tz_offset=0)
    def test_handles_missing_user(self):
        """Test that task handles non-existent user gracefully and cleans up DigestSchedule."""
        # Create a DigestSchedule record for the non-existent user
        DigestSchedule.objects.create(
            user_id=99999,
            cadence_type=EmailCadence.DAILY,
            delivery_time=datetime(2026, 3, 6, 17, 0, tzinfo=UTC),
            task_id='orphan-task-id',
        )
        # Should not raise
        send_user_digest_email_task(  # pylint: disable=no-value-for-parameter
            user_id=99999,
            cadence_type=EmailCadence.DAILY,
        )

        # Verify orphaned DigestSchedule was cleaned up
        assert not DigestSchedule.objects.filter(user_id=99999).exists()

    @freeze_time("2026-03-06 17:00:00", tz_offset=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.ace.send')
    def test_clears_scheduled_flags_even_when_cron_sent(self, mock_ace_send):
        """Test that scheduled flags and DigestSchedule record are cleared even when cron already sent."""
        created_time = datetime(2026, 3, 6, 10, 0, tzinfo=UTC)
        notif = Notification.objects.create(
            user=self.user,
            course_id=str(self.course.id),
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            content_context=get_new_post_notification_content_context(),
            email=True,
            email_scheduled=True,
            email_sent_on=datetime(2026, 3, 6, 15, 0, tzinfo=UTC),  # Sent by cron
            created=created_time,
        )
        DigestSchedule.objects.create(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=datetime(2026, 3, 6, 17, 0, tzinfo=UTC),
            task_id='test-task-id',
        )
        send_user_digest_email_task(  # pylint: disable=no-value-for-parameter
            user_id=self.user.id,
            cadence_type=EmailCadence.DAILY,
        )

        notif.refresh_from_db()
        assert notif.email_scheduled is False
        assert not mock_ace_send.called
        assert not DigestSchedule.objects.filter(user=self.user, cadence_type=EmailCadence.DAILY).exists()


@ddt.ddt
class TestDigestSchedulingIntegration(ModuleStoreTestCase):
    """Integration tests for the full digest scheduling flow."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create(display_name='Test Course')

        NotificationPreference.objects.filter(user=self.user).delete()
        NotificationPreference.objects.create(
            user=self.user,
            app='discussion',
            type='new_discussion_post',
            email=True,
            email_cadence=EmailCadence.DAILY,
        )
        # Patch transaction.on_commit to execute callbacks immediately in tests
        self.on_commit_patcher = patch('django.db.transaction.on_commit', side_effect=lambda func: func())
        self.on_commit_patcher.start()

    def tearDown(self):
        self.on_commit_patcher.stop()
        super().tearDown()

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    @override_settings(NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR=17, NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.send_user_digest_email_task.apply_async')
    def test_notification_triggers_digest_scheduling(self, mock_apply_async):
        """Test that creating a notification triggers digest scheduling via send_notifications."""
        context = {
            'username': 'User',
            'post_title': 'Test Post'
        }
        send_notifications(
            [self.user.id],
            str(self.course.id),
            'discussion',
            'new_discussion_post',
            context,
            'http://test.url'
        )

        # A digest task should have been scheduled
        assert mock_apply_async.called

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    @override_settings(NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR=17, NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.send_user_digest_email_task.apply_async')
    def test_multiple_notifications_schedule_only_once(self, mock_apply_async):
        """Test that multiple notifications in same window only schedule one task."""
        context = {
            'username': 'User',
            'post_title': 'Test Post'
        }

        send_notifications(
            [self.user.id],
            str(self.course.id),
            'discussion',
            'new_discussion_post',
            context.copy(),
            'http://test.url'
        )
        send_notifications(
            [self.user.id],
            str(self.course.id),
            'discussion',
            'new_discussion_post',
            context.copy(),
            'http://test.url'
        )

        # Should be called only once because second time notifications are already scheduled
        assert mock_apply_async.call_count == 1

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    @override_settings(NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR=17, NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE=0)
    @patch('openedx.core.djangoapps.notifications.email.tasks.ace.send')
    @patch('openedx.core.djangoapps.notifications.email.tasks.send_user_digest_email_task.apply_async')
    def test_immediate_cadence_does_not_trigger_digest(self, mock_digest_async, mock_ace_send):
        """Test that immediate cadence users don't get digest scheduled."""
        NotificationPreference.objects.filter(user=self.user).delete()
        NotificationPreference.objects.create(
            user=self.user,
            app='discussion',
            type='new_discussion_post',
            email=True,
            email_cadence=EmailCadence.IMMEDIATELY,
        )

        context = {
            'username': 'User',
            'post_title': 'Test Post'
        }

        send_notifications(
            [self.user.id],
            str(self.course.id),
            'discussion',
            'new_discussion_post',
            context,
            'http://test.url'
        )

        # Immediate email should be sent, NOT a digest scheduled
        assert mock_ace_send.called
        assert not mock_digest_async.called


@ddt.ddt
class TestGetNextDigestDeliveryTimeSettingsValidation(ModuleStoreTestCase):
    """Tests for settings validation in get_next_digest_delivery_time."""

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    @override_settings(NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR=25, NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE=99)
    def test_daily_invalid_settings_clamped(self):
        """Test that invalid hour/minute values are clamped to valid ranges."""
        delivery_time = get_next_digest_delivery_time(EmailCadence.DAILY)
        assert delivery_time.hour == 23  # clamped from 25
        assert delivery_time.minute == 59  # clamped from 99

    @freeze_time("2026-03-06 10:00:00", tz_offset=0)
    @override_settings(
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_DAY=10,
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_HOUR=-1,
        NOTIFICATION_WEEKLY_DIGEST_DELIVERY_MINUTE=-5,
    )
    def test_weekly_invalid_settings_clamped(self):
        """Test that invalid day/hour/minute values are clamped to valid ranges."""
        delivery_time = get_next_digest_delivery_time(EmailCadence.WEEKLY)
        assert delivery_time.weekday() == 6  # clamped from 10 → min(6, max(0, 10)) = 6 (Sunday)
        assert delivery_time.hour == 0  # clamped from -1
        assert delivery_time.minute == 0  # clamped from -5


@ddt.ddt
class TestClaimDigestSchedule(ModuleStoreTestCase):
    """Tests for _claim_digest_schedule."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()

    @freeze_time("2026-03-06 17:00:00", tz_offset=0)
    def test_claims_current_window_record(self):
        """Test that the current window's DigestSchedule record is deleted and returns True."""
        DigestSchedule.objects.create(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=datetime(2026, 3, 6, 17, 0, tzinfo=UTC),
            task_id='current-task-id',
        )

        result = _claim_digest_schedule(self.user.id, EmailCadence.DAILY)

        assert result is True
        assert not DigestSchedule.objects.filter(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=datetime(2026, 3, 6, 17, 0, tzinfo=UTC),
        ).exists()

    @freeze_time("2026-03-06 17:00:00", tz_offset=0)
    def test_second_claim_returns_false(self):
        """Test that a second claim attempt returns False (dedup)."""
        DigestSchedule.objects.create(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=datetime(2026, 3, 6, 17, 0, tzinfo=UTC),
            task_id='current-task-id',
        )

        first = _claim_digest_schedule(self.user.id, EmailCadence.DAILY)
        second = _claim_digest_schedule(self.user.id, EmailCadence.DAILY)

        assert first is True
        assert second is False

    @freeze_time("2026-03-06 17:00:00", tz_offset=0)
    def test_preserves_future_window_record(self):
        """Test that a future window's DigestSchedule record is NOT deleted."""
        DigestSchedule.objects.create(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=datetime(2026, 3, 6, 17, 0, tzinfo=UTC),
            task_id='current-task-id',
        )
        DigestSchedule.objects.create(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=datetime(2026, 3, 7, 17, 0, tzinfo=UTC),
            task_id='future-task-id',
        )

        result = _claim_digest_schedule(self.user.id, EmailCadence.DAILY)

        assert result is True
        assert not DigestSchedule.objects.filter(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=datetime(2026, 3, 6, 17, 0, tzinfo=UTC),
        ).exists()
        assert DigestSchedule.objects.filter(
            user=self.user,
            cadence_type=EmailCadence.DAILY,
            delivery_time=datetime(2026, 3, 7, 17, 0, tzinfo=UTC),
        ).exists()

    @freeze_time("2026-03-09 17:00:00", tz_offset=0)
    def test_weekly_preserves_future_record(self):
        """Test that weekly cleanup preserves next week's record."""
        DigestSchedule.objects.create(
            user=self.user,
            cadence_type=EmailCadence.WEEKLY,
            delivery_time=datetime(2026, 3, 9, 17, 0, tzinfo=UTC),
            task_id='current-task-id',
        )
        DigestSchedule.objects.create(
            user=self.user,
            cadence_type=EmailCadence.WEEKLY,
            delivery_time=datetime(2026, 3, 16, 17, 0, tzinfo=UTC),
            task_id='next-week-task-id',
        )

        result = _claim_digest_schedule(self.user.id, EmailCadence.WEEKLY)

        assert result is True
        assert not DigestSchedule.objects.filter(
            user=self.user,
            delivery_time=datetime(2026, 3, 9, 17, 0, tzinfo=UTC),
        ).exists()
        assert DigestSchedule.objects.filter(
            user=self.user,
            delivery_time=datetime(2026, 3, 16, 17, 0, tzinfo=UTC),
        ).exists()
