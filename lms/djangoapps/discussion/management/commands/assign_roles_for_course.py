"""
This must be run only after seed_permissions_roles.py!

Creates default roles for all users in the provided course. Just runs through
Enrollments.
"""


from django.core.management.base import BaseCommand

from common.djangoapps.student.models import CourseEnrollment
from openedx.core.djangoapps.django_comment_common.models import assign_default_role_on_enrollment


class Command(BaseCommand):  # pylint: disable=missing-class-docstring
    help = 'Add roles for all users in a course.'

    def add_arguments(self, parser):
        parser.add_argument('course_id',
                            help='the edx course_id')

    def handle(self, *args, **options):
        course_id = options['course_id']

        print('Updated roles for ', end=' ')
        for i, enrollment in enumerate(CourseEnrollment.objects.filter(course_id=course_id, is_active=1), start=1):
            assign_default_role_on_enrollment(None, enrollment)
            if i % 1000 == 0:
                print(f'{i}...', end=' ')
        print()
