"""
Enrollments Service Tests
"""
import ddt

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.enrollments.services import EnrollmentsService
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)


@ddt.ddt
class EnrollmentsServiceTests(ModuleStoreTestCase):
    """
    Tests for Enrollments Service
    """
    def setUp(self):
        super().setUp()
        self.service = EnrollmentsService()
        self.course_modes = [
            CourseMode.AUDIT,
            CourseMode.EXECUTIVE_EDUCATION,
            CourseMode.HONOR,
            CourseMode.MASTERS,
            CourseMode.PROFESSIONAL,
            CourseMode.VERIFIED
        ]
        self.users = []
        self.course = CourseOverviewFactory.create(enable_proctored_exams=True)

        for index in range(len(self.course_modes)):
            course_mode = self.course_modes[index]
            course_id = self.course.id

            CourseModeFactory.create(mode_slug=course_mode, course_id=course_id)
            user = UserFactory(
                username=f'user{index}',
                email=f'LEARNER{index}@example.com'
            )
            self.users.append(user)
            CourseEnrollment.enroll(user, course_id, mode=course_mode)

    def enrollment_to_dict(self, enrollment):
        return {'username': enrollment.username, 'mode': enrollment.mode}

    def test_get_active_enrollments_by_course(self):
        """
        Test that it returns a list of active enrollments for a course
        """
        enrollments = self.service.get_active_enrollments_by_course(course_id=str(self.course.id))
        self.assertEqual(len(self.course_modes), len(enrollments))  # noqa: PT009

    def test_get_active_enrollments_by_course_and_user(self):
        """
        Test that it returns a list of active enrollments for a course and user
        """
        enrollment = self.service.get_active_enrollments_by_course_and_user(
            str(self.course.id),
            self.users[0].id,
        )
        expected_values = {'username': 'user0', 'mode': 'audit'}
        self.assertDictEqual(self.enrollment_to_dict(enrollment), expected_values)  # noqa: PT009

    def test_get_active_enrollments_by_course_and_user_none_course(self):
        """
        Test that it returns a list of active enrollments for a course and user
        for a non existing enrollment
        """
        enrollments = self.service.get_active_enrollments_by_course_and_user(
            'course-v1:testx+none+123',
            self.users[0].id,
        )
        self.assertIsNone(enrollments)  # noqa: PT009

    def test_get_active_enrollments_by_course_and_user_none_user(self):
        """
        Test getting enrollments for a course and non existing user raise error
        """
        with self.assertRaises(AssertionError):  # noqa: PT027
            self.service.get_active_enrollments_by_course_and_user(
                str(self.course.id),
                0000,
            )

    def test_get_enrollments_can_take_proctored_exams_by_course(self):
        """
        Test that it returns a list of active enrollments
        """
        enrollments = self.service.get_enrollments_can_take_proctored_exams(str(self.course.id))

        expected_values = [
            {'username': 'user1', 'mode': 'executive-education'},
            {'username': 'user3', 'mode': 'masters'},
            {'username': 'user4', 'mode': 'professional'},
            {'username': 'user5', 'mode': 'verified'}
        ]
        actual_values = [self.enrollment_to_dict(e) for e in enrollments]
        self.assertEqual(actual_values, expected_values)  # noqa: PT009

    def test_get_enrollments_can_take_proctored_exams_by_course_ignore_inactive(self):
        """
        Test that inactive enrollments are ignored
        """
        inactive_enrollment = CourseEnrollment.objects.get(course_id=self.course.id, user__username='user1')
        inactive_enrollment.is_active = False
        inactive_enrollment.save()

        enrollments = self.service.get_enrollments_can_take_proctored_exams(str(self.course.id))

        assert len(enrollments) == 3

    def test_get_enrollments_can_take_proctored_exams_no_enrollments(self):
        """
        Test that an empty list is returned if a course has no enrollments
        """
        course = CourseOverviewFactory.create(enable_proctored_exams=True)

        enrollments = self.service.get_enrollments_can_take_proctored_exams(str(course.id))  # pylint: disable=no-member

        assert not enrollments.exists()

    def test_get_enrollments_can_take_proctored_exams_allow_honor(self):
        self.course.proctoring_provider = 'test'
        self.course.save()

        mock_proctoring_backend = {
            'test': {
                'allow_honor_mode': True
            }
        }
        with self.settings(PROCTORING_BACKENDS=mock_proctoring_backend):
            enrollments = self.service.get_enrollments_can_take_proctored_exams(str(self.course.id))

        expected_values = [
            {'username': 'user1', 'mode': 'executive-education'},
            {'username': 'user2', 'mode': 'honor'},
            {'username': 'user3', 'mode': 'masters'},
            {'username': 'user4', 'mode': 'professional'},
            {'username': 'user5', 'mode': 'verified'}

        ]
        actual_values = [self.enrollment_to_dict(e) for e in enrollments]
        self.assertEqual(actual_values, expected_values)  # noqa: PT009

    def test_get_enrollments_can_take_proctored_exams_not_enable_proctored_exams(self):
        self.course.enable_proctored_exams = False
        self.course.save()

        enrollments = self.service.get_enrollments_can_take_proctored_exams(str(self.course.id))

        assert enrollments is None

    def test_get_enrollments_can_take_proctored_exams_no_course(self):
        enrollments = self.service.get_enrollments_can_take_proctored_exams('org.0/course_0/Run_100')

        assert enrollments is None

    @ddt.data('ser', 'uSeR', 'leaRNer', 'LEARNER', '@example.com')
    def test_text_search_partial_return_all(self, text_search):
        enrollments = self.service.get_enrollments_can_take_proctored_exams(
            str(self.course.id),
            text_search=text_search
        )

        expected_values = [
            {'username': 'user1', 'mode': 'executive-education'},
            {'username': 'user3', 'mode': 'masters'},
            {'username': 'user4', 'mode': 'professional'},
            {'username': 'user5', 'mode': 'verified'}
        ]
        actual_values = [self.enrollment_to_dict(e) for e in enrollments]
        self.assertEqual(actual_values, expected_values)  # noqa: PT009

    def test_text_search_partial_return_some(self):
        enrollments = self.service.get_enrollments_can_take_proctored_exams(
            str(self.course.id),
            text_search='3'
        )

        expected_values = [
            {'username': 'user3', 'mode': 'masters'}
        ]
        actual_values = [self.enrollment_to_dict(e) for e in enrollments]
        self.assertEqual(actual_values, expected_values)  # noqa: PT009

    @ddt.data('user1', 'USER1', 'LEARNER1@example.com', 'lEarNer1@eXAMPLE.com')
    def test_text_search_exact_return_one(self, text_search):
        enrollments = self.service.get_enrollments_can_take_proctored_exams(
            str(self.course.id),
            text_search=text_search
        )

        expected_values = [
            {'username': 'user1', 'mode': 'executive-education'}
        ]
        actual_values = [self.enrollment_to_dict(e) for e in enrollments]
        self.assertEqual(actual_values, expected_values)  # noqa: PT009

    def test_text_search_return_none(self):
        enrollments = self.service.get_enrollments_can_take_proctored_exams(
            str(self.course.id),
            text_search='abc'
        )

        assert not enrollments.exists()


