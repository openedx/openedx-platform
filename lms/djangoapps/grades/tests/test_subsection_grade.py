"""
Tests of the SubsectionGrade classes.
"""


from ddt import data, ddt, unpack

from ..models import PersistentSubsectionGrade
from ..subsection_grade import CreateSubsectionGrade, ReadSubsectionGrade
from .base import GradeTestBase
from .utils import mock_get_score


@ddt
class SubsectionGradeTest(GradeTestBase):  # pylint: disable=missing-class-docstring

    @data((50, 100, .5), (.5949, 100, .0059), (.5951, 100, .006), (.595, 100, .0059), (.605, 100, .006))
    @unpack
    def test_create_and_read(self, mock_earned, mock_possible, expected_result):
        with mock_get_score(mock_earned, mock_possible):
            # Create a grade that *isn't* saved to the database
            created_grade = CreateSubsectionGrade(
                self.sequence,
                self.course_structure,
                self.subsection_grade_factory._submissions_scores,  # pylint: disable=protected-access
                self.subsection_grade_factory._csm_scores,  # pylint: disable=protected-access
            )
            assert PersistentSubsectionGrade.objects.count() == 0
            assert created_grade.percent_graded == expected_result

            # save to db, and verify object is in database
            created_grade.update_or_create_model(self.request.user)
            assert PersistentSubsectionGrade.objects.count() == 1

            # read from db, and ensure output matches input
            saved_model = PersistentSubsectionGrade.read_grade(
                user_id=self.request.user.id,
                usage_key=self.sequence.location,
            )
            read_grade = ReadSubsectionGrade(
                self.sequence,
                saved_model,
                self.subsection_grade_factory
            )

            assert created_grade.url_name == read_grade.url_name
            read_grade.all_total.first_attempted = created_grade.all_total.first_attempted = None
            assert created_grade.all_total == read_grade.all_total
            assert created_grade.percent_graded == expected_result

    def test_zero(self):
        with mock_get_score(1, 0):
            grade = CreateSubsectionGrade(
                self.sequence,
                self.course_structure,
                self.subsection_grade_factory._submissions_scores,  # pylint: disable=protected-access
                self.subsection_grade_factory._csm_scores,  # pylint: disable=protected-access
            )
            assert grade.percent_graded == 0.0
