"""
Tests for the cert_generation command
"""

from unittest import mock

import pytest
from django.core.management import CommandError, call_command

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.certificates.tests.test_generation_handler import ID_VERIFIED_METHOD
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@mock.patch(ID_VERIFIED_METHOD, mock.Mock(return_value=True))
class CertGenerationTests(ModuleStoreTestCase):
    """
    Tests for the cert_generation management command
    """

    def setUp(self):
        super().setUp()

        # Create users, a course run, and enrollments
        self.user = UserFactory()
        self.course_run = CourseFactory()
        self.course_run_key = self.course_run.id  # pylint: disable=no-member
        self.enrollment = CourseEnrollmentFactory(
            user=self.user,
            course_id=self.course_run_key,
            is_active=True,
            mode="verified",
        )

        self.user2 = UserFactory()
        self.enrollment2 = CourseEnrollmentFactory(
            user=self.user2,
            course_id=self.course_run_key,
            is_active=True,
            mode="verified",
        )

    def test_command_with_missing_param_course_key(self):
        """
        Verify command with a missing param -- course key.
        """
        with pytest.raises(CommandError, match="You must specify a course-key"):
            call_command("cert_generation", "--u", self.user.username)

    def test_command_with_missing_param_users(self):
        """
        Verify command with a missing param -- users.
        """
        with pytest.raises(CommandError, match="You must specify a list of users"):
            call_command("cert_generation", "--c", "blah")

    def test_command_with_invalid_key(self):
        """
        Verify command with an invalid course run key
        """
        with pytest.raises(CommandError, match="You must specify a valid course-key"):
            call_command("cert_generation", "--u", self.user.username, "--c", "blah")

    def test_successful_generation(self):
        """
        Test generation for 1 user
        """
        call_command("cert_generation", "--u", self.user.id, "--c", self.course_run_key)

    def test_successful_generation_multiple_users(self):
        """
        Test generation for multiple user
        """
        call_command("cert_generation",
                     "--u",
                     self.user.id,
                     self.user2.id,
                     "999999",  # non-existent userid
                     "--c",
                     self.course_run_key)
