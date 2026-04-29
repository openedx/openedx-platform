"""
Signal handlers for course goals.
"""


from django.db.models.signals import post_save
from django.dispatch import receiver
from eventtracking import tracker

from common.djangoapps.track import segment
from lms.djangoapps.course_goals.models import CourseGoal


@receiver(post_save, sender=CourseGoal, dispatch_uid="emit_course_goals_event")
def emit_course_goal_event(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """Emit events for both tracking logs and for Segment."""
    name = 'edx.course.goal.added' if kwargs.get('created', False) else 'edx.course.goal.updated'
    properties = {
        'courserun_key': str(instance.course_key),
        'days_per_week': instance.days_per_week,
        'subscribed_to_reminders': instance.subscribed_to_reminders,
    }
    tracker.emit(name, properties)
    segment.track(instance.user.id, name, properties)
