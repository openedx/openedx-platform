"""
Models for Calendar Sync
"""


from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db import models
from opaque_keys.edx.django.models import CourseKeyField
from simple_history.models import HistoricalRecords


class UserCalendarSyncConfig(models.Model):  # noqa: DJ008
    """
    Model to track if a user has the calendar integration enabled for a specific Course

    .. no_pii:
    """
    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    course_key = CourseKeyField(max_length=255, db_index=True)
    enabled = models.BooleanField(default=False)
    ics_sequence = models.IntegerField(default=0)

    history = HistoricalRecords()

    class Meta:
        unique_together = ('user', 'course_key',)

    @classmethod
    def is_enabled_for_course(cls, user, course_key):
        """
        Check if the User calendar sync is enabled for a particular course.
        Returns False if the object does not exist.

        Parameters:
            user (User): The user to check against
            course_key (CourseKey): The course key to check against
        Returns:
            (bool) True if the config exists and is enabled. Otherwise, False
        """
        try:
            return cls.objects.get(user=user, course_key=course_key).enabled
        except cls.DoesNotExist:
            return False
