"""Tests for course run serializers"""


import datetime

import ddt
import pytz
from django.test import RequestFactory

from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.lib.courses import course_image_url
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ...serializers.course_runs import CourseRunSerializer
from ..utils import serialize_datetime


@ddt.ddt
class CourseRunSerializerTests(ModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    def setUp(self):
        super().setUp()

        self.course_start = datetime.datetime.now(pytz.UTC)
        self.course_end = self.course_start + datetime.timedelta(days=30)

        self.request = RequestFactory().get('')

    def setup_course(self, self_paced, enrollment_start=None, enrollment_end=None):
        return CourseFactory(
            start=self.course_start,
            end=self.course_end,
            self_paced=self_paced,
            enrollment_start=enrollment_start,
            enrollment_end=enrollment_end
        )

    def setup_course_user_roles(self, course):
        """
        get course staff and instructor roles user
        """
        instructor = UserFactory()
        CourseInstructorRole(course.id).add_users(instructor)
        staff = UserFactory()
        CourseStaffRole(course.id).add_users(staff)

        return instructor, staff

    def get_expected_course_data(
        self, course, enrollment_start, enrollment_end,
        instructor, staff, expected_pacing_type
    ):
        return {
            'id': str(course.id),
            'title': course.display_name,
            'schedule': {
                'start': serialize_datetime(self.course_start),
                'end': serialize_datetime(self.course_end),
                'enrollment_start': enrollment_start,
                'enrollment_end': enrollment_end,
            },
            'team': [
                {
                    'user': instructor.username,
                    'role': 'instructor',
                },
                {
                    'user': staff.username,
                    'role': 'staff',
                },
            ],
            'images': {
                'card_image': self.request.build_absolute_uri(course_image_url(course)),
            },
            'pacing_type': expected_pacing_type,
        }

    @ddt.data(
        ('instructor_paced', False),
        ('self_paced', True),
    )
    @ddt.unpack
    def test_data_with_enrollment_dates(self, expected_pacing_type, self_paced):
        """
        Verify that CourseRunSerializer serializes the course object.
        """

        enrollment_start = self.course_start - datetime.timedelta(days=7)
        enrollment_end = self.course_end - datetime.timedelta(days=14)
        course = self.setup_course(self_paced, enrollment_start, enrollment_end)
        instructor, staff = self.setup_course_user_roles(course)
        serializer = CourseRunSerializer(course, context={'request': self.request})

        expected_course_data = self.get_expected_course_data(
            course, serialize_datetime(enrollment_start), serialize_datetime(enrollment_end),
            instructor, staff, expected_pacing_type
        )

        assert serializer.data == expected_course_data

    @ddt.data(
        ('instructor_paced', False),
        ('self_paced', True),
    )
    @ddt.unpack
    def test_data_without_enrollment_dates(self, expected_pacing_type, self_paced):
        """
        Verify that CourseRunSerializer serializes the course object without enrollment
        start and end dates.
        """
        course = self.setup_course(self_paced)
        instructor, staff = self.setup_course_user_roles(course)
        serializer = CourseRunSerializer(course, context={'request': self.request})

        expected_course_data = self.get_expected_course_data(
            course, None, None, instructor, staff, expected_pacing_type
        )

        assert serializer.data == expected_course_data
