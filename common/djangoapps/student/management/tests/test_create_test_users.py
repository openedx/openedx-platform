"""
Test the create_random_users command line script
"""

import ddt
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from opaque_keys import InvalidKeyError

from common.djangoapps.student.helpers import AccountValidationError
from common.djangoapps.student.models import CourseAccessRole, CourseEnrollment
from common.djangoapps.student.roles import CourseStaffRole
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@ddt.ddt
class CreateTestUsersTestCase(SharedModuleStoreTestCase):
    """
    Test creating users via command line, with various options
    """
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.course_id = str(self.course.id)
        self.user_model = get_user_model()
        self.num_users_start = len(self.user_model.objects.all())

    def call_command(
        self,
        users,
        course=None,
        mode=None,
        password=None,
        domain=None,
        course_staff=False,
        ignore_user_already_exists=False
    ):
        """ Helper method to call the management command with various arguments """
        args = list(users)
        if course:
            args.extend(['--course', course])
        if mode:
            args.extend(['--mode', mode])
        if password:
            args.extend(['--password', password])
        if domain:
            args.extend(['--domain', domain])
        if course_staff:
            args.append('--course_staff')
        if ignore_user_already_exists:
            args.append('--ignore_user_already_exists')

        call_command('create_test_users', *args)

    def test_create_users(self):
        """
        Calls the command with a list of usernames to create users.
        """
        usernames = ['test_create_users_u1', 'test_create_users_u2']

        # Check users don't already exist
        assert self.user_model.objects.filter(username__in=usernames).count() == 0

        self.call_command(usernames)

        # Verify users were created, are active, and were created with default settings
        users = self.user_model.objects.filter(username__in=usernames).all()
        assert len(users) == len(usernames)
        for user in users:
            assert user.is_active
            assert user.email == f'{user.username}@example.com'
            assert self.client.login(username=user.username, password='12345')

        assert not CourseEnrollment.objects.filter(user__in=users).exists()

    def test_create_user__username_taken(self):
        """
        Try to create a user with a taken username
        """
        username = 'user1'
        # Create a user with the given username but a different email
        self.user_model.objects.create_user(username, 'taken_email@example.com', '12345')
        with self.assertRaisesMessage(AccountValidationError, "'user1' already exists"):
            self.call_command([username])

    def test_create_user_with_course(self):
        """
        Create users and have them enroll in a course
        """
        usernames = ['username1', 'username2']
        self.call_command(usernames, course=self.course_id)

        # Check that the users exist and were enrolled in the course with the default settings
        users = self.user_model.objects.filter(username__in=usernames).all()
        assert len(users) == len(usernames)
        for user in users:
            enrollment = CourseEnrollment.get_enrollment(user, self.course.id)
            assert enrollment.mode == 'audit'
            assert not CourseStaffRole(self.course.id).has_user(user)

    def test_create_user_with_course__bad_course(self):
        """
        The test passes in a bad course id, no users or CourseEnrollments should be created
        """
        with pytest.raises(InvalidKeyError):
            self.call_command(['username1'], course='invalid_course_id')

        # Verify no users have been created
        assert self.num_users_start == len(self.user_model.objects.all())
        # Verify that no one is enrolled in the course
        assert len(CourseEnrollment.objects.filter(course__id=self.course.id)) == 0

    def test_create_user__mode(self):
        """
        Create a test for a user in verified mode.
        """
        # Create a user in verified rather than default audit
        username = 'user1'
        self.call_command([username], course=self.course_id, mode='verified')

        # Verify enrollment
        user = self.user_model.objects.get(username='user1')
        enrollment = CourseEnrollment.get_enrollment(user, self.course.id)
        assert enrollment.mode == 'verified'

    def test_create_user__mode__invalid(self):
        """
        Create a test for a user in an invalid mode.
        """
        username = 'user1'
        with self.assertRaisesMessage(CommandError, "argument --mode: invalid choice: 'cataclysmic'"):
            self.call_command([username], course=self.course_id, mode='cataclysmic')

    def test_create_user__domain(self):
        """
        Create a test user with a specific email domain
        """
        username = 'user1'
        domain = 'another-super-example.horse'
        self.call_command([username], domain=domain)

        user = self.user_model.objects.get(username=username)
        assert user.email == f'{username}@{domain}'

    def test_create_user__email_taken(self):
        """
        Try to create a user with a taken email
        """
        existing_username = 'some-username'
        self.user_model.objects.create_user(existing_username, 'taken_email@example.com', '12345')
        with pytest.raises(ValidationError):
            self.call_command(['taken_email'], domain='example.com')

    def test_create_user__bad_domain(self):
        """
        Try to create a user with a bad email domain
        """
        username = 'user1'
        with pytest.raises(ValidationError):
            self.call_command([username], domain='this-aint-no-domain')
        assert not self.user_model.objects.filter(username=username).exists()

    def test_create_user__password(self):
        """
        Create test user with specified password
        """
        username = 'user1'
        password = 'somepassword1234512341241234'
        self.call_command([username], password=password)

        assert self.client.login(username=username, password=password)

    def test_create_user__password__error(self):
        """
        Try to create user with a password that's too short
        """
        self.call_command(['user1'], password='a')

    def test_create_user__course_staff(self):
        """
        Create a user and set them as course staff
        """
        username = 'user1'
        self.call_command([username], course=self.course_id, course_staff=True)

        user = self.user_model.objects.get(username=username)
        enrollment = CourseEnrollment.get_enrollment(user, self.course.id)
        assert enrollment.mode == 'audit'
        assert CourseStaffRole(self.course.id).has_user(user)

    def test_create_user__course_staff__ignore_mode(self):
        """
        Test that mode is ignored when --course_staff is specified
        """
        username = 'user1'
        self.call_command([username], course=self.course_id, course_staff=True, mode='verified')

        user = self.user_model.objects.get(username=username)
        enrollment = CourseEnrollment.get_enrollment(user, self.course.id)
        assert enrollment.mode == 'audit'
        assert CourseStaffRole(self.course.id).has_user(user)

    def test_create_user__ignore_course_staff_and_mode_when_no_course(self):
        """
        Test that --course_staff and --mode are ignored when there is no --course
        """
        username = 'user1'
        self.call_command([username], course_staff=True, mode='verified')

        user = self.user_model.objects.get(username=username)
        assert not CourseAccessRole.objects.filter(user=user).exists()
        assert not CourseEnrollment.objects.filter(user=user).exists()

    def test_create_user__ignore_user_already_exists(self):
        """
        Test that ignore_user_already_exists will allow us to specify a username
        that already exists without raising an exception
        """
        test_username = 'IgnoreUserAlreadyExistsUser'
        assert not self.user_model.objects.filter(username=test_username).exists()

        self.call_command([test_username])
        assert self.user_model.objects.filter(username=test_username).exists()

        with self.assertRaises(ValidationError):
            self.call_command([test_username], ignore_user_already_exists=False)

        self.call_command([test_username], ignore_user_already_exists=True)