@ddt.ddt
class EnrollmentsServicePerformanceTests(ModuleStoreTestCase):
    """
    Tests for Enrollments Service performance
    """
    def setUp(self):
        super().setUp()
        self.service = EnrollmentsService()
        self.course = CourseOverviewFactory.create(enable_proctored_exams=True)
        self.course_modes = [
            CourseMode.AUDIT,
            CourseMode.EXECUTIVE_EDUCATION,
            CourseMode.HONOR,
            CourseMode.MASTERS,
            CourseMode.PROFESSIONAL,
            CourseMode.VERIFIED,
        ]

        for index in range(len(self.course_modes)):
            CourseModeFactory.create(mode_slug=self.course_modes[index], course_id=self.course.id)

    def create_and_enroll_users(self, num_users):
        num_course_modes = len(self.course_modes)
        for index in range(num_users):
            user = UserFactory(username=f'user{index}')
            CourseEnrollment.enroll(user, self.course.id, mode=self.course_modes[index % num_course_modes])

    @ddt.data(10, 25, 50)
    def test_get_enrollments_can_take_proctored_exams_num_queries(self, num_users):
        self.create_and_enroll_users(num_users)

        with self.assertNumQueries(1):
            enrollments = self.service.get_enrollments_can_take_proctored_exams(str(self.course.id))
            # force execution of the QuerySet so that queries are exectued
            repr(enrollments)

    @ddt.data(10, 25, 50)
    def test_get_enrollments_can_take_proctored_exams_num_queries_text_search(self, num_users):
        self.create_and_enroll_users(num_users)

        with self.assertNumQueries(1):
            enrollments = self.service.get_enrollments_can_take_proctored_exams(str(self.course.id), text_search='edX')
            # force execution of the QuerySet so that queries are exectued
            repr(enrollments)
