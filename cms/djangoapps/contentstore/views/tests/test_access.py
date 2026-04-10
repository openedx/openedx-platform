"""
Tests access.py
"""


from django.test import TestCase
from opaque_keys.edx.locator import CourseLocator

from common.djangoapps.student.auth import add_users
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import AdminFactory, UserFactory

from ..access import get_user_role


class RolesTest(TestCase):
    """
    Tests for lti user role serialization.
    """
    def setUp(self):
        """ Test case setup """
        super().setUp()

        self.global_admin = AdminFactory()
        self.instructor = UserFactory.create(
            username='testinstructor',
            email='testinstructor+courses@edx.org',
            password='foo',
        )
        self.staff = UserFactory.create(
            username='teststaff',
            email='teststaff+courses@edx.org',
            password='foo',
        )
        self.course_key = CourseLocator('mitX', '101', 'test')

    def test_get_user_role_instructor(self):
        """
        Verifies if user is instructor.
        """
        add_users(self.global_admin, CourseInstructorRole(self.course_key), self.instructor)
        self.assertEqual(  # noqa: PT009
            'instructor',
            get_user_role(self.instructor, self.course_key)
        )
        add_users(self.global_admin, CourseStaffRole(self.course_key), self.staff)
        self.assertEqual(  # noqa: PT009
            'instructor',
            get_user_role(self.instructor, self.course_key)
        )

    def test_get_user_role_staff(self):
        """
        Verifies if user is staff.
        """
        add_users(self.global_admin, CourseStaffRole(self.course_key), self.staff)
        self.assertEqual(  # noqa: PT009
            'staff',
            get_user_role(self.staff, self.course_key)
        )
