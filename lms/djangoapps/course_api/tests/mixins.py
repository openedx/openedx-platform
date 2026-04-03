"""
Common mixins for Course API Tests
"""


from datetime import datetime

from common.djangoapps.student.tests.factories import CourseAccessRoleFactory, CourseEnrollmentFactory, UserFactory
from xmodule.modulestore.tests.factories import ToyCourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

TEST_PASSWORD = 'Password1234'


class CourseApiFactoryMixin:
    """
    Mixin to allow creation of test courses and users.
    """

    @staticmethod
    def create_course(end=datetime(2015, 9, 19, 18, 0, 0), **kwargs):
        """
        Create a course for use in test cases
        """
        return ToyCourseFactory.create(
            end=end,
            enrollment_start=datetime(2015, 6, 15, 0, 0, 0),
            enrollment_end=datetime(2015, 7, 15, 0, 0, 0),
            certificate_available_date=datetime(2015, 8, 14, 0, 0, 0),
            emit_signals=True,
            **kwargs
        )

    @staticmethod
    def create_user(username, is_staff):
        """
        Create a user as identified by username, email, password and is_staff.
        """
        return UserFactory(
            username=username,
            email=f'{username}@example.com',
            password=TEST_PASSWORD,
            is_staff=is_staff
        )

    @staticmethod
    def create_enrollment(**kwargs):
        """
        Create a CourseEnrollment to use in tests.
        """
        return CourseEnrollmentFactory(**kwargs)

    @staticmethod
    def create_courseaccessrole(**kwargs):
        """
        Create a CourseAccessRole to use in tests.
        """
        return CourseAccessRoleFactory(**kwargs)
