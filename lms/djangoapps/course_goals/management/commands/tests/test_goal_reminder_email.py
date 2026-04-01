"""Tests for the goal_reminder_email command"""
import uuid
from datetime import datetime

from botocore.exceptions import NoCredentialsError
from django.contrib.sites.models import Site
from edx_ace import Recipient, Message
from pytz import UTC
from unittest import mock  # lint-amnesty, pylint: disable=wrong-import-order

import ddt
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings
from edx_toggles.toggles.testutils import override_waffle_flag
from freezegun import freeze_time
from waffle import get_waffle_flag_model  # pylint: disable=invalid-django-waffle-import

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.course_goals.management.commands.goal_reminder_email import send_email_using_ses, send_ace_message
from lms.djangoapps.course_goals.models import CourseGoalReminderStatus
from lms.djangoapps.course_goals.tests.factories import (
    CourseGoalFactory, CourseGoalReminderStatusFactory, UserActivityFactory,
)
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.core.lib.celery.task_utils import emulate_http_request
from openedx.features.course_experience import ENABLE_COURSE_GOALS, ENABLE_SES_FOR_GOALREMINDER

# Some constants just for clarity of tests (assuming week starts on a Monday, as March 2021 used below does)
MONDAY = 0
TUESDAY = 1
WEDNESDAY = 2
THURSDAY = 3
FRIDAY = 4
SATURDAY = 5
SUNDAY = 6


