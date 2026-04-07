"""
Mixins classes being used by all test classes within this folder
"""


from datetime import datetime

from pytz import UTC

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, GlobalStaffFactory, UserFactory
from lms.djangoapps.program_enrollments.tests.factories import ProgramCourseEnrollmentFactory, ProgramEnrollmentFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class GradeViewTestMixin(SharedModuleStoreTestCase):
    """
    Mixin class for grades related view tests
    The following tests assume that the grading policy is the edX default one:
    {
        "GRADER": [
            {
                "drop_count": 2,
                "min_count": 12,
                "short_label": "HW",
                "type": "Homework",
                "weight": 0.15
            },
            {
                "drop_count": 2,
                "min_count": 12,
                "type": "Lab",
                "weight": 0.15
            },
            {
                "drop_count": 0,
                "min_count": 1,
                "short_label": "Midterm",
                "type": "Midterm Exam",
                "weight": 0.3
            },
            {
                "drop_count": 0,
                "min_count": 1,
                "short_label": "Final",
                "type": "Final Exam",
                "weight": 0.4
            }
        ],
        "GRADE_CUTOFFS": {
            "Pass": 0.5
        }
    }
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.date = datetime(2013, 1, 22, tzinfo=UTC)
        cls.course = cls._create_test_course_with_default_grading_policy(
            display_name='test course', run="Testing_course"
        )
        cls.empty_course = cls._create_test_course_with_default_grading_policy(
            display_name='empty test course', run="Empty_testing_course"
        )
        cls.course_key = cls.course.id

    def _create_user_enrollments(self, *users):
        for user in users:
            CourseEnrollmentFactory(
                course_id=self.course.id,
                user=user,
                created=self.date,
            )

    def _create_user_program_enrollments(self, *users, **kwargs):  # lint-amnesty, pylint: disable=missing-function-docstring
        # supply mode for enrollment. Use 'masters' to create a masters track enrollment
        for index, user in enumerate(users):
            course_enrollment = CourseEnrollmentFactory(
                course_id=self.course.id,
                user=user,
                created=self.date,
                mode=kwargs.get('mode', 'audit')
            )

            program_enrollment = ProgramEnrollmentFactory(
                user=user,
                external_user_key=f'program_user_key_{index}',
            )

            ProgramCourseEnrollmentFactory(
                program_enrollment=program_enrollment,
                course_enrollment=course_enrollment,
                course_key=self.course.id,
            )

    def setUp(self):
        super().setUp()
        self.password = self.TEST_PASSWORD
        self.global_staff = GlobalStaffFactory.create()
        self.student = UserFactory(password=self.password, username='student', email='student@example.com')
        self.other_student = UserFactory(
            password=self.password,
            username='other_student',
            email='i_like_learning@example.com',
        )
        self.program_student = UserFactory(
            password=self.password,
            username='program_student',
            email='i_love_learning@example.com',
        )
        self.program_masters_student = UserFactory(
            password=self.password,
            username='program_masters_student',
            email='i_love_learning@example.com',
        )
        self._create_user_enrollments(self.student, self.other_student)
        self._create_user_program_enrollments(self.program_student)
        self._create_user_program_enrollments(self.program_masters_student, mode='masters')

    @classmethod
    def _create_test_course_with_default_grading_policy(cls, display_name, run):
        """
        Utility method to create a course with a default grading policy
        """
        course = CourseFactory.create(display_name=display_name, run=run)
        _ = CourseOverviewFactory.create(id=course.id)

        chapter = BlockFactory.create(
            category='chapter',
            parent_location=course.location,
            display_name="Chapter 1",
        )
        # create a problem for each type and minimum count needed by the grading policy
        # A section is not considered if the student answers less than "min_count" problems
        for grading_type, min_count in (("Homework", 12), ("Lab", 12), ("Midterm Exam", 1), ("Final Exam", 1)):
            for num in range(min_count):
                section = BlockFactory.create(
                    category='sequential',
                    parent_location=chapter.location,
                    due=datetime(2017, 12, 18, 11, 30, 00),
                    display_name=f'Sequential {grading_type} {num}',
                    format=grading_type,
                    graded=True,
                )
                vertical = BlockFactory.create(
                    category='vertical',
                    parent_location=section.location,
                    display_name=f'Vertical {grading_type} {num}',
                )
                BlockFactory.create(
                    category='problem',
                    parent_location=vertical.location,
                    display_name=f'Problem {grading_type} {num}',
                )

        return course
