"""
Test the create_random_users command line script
"""


import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from opaque_keys import InvalidKeyError

from common.djangoapps.student.models import CourseEnrollment
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class CreateRandomUserTests(SharedModuleStoreTestCase):
    """
    Test creating random users via command line, with various options
    """
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.user_model = get_user_model()
        self.num_users_start = len(self.user_model.objects.all())

    def test_create_users(self):
        """
        The command should create users_to_create number of random users
        """
        users_to_create = 5
        call_command('create_random_users', str(users_to_create))

        # Verify correct number of users are now in the database
        assert (self.num_users_start + users_to_create) == len(self.user_model.objects.all())

    def test_create_users_with_course(self):
        """
        The command should create users_to_create number of random users and add them to self.course
        """
        users_to_create = 3
        call_command('create_random_users', str(users_to_create), str(self.course.id))

        # Verify correct number of users are now in the database
        assert (self.num_users_start + users_to_create) == len(self.user_model.objects.all())

        # Verify that the users are enrolled in our course
        assert len(CourseEnrollment.objects.filter(course__id=self.course.id)) == users_to_create

    def test_create_users_with_bad_course(self):
        """
        The test passes in a bad course id, no users or CourseEnrollments should be created
        """
        users_to_create = 3

        with pytest.raises(InvalidKeyError):
            call_command('create_random_users', str(users_to_create), 'invalid_course_id')

        # Verify correct number of users are now in the database
        assert self.num_users_start == len(self.user_model.objects.all())

        # Verify that the users are enrolled in our course
        assert len(CourseEnrollment.objects.filter(course__id=self.course.id)) == 0
