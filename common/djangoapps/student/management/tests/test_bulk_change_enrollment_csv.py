# lint-amnesty, pylint: disable=missing-module-docstring

from tempfile import NamedTemporaryFile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from testfixtures import LogCapture

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import BulkChangeEnrollmentConfiguration, CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

LOGGER_NAME = 'common.djangoapps.student.management.commands.bulk_change_enrollment_csv'


class BulkChangeEnrollmentCSVTests(SharedModuleStoreTestCase):
    """
    Tests bulk_change_enrollmetn_csv command
    """
    def setUp(self):
        super().setUp()
        self.courses = []

        self.user_info = [
            ('amy', 'amy@pond.com', 'password'),
            ('rory', 'rory@theroman.com', 'password'),
            ('river', 'river@song.com', 'password')
        ]

        self.enrollments = []
        self.users = []

        for username, email, password in self.user_info:
            user = UserFactory.create(username=username, email=email, password=password)
            self.users.append(user)
            course = CourseFactory.create()
            CourseModeFactory.create(course_id=course.id, mode_slug=CourseMode.AUDIT)
            CourseModeFactory.create(course_id=course.id, mode_slug=CourseMode.VERIFIED)
            self.courses.append(course)
            self.enrollments.append(CourseEnrollment.enroll(user, course.id, mode=CourseMode.AUDIT))

    def _write_test_csv(self, csv, lines):
        """Write a test csv file with the lines provided"""
        csv.write(b"course_id,user,mode,\n")
        for line in lines:
            csv.write(line.encode())
        csv.seek(0)
        return csv

    @skip_unless_lms
    def test_user_not_exist(self):
        """Verify that warning is logged for non existing user."""
        with NamedTemporaryFile() as csv:
            csv = self._write_test_csv(csv, lines=["course-v1:edX+DemoX+Demo_Course,user,audit\n"])

            with LogCapture(LOGGER_NAME) as log:
                call_command("bulk_change_enrollment_csv", f"--csv_file_path={csv.name}")
                log.check(
                    (
                        LOGGER_NAME,
                        'WARNING',
                        'Invalid or non-existent user [user]'
                    )
                )

    @skip_unless_lms
    def test_invalid_course_key(self):
        """Verify in case of invalid course key warning is logged."""
        with NamedTemporaryFile() as csv:
            csv = self._write_test_csv(csv, lines=["Demo_Course,river,audit\n"])

            with LogCapture(LOGGER_NAME) as log:
                call_command("bulk_change_enrollment_csv", f"--csv_file_path={csv.name}")
                log.check(
                    (
                        LOGGER_NAME,
                        'WARNING',
                        'Invalid or non-existent course id [Demo_Course]'
                    )
                )

    @skip_unless_lms
    def test_already_enrolled_student(self):
        """ Verify in case if a user is already enrolled warning is logged."""
        with NamedTemporaryFile() as csv:
            csv = self._write_test_csv(csv, lines=[str(self.courses[0].id) + ",amy,audit\n"])

            with LogCapture(LOGGER_NAME) as log:
                call_command("bulk_change_enrollment_csv", f"--csv_file_path={csv.name}")
                log.check(
                    (
                        LOGGER_NAME,
                        'INFO',
                        'Student [{}] is already enrolled in Course [{}] in mode [{}].'.format(
                            'amy',
                            str(self.courses[0].id),
                            'audit',
                        )
                    )
                )

    @skip_unless_lms
    def test_bulk_enrollment(self):
        """ Test all users are enrolled using the command."""
        lines = [
            str(enrollment.course.id) + "," + str(enrollment.user.username) + ",verified\n"
            for enrollment in self.enrollments
        ]

        with NamedTemporaryFile() as csv:
            csv = self._write_test_csv(csv, lines=lines)
            call_command("bulk_change_enrollment_csv", f"--csv_file_path={csv.name}")

        for enrollment in self.enrollments:
            new_enrollment = CourseEnrollment.get_enrollment(user=enrollment.user, course_key=enrollment.course)
            assert new_enrollment.is_active is True
            assert new_enrollment.mode == CourseMode.VERIFIED

    @skip_unless_lms
    def test_bulk_enrollment_from_config_model(self):
        """ Test all users are enrolled using the config model."""
        lines = "course_id,user,mode\n"
        for enrollment in self.enrollments:
            lines += str(enrollment.course.id) + "," + str(enrollment.user.username) + ",verified\n"

        csv_file = SimpleUploadedFile(name='test.csv', content=lines.encode('utf-8'), content_type='text/csv')
        BulkChangeEnrollmentConfiguration.objects.create(enabled=True, csv_file=csv_file)
        call_command("bulk_change_enrollment_csv", "--file_from_database")

        for enrollment in self.enrollments:
            new_enrollment = CourseEnrollment.get_enrollment(user=enrollment.user, course_key=enrollment.course)
            assert new_enrollment.is_active is True
            assert new_enrollment.mode == CourseMode.VERIFIED

    @skip_unless_lms
    def test_command_error_for_config_model(self):
        """ Test command error raised if file_from_database is required and the config model is not enabled"""

        with pytest.raises(CommandError):
            call_command("bulk_change_enrollment_csv", "--file_from_database")
