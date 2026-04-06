"""
Test Student api.py
"""

from common.djangoapps.student.api import (
    get_course_enrollments,
    is_user_enrolled_in_course,
    is_user_staff_or_instructor_in_course,
)
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import (
    CourseEnrollmentFactory,
    GlobalStaffFactory,
    InstructorFactory,
    StaffFactory,
    UserFactory,
)
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class TestStudentApi(SharedModuleStoreTestCase):
    """
    Tests for functionality in the api.py file of the Student django app.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.course_run_key = self.course.id

    def test_is_user_enrolled_in_course(self):
        """
        Verify the correct value is returned when a learner is actively enrolled in a course-run.
        """
        CourseEnrollmentFactory.create(user_id=self.user.id, course_id=self.course.id)

        result = is_user_enrolled_in_course(self.user, self.course_run_key)
        assert result

    def test_is_user_enrolled_in_course_not_active(self):
        """
        Verify the correct value is returned when a learner is not actively enrolled in a course-run.
        """
        CourseEnrollmentFactory.create(user_id=self.user.id, course_id=self.course.id, is_active=False)

        result = is_user_enrolled_in_course(self.user, self.course_run_key)
        assert not result

    def test_is_user_enrolled_in_course_no_enrollment(self):
        """
        Verify the correct value is returned when a learner is not enrolled in a course-run.
        """
        result = is_user_enrolled_in_course(self.user, self.course_run_key)
        assert not result

    def test_is_user_staff_or_instructor(self):
        """
        Verify the correct value is returned for users with different access levels.
        """
        course_id_string = str(self.course.id)
        global_staff_user = GlobalStaffFactory.create()
        staff_user = StaffFactory.create(course_key=self.course_run_key)
        instructor = InstructorFactory.create(course_key=self.course_run_key)

        different_course = CourseFactory.create()
        instructor_different_course = InstructorFactory.create(course_key=different_course.id)

        assert is_user_staff_or_instructor_in_course(instructor, course_id_string)
        assert is_user_staff_or_instructor_in_course(global_staff_user, self.course_run_key)
        assert is_user_staff_or_instructor_in_course(staff_user, self.course_run_key)
        assert is_user_staff_or_instructor_in_course(instructor, self.course_run_key)
        assert not is_user_staff_or_instructor_in_course(self.user, self.course_run_key)
        assert not is_user_staff_or_instructor_in_course(instructor_different_course, self.course_run_key)

    def test_get_course_enrollments(self):
        """Verify all enrollments can be retrieved"""
        course_2 = CourseFactory.create()
        CourseEnrollmentFactory.create(user_id=self.user.id, course_id=self.course.id)
        CourseEnrollmentFactory.create(user_id=self.user.id, course_id=course_2.id)
        expected = CourseEnrollment.objects.all()

        result = get_course_enrollments(self.user)

        self.assertEqual(list(expected), list(result))

    def test_get_filtered_course_enrollments(self):
        """Verify a filtered subset of enrollments can be retrieved"""
        course_2 = CourseFactory.create()
        CourseEnrollmentFactory.create(user_id=self.user.id, course_id=self.course.id)
        ce_2 = CourseEnrollmentFactory.create(user_id=self.user.id, course_id=course_2.id)
        expected = CourseEnrollment.objects.filter(id=ce_2.id)

        result = get_course_enrollments(self.user, True, course_ids=[course_2.id])

        self.assertEqual(list(expected), list(result))
