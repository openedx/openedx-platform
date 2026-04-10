"""
Tests for graph traversal generator functions.
"""

from unittest import TestCase

import ddt
import pytest

from ..grade_utils import compare_scores, round_away_from_zero


@ddt.ddt
class TestGradeUtils(TestCase):
    """ Tests for the grade_utils module. """
    @ddt.data(
        (1, 2, 3, 4, False, True, 0.5, 0.75),
        (3, 4, 1, 2, False, False, 0.75, 0.5),
        (1, 2, 1, 2, False, True, 0.5, 0.5),
        (1, 1, 0, 1, False, False, 1, 0),
    )
    @ddt.unpack
    def test_compare_scores_happy_path(
        self, earned_1, possible_1, earned_2, possible_2, treat_undefined_as_zero,
        expected_is_higher, expected_percentage_1, expected_percentage_2
    ):
        is_higher, percentage_1, percentage_2 = compare_scores(
            earned_1, possible_1, earned_2, possible_2, treat_undefined_as_zero
        )
        assert expected_is_higher == is_higher
        assert expected_percentage_1 == percentage_1
        assert expected_percentage_2 == percentage_2

    def test_compare_scores_raise_zero_division(self):
        with pytest.raises(ZeroDivisionError):
            compare_scores(1, 0, 1, 2)

        with pytest.raises(ZeroDivisionError):
            compare_scores(1, 2, 0, 0)

    def test_compare_scores_treat_undefined_as_zero(self):
        is_higher, percentage_1, percentage_2 = compare_scores(
            0, 0, 0, 0, treat_undefined_as_zero=True
        )
        assert is_higher is True
        assert 0 == percentage_1
        assert 0 == percentage_2

    @ddt.data(
        (0.5, 1),
        (1.49, 1),
        (1.5, 2),
        (1.51, 2),
        (2.5, 3),
        (1.45, 1.5, 1),
        (-0.5, -1.0),
        (-1.5, -2.0),
        (-2.5, -3.0),
        (-0.1, -0.0),
        (0.1, 0.0),
        (0.0, 0.0)
    )
    @ddt.unpack
    def test_round_away_from_zero(self, precise, expected_rounded_number, rounding_precision=0):  # noqa: PT028
        assert round_away_from_zero(precise, rounding_precision) == expected_rounded_number
