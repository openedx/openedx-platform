"""
Management command to seed default permissions and roles.
"""


from django.core.management.base import BaseCommand
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.django_comment_common.utils import seed_permissions_roles


class Command(BaseCommand):  # pylint: disable=missing-class-docstring
    help = 'Seed default permisssions and roles.'

    def add_arguments(self, parser):
        parser.add_argument('course_id',
                            help='the edx course_id')

    def handle(self, *args, **options):
        course_id = options['course_id']

        course_key = CourseKey.from_string(course_id)
        seed_permissions_roles(course_key)
