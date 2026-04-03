"""
Utilities for grades related tests
"""


from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytz

from lms.djangoapps.courseware.block_render import get_block
from lms.djangoapps.courseware.model_data import FieldDataCache
from xmodule.graders import ProblemScore  # lint-amnesty, pylint: disable=wrong-import-order


@contextmanager
def mock_passing_grade(letter_grade='Pass', percent=0.75, last_updated=None):
    """
    Mock the grading function to always return a passing grade.
    """
    passing_grade_fields = dict(
        letter_grade=letter_grade,
        percent=percent,
        passed=letter_grade is not None,
        attempted=True,
        last_updated=last_updated,
    )
    with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_grade_read:
        mock_grade_read.return_value = MagicMock(**passing_grade_fields)
        with patch('lms.djangoapps.grades.course_grade.CourseGrade.update') as mock_grade_update:
            mock_grade_update.return_value = MagicMock(**passing_grade_fields)
            yield


@contextmanager
def mock_get_score(earned=0, possible=1, first_attempted=datetime(2000, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)):
    """
    Mocks the get_score function to return a valid grade.
    """
    with patch('lms.djangoapps.grades.subsection_grade.get_score') as mock_score:
        mock_score.return_value = ProblemScore(
            raw_earned=earned,
            raw_possible=possible,
            weighted_earned=earned,
            weighted_possible=possible,
            weight=1,
            graded=True,
            first_attempted=first_attempted
        )
        yield mock_score


@contextmanager
def mock_get_submissions_score(earned=0, possible=1, first_attempted=datetime(2000, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)):
    """
    Mocks the _get_submissions_score function to return the specified values
    """
    with patch('lms.djangoapps.grades.scores._get_score_from_submissions') as mock_score:
        mock_score.return_value = (earned, possible, earned, possible, first_attempted)
        yield mock_score


def answer_problem(course, request, problem, score=1, max_value=1):
    """
    Records a correct answer for the given problem.

    Arguments:
        course (Course): Course object, the course the required problem is in
        request (Request): request Object
        problem (xblock): xblock object, the problem to be answered
    """

    user = request.user
    grade_dict = {'value': score, 'max_value': max_value, 'user_id': user.id}
    field_data_cache = FieldDataCache.cache_for_block_descendents(
        course.id,
        user,
        course,
        depth=2
    )
    block = get_block(
        user,
        request,
        problem.scope_ids.usage_id,
        field_data_cache,
    )
    block.runtime.publish(problem, 'grade', grade_dict)
