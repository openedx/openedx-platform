"""
Un-enroll Bulk users course wide as well as specified in csv
"""
import logging

import unicodecsv
from django.core.management.base import BaseCommand
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import BulkUnenrollConfiguration, CourseEnrollment

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class Command(BaseCommand):
    """
    Management command to un-enroll course enrollments at once.
    """
    help = """"
    Un-enroll bulk users from the courses.
    It expect that the data will be provided in a csv file format with
    first row being the header and columns will be either one of the
    following:
    | username | course_id |
    OR
    | course_id |

    Example:
            $ ... bulk_unenroll --csv_path=foo.csv
            $ ... bulk_unenroll --csv_path=foo.csv --commit
    """
    commit = False

    def add_arguments(self, parser):
        parser.add_argument(
            '-p', '--csv_path',
            metavar='csv_path',
            dest='csv_path',
            required=False,
            help='Path to CSV file.')
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Save the changes, without this flag only a dry run will be performed and nothing will be changed')

    def handle(self, *args, **options):
        csv_path = options['csv_path']
        self.commit = options['commit']

        if csv_path:
            with open(csv_path, 'rb') as csv_file:
                self.unenroll_users_from_csv(csv_file)
        else:
            csv_file = BulkUnenrollConfiguration.current().csv_file
            self.unenroll_users_from_csv(csv_file)

    def unenroll_users_from_csv(self, csv_file):
        """
        Un-enroll the given set of users provided in csv
        """
        reader = list(unicodecsv.DictReader(csv_file))
        for row in reader:
            self.unenroll_learners(row.get('course_id'), username=row.get('username', None))

    def unenroll_learners(self, course_id, username=None):
        """
        Un-enroll learners from course_id(s)
        """
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            msg = f'Invalid course id {course_id}, skipping un-enrollement.'
            logger.warning(msg)
            return

        enrollments = CourseEnrollment.objects.filter(course_id=course_key, is_active=True)
        if username:
            enrollments = enrollments.filter(user__username=username)

        logger.info(f"Processing [{course_id}] with [{enrollments.count()}] enrollments.")

        if self.commit:
            for enrollment in enrollments:
                enrollment.update_enrollment(is_active=False, skip_refund=True)
                logger.info(
                    "User [{}] have been successfully unenrolled from the course: {}".format(
                        enrollment.user.username, course_key
                    )
                )
