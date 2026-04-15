"""
test utils
"""


import uuid
from smtplib import SMTPException
from unittest import mock

from ccx_keys.locator import CCXLocator

from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentException
from common.djangoapps.student.roles import CourseCcxCoachRole, CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import AdminFactory
from lms.djangoapps.ccx.tests.factories import CcxFactory
from lms.djangoapps.ccx.tests.utils import CcxTestCase
from lms.djangoapps.ccx.utils import add_master_course_staff_to_ccx, ccx_course, remove_master_course_staff_from_ccx
from lms.djangoapps.instructor.access import list_with_level
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class TestGetCCXFromCCXLocator(ModuleStoreTestCase):
    """Verify that get_ccx_from_ccx_locator functions properly"""
    def setUp(self):
        """Set up a course, coach, ccx and user"""
        super().setUp()
        self.course = CourseFactory.create()
        coach = self.coach = AdminFactory.create()
        role = CourseCcxCoachRole(self.course.id)
        role.add_users(coach)

    def call_fut(self, course_id):
        """call the function under test in this test case"""
        from lms.djangoapps.ccx.utils import get_ccx_from_ccx_locator
        return get_ccx_from_ccx_locator(course_id)

    def test_non_ccx_locator(self):
        """verify that nothing is returned if locator is not a ccx locator
        """
        result = self.call_fut(self.course.id)
        assert result is None

    def test_ccx_locator(self):
        """verify that the ccx is retuned if using a ccx locator
        """
        ccx = CcxFactory(course_id=self.course.id, coach=self.coach)
        course_key = CCXLocator.from_course_locator(self.course.id, ccx.id)
        result = self.call_fut(course_key)
        assert result == ccx


