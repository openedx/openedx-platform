"""
Tests for masquerading functionality on course_experience
"""

from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.courseware.tests.helpers import MasqueradeMixin
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order
from xmodule.partitions.partitions import (
    ENROLLMENT_TRACK_PARTITION_ID,  # pylint: disable=wrong-import-order
)
from xmodule.partitions.partitions_service import PartitionService  # pylint: disable=wrong-import-order

from .helpers import add_course_mode

TEST_PASSWORD = 'Password1234'


class MasqueradeTestBase(SharedModuleStoreTestCase, MasqueradeMixin):
    """
    Base test class for masquerading functionality on course_experience
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Create two courses
        cls.verified_course = CourseFactory.create()
        cls.masters_course = CourseFactory.create()
        # Create a verifiable course mode with an upgrade deadline in each course
        add_course_mode(cls.verified_course, upgrade_deadline_expired=False)
        add_course_mode(cls.masters_course, upgrade_deadline_expired=False)
        add_course_mode(cls.masters_course, mode_slug='masters', mode_display_name='Masters')

    def setUp(self):
        super().setUp()
        self.course_staff = UserFactory.create()
        CourseStaffRole(self.verified_course.id).add_users(self.course_staff)
        CourseStaffRole(self.masters_course.id).add_users(self.course_staff)

        # Enroll the user in the two courses
        CourseEnrollmentFactory.create(user=self.course_staff, course_id=self.verified_course.id)
        CourseEnrollmentFactory.create(user=self.course_staff, course_id=self.masters_course.id)

        # Log the staff user in
        self.client.login(username=self.course_staff.username, password=TEST_PASSWORD)

    def get_group_id_by_course_mode_name(self, course_id, mode_name):
        """
        Get the needed group_id from the Enrollment_Track partition for the specific masquerading track.
        """
        partition_service = PartitionService(course_id)
        enrollment_track_user_partition = partition_service.get_user_partition(ENROLLMENT_TRACK_PARTITION_ID)
        for group in enrollment_track_user_partition.groups:
            if group.name == mode_name:
                return group.id
        return None
