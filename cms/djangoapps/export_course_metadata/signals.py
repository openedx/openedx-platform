"""
This file calls the task that exports metadata about the course
"""

from django.dispatch import receiver

from xmodule.modulestore.django import SignalHandler

from .tasks import export_course_metadata_task
from .toggles import EXPORT_COURSE_METADATA_FLAG


@receiver(SignalHandler.course_published)
def export_course_metadata(sender, course_key, **kwargs):  # pylint: disable=unused-argument
    """
    Export course metadata on course publish.
    """
    if EXPORT_COURSE_METADATA_FLAG.is_enabled():
        export_course_metadata_task.delay(str(course_key))
