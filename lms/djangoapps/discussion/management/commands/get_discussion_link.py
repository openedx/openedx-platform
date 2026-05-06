# pylint: disable=missing-module-docstring
from django.core.management.base import BaseCommand, CommandError
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.courseware.courses import get_course


class Command(BaseCommand):  # pylint: disable=missing-class-docstring
    help = 'Write a discussion link for a given course on standard output.'

    def add_arguments(self, parser):
        parser.add_argument('course_id',
                            help='course for which to write a discussion link')

    def handle(self, *args, **options):
        course_id = options['course_id']

        course_key = CourseKey.from_string(course_id)

        course = get_course(course_key)
        if not course:
            raise CommandError(f'Invalid course id: {course_id}')

        if course.discussion_link:
            self.stdout.write(course.discussion_link)
