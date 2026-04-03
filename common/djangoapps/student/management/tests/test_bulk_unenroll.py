"""Tests for Bulk Un-enroll Management command"""

from tempfile import NamedTemporaryFile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from testfixtures import LogCapture

from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import BulkUnenrollConfiguration, CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

LOGGER_NAME = 'common.djangoapps.student.management.commands.bulk_unenroll'


class BulkUnenrollTests(SharedModuleStoreTestCase):
    """Test Bulk un-enroll command works fine for all test cases."""
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.audit_mode = CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='audit',
            mode_display_name='Audit',
        )

        self.user_info = [
            ('amy', 'amy@pond.com', 'password'),
            ('rory', 'rory@theroman.com', 'password'),
            ('river', 'river@song.com', 'password')
        ]
        self.enrollments = []
        self.users = []

        for username, email, password in self.user_info:
            user = UserFactory.create(
                username=username, email=email, password=password
            )
            self.users.append(user)
            self.enrollments.append(
                CourseEnrollment.enroll(user, self.course.id, mode='audit')
            )

    def _write_test_csv(self, csv, lines):
        """Write a test csv file with the lines provided"""
        csv.write(b"username,course_id\n")
        for line in lines:
            csv.write(line.encode())
        csv.seek(0)
        return csv

    def test_invalid_course_key(self):
        """Verify in case of invalid course key warning is logged."""
        with NamedTemporaryFile() as csv:
            csv = self._write_test_csv(csv, lines=["amy,test_course\n"])

            with LogCapture(LOGGER_NAME) as log:
                call_command("bulk_unenroll", f"--csv_path={csv.name}", "--commit")
                expected_message = 'Invalid course id {}, skipping un-enrollement.'.\
                    format('test_course')

                log.check_present(
                    (LOGGER_NAME, 'WARNING', expected_message)
                )

    def test_bulk_un_enroll(self):
        """Verify users are unenrolled using the command."""
        lines = [
            enrollment.user.username + "," +
            str(enrollment.course.id) + "\n"
            for enrollment in self.enrollments
        ]
        with NamedTemporaryFile() as csv:
            csv = self._write_test_csv(csv, lines=lines)

            call_command("bulk_unenroll", f"--csv_path={csv.name}", "--commit")
            for enrollment in CourseEnrollment.objects.all():
                assert enrollment.is_active is False

    def test_bulk_un_enroll_without_commit(self):
        """
        Verify the ability to dry-run the command.
        """
        lines = [
            enrollment.user.username + "," +
            str(enrollment.course.id) + "\n"
            for enrollment in self.enrollments
        ]
        with NamedTemporaryFile() as csv:
            csv = self._write_test_csv(csv, lines=lines)

            call_command("bulk_unenroll", f"--csv_path={csv.name}")
            for enrollment in CourseEnrollment.objects.all():
                assert enrollment.is_active is True

    def test_bulk_unenroll_from_config_model(self):
        """Verify users are unenrolled using the command."""
        lines = "user_id,username,email,course_id\n"
        for enrollment in self.enrollments:
            lines += str(enrollment.user.id) + "," + enrollment.user.username + "," + \
                enrollment.user.email + "," + str(enrollment.course.id) + "\n"

        csv_file = SimpleUploadedFile(name='test.csv', content=lines.encode('utf-8'), content_type='text/csv')
        BulkUnenrollConfiguration.objects.create(enabled=True, csv_file=csv_file)

        call_command("bulk_unenroll", "--commit")
        for enrollment in CourseEnrollment.objects.all():
            assert enrollment.is_active is False

    def test_users_unenroll_successfully_logged(self):
        """Verify users unenrolled are logged """
        lines = "username,course_id\n"
        lines += self.enrollments[0].username + "," + str(self.enrollments[0].course.id) + "\n"
        csv_file = SimpleUploadedFile(name='test.csv', content=lines.encode('utf-8'), content_type='text/csv')
        BulkUnenrollConfiguration.objects.create(enabled=True, csv_file=csv_file)

        course_id = self.enrollments[0].course.id
        with LogCapture(LOGGER_NAME) as log:
            call_command("bulk_unenroll", "--commit")
            log.check(
                (
                    LOGGER_NAME,
                    'INFO',
                    f'Processing [{course_id}] with [1] enrollments.',
                ),
                (
                    LOGGER_NAME,
                    'INFO',
                    'User [{}] have been successfully unenrolled from the course: {}'.format(
                        self.enrollments[0].username, self.enrollments[0].course.id
                    )
                ),
            )
