"""
Management Command to delete course.
"""


from django.core.management.base import BaseCommand, CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from cms.djangoapps.contentstore.utils import delete_course
from xmodule.contentstore.django import contentstore  # pylint: disable=wrong-import-order
from xmodule.modulestore import ModuleStoreEnum  # pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from .prompt import query_yes_no


class Command(BaseCommand):
    """
    Delete a MongoDB backed course

    Example usage:
        $ ./manage.py cms delete_course 'course-v1:edX+DemoX+Demo_Course' --settings=devstack
        $ ./manage.py cms delete_course 'course-v1:edX+DemoX+Demo_Course' --keep-instructors --settings=devstack
        $ ./manage.py cms delete_course 'course-v1:edX+DemoX+Demo_Course' --remove-assets --settings=devstack

    Note:
        The keep-instructors option is useful for resolving issues that arise when a course run's ID is duplicated
        in a case-insensitive manner. MongoDB is case-sensitive, but MySQL is case-insensitive. This results in
        course-v1:edX+DemoX+1t2017 being treated differently in MongoDB from course-v1:edX+DemoX+1T2017 (capital 'T').

        If you need to remove a duplicate that has resulted from casing issues, use the --keep-instructors flag
        to ensure that permissions for the remaining course run are not deleted.

        Use the remove-assets option to ensure all assets are deleted. This is especially relevant to users of the
        split Mongo modulestore.
    """
    help = 'Delete a MongoDB backed course'

    def add_arguments(self, parser):
        parser.add_argument(
            'course_key',
            help='ID of the course to delete.',
        )

        parser.add_argument(
            '--keep-instructors',
            action='store_true',
            default=False,
            help='Do not remove permissions of users and groups for course',
        )

        parser.add_argument(
            '--remove-assets',
            action='store_true',
            help='Remove all assets associated with the course. '
                 'Be careful! These assets may be associated with another course',
        )

    def handle(self, *args, **options):
        try:
            # a course key may have unicode chars in it
            try:
                course_key = str(options['course_key'], 'utf8')
            # May already be decoded to unicode if coming in through tests, this is ok.
            except TypeError:
                course_key = str(options['course_key'])
            course_key = CourseKey.from_string(course_key)
        except InvalidKeyError:
            raise CommandError('Invalid course_key: {}'.format(options['course_key']))  # pylint: disable=raise-missing-from  # noqa: B904

        if not modulestore().get_course(course_key):
            raise CommandError('Course not found: {}'.format(options['course_key']))

        print('Preparing to delete course %s from module store....' % options['course_key'])  # noqa: UP031

        if query_yes_no(f'Are you sure you want to delete course {course_key}?', default='no'):
            if query_yes_no('Are you sure? This action cannot be undone!', default='no'):
                delete_course(course_key, ModuleStoreEnum.UserID.mgmt_command, options['keep_instructors'])

                if options['remove_assets']:
                    contentstore().delete_all_course_assets(course_key)
                    print(f'Deleted assets for course {course_key}')  # pylint: disable=too-many-format-args

                print(f'Deleted course {course_key}')
