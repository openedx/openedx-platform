"""
Management command to change many user enrollments in many courses using
csv file.
"""


import logging
from os import path

import unicodecsv
from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import (
    BulkChangeEnrollmentConfiguration,
    CourseEnrollment,
    CourseEnrollmentAttribute,
    User,
)

logger = logging.getLogger('common.djangoapps.student.management.commands.bulk_change_enrollment_csv')


class Command(BaseCommand):
    """
        Management command to change many user enrollments in many
        courses using the csv file
    """

    help = """
        Change the enrollment status of all the users specified in
        the csv file in the specified course to specified course
        mode.
        Could be used to update effected users by order
        placement issues. If large number of students are effected
        in different courses.
        Similar to bulk_change_enrollment but uses the csv file
        input format and can enroll students in multiple courses.

        Example:
            $ ... bulk_change_enrollment_csv csv_file_path
        """

    def add_arguments(self, parser):
        """ Add argument to the command parser. """
        parser.add_argument(
            '--csv_file_path',
            required=False,
            help='Csv file path'
        )
        parser.add_argument(
            '--file_from_database',
            action='store_true',
            help='Use file from the BulkChangeEnrollmentConfiguration model instead of the command line.',
        )

    def get_file_from_database(self):
        """ Returns an options dictionary from the current SSPVerificationRetryConfig model. """

        enrollment_config = BulkChangeEnrollmentConfiguration.current()
        if not enrollment_config.enabled:
            raise CommandError('BulkChangeEnrollmentConfiguration is disabled or empty, '
                               'but --file_from_database from was requested.')

        return enrollment_config.csv_file

    def handle(self, *args, **options):
        """ Main handler for the command."""
        file_path = options.get('csv_file_path', None)
        file_from_database = options['file_from_database']

        if file_from_database:
            csv_file = self.get_file_from_database()
            self.change_enrollments(csv_file)

        elif file_path:
            if not path.isfile(file_path):
                raise CommandError("File not found.")

            with open(file_path, 'rb') as csv_file:
                self.change_enrollments(csv_file)

        else:
            raise CommandError('No file is provided. File is required')

    def change_enrollments(self, csv_file):
        """ change the enrollments of the learners. """
        course_key = None
        user = None
        file_reader = unicodecsv.DictReader(csv_file)
        headers = file_reader.fieldnames

        if not ('course_id' in headers and 'mode' in headers and 'user' in headers):
            raise CommandError('Invalid input CSV file.')

        for row in list(file_reader):
            try:
                course_key = CourseKey.from_string(row['course_id'])
            except InvalidKeyError:
                logger.warning('Invalid or non-existent course id [{}]'.format(row['course_id']))

            try:
                user = User.objects.get(username=row['user'])
            except ObjectDoesNotExist:
                logger.warning('Invalid or non-existent user [{}]'.format(row['user']))

            if course_key and user:
                try:
                    course_enrollment = self.get_course_enrollment(course_key, user)

                    if course_enrollment:
                        mode = row['mode']
                        self.update_enrollment_mode(course_key, user, mode, course_enrollment)

                    else:
                        # if student enrollment do not exists directly enroll in new mode.
                        CourseEnrollment.enroll(user=user, course_key=course_key, mode=row['mode'])

                except Exception as e:  # pylint: disable=broad-except
                    logger.info("Unable to update student [%s] course [%s] enrollment to mode [%s] "
                                "because of Exception [%s]", row['user'], row['course_id'], row['mode'], repr(e))

    def get_course_enrollment(self, course_key, user):
        """
        If student is not enrolled in course enroll the student in free mode
        """

        course_enrollment = CourseEnrollment.get_enrollment(user, course_key)
        #  If student is not enrolled in course enroll the student in free mode
        if not course_enrollment:
            # try to create a enroll user in default course enrollment mode in case of
            # professional it will break because of no default course mode.
            try:
                course_enrollment = CourseEnrollment.get_or_create_enrollment(user=user,
                                                                              course_key=course_key)

            except Exception:  # pylint: disable=broad-except
                # In case if no free mode is available.
                course_enrollment = None

        return course_enrollment

    def update_enrollment_mode(self, course_key, user, mode, course_enrollment):
        """
        update the enrollment mode based on the learner existing state.
        """
        # if student already had a enrollment and its mode is same as the provided one
        if course_enrollment.mode == mode:
            logger.info("Student [%s] is already enrolled in Course [%s] in mode [%s].", user.username,
                        course_key, course_enrollment.mode)
            # set the enrollment to active if its not already active.
            if not course_enrollment.is_active:
                course_enrollment.update_enrollment(is_active=True)
        else:
            # if student enrollment exists update it to new mode.
            with transaction.atomic():
                course_enrollment.update_enrollment(
                    mode=mode,
                    is_active=True,
                    skip_refund=True
                )
                course_enrollment.save()

                if mode == 'credit':
                    enrollment_attrs = [{'namespace': 'credit',
                                         'name': 'provider_id',
                                         'value': course_key.org
                                         }]
                    CourseEnrollmentAttribute.add_enrollment_attr(enrollment=course_enrollment,
                                                                  data_list=enrollment_attrs)