@ddt.ddt
@skip_unless_lms
@override_waffle_flag(ENABLE_COURSE_GOALS, active=True)
class TestGoalReminderEmailCommand(TestCase):
    """
    Test goal_reminder_email management command.

    A lot of these methods will hardcode references to March 2021. This is just a convenient anchor point for us
    because it started on a Monday. Calls to the management command will freeze time so it's during March.
    """

    def make_valid_goal(self, **kwargs):
        """Creates a goal that will cause an email to be sent as the goal is valid but has been missed"""
        kwargs.setdefault('days_per_week', 6)
        kwargs.setdefault('subscribed_to_reminders', True)
        kwargs.setdefault('overview__start', datetime(2021, 1, 1, tzinfo=UTC))
        kwargs.setdefault('overview__end', datetime(2021, 4, 1, tzinfo=UTC))  # Have it end in the future
        goal = CourseGoalFactory(**kwargs)

        with freeze_time('2021-02-01 10:00:00'):  # Create enrollment before March
            CourseEnrollmentFactory(user=goal.user, course_id=goal.course_key)

        return goal

    def call_command(self, day=TUESDAY, expect_sent=None, expect_send_count=None, time=None):
        """Calls the management command with a frozen time and optionally checks whether we sent an email"""
        with mock.patch('lms.djangoapps.course_goals.management.commands.goal_reminder_email.send_ace_message') as mock_send:  # pylint: disable=line-too-long
            with freeze_time(time or f'2021-03-0{day + 1} 10:00:00'):  # March 2021 starts on a Monday
                call_command('goal_reminder_email')

        if expect_sent is not None:
            assert CourseGoalReminderStatus.objects.filter(email_reminder_sent=True).exists() == expect_sent

            if expect_send_count is None:
                expect_send_count = 1 if expect_sent else 0
            assert mock_send.call_count == expect_send_count

    def test_happy_path(self):
        """Confirm that with default arguments, our test methods send an email"""
        # A lot of our "negative" tests below assume that these methods called with these arguments will give you a
        # working "email sent" state. And then tweak one thing to confirm it didn't send.
        # So, if you change this method at all, go also change the "failure case" tests below to match.
        self.make_valid_goal()
        self.call_command(expect_sent=True)

    def test_clear_all_on_monday(self):
        """Verify that we reset all email tracking on Monday"""
        CourseGoalReminderStatusFactory(email_reminder_sent=True)
        CourseGoalReminderStatusFactory(email_reminder_sent=False)
        self.call_command(MONDAY)
        assert CourseGoalReminderStatus.objects.filter(email_reminder_sent=False).count() == 2
        assert CourseGoalReminderStatus.objects.filter(email_reminder_sent=True).count() == 0

    @ddt.data(
        (4, 5, SATURDAY, False),  # Already made target
        (2, 0, FRIDAY, False),  # Just before end of week cutoff
        (2, 0, SATURDAY, True),  # Just after end of week cutoff
        (2, 0, SUNDAY, False),  # Day after end of week cutoff
        (2, 1, SATURDAY, False),  # With some activity in the bag already, that cutoff moves up
        (2, 1, SUNDAY, True),  # ...to Sunday
        (7, 3, WEDNESDAY, False),  # Same as above, but with some more interesting numbers
        (7, 3, THURSDAY, True),  # ditto (after cutoff)
        (7, 3, FRIDAY, False),  # ditto (day after)
        (7, 0, MONDAY, False),  # We never send on Mondays, only clear
        # Here are some unrealistic edge cases - just want to make sure we don't blow up with an exception
        (9, 1, TUESDAY, False),
        (9, 9, TUESDAY, False),
        (1, 9, TUESDAY, False),
    )
    @ddt.unpack
    def test_will_send_on_right_day(self, days_per_week, days_of_activity, current_day, expect_sent):
        """Verify that via the normal conditions, we either send or not based on the days of activity"""
        goal = self.make_valid_goal(days_per_week=days_per_week)
        for day in range(days_of_activity):
            UserActivityFactory(user=goal.user, course_key=goal.course_key, date=datetime(2021, 3, day + 1, tzinfo=UTC))

        self.call_command(day=current_day, expect_sent=expect_sent)

    def test_will_send_at_the_right_time(self):
        """ We only send the emails during the day in the user's time"""
        self.make_valid_goal()
        self.call_command(expect_sent=False, time='2021-03-02 6:00:00')
        self.call_command(expect_sent=True, time='2021-03-02 10:00:00')

    def test_feature_disabled(self):
        self.make_valid_goal()
        with override_waffle_flag(ENABLE_COURSE_GOALS, active=False):
            self.call_command(expect_sent=False)

    def test_feature_enabled_for_user(self):
        goal = self.make_valid_goal()
        with override_waffle_flag(ENABLE_COURSE_GOALS, active=None):
            # We want to ensure that when we set up a fake request
            # it works correctly if the flag is only enabled for specific users
            flag = get_waffle_flag_model().get(ENABLE_COURSE_GOALS.name)
            flag.users.add(goal.user)
            self.call_command(expect_sent=True)

    def test_never_enrolled(self):
        self.make_valid_goal()
        CourseEnrollment.objects.all().delete()
        self.call_command(expect_sent=False)

    def test_inactive_enrollment(self):
        self.make_valid_goal()
        CourseEnrollment.objects.update(is_active=False)
        self.call_command(expect_sent=False)

    def test_recent_enrollment(self):
        self.make_valid_goal()
        CourseEnrollment.objects.update(created=datetime(2021, 3, 1, tzinfo=UTC))
        self.call_command(expect_sent=False)

    @ddt.data(
        (datetime(2021, 3, 8, tzinfo=UTC), True),
        (datetime(2021, 3, 7, tzinfo=UTC), False),
    )
    @ddt.unpack
    @mock.patch('lms.djangoapps.course_goals.management.commands.goal_reminder_email.get_user_course_expiration_date')
    def test_access_expired(self, expiration_date, expect_sent, mock_get_expiration_date):
        self.make_valid_goal()
        mock_get_expiration_date.return_value = expiration_date  # awkward to set up, so just mock it
        self.call_command(expect_sent=expect_sent)

    def test_cert_already_generated(self):
        goal = self.make_valid_goal()
        GeneratedCertificateFactory(user=goal.user, course_id=goal.course_key, status=CertificateStatuses.downloadable)
        self.call_command(expect_sent=False)

    def test_unsubscribed(self):
        self.make_valid_goal(subscribed_to_reminders=False)
        self.call_command(expect_sent=False)

    def test_no_double_sends(self):
        self.make_valid_goal()
        self.call_command(expect_sent=True, expect_send_count=1)
        self.call_command(expect_sent=True, expect_send_count=0)

    def test_no_days_per_week(self):
        self.make_valid_goal(days_per_week=0)
        self.call_command(expect_sent=False)

    @ddt.data(
        datetime(2021, 2, 1, tzinfo=UTC),  # very over and done with
        datetime(2021, 3, 7, tzinfo=UTC),  # ending this Sunday
    )
    def test_old_course(self, end):
        self.make_valid_goal(overview__end=end)
        self.call_command(expect_sent=False)

    @mock.patch('lms.djangoapps.course_goals.management.commands.goal_reminder_email.send_email_using_ses')
    def test_params_with_ses(self, mock_send_email_using_ses):
        """Test that the parameters of the msg passed to ace.send() are set correctly when SES is enabled"""
        with override_waffle_flag(ENABLE_SES_FOR_GOALREMINDER, active=None):
            goal = self.make_valid_goal()
            flag = get_waffle_flag_model().get(ENABLE_SES_FOR_GOALREMINDER.name)
            flag.users.add(goal.user)

            with freeze_time('2021-03-02 10:00:00'):
                call_command('goal_reminder_email')

            assert mock_send_email_using_ses.call_count == 1
            msg = mock_send_email_using_ses.call_args[0][1]
            assert msg.options['override_default_channel'] == 'django_email'
            assert msg.options['from_address'] == settings.LMS_COMM_DEFAULT_FROM_EMAIL

    @mock.patch('lms.djangoapps.course_goals.management.commands.goal_reminder_email.ace.send')
    def test_params_without_ses(self, mock_ace):
        """Test that the parameters of the msg passed to ace.send() are set correctly when SES is not enabled"""
        self.make_valid_goal()

        with freeze_time('2021-03-02 10:00:00'):
            call_command('goal_reminder_email')

        assert mock_ace.call_count == 1
        msg = mock_ace.call_args[0][0]
        assert msg.options['transactional'] is True
        assert 'override_default_channel' not in msg.options
        assert 'from_address' not in msg.options

    @ddt.data(True, False)
    @mock.patch('lms.djangoapps.course_goals.management.commands.goal_reminder_email.ace.send')
    def test_goal_reminder_email_sent_to_disable_user(self, value, mock_ace):
        """
        Test that the goal reminder email is not sent to disabled users.
        """
        goal = self.make_valid_goal()
        if value:
            goal.user.set_password("12345678")
        else:
            goal.user.set_unusable_password()
        goal.user.save()
        send_ace_message(goal, str(uuid.uuid4()))
        assert mock_ace.called is value

    def test_goals_unsubscribe_url_uses_site_config(self):
        """Test goals unsubscribe URL uses site-configured MFE base."""
        goal = self.make_valid_goal()
        with mock.patch('lms.djangoapps.course_goals.management.commands.goal_reminder_email.ace.send') as mock_ace:
            with mock.patch(
                'lms.djangoapps.course_goals.management.commands.goal_reminder_email.configuration_helpers.get_value',
                return_value='https://learning.siteconf',
            ) as mock_get_value:
                assert send_ace_message(goal, str(uuid.uuid4())) is True

        assert mock_ace.call_count == 1
        msg = mock_ace.call_args[0][0]
        assert msg.context[
            'goals_unsubscribe_url'
        ] == f'https://learning.siteconf/goal-unsubscribe/{goal.unsubscribe_token}'
        mock_get_value.assert_any_call('LEARNING_MICROFRONTEND_URL', settings.LEARNING_MICROFRONTEND_URL)

    def test_goals_unsubscribe_url_falls_back_to_settings(self):
        """Test goals unsubscribe URL falls back to settings when site config is absent."""
        default_url = 'https://learning.default'
        goal = self.make_valid_goal()
        with override_settings(LEARNING_MICROFRONTEND_URL=default_url):
            with mock.patch(
                'lms.djangoapps.course_goals.management.commands.goal_reminder_email.ace.send',
            ) as mock_ace:
                with mock.patch(
                    (
                        'lms.djangoapps.course_goals.management.commands.'
                        'goal_reminder_email.configuration_helpers.get_value'
                    ),
                    side_effect=lambda k, d: d,
                ) as mock_get_value:
                    assert send_ace_message(goal, str(uuid.uuid4())) is True

        assert mock_ace.call_count == 1
        msg = mock_ace.call_args[0][0]
        assert msg.context['goals_unsubscribe_url'] == f'{default_url}/goal-unsubscribe/{goal.unsubscribe_token}'
        mock_get_value.assert_any_call('LEARNING_MICROFRONTEND_URL', default_url)


class TestGoalReminderEmailSES(TestCase):
    """
    Tests for the send_email_using_ses function
    """
    def test_send_email_using_ses(self):
        """
        Test that the send_email_using_ses function sends an email using the SES channel
        """
        user = UserFactory()

        options = {
            'transactional': True,
            'from_address': settings.LMS_COMM_DEFAULT_FROM_EMAIL,
            'override_default_channel': 'django_email',
        }
        site = Site.objects.get_current()
        message_context = get_base_template_context(site)
        message_context.update({
            'email': user.email,
            'platform_name': 'edx',
            'course_name': 'zombie survival',
            'course_id': 'course.101',
            'days_per_week': 3,
            'course_url': 'test.com',
            'goals_unsubscribe_url': 'test.com',
            'image_url': 'test',
            'unsubscribe_url': None,
            'omit_unsubscribe_link': True,
            'courses_url': 'course.example.com',
            'programs_url': 'course.example.com',
        })
        with emulate_http_request(site, user):
            msg = Message(
                name="goalreminder",
                app_label="course_goals",
                recipient=Recipient(user.id, user.email),
                language='en',
                context=message_context,
                options=options,
            )
            # expect an exception here
            with self.assertRaises(NoCredentialsError):
                send_email_using_ses(user, msg)
