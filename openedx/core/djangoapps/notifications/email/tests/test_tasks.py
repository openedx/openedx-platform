"""
Test cases for notifications/email/tasks.py
"""
import datetime
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import ddt
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from edx_toggles.toggles.testutils import override_waffle_flag
from freezegun import freeze_time

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.notifications.email.tasks import (
    add_to_existing_buffer,
    decide_email_action,
    get_audience_for_cadence_email,
    schedule_digest_buffer,
    send_buffered_digest,
    send_digest_email_to_all_users,
    send_digest_email_to_user,
    send_immediate_cadence_email,
    send_immediate_email
)
from openedx.core.djangoapps.notifications.email.utils import get_start_end_date
from openedx.core.djangoapps.notifications.email_notifications import EmailCadence
from openedx.core.djangoapps.notifications.models import Notification, NotificationPreference
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
class TestEmailDigestAudience(ModuleStoreTestCase):
    """
    Tests audience for notification digest email
    """

    def setUp(self):
        """
        Setup
        """
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create(display_name='test course', run="Testing_course")

    @patch('openedx.core.djangoapps.notifications.email.tasks.send_digest_email_to_user')
    def test_email_func_not_called_if_no_notification(self, mock_func):
        """
        Tests email sending function is not called if user has no notifications
        """
        send_digest_email_to_all_users(EmailCadence.DAILY)
        assert not mock_func.called

    @patch('openedx.core.djangoapps.notifications.email.tasks.send_digest_email_to_user')
    def test_email_func_called_if_user_has_notification(self, mock_func):
        """
        Tests email sending function is called if user has notification
        """
        created_date = datetime.now() - timedelta(days=1)
        create_notification(self.user, self.course.id, created=created_date)
        send_digest_email_to_all_users(EmailCadence.DAILY)
        assert mock_func.called

    @patch('openedx.core.djangoapps.notifications.email.tasks.send_digest_email_to_user')
    def test_email_func_not_called_if_user_notification_is_not_duration(self, mock_func):
        """
        Tests email sending function is not called if user has notification
        which is not in duration
        """
        created_date = datetime.now() - timedelta(days=10)
        create_notification(self.user, self.course.id, created=created_date)
        send_digest_email_to_all_users(EmailCadence.DAILY)
        assert not mock_func.called

    @patch('edx_ace.ace.send')
    def test_email_is_sent_to_user_when_task_is_called(self, mock_func):
        created_date = datetime.now() - timedelta(days=1)
        create_notification(self.user, self.course.id, created=created_date)
        send_digest_email_to_all_users(EmailCadence.DAILY)
        assert mock_func.called
        assert mock_func.call_count == 1

    def test_audience_query_count(self):
        with self.assertNumQueries(1):
            audience = get_audience_for_cadence_email(EmailCadence.DAILY)
            list(audience)  # evaluating queryset

    @ddt.data(True, False)
    @patch('edx_ace.ace.send')
    def test_digest_should_contain_email_enabled_notifications(self, email_value, mock_func):
        """
        Tests email is sent only when notifications with email=True exists
        """
        start_date, end_date = get_start_end_date(EmailCadence.DAILY)
        created_date = datetime.now() - timedelta(hours=23, minutes=59)
        create_notification(self.user, self.course.id, created=created_date, email=email_value)
        send_digest_email_to_user(self.user, EmailCadence.DAILY, start_date, end_date)
        assert mock_func.called is email_value


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

    @patch('edx_ace.ace.send')
    def test_email_not_sent_when_cadence_is_not_immediate(self, mock_ace_send):
        """
        Tests that an email is NOT sent via send_notifications when cadence is DAILY.
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
    @override_settings(NOTIFICATION_EMAIL_BUFFER_MINUTES=15)
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
        with patch('openedx.core.djangoapps.notifications.email.tasks.logger') as mock_logger:
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
    @override_settings(NOTIFICATION_EMAIL_BUFFER_MINUTES=15)
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
        """Test that only email_scheduled field is updated."""
        notification = Notification.objects.create(
            user=self.user,
            course_id=str(self.course.id),
            app_name='discussion',
            notification_type='new_discussion_post',
            content_url='http://example.com',
            email=True,
            content_context=get_new_post_notification_content_context()
        )

        add_to_existing_buffer(notification)

        notification.refresh_from_db()
        assert 'Hello world' in notification.content
        assert notification.email_scheduled is True


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

        with self.assertRaises(Exception):
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
    @override_settings(NOTIFICATION_EMAIL_BUFFER_MINUTES=15)
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
    @override_settings(NOTIFICATION_EMAIL_BUFFER_MINUTES=15)
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
        notif1 = Notification.objects.create(
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
