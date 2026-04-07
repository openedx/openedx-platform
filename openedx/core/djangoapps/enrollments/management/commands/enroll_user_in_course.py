"""
Management command for enrolling a user into a course via the enrollment api
"""

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.management.base import BaseCommand

from openedx.core.djangoapps.enrollments.api import add_enrollment
from openedx.core.djangoapps.enrollments.data import CourseEnrollmentExistsError


class Command(BaseCommand):
    """
    Enroll a user into a course
    """
    help = """
    This enrolls a user into a given course

    User email and course ID are required.
    Mode is optional. It defaults to the default mode (e.g., 'honor', 'audit', etc).

    example:
        # Enroll a user test@example.com into the demo course
        manage.py ... enroll_user_in_course -e test@example.com -c edX/Open_DemoX/edx_demo_course

        This command can be run multiple times on the same user+course (i.e. it is idempotent).
    """

    def add_arguments(self, parser):

        parser.add_argument(
            '-e', '--email',
            nargs=1,
            required=True,
            help='Email for user'
        )
        parser.add_argument(
            '-c', '--course',
            nargs=1,
            required=True,
            help='course ID to enroll the user in'
        )
        parser.add_argument(
            '-m', '--mode',
            required=False,
            default=None,
            help='course mode to enroll the user in'
        )

    def handle(self, *args, **options):
        """
        Get and enroll a user in the given course. Mode is optional and defers to the enrollment API for defaults.
        """
        email = options['email'][0]
        course = options['course'][0]
        mode = options['mode']

        user = User.objects.get(email=email)
        try:
            add_enrollment(user.username, course, mode=mode)
        except CourseEnrollmentExistsError:
            # If the user is already enrolled in the course, do nothing.
            pass
