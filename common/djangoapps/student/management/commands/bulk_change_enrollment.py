"""Management command to change many user enrollments at once."""


import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class Command(BaseCommand):
    """
    Management command to change many user enrollments at once.
    """

    help = """
    Change the enrollment status for all users enrolled in a
    particular mode for a course. Similar to the change_enrollment
    script, but more useful for bulk moves.

    Example:

    Change enrollment for all audit users to honor in the given course.
        $ ... bulk_change_enrollment -c course-v1:SomeCourse+SomethingX+2016 --from_mode audit --to_mode honor --commit

    Without the --commit option, the command will have no effect.
    """

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            '-c', '--course',
            help='The course to change enrollments in')
        group.add_argument(
            '-o', '--org',
            help='All courses belonging to this org will be selected for changing the enrollments')

        parser.add_argument(
            '-f', '--from_mode',
            required=True,
            help='Move from this enrollment mode')
        parser.add_argument(
            '-t', '--to_mode',
            required=True,
            help='Move to this enrollment mode')
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Save the changes, without this flag only a dry run will be performed and nothing will be changed')

    def handle(self, *args, **options):
        course_id = options['course']
        org = options['org']
        from_mode = options['from_mode']
        to_mode = options['to_mode']
        commit = options['commit']
        course_keys = []
        if course_id:
            try:
                course_key = CourseKey.from_string(course_id)
            except InvalidKeyError:
                raise CommandError(f'Course ID {course_id} is invalid.')  # lint-amnesty, pylint: disable=raise-missing-from

            if modulestore().get_course(course_key) is None:
                raise CommandError(f'The given course {course_id} does not exist.')

            course_keys.append(course_key)
        else:
            course_keys = [course.id for course in CourseOverview.get_all_courses(orgs=[org])]
            if not course_keys:
                raise CommandError(f'No courses exist for the org "{org}".')

        for course_key in course_keys:
            self.move_users_for_course(course_key, from_mode, to_mode, commit)

        if not commit:
            logger.info('Dry run, changes have not been saved. Run again with "commit" argument to save changes')

    def move_users_for_course(self, course_key, from_mode, to_mode, commit):
        """
        Change the enrollment mode of users for a course.

        Arguments:
            course_key (CourseKey): to lookup the course.
            from_mode (str): the enrollment mode to change.
            to_mode (str): the enrollment mode to change to.
            commit (bool): required to make the change to the database. Otherwise
                                     just a count will be displayed.
        """
        unicode_course_key = str(course_key)
        if CourseMode.mode_for_course(course_key, to_mode) is None:
            logger.info(f'Mode ({to_mode}) does not exist for course ({unicode_course_key}).')
            return

        course_enrollments = CourseEnrollment.objects.filter(course_id=course_key, mode=from_mode)
        logger.info(
            'Moving %d users from %s to %s in course %s.',
            course_enrollments.count(), from_mode, to_mode, unicode_course_key
        )
        if commit:
            # call `change_mode` which will change the mode and also emit tracking event
            for enrollment in course_enrollments:
                with transaction.atomic():
                    enrollment.change_mode(mode=to_mode)

            logger.info('Finished moving users from %s to %s in course %s.', from_mode, to_mode, unicode_course_key)
