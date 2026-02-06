"""
Tests for the CourseGradeFactory class.
"""
from unittest.mock import patch

import ddt

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.access import has_access
from openedx.core.djangoapps.content.block_structure.factory import BlockStructureFactory
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..course_grade import CourseGrade, ZeroCourseGrade
from ..course_grade_factory import CourseGradeFactory
from ..subsection_grade import ReadSubsectionGrade, ZeroSubsectionGrade
from .base import GradeTestBase
from .utils import mock_get_score


@ddt.ddt
class TestCourseGradeFactory(GradeTestBase):
    """
    Test that CourseGrades are calculated properly
    """
    def _assert_zero_grade(self, course_grade, expected_grade_class):
        """
        Asserts whether the given course_grade is as expected with
        zero values.
        """
        assert isinstance(course_grade, expected_grade_class)
        assert course_grade.letter_grade is None
        assert course_grade.percent == 0.0
        assert course_grade.chapter_grades is not None

    def test_course_grade_no_access(self):
        """
        Test to ensure a grade can be calculated for a student in a course, even if they themselves do not have access.
        """
        invisible_course = CourseFactory.create(visible_to_staff_only=True)
        access = has_access(self.request.user, 'load', invisible_course)
        assert access.has_access is False
        assert access.error_code == 'not_visible_to_user'

        # with self.assertNoExceptionRaised: <- this isn't a real method, it's an implicit assumption
        grade = CourseGradeFactory().read(self.request.user, invisible_course)
        assert grade.percent == 0

    def test_read_and_update(self):
        grade_factory = CourseGradeFactory()

        def _assert_read(expected_pass, expected_percent):
            """
            Creates the grade, ensuring it is as expected.
            """
            course_grade = grade_factory.read(self.request.user, self.course)
            _assert_grade_values(course_grade, expected_pass, expected_percent)
            _assert_section_order(course_grade)

        def _assert_grade_values(course_grade, expected_pass, expected_percent):
            assert course_grade.letter_grade == ('Pass' if expected_pass else None)
            assert course_grade.percent == expected_percent

        def _assert_section_order(course_grade):
            sections = course_grade.chapter_grades[self.chapter.location]['sections']
            assert [section.display_name for section in sections] == [
                self.sequence.display_name,
                self.sequence2.display_name
            ]

        with self.assertNumQueries(3), mock_get_score(1, 2):
            _assert_read(expected_pass=False, expected_percent=0)  # start off with grade of 0

        num_queries = 44
        with self.assertNumQueries(num_queries), mock_get_score(1, 2):
            grade_factory.update(self.request.user, self.course, force_update_subsections=True)

        with self.assertNumQueries(3):
            _assert_read(expected_pass=True, expected_percent=0.5)  # updated to grade of .5

        num_queries = 8
        with self.assertNumQueries(num_queries), mock_get_score(1, 4):
            grade_factory.update(self.request.user, self.course, force_update_subsections=False)

        with self.assertNumQueries(3):
            _assert_read(expected_pass=True, expected_percent=0.5)  # NOT updated to grade of .25

        num_queries = 20
        with self.assertNumQueries(num_queries), mock_get_score(2, 2):
            grade_factory.update(self.request.user, self.course, force_update_subsections=True)

        with self.assertNumQueries(3):
            _assert_read(expected_pass=True, expected_percent=1.0)  # updated to grade of 1.0

        num_queries = 30
        with self.assertNumQueries(num_queries), mock_get_score(0, 0):  # the subsection now is worth zero
            grade_factory.update(self.request.user, self.course, force_update_subsections=True)

        with self.assertNumQueries(3):
            _assert_read(expected_pass=False, expected_percent=0.0)  # updated to grade of 0.0

    @ddt.data((True, False))
    def test_read_zero(self, create_if_needed):
        grade_factory = CourseGradeFactory()
        course_grade = grade_factory.read(self.request.user, self.course, create_if_needed=create_if_needed)
        if create_if_needed:
            self._assert_zero_grade(course_grade, ZeroCourseGrade)
        else:
            assert course_grade is None

    def test_read_optimization(self):
        grade_factory = CourseGradeFactory()
        with patch('lms.djangoapps.grades.course_data.get_course_blocks') as mocked_course_blocks:
            mocked_course_blocks.return_value = self.course_structure
            with mock_get_score(1, 2):
                grade_factory.update(self.request.user, self.course, force_update_subsections=True)
                assert mocked_course_blocks.call_count == 1

        with patch('lms.djangoapps.grades.course_data.get_course_blocks') as mocked_course_blocks:
            with patch('lms.djangoapps.grades.subsection_grade.get_score') as mocked_get_score:
                course_grade = grade_factory.read(self.request.user, self.course)
                assert course_grade.percent == 0.5
                # make sure it's not a zero-valued course grade
                assert not mocked_get_score.called
                # no calls to CSM/submissions tables
                assert not mocked_course_blocks.called
                # no user-specific transformer calculation

    def test_subsection_grade(self):
        grade_factory = CourseGradeFactory()
        with mock_get_score(1, 2):
            grade_factory.update(self.request.user, self.course, force_update_subsections=True)
        course_grade = grade_factory.read(self.request.user, course_structure=self.course_structure)
        subsection_grade = course_grade.subsection_grade(self.sequence.location)
        assert subsection_grade.percent_graded == 0.5

    def test_subsection_type_graders(self):
        graders = CourseGrade.get_subsection_type_graders(self.course)
        assert len(graders) == 2
        assert graders['Homework'].type == 'Homework'
        assert graders['NoCredit'].min_count == 0

    def test_create_zero_subs_grade_for_nonzero_course_grade(self):
        subsection = self.course_structure[self.sequence.location]
        with mock_get_score(1, 2):
            self.subsection_grade_factory.update(subsection)
        course_grade = CourseGradeFactory().update(self.request.user, self.course)
        subsection1_grade = course_grade.subsection_grades[self.sequence.location]
        subsection2_grade = course_grade.subsection_grades[self.sequence2.location]
        assert isinstance(subsection1_grade, ReadSubsectionGrade)
        assert isinstance(subsection2_grade, ZeroSubsectionGrade)

    @ddt.data(True, False)
    def test_iter_force_update(self, force_update):
        with patch('lms.djangoapps.grades.subsection_grade_factory.SubsectionGradeFactory.update') as mock_update:
            set(CourseGradeFactory().iter(
                users=[self.request.user], course=self.course, force_update=force_update,
            ))
        assert mock_update.called == force_update

    def test_course_grade_summary(self):
        with mock_get_score(1, 2):
            self.subsection_grade_factory.update(self.course_structure[self.sequence.location])
        course_grade = CourseGradeFactory().update(self.request.user, self.course)
        subsection_grades = list(course_grade.subsection_grades.values())

        actual_summary = course_grade.summary

        # We should have had a zero subsection grade for sequential 2, since we never
        # gave it a mock score above.
        expected_summary = {
            'grade': None,
            'grade_breakdown': {
                'Homework': {
                    'category': 'Homework',
                    'percent': 0.25,
                    'detail': 'Homework = 25.00% of a possible 100.00%',
                },
                'NoCredit': {
                    'category': 'NoCredit',
                    'percent': 0.0,
                    'detail': 'NoCredit = 0.00% of a possible 0.00%',
                }
            },
            'percent': 0.25,
            'section_breakdown': [
                {
                    'category': 'Homework',
                    'detail': 'Homework 1 - Test Sequential X with an & Ampersand - 50.00% (1/2)',
                    'label': 'HW 01',
                    'percent': 0.5,
                    'sequential_id': str(subsection_grades[0].location),
                },
                {
                    'category': 'Homework',
                    'detail': 'Homework 2 - Test Sequential A - 0.00% (0/1)',
                    'label': 'HW 02',
                    'percent': 0.0,
                    'sequential_id': str(subsection_grades[1].location),
                },
                {
                    'category': 'Homework',
                    'detail': 'Homework Average = 25.00%',
                    'label': 'HW Avg',
                    'percent': 0.25,
                    'prominent': True
                },
                {
                    'category': 'NoCredit',
                    'detail': 'NoCredit Average = 0.00%',
                    'label': 'NC Avg',
                    'percent': 0,
                    'prominent': True
                },
            ]
        }
        assert expected_summary == actual_summary


