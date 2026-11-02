"""
Management command to send Schedule course updates
"""

import datetime
from zoneinfo import ZoneInfo
from textwrap import dedent  # lint-amnesty, pylint: disable=wrong-import-order

from django.contrib.sites.models import Site

from openedx.core.djangoapps.schedules.management.commands import SendEmailBaseCommand
from openedx.core.djangoapps.schedules.tasks import ScheduleCourseNextSectionUpdate


class Command(SendEmailBaseCommand):
    """
    Command to send Schedule course updates for Self-paced Courses
    """
    help = dedent(__doc__).strip()
    async_send_task = ScheduleCourseNextSectionUpdate
    log_prefix = 'Course Update'

    def handle(self, *args, ** options):
        current_date = datetime.datetime(
            *[int(x) for x in options['date'].split('-')],
            tzinfo=ZoneInfo("UTC")
        )

        site = Site.objects.get(domain__iexact=options['site_domain_name'])
        override_recipient_email = options.get('override_recipient_email')

        # day_offset set to 1 as we'll always be looking for yesterday
        self.async_send_task.enqueue(site, current_date, 1, override_recipient_email)
