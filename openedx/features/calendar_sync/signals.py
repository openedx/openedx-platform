"""
Signal handler for calendar sync models
"""
from django.db.models.signals import post_save
from django.dispatch.dispatcher import receiver

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.features.course_experience import CALENDAR_SYNC_FLAG, RELATIVE_DATES_FLAG

from .ics import generate_ics_files_for_user_course
from .models import UserCalendarSyncConfig
from .utils import send_email_with_attachment


@receiver(post_save, sender=UserCalendarSyncConfig)
def handle_calendar_sync_email(sender, instance, created, **kwargs):  # pylint: disable=missing-function-docstring, unused-argument
    if (
        CALENDAR_SYNC_FLAG.is_enabled(instance.course_key) and
        RELATIVE_DATES_FLAG.is_enabled(instance.course_key) and
        created
    ):
        user = instance.user
        email = user.email
        course_overview = CourseOverview.objects.get(id=instance.course_key)
        ics_files = generate_ics_files_for_user_course(course_overview, user, instance)
        send_email_with_attachment(
            [email],
            ics_files,
            course_overview.display_name,
            created
        )
        post_save.disconnect(handle_calendar_sync_email, sender=UserCalendarSyncConfig)
        instance.ics_sequence = instance.ics_sequence + 1
        instance.save()
        post_save.connect(handle_calendar_sync_email, sender=UserCalendarSyncConfig)
