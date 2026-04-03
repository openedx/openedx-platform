"""
Tests for the reset_enrollment_data management command.
"""


import sys
from contextlib import contextmanager
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from six import StringIO

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.program_enrollments.management.commands import reset_enrollment_data
from lms.djangoapps.program_enrollments.models import ProgramCourseEnrollment, ProgramEnrollment
from lms.djangoapps.program_enrollments.tests.factories import ProgramCourseEnrollmentFactory, ProgramEnrollmentFactory


class TestResetEnrollmentData(TestCase):
    """ Test reset_enrollment_data command """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.command = reset_enrollment_data.Command()
        cls.program_uuid = uuid4()

    def setUp(self):
        super().setUp()
        self.user = UserFactory()

    @contextmanager
    def _replace_stdin(self, text):
        """
        Mock out standard input to return `text` when read from.
        """
        orig = sys.stdin
        sys.stdin = StringIO(text)
        yield
        sys.stdin = orig

    def _create_program_and_course_enrollment(self, program_uuid, user):
        program_enrollment = ProgramEnrollmentFactory(user=user, program_uuid=program_uuid)
        ProgramCourseEnrollmentFactory(program_enrollment=program_enrollment)
        return program_enrollment

    def _validate_enrollments_count(self, n):
        assert len(CourseEnrollment.objects.all()) == n
        assert len(ProgramCourseEnrollment.objects.all()) == n
        assert len(ProgramEnrollment.objects.all()) == n

    def test_reset(self):
        """ Validate enrollments with a user and waiting enrollments without a user are removed """
        self._create_program_and_course_enrollment(self.program_uuid, self.user)
        self._create_program_and_course_enrollment(self.program_uuid, None)

        call_command(self.command, self.program_uuid, force=True)

        self._validate_enrollments_count(0)

    def test_reset_confirmation(self):
        """ By default this command will require user input to confirm """
        self._create_program_and_course_enrollment(self.program_uuid, self.user)

        with self._replace_stdin('confirm'):
            call_command(self.command, self.program_uuid)

        self._validate_enrollments_count(0)

    def test_reset_confirmation_failure(self):
        """ Failing to confirm reset will result in no modifications """
        self._create_program_and_course_enrollment(self.program_uuid, self.user)

        with pytest.raises(CommandError):
            with self._replace_stdin('no'):
                call_command(self.command, self.program_uuid)

        self._validate_enrollments_count(1)

    def test_reset_scope(self):
        """ reset should only affect provided programs """
        self._create_program_and_course_enrollment(self.program_uuid, self.user)

        alt_program_uuid = uuid4()
        alt_user = UserFactory()
        self._create_program_and_course_enrollment(alt_program_uuid, alt_user)

        call_command(self.command, self.program_uuid, force=True)

        # enrollment with different uuid still exists
        program_enrollment = ProgramEnrollment.objects.get(program_uuid=alt_program_uuid)
        program_course_enrollment = ProgramCourseEnrollment.objects.get(program_enrollment=program_enrollment)
        course_enrollment = program_course_enrollment.course_enrollment
        assert course_enrollment is not None

        # other enrollments have been deleted
        self._validate_enrollments_count(1)

    def test_reset_multiple_programs(self):
        self._create_program_and_course_enrollment(self.program_uuid, self.user)
        alt_program_uuid = uuid4()
        alt_user = UserFactory()
        self._create_program_and_course_enrollment(alt_program_uuid, alt_user)

        call_command(self.command, f'{self.program_uuid},{alt_program_uuid}', force=True)

        self._validate_enrollments_count(0)
