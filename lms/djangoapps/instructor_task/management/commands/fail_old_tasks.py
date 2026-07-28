"""
Commands to fail old tasks
"""


from datetime import datetime
from textwrap import dedent

from celery.states import FAILURE
from django.core.management.base import BaseCommand, CommandError
from pytz import utc

from lms.djangoapps.instructor_task.models import PROGRESS, QUEUING, InstructorTask


class Command(BaseCommand):
    """
    Command to manually fail old "QUEUING" or "PROGRESS" tasks in the
    instructor task table.

    Example:
    ./manage.py lms fail_old_tasks QUEUING --dry-run --after 2001-01-03 \
        --before 2001-01-06 --task-type bulk_course_email
    """
    help = dedent(__doc__).strip()

    def add_arguments(self, parser):
        """
        Add arguments to the command parser.
        """

        parser.add_argument(
            "task_state",
            type=str,
            choices=[QUEUING, PROGRESS],
            help="choose the current task_state of tasks you want to fail"
        )

        parser.add_argument(
            '--before',
            type=str,
            dest='before',
            help='Manually fail instructor tasks created before or on this date.',
        )

        parser.add_argument(
            '--after',
            type=str,
            dest='after',
            help='Manually fail instructor tasks created after or on this date.',
        )

        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            default=False,
            help='Return the records this command will update without updating them.',
        )

        parser.add_argument(
            '--task-type',
            dest='task_type',
            type=str,
            default=None,
            help='Specify the type of task that you want to fail.',
        )

    @staticmethod
    def parse_date(date_string):
        """
        Converts an isoformat string into a python datetime object. Localizes
        that datetime object to UTC.
        """
        return utc.localize(datetime.strptime(date_string, "%Y-%m-%d"))

    def handle(self, *args, **options):

        if options['before'] is None:
            raise CommandError("Must provide a 'before' date")

        if options['after'] is None:
            raise CommandError("Must provide an 'after' date")

        before = self.parse_date(options['before'])
        after = self.parse_date(options['after'])
        filter_kwargs = {
            "task_state": options['task_state'],
            "created__lte": before,
            "created__gte": after,
        }
        if options['task_type'] is not None:
            filter_kwargs.update({"task_type": options['task_type']})

        tasks = InstructorTask.objects.filter(**filter_kwargs)

        for task in tasks:
            print(
                "{task_state} task '{task_id}', of type '{task_type}', created on '{created}', will be marked as 'FAILURE'".format(  # pylint: disable=line-too-long  # noqa: UP032
                    task_state=task.task_state,
                    task_id=task.task_id,
                    task_type=task.task_type,
                    created=task.created,
                )
            )

        if not options['dry_run']:
            tasks_updated = tasks.update(
                task_state=FAILURE,
            )
            print("{tasks_updated} records updated.".format(  # noqa: UP032
                tasks_updated=tasks_updated
            ))
        else:
            print(
                "This was a dry run, so no records were updated. "  # noqa: UP032
                "If this command were run for real, {number} records would have been updated.".format(
                    number=tasks.count()
                )
            )