class TestStaffOnCCX(CcxTestCase):
    """
    Tests for staff on ccx courses.
    """
    def setUp(self):
        super().setUp()

        # Create instructor account
        self.client.login(username=self.coach.username, password="test")

        # create an instance of modulestore
        self.mstore = modulestore()

        self.make_coach()
        self.ccx = self.make_ccx()
        self.ccx_locator = CCXLocator.from_course_locator(self.course.id, self.ccx.id)

    def test_add_master_course_staff_to_ccx(self):
        """
        Test add staff of master course to ccx course
        """
        # adding staff to master course.
        staff = self.make_staff()
        assert CourseStaffRole(self.course.id).has_user(staff)

        # adding instructor to master course.
        instructor = self.make_instructor()
        assert CourseInstructorRole(self.course.id).has_user(instructor)

        add_master_course_staff_to_ccx(self.course, self.ccx_locator, self.ccx.display_name)

        # assert that staff and instructors of master course has staff and instructor roles on ccx
        list_staff_master_course = list_with_level(self.course.id, 'staff')
        list_instructor_master_course = list_with_level(self.course.id, 'instructor')

        with ccx_course(self.ccx_locator) as course_ccx:
            list_staff_ccx_course = list_with_level(course_ccx.id, 'staff')
            assert len(list_staff_master_course) == len(list_staff_ccx_course)
            assert list_staff_master_course[0].email == list_staff_ccx_course[0].email

            list_instructor_ccx_course = list_with_level(course_ccx.id, 'instructor')
            assert len(list_instructor_ccx_course) == len(list_instructor_master_course)
            assert list_instructor_ccx_course[0].email == list_instructor_master_course[0].email

    def test_add_master_course_staff_to_ccx_with_exception(self):
        """
        When exception raise from ``enroll_email`` assert that enrollment skipped for that staff or
        instructor.
        """
        staff = self.make_staff()
        assert CourseStaffRole(self.course.id).has_user(staff)

        # adding instructor to master course.
        instructor = self.make_instructor()
        assert CourseInstructorRole(self.course.id).has_user(instructor)

        with mock.patch.object(CourseEnrollment, 'enroll_by_email', side_effect=CourseEnrollmentException()):
            add_master_course_staff_to_ccx(self.course, self.ccx_locator, self.ccx.display_name)

            assert not CourseEnrollment.objects.filter(course_id=self.ccx_locator, user=staff).exists()
            assert not CourseEnrollment.objects.filter(course_id=self.ccx_locator, user=instructor).exists()

        with mock.patch.object(CourseEnrollment, 'enroll_by_email', side_effect=SMTPException()):
            add_master_course_staff_to_ccx(self.course, self.ccx_locator, self.ccx.display_name)

            assert not CourseEnrollment.objects.filter(course_id=self.ccx_locator, user=staff).exists()
            assert not CourseEnrollment.objects.filter(course_id=self.ccx_locator, user=instructor).exists()

    def test_remove_master_course_staff_from_ccx(self):
        """
        Test remove staff of master course to ccx course
        """
        staff = self.make_staff()
        assert CourseStaffRole(self.course.id).has_user(staff)

        # adding instructor to master course.
        instructor = self.make_instructor()
        assert CourseInstructorRole(self.course.id).has_user(instructor)

        add_master_course_staff_to_ccx(self.course, self.ccx_locator, self.ccx.display_name, send_email=False)

        list_staff_master_course = list_with_level(self.course.id, 'staff')
        list_instructor_master_course = list_with_level(self.course.id, 'instructor')

        with ccx_course(self.ccx_locator) as course_ccx:
            list_staff_ccx_course = list_with_level(course_ccx.id, 'staff')
            assert len(list_staff_master_course) == len(list_staff_ccx_course)
            assert list_staff_master_course[0].email == list_staff_ccx_course[0].email

            list_instructor_ccx_course = list_with_level(course_ccx.id, 'instructor')
            assert len(list_instructor_ccx_course) == len(list_instructor_master_course)
            assert list_instructor_ccx_course[0].email == list_instructor_master_course[0].email

            # assert that role of staff and instructors of master course removed from ccx.
            remove_master_course_staff_from_ccx(
                self.course, self.ccx_locator, self.ccx.display_name, send_email=False
            )
            list_staff_ccx_course = list_with_level(course_ccx.id, 'staff')
            assert len(list_staff_master_course) != len(list_staff_ccx_course)

            list_instructor_ccx_course = list_with_level(course_ccx.id, 'instructor')
            assert len(list_instructor_ccx_course) != len(list_instructor_master_course)

            for user in list_staff_master_course:
                assert user not in list_staff_ccx_course
            for user in list_instructor_master_course:
                assert user not in list_instructor_ccx_course

    def test_remove_master_course_staff_from_ccx_idempotent(self):
        """
        Test remove staff of master course from ccx course
        """
        staff = self.make_staff()
        assert CourseStaffRole(self.course.id).has_user(staff)

        # adding instructor to master course.
        instructor = self.make_instructor()
        assert CourseInstructorRole(self.course.id).has_user(instructor)

        outbox = self.get_outbox()
        assert len(outbox) == 0
        add_master_course_staff_to_ccx(self.course, self.ccx_locator, self.ccx.display_name, send_email=False)

        list_staff_master_course = list_with_level(self.course.id, 'staff')
        list_instructor_master_course = list_with_level(self.course.id, 'instructor')

        with ccx_course(self.ccx_locator) as course_ccx:
            list_staff_ccx_course = list_with_level(course_ccx.id, 'staff')
            assert len(list_staff_master_course) == len(list_staff_ccx_course)
            assert list_staff_master_course[0].email == list_staff_ccx_course[0].email

            list_instructor_ccx_course = list_with_level(course_ccx.id, 'instructor')
            assert len(list_instructor_ccx_course) == len(list_instructor_master_course)
            assert list_instructor_ccx_course[0].email == list_instructor_master_course[0].email

            # assert that role of staff and instructors of master course removed from ccx.
            remove_master_course_staff_from_ccx(
                self.course, self.ccx_locator, self.ccx.display_name, send_email=True
            )
            assert len(outbox) == (len(list_staff_master_course) + len(list_instructor_master_course))

            list_staff_ccx_course = list_with_level(course_ccx.id, 'staff')
            assert len(list_staff_master_course) != len(list_staff_ccx_course)

            list_instructor_ccx_course = list_with_level(course_ccx.id, 'instructor')
            assert len(list_instructor_ccx_course) != len(list_instructor_master_course)

            for user in list_staff_master_course:
                assert user not in list_staff_ccx_course
            for user in list_instructor_master_course:
                assert user not in list_instructor_ccx_course

        # Run again
        remove_master_course_staff_from_ccx(self.course, self.ccx_locator, self.ccx.display_name)
        assert len(outbox) == (len(list_staff_master_course) + len(list_instructor_master_course))

        with ccx_course(self.ccx_locator) as course_ccx:
            list_staff_ccx_course = list_with_level(course_ccx.id, 'staff')
            assert len(list_staff_master_course) != len(list_staff_ccx_course)

            list_instructor_ccx_course = list_with_level(course_ccx.id, 'instructor')
            assert len(list_instructor_ccx_course) != len(list_instructor_master_course)

            for user in list_staff_master_course:
                assert user not in list_staff_ccx_course
            for user in list_instructor_master_course:
                assert user not in list_instructor_ccx_course

    def test_add_master_course_staff_to_ccx_display_name(self):
        """
        Test add staff of master course to ccx course.
        Specific test to check that a passed display name is in the
        subject of the email sent to the enrolled users.
        """
        staff = self.make_staff()
        assert CourseStaffRole(self.course.id).has_user(staff)

        # adding instructor to master course.
        instructor = self.make_instructor()
        assert CourseInstructorRole(self.course.id).has_user(instructor)
        outbox = self.get_outbox()
        # create a unique display name
        display_name = f'custom_display_{uuid.uuid4()}'
        list_staff_master_course = list_with_level(self.course.id, 'staff')
        list_instructor_master_course = list_with_level(self.course.id, 'instructor')
        assert len(outbox) == 0
        # give access to the course staff/instructor
        add_master_course_staff_to_ccx(self.course, self.ccx_locator, display_name)
        assert len(outbox) == (len(list_staff_master_course) + len(list_instructor_master_course))
        for email in outbox:
            assert display_name in email.subject

    def test_remove_master_course_staff_from_ccx_display_name(self):
        """
        Test remove role of staff of master course on ccx course.
        Specific test to check that a passed display name is in the
        subject of the email sent to the unenrolled users.
        """
        staff = self.make_staff()
        assert CourseStaffRole(self.course.id).has_user(staff)

        # adding instructor to master course.
        instructor = self.make_instructor()
        assert CourseInstructorRole(self.course.id).has_user(instructor)
        outbox = self.get_outbox()
        add_master_course_staff_to_ccx(self.course, self.ccx_locator, self.ccx.display_name, send_email=False)
        # create a unique display name
        display_name = f'custom_display_{uuid.uuid4()}'
        list_staff_master_course = list_with_level(self.course.id, 'staff')
        list_instructor_master_course = list_with_level(self.course.id, 'instructor')
        assert len(outbox) == 0
        # give access to the course staff/instructor
        remove_master_course_staff_from_ccx(self.course, self.ccx_locator, display_name)
        assert len(outbox) == (len(list_staff_master_course) + len(list_instructor_master_course))
        for email in outbox:
            assert display_name in email.subject

    def test_add_master_course_staff_to_ccx_idempotent(self):
        """
        Test add staff of master course to ccx course multiple time will
        not result in multiple enrollments.
        """
        staff = self.make_staff()
        assert CourseStaffRole(self.course.id).has_user(staff)

        # adding instructor to master course.
        instructor = self.make_instructor()
        assert CourseInstructorRole(self.course.id).has_user(instructor)
        outbox = self.get_outbox()
        list_staff_master_course = list_with_level(self.course.id, 'staff')
        list_instructor_master_course = list_with_level(self.course.id, 'instructor')
        assert len(outbox) == 0

        # run the assignment the first time
        add_master_course_staff_to_ccx(self.course, self.ccx_locator, self.ccx.display_name)
        assert len(outbox) == (len(list_staff_master_course) + len(list_instructor_master_course))
        with ccx_course(self.ccx_locator) as course_ccx:
            list_staff_ccx_course = list_with_level(course_ccx.id, 'staff')
            list_instructor_ccx_course = list_with_level(course_ccx.id, 'instructor')
        assert len(list_staff_master_course) == len(list_staff_ccx_course)
        for user in list_staff_master_course:
            assert user in list_staff_ccx_course
        assert len(list_instructor_master_course) == len(list_instructor_ccx_course)
        for user in list_instructor_master_course:
            assert user in list_instructor_ccx_course

        # run the assignment again
        add_master_course_staff_to_ccx(self.course, self.ccx_locator, self.ccx.display_name)
        # there are no new duplicated email
        assert len(outbox) == (len(list_staff_master_course) + len(list_instructor_master_course))
        # there are no duplicated staffs
        with ccx_course(self.ccx_locator) as course_ccx:
            list_staff_ccx_course = list_with_level(course_ccx.id, 'staff')
            list_instructor_ccx_course = list_with_level(course_ccx.id, 'instructor')
        assert len(list_staff_master_course) == len(list_staff_ccx_course)
        for user in list_staff_master_course:
            assert user in list_staff_ccx_course
        assert len(list_instructor_master_course) == len(list_instructor_ccx_course)
        for user in list_instructor_master_course:
            assert user in list_instructor_ccx_course

    def test_add_master_course_staff_to_ccx_no_email(self):
        """
        Test add staff of master course to ccx course without
        sending enrollment email.
        """
        staff = self.make_staff()
        assert CourseStaffRole(self.course.id).has_user(staff)

        # adding instructor to master course.
        instructor = self.make_instructor()
        assert CourseInstructorRole(self.course.id).has_user(instructor)
        outbox = self.get_outbox()
        assert len(outbox) == 0
        add_master_course_staff_to_ccx(self.course, self.ccx_locator, self.ccx.display_name, send_email=False)
        assert len(outbox) == 0

    def test_remove_master_course_staff_from_ccx_no_email(self):
        """
        Test remove role of staff of master course on ccx course without
        sending enrollment email.
        """
        staff = self.make_staff()
        assert CourseStaffRole(self.course.id).has_user(staff)

        # adding instructor to master course.
        instructor = self.make_instructor()
        assert CourseInstructorRole(self.course.id).has_user(instructor)
        outbox = self.get_outbox()
        assert len(outbox) == 0
        remove_master_course_staff_from_ccx(self.course, self.ccx_locator, self.ccx.display_name, send_email=False)
        assert len(outbox) == 0
