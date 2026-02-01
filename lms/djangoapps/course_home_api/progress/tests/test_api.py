"""
Tests for the Python APIs exposed by the Progress API of the Course Home API app.
"""

from unittest.mock import patch

from django.test import TestCase
from xblock.scorable import ShowCorrectness

from lms.djangoapps.course_home_api.progress.api import (
    calculate_progress_for_learner_in_course,
    aggregate_assignment_type_grade_summary,
)
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


def _make_subsection(fmt, earned, possible, show_corr, *, due_delta_days=None, is_included=True):
    """Build a lightweight subsection object for testing aggregation scenarios."""
    graded_total = SimpleNamespace(earned=earned, possible=possible)
    due = None
    if due_delta_days is not None:
        due = datetime.now(timezone.utc) + timedelta(days=due_delta_days)
    return SimpleNamespace(
        graded=True,
        format=fmt,
        graded_total=graded_total,
        show_correctness=show_corr,
        due=due,
        show_grades=lambda staff: is_included,
    )


_AGGREGATION_SCENARIOS = [
    (
        'all_visible_always',
        {'type': 'Homework', 'weight': 1.0, 'drop_count': 0, 'min_count': 2, 'short_label': 'HW'},
        [
            _make_subsection('Homework', 1, 1, ShowCorrectness.ALWAYS),
            _make_subsection('Homework', 0.5, 1, ShowCorrectness.ALWAYS),
        ],
        {'avg': 0.75, 'weighted': 0.75, 'hidden': 'none', 'final': 0.75},
    ),
    (
        'some_hidden_never_but_include',
        {'type': 'Exam', 'weight': 1.0, 'drop_count': 0, 'min_count': 2, 'short_label': 'EX'},
        [
            _make_subsection('Exam', 1, 1, ShowCorrectness.ALWAYS),
            _make_subsection('Exam', 0.5, 1, ShowCorrectness.NEVER_BUT_INCLUDE_GRADE),
        ],
        {'avg': 0.5, 'weighted': 0.5, 'hidden': 'some', 'final': 0.75},
    ),
    (
        'all_hidden_never_but_include',
        {'type': 'Quiz', 'weight': 1.0, 'drop_count': 0, 'min_count': 2, 'short_label': 'QZ'},
        [
            _make_subsection('Quiz', 0.4, 1, ShowCorrectness.NEVER_BUT_INCLUDE_GRADE),
            _make_subsection('Quiz', 0.6, 1, ShowCorrectness.NEVER_BUT_INCLUDE_GRADE),
        ],
        {'avg': 0.0, 'weighted': 0.0, 'hidden': 'all', 'final': 0.5},
    ),
    (
        'past_due_mixed_visibility',
        {'type': 'Lab', 'weight': 1.0, 'drop_count': 0, 'min_count': 2, 'short_label': 'LB'},
        [
            _make_subsection('Lab', 0.8, 1, ShowCorrectness.PAST_DUE, due_delta_days=-1, is_included=True),
            _make_subsection('Lab', 0.2, 1, ShowCorrectness.PAST_DUE, due_delta_days=+3, is_included=True),
        ],
        {'avg': 0.4, 'weighted': 0.4, 'hidden': 'some', 'final': 0.5, 'last_grade_publish_date_days': 3},
    ),
    (
        'drop_lowest_keeps_high_scores',
        {'type': 'Project', 'weight': 1.0, 'drop_count': 2, 'min_count': 4, 'short_label': 'PR'},
        [
            _make_subsection('Project', 1, 1, ShowCorrectness.ALWAYS),
            _make_subsection('Project', 1, 1, ShowCorrectness.ALWAYS),
            _make_subsection('Project', 0, 1, ShowCorrectness.ALWAYS),
            _make_subsection('Project', 0, 1, ShowCorrectness.ALWAYS),
        ],
        {'avg': 1.0, 'weighted': 1.0, 'hidden': 'none', 'final': 1.0},
    ),
    (
        'unreleased_with_future_due_date',
        {'type': 'Midterm', 'weight': 1.0, 'drop_count': 0, 'min_count': 1, 'short_label': 'MT'},
        [
            _make_subsection('Midterm', 0.5, 1, ShowCorrectness.PAST_DUE, due_delta_days=7, is_included=False),
        ],
        {'avg': 0.0, 'weighted': 0.0, 'hidden': 'all', 'final': 0.0, 'last_grade_publish_date_days': 7},
    ),
]


