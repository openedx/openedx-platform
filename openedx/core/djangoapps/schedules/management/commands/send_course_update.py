"""
Management command to send Schedule course updates
"""


from textwrap import dedent

from openedx.core.djangoapps.schedules.management.commands import SendEmailBaseCommand
from openedx.core.djangoapps.schedules.tasks import ScheduleCourseUpdate


class Command(SendEmailBaseCommand):
    """
    Command to send Schedule course updates for Instructor-paced Courses
    """
    help = dedent(__doc__).strip()
    async_send_task = ScheduleCourseUpdate
    log_prefix = 'Course Update'
