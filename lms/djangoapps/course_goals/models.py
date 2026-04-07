"""
Course Goals Models
"""

import logging
import uuid
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.db import models
from edx_django_utils.cache import TieredCache
from model_utils import Choices
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField
from simple_history.models import HistoricalRecords

from lms.djangoapps.courseware.context_processor import get_user_timezone_or_last_seen_timezone_or_utc
from lms.djangoapps.courseware.masquerade import is_masquerading
from openedx.core.lib.mobile_utils import is_request_from_mobile_app
from openedx.features.course_experience import ENABLE_COURSE_GOALS

_GOAL_KEY_CHOICES = Choices(
    ('certify', 'Earn a certificate'),
    ('complete', 'Complete the course'),
    ('explore', 'Explore the course'),
    ('unsure', 'Not sure yet'),
)

User = get_user_model()
log = logging.getLogger(__name__)


class CourseGoal(models.Model):
    """
    Represents a course goal set by a user on the course home page.

    .. no_pii:
    """
    class Meta:
        app_label = 'course_goals'
        unique_together = ('user', 'course_key')

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    course_key = CourseKeyField(max_length=255, db_index=True)
    # The goal a user has set for the number of days they want to learn per week
    days_per_week = models.PositiveIntegerField(default=0)

    # Controls whether a user will receive emails reminding them to stay on track with their learning goal
    subscribed_to_reminders = models.BooleanField(default=False)

    # With this token, anyone can unsubscribe this user from reminders. That's a mild enough action that we don't stress
    # about the risk of keeping this key around long term in the database or bother using a higher-security generator
    # than uuid4. The worst someone can do with this is unsubscribe us. And we want old tokens sitting in folks' email
    # inboxes to still be valid as long as possible.
    unsubscribe_token = models.UUIDField(null=True, blank=True, unique=True, editable=False, default=uuid.uuid4,
                                         help_text='Used to validate unsubscribe requests without requiring a login')

    # Deprecated and unused - replaced by days_per_week and its subscription-based approach to goals
    goal_key = models.CharField(max_length=100, choices=_GOAL_KEY_CHOICES, default=_GOAL_KEY_CHOICES.unsure)

    history = HistoricalRecords()

    def __str__(self):
        return 'CourseGoal: {user} set goal to {goal} days per week for course {course}'.format(
            user=self.user.username,
            goal=self.days_per_week,
            course=self.course_key,
        )

    def save(self, **kwargs):  # pylint: disable=arguments-differ
        # Ensure we have an unsubscribe token (lazy migration from old goals, before this field was added)
        if self.unsubscribe_token is None:
            self.unsubscribe_token = uuid.uuid4()
        super().save(**kwargs)


class CourseGoalReminderStatus(TimeStampedModel):
    """
    Tracks whether we've sent a reminder about a particular goal this week.

    See the management command goal_reminder_email for more detail about how this is used.

    .. no_pii:
    """
    class Meta:
        verbose_name_plural = "Course goal reminder statuses"

    goal = models.OneToOneField(CourseGoal, on_delete=models.CASCADE, related_name='reminder_status')
    email_reminder_sent = models.BooleanField(
        default=False, help_text='Tracks if the email reminder to complete the Course Goal has been sent this week.'
    )


class UserActivity(models.Model):
    """
    Tracks the date a user performs an activity in a course for goal purposes.
    To be used in conjunction with the CourseGoal model to establish if a learner is hitting
    their desired days_per_week.

    To start, this model will only be tracking page views that count towards a learner's goal,
    but could grow to tracking other types of goal achieving activities in the future.

    .. no_pii:
    """
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'course_key', 'date'], name='unique_user_course_date')]
        indexes = [models.Index(fields=['user', 'course_key'], name='user_course_index')]
        verbose_name_plural = 'User activities'

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    course_key = CourseKeyField(max_length=255)
    date = models.DateField()

    @classmethod
    def record_user_activity(cls, user, course_key, request=None, only_if_mobile_app=False):
        '''
        Update the user activity table with a record for this activity.

        Since we store one activity per date, we don't need to query the database
        for every activity on a given date.
        To avoid unnecessary queries, we store a record in a cache once we have an activity for the date,
        which times out at the end of that date (in the user's timezone).

        The request argument is only used to check if the request is coming from a mobile app.
        Once the only_if_mobile_app argument is removed the request argument can be removed as well.

        The return value is the id of the object that was created, or retrieved.
        A return value of None signifies that a user activity record was not stored or retrieved
        '''
        if not ENABLE_COURSE_GOALS.is_enabled(course_key):
            return None

        if not (user and user.id) or not course_key:
            return None

        if only_if_mobile_app and request and not is_request_from_mobile_app(request):
            return None

        if is_masquerading(user, course_key):
            return None

        timezone = get_user_timezone_or_last_seen_timezone_or_utc(user)
        now = datetime.now(timezone)
        date = now.date()

        cache_key = 'goals_user_activity_{}_{}_{}'.format(str(user.id), str(course_key), str(date))

        cached_value = TieredCache.get_cached_response(cache_key)
        if cached_value.is_found:
            return cached_value.value, False

        activity_object, __ = cls.objects.get_or_create(user=user, course_key=course_key, date=date)

        # Cache result until the end of the day to avoid unnecessary database requests
        tomorrow = now + timedelta(days=1)
        midnight = datetime(year=tomorrow.year, month=tomorrow.month,
                            day=tomorrow.day, hour=0, minute=0, second=0, tzinfo=timezone)
        seconds_until_midnight = (midnight - now).seconds

        TieredCache.set_all_tiers(cache_key, activity_object.id, seconds_until_midnight)
        # Temporary debugging log for testing mobile app connection
        if request:
            log.info(
                'Set cached value with request {} for user and course combination {} {}'.format(
                    str(request.build_absolute_uri()), str(user.id), str(course_key)
                )
            )
        return activity_object.id