class ProgressApiTests(TestCase):
    """
    Tests for the progress calculation functions.
    """

    @patch("lms.djangoapps.course_home_api.progress.api.get_course_blocks_completion_summary")
    def test_calculate_progress_for_learner_in_course(self, mock_get_summary):
        """
        A test to verify functionality of the function under test.
        """
        mock_get_summary.return_value = {
            "complete_count": 5,
            "incomplete_count": 2,
            "locked_count": 1,
        }

        expected_data = {
            "complete_count": 5,
            "incomplete_count": 2,
            "locked_count": 1,
            "total_count": 8,
            "complete_percentage": 0.62,
            "locked_percentage": 0.12,
            "incomplete_percentage": 0.26,
        }

        results = calculate_progress_for_learner_in_course("some_course", "some_user")
        mock_get_summary.assert_called_once_with("some_course", "some_user")
        assert results == expected_data

    @patch("lms.djangoapps.course_home_api.progress.api.get_course_blocks_completion_summary")
    def test_handle_division_by_zero(self, mock_get_summary):
        """
        A test to verify that we're avoiding division-by-zero errors if the total number of units is 0.
        """
        mock_get_summary.return_value = {
            "complete_count": 0,
            "incomplete_count": 0,
            "locked_count": 0,
        }

        expected_data = {
            "complete_count": 0,
            "incomplete_count": 0,
            "locked_count": 0,
            "total_count": 0,
            "complete_percentage": 0.0,
            "locked_percentage": 0.0,
            "incomplete_percentage": 0.0,
        }

        results = calculate_progress_for_learner_in_course("some_course", "some_user")
        mock_get_summary.assert_called_once_with("some_course", "some_user")
        assert results == expected_data

    @patch("lms.djangoapps.course_home_api.progress.api.get_course_blocks_completion_summary")
    def test_calculate_progress_for_learner_in_course_summary_empty(self, mock_get_summary):
        """
        A test to verify functionality of the function under test if a block summary is not received.
        """
        mock_get_summary.return_value = {}

        results = calculate_progress_for_learner_in_course("some_course", "some_user")
        assert not results

    def test_aggregate_assignment_type_grade_summary_scenarios(self):
        """
        A test to verify functionality of aggregate_assignment_type_grade_summary.
            1. Test visibility modes (always, never but include grade, past due)
            2. Test drop-lowest behavior
            3. Test weighting behavior
            4. Test final grade calculation
            5. Test average grade calculation
            6. Test weighted grade calculation
            7. Test has_hidden_contribution calculation
        """

        for case_name, policy, subsections, expected in _AGGREGATION_SCENARIOS:
            with self.subTest(case_name=case_name):
                course_grade = SimpleNamespace(chapter_grades={'chapter': {'sections': subsections}})
                grading_policy = {'GRADER': [policy]}

                result = aggregate_assignment_type_grade_summary(
                    course_grade,
                    grading_policy,
                    has_staff_access=False,
                )

                assert 'results' in result and 'final_grades' in result
                assert result['final_grades'] == expected['final']
                assert len(result['results']) == 1

                row = result['results'][0]
                assert row['type'] == policy['type'], case_name
                assert row['average_grade'] == expected['avg']
                assert row['weighted_grade'] == expected['weighted']
                assert row['has_hidden_contribution'] == expected['hidden']
                assert row['num_droppable'] == policy['drop_count']
                assert (row['last_grade_publish_date'] is not None) == (
                    'last_grade_publish_date_days' in expected
                )
