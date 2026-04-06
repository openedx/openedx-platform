"""
Command to load course overviews.
"""


import logging

from django.core.management.base import BaseCommand, CommandError
from opaque_keys import InvalidKeyError

from openedx.core.djangoapps.content.course_overviews.tasks import (
    DEFAULT_ALL_COURSES,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_FORCE_UPDATE,
    enqueue_async_course_overview_update_tasks,
)

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Example usage:
        $ ./manage.py lms generate_course_overview --all-courses --settings=devstack --chunk-size=100
        $ ./manage.py lms generate_course_overview 'edX/DemoX/Demo_Course' --settings=devstack
    """
    args = '<course_id course_id ...>'
    help = 'Generates and stores course overview for one or more courses.'

    def add_arguments(self, parser):
        """
        Add arguments to the command parser.
        """
        parser.add_argument(
            '--all-courses', '--all',
            dest='all_courses',
            action='store_true',
            default=DEFAULT_ALL_COURSES,
            help='Generate course overview for all courses.',
        )
        parser.add_argument(
            '--force-update', '--force_update',
            action='store_true',
            default=DEFAULT_FORCE_UPDATE,
            help='Force update course overviews for the requested courses.',
        )
        parser.add_argument(
            '--chunk-size',
            action='store',
            type=int,
            default=DEFAULT_CHUNK_SIZE,
            help='The maximum number of courses each task will generate a course overview for.'
        )
        parser.add_argument(
            '--routing-key',
            dest='routing_key',
            help='The celery routing key to use.'
        )

    def handle(self, *args, **options):
        if not options.get('all_courses') and len(args) < 1:
            raise CommandError('At least one course or --all-courses must be specified.')

        kwargs = {}
        for key in ('all_courses', 'force_update', 'chunk_size', 'routing_key'):
            if options.get(key):
                kwargs[key] = options[key]

        try:
            enqueue_async_course_overview_update_tasks(
                course_ids=args,
                **kwargs
            )
        except InvalidKeyError as exc:
            raise CommandError('Invalid Course Key: ' + str(exc))  # lint-amnesty, pylint: disable=raise-missing-from
