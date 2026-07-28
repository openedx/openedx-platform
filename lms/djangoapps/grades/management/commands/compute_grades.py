"""
Command to compute all grades for specified courses.
"""


import hashlib
import logging

from django.core.management.base import BaseCommand

from lms.djangoapps.grades import tasks
from lms.djangoapps.grades.config.models import ComputeGradesSetting
from openedx.core.lib.command_utils import get_mutually_exclusive_required_option, parse_course_keys
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Example usage:
        $ ./manage.py lms compute_grades --all_courses --settings=devstack
        $ ./manage.py lms compute_grades 'edX/DemoX/Demo_Course' --settings=devstack
    """
    args = '<course_id course_id ...>'
    help = 'Computes grade values for all learners in specified courses.'

    def add_arguments(self, parser):
        """
        Entry point for subclassed commands to add custom arguments.
        """
        parser.add_argument(
            '--courses',
            dest='courses',
            nargs='+',
            help='List of (space separated) courses that need grades computed.',
        )
        parser.add_argument(
            '--all_courses',
            help='Compute grades for all courses.',
            action='store_true',
            default=False,
        )
        parser.add_argument(
            '--from_settings',
            help='Compute grades with settings set via Django admin',
            action='store_true',
            default=False,
        )
        parser.add_argument(
            '--routing_key',
            dest='routing_key',
            help='Celery routing key to use.',
        )
        parser.add_argument(
            '--batch_size',
            help='Maximum number of students to calculate grades for, per celery task.',
            default=100,
            type=int,
        )
        parser.add_argument(
            '--start_index',
            help='Offset from which to start processing enrollments.',
            default=0,
            type=int,
        )
        parser.add_argument(
            '--no_estimate_first_attempted',
            help='Use score data to estimate first_attempted timestamp.',
            action='store_false',
            dest='estimate_first_attempted',
        )

    def handle(self, *args, **options):
        self._set_log_level(options)
        self.enqueue_all_shuffled_tasks(options)

    def enqueue_all_shuffled_tasks(self, options):
        """
        Enqueue all tasks, in shuffled order.
        """
        task_options = {'queue': options['routing_key']} if options.get('routing_key') else {}
        for seq_id, kwargs in enumerate(self._shuffled_task_kwargs(options)):
            kwargs['seq_id'] = seq_id
            result = tasks.compute_grades_for_course_v2.apply_async(kwargs=kwargs, **task_options)
            log.info("Grades: Created {task_name}[{task_id}] with arguments {kwargs}".format(  # noqa: UP032
                task_name=tasks.compute_grades_for_course.name,
                task_id=result.task_id,
                kwargs=kwargs,
            ))

    def _shuffled_task_kwargs(self, options):
        """
        Iterate over all task keyword arguments in random order.

        Randomizing them will help even out the load on the task workers,
        though it will not entirely prevent the possibility of spikes.  It will
        also make the overall time to completion more predictable.
        """
        all_args = []
        estimate_first_attempted = options['estimate_first_attempted']
        for course_key in self._get_course_keys(options):
            # This is a tuple to reduce memory consumption.
            # The dictionaries with their extra overhead will be created
            # and consumed one at a time.
            for task_arg_tuple in tasks._course_task_args(course_key, **options):  # pylint: disable=protected-access
                all_args.append(task_arg_tuple)

        all_args.sort(key=lambda x: hashlib.md5(f'{x!r}'.encode('utf-8')).digest())  # noqa: UP012

        for args in all_args:
            yield {
                'course_key': args[0],
                'offset': args[1],
                'batch_size': args[2],
                'estimate_first_attempted': estimate_first_attempted,
            }

    def _get_course_keys(self, options):
        """
        Return a list of courses that need scores computed.
        """

        courses_mode = get_mutually_exclusive_required_option(options, 'courses', 'all_courses', 'from_settings')
        if courses_mode == 'all_courses':
            course_keys = [course.id for course in modulestore().get_course_summaries()]
        elif courses_mode == 'courses':
            course_keys = parse_course_keys(options['courses'])
        else:
            course_keys = parse_course_keys(self._latest_settings().course_ids.split())
        return course_keys

    def _set_log_level(self, options):
        """
        Sets logging levels for this module and the block structure
        cache module, based on the given the options.
        """
        if options.get('verbosity') == 0:
            log_level = logging.WARNING
        elif options.get('verbosity') >= 1:
            log_level = logging.INFO
        log.setLevel(log_level)

    def _latest_settings(self):
        """
        Return the latest version of the ComputeGradesSetting
        """
        return ComputeGradesSetting.current()
