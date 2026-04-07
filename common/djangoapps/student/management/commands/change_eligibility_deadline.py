""" Command line script to change credit course eligibility deadline. """


import logging
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment, User
from openedx.core.djangoapps.credit.models import CreditEligibility

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name

DEFAULT_DAYS = 30


class IncorrectDeadline(Exception):
    """
    Exception raised explicitly to use default date when date given by user is prior to today.
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


class Command(BaseCommand):  # lint-amnesty, pylint: disable=missing-class-docstring

    help = """
    Changes the credit course eligibility deadline for a student in a particular course.
    It can be used to update the expired deadline to make student credit eligible.

    Example:

        Change credit eligibility deadline for user joe enrolled in credit course :

            $ ... change_eligibility_deadline -u joe -d 2018-12-30 -c course-v1:org-course-run
    """

    def add_arguments(self, parser):
        parser.add_argument('-u', '--username',
                            metavar='USERNAME',
                            required=True,
                            help='username of the student')
        parser.add_argument('-d', '--date',
                            dest='deadline',
                            metavar='DEADLINE',
                            help='Desired eligibility deadline for credit course')
        parser.add_argument('-c', '--course',
                            metavar='COURSE_KEY',
                            dest='course_key',
                            required=True,
                            help='Course Key')

    def handle(self, *args, **options):
        """
        Handler for the command

        It performs checks for username, course and enrollment validity and
        then calls update_deadline for the given arguments
        """
        username = options['username']
        course_id = options['course_key']

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            logger.exception(f'Invalid or non-existent username {username}')
            raise

        try:
            course_key = CourseKey.from_string(course_id)
            CourseEnrollment.objects.get(user=user, course_id=course_key, mode=CourseMode.CREDIT_MODE)
        except InvalidKeyError:
            logger.exception(f'Invalid or non-existent course id {course_id}')
            raise
        except CourseEnrollment.DoesNotExist:
            logger.exception('No enrollment found in database for {username} in course {course_id}'
                             .format(username=username, course_id=course_id))
            raise

        try:
            expected_date = datetime.strptime(options['deadline'], '%Y-%m-%d')
            current_date = datetime.utcnow()
            if expected_date < current_date:
                raise IncorrectDeadline('Incorrect Deadline')
        except (TypeError, KeyError, IncorrectDeadline):
            logger.warning('Invalid date or date not provided. Setting deadline to one month from now')
            expected_date = datetime.utcnow() + timedelta(days=DEFAULT_DAYS)

        self.update_credit_eligibility_deadline(username, course_key, expected_date)
        logger.info(f"Successfully updated credit eligibility deadline for {username}")

    def update_credit_eligibility_deadline(self, username, course_key, new_deadline):
        """ Update Credit Eligibility new_deadline for a specific user """
        try:
            eligibility_record = CreditEligibility.objects.get(username=username, course__course_key=course_key)
            eligibility_record.deadline = new_deadline
            eligibility_record.save()
        except CreditEligibility.DoesNotExist:
            logger.exception('User is not credit eligible')
            raise