class TestGradeIteration(SharedModuleStoreTestCase):
    """
    Test iteration through student course grades.
    """
    COURSE_NUM = "1000"
    COURSE_NAME = "grading_test_course"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            display_name=cls.COURSE_NAME,
            number=cls.COURSE_NUM
        )

    def setUp(self):
        """
        Create a course and a handful of users to assign grades
        """
        super().setUp()

        self.students = [
            UserFactory.create(username='student1'),
            UserFactory.create(username='student2'),
            UserFactory.create(username='student3'),
            UserFactory.create(username='student4'),
            UserFactory.create(username='student5'),
        ]

    def test_empty_student_list(self):
        """
        If we don't pass in any students, it should return a zero-length
        iterator, but it shouldn't error.
        """
        grade_results = list(CourseGradeFactory().iter([], self.course))
        assert not grade_results

    def test_all_empty_grades(self):
        """
        No students have grade entries.
        """
        with patch.object(
            BlockStructureFactory,
            'create_from_store',
            wraps=BlockStructureFactory.create_from_store
        ) as mock_create_from_store:
            all_course_grades, all_errors = self._course_grades_and_errors_for(self.course, self.students)
            assert mock_create_from_store.call_count == 1

        assert len(all_errors) == 0
        for course_grade in all_course_grades.values():
            assert course_grade.letter_grade is None
            assert course_grade.percent == 0.0

    @patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read')
    def test_grading_exception(self, mock_course_grade):
        """Test that we correctly capture exception messages that bubble up from
        grading. Note that we only see errors at this level if the grading
        process for this student fails entirely due to an unexpected event --
        having errors in the problem sets will not trigger this.

        We patch the grade() method with our own, which will generate the errors
        for student3 and student4.
        """

        student1, student2, student3, student4, student5 = self.students
        mock_course_grade.side_effect = [
            Exception(f"Error for {student.username}.")
            if student.username in ['student3', 'student4']
            else mock_course_grade.return_value
            for student in self.students
        ]
        with self.assertNumQueries(20):
            all_course_grades, all_errors = self._course_grades_and_errors_for(self.course, self.students)
        assert {student: str(all_errors[student]) for student in all_errors} == {
            student3: 'Error for student3.',
            student4: 'Error for student4.'
        }

        # But we should still have five gradesets
        assert len(all_course_grades) == 5

        # Even though two will simply be empty
        assert all_course_grades[student3] is None
        assert all_course_grades[student4] is None

        # The rest will have grade information in them
        assert all_course_grades[student1] is not None
        assert all_course_grades[student2] is not None
        assert all_course_grades[student5] is not None

    def _course_grades_and_errors_for(self, course, students):
        """
        Simple helper method to iterate through student grades and give us
        two dictionaries -- one that has all students and their respective
        course grades, and one that has only students that could not be graded
        and their respective error messages.
        """
        students_to_course_grades = {}
        students_to_errors = {}

        for student, course_grade, error in CourseGradeFactory().iter(students, course):
            students_to_course_grades[student] = course_grade
            if error:
                students_to_errors[student] = error

        return students_to_course_grades, students_to_errors
