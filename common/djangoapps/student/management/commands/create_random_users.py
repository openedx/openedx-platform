"""
A script to create some dummy users
"""
import uuid

from django.core.management.base import BaseCommand
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.management.commands._create_users import create_users


def random_user_data_generator(num_users):
    for _ in range(num_users):
        identification = uuid.uuid4().hex[:8]
        yield {
            'username': f'user_{identification}',
            'email': f'email_{identification}@example.com',
            'password': '12345',
            'name': f'User {identification}',
        }


class Command(BaseCommand):  # pylint: disable=missing-class-docstring
    help = """Create N new users, with random parameters.

Usage: create_random_users.py N [course_id_to_enroll_in].

Examples:
  create_random_users.py 1
  create_random_users.py 10 MITx/6.002x/2012_Fall
  create_random_users.py 100 HarvardX/CS50x/2012
"""

    def add_arguments(self, parser):
        parser.add_argument('num_users',
                            help='Number of users to create',
                            type=int)
        parser.add_argument('course_key',
                            help='Add newly created users to this course',
                            nargs='?')

    def handle(self, *args, **options):
        num = options['num_users']
        course_key = CourseKey.from_string(options['course_key']) if options['course_key'] else None
        create_users(course_key, random_user_data_generator(num))
