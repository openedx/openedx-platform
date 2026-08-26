"""
Tests for the SubsectionGradeFactory class.
"""


from unittest.mock import patch

import ddt
from django.test import override_settings

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.tests.test_submitting_problems import ProblemSubmissionTestMixin

from ..constants import GradeOverrideFeatureEnum
from ..models import PersistentSubsectionGrade, PersistentSubsectionGradeOverride
from ..subsection_grade_factory import ZeroSubsectionGrade
from .base import GradeTestBase
from .utils import mock_get_score


@ddt.ddt
class TestSubsectionGradeFactory(ProblemSubmissionTestMixin, GradeTestBase):
    """
    Tests for SubsectionGradeFactory functionality.

    Ensures that SubsectionGrades are created and updated properly, that
    persistent grades are functioning as expected, and that the flag to
    enable saving subsection grades blocks/enables that feature as expected.
    """

    def assert_grade(self, grade, expected_earned, expected_possible):
        """
        Asserts that the given grade object has the expected score.
        """
        assert (grade.all_total.earned, grade.all_total.possible) == (expected_earned, expected_possible)
        assert (grade.graded_total.earned, grade.graded_total.possible) == (expected_earned, expected_possible)

    def test_create_zero(self):
        """
        Test that a zero grade is returned.
        """
        grade = self.subsection_grade_factory.create(self.sequence)
        assert isinstance(grade, ZeroSubsectionGrade)
        self.assert_grade(grade, 0.0, 1.0)

    @override_settings(ENABLE_COURSE_ASSESSMENT_GRADE_CHANGE_SIGNAL=True)
    def test_update(self):
        """
        Assuming the underlying score reporting methods work,
        test that the score is calculated properly.
        """
        with mock_get_score(1, 2):
            with patch(
                'openedx.core.djangoapps.signals.signals.COURSE_ASSESSMENT_GRADE_CHANGED.send'
            ) as mock_update_grades_signal:
                grade = self.subsection_grade_factory.update(self.sequence)
        self.assert_grade(grade, 1, 2)
        assert mock_update_grades_signal.called

    def test_write_only_if_engaged(self):
        """
        Test that scores are not persisted when a learner has
        never attempted a problem, but are persisted if the
        learner's state has been deleted.
        """
        with mock_get_score(0, 0, None):
            self.subsection_grade_factory.update(self.sequence)
        # ensure no grades have been persisted
        assert 0 == len(PersistentSubsectionGrade.objects.all())

        with mock_get_score(0, 0, None):
            self.subsection_grade_factory.update(self.sequence, score_deleted=True)
        # ensure a grade has been persisted
        assert 1 == len(PersistentSubsectionGrade.objects.all())

    def test_update_if_higher_zero_denominator(self):
        """
        Test that we get an updated score of 0, and not a ZeroDivisionError,
        when dealing with an invalid score like 0/0.
        """
        # This will create a PersistentSubsectionGrade with a score of 0/0.
        with mock_get_score(0, 0):
            grade = self.subsection_grade_factory.update(self.sequence)
        self.assert_grade(grade, 0, 0)

        # Ensure that previously storing a possible score of 0
        # does not raise a ZeroDivisionError when updating the grade.
        with mock_get_score(2, 2):
            grade = self.subsection_grade_factory.update(self.sequence, only_if_higher=True)
        self.assert_grade(grade, 2, 2)

    def test_update_if_higher(self):
        def verify_update_if_higher(mock_score, expected_grade):
            """
            Updates the subsection grade and verifies the
            resulting grade is as expected.
            """
            with mock_get_score(*mock_score):
                grade = self.subsection_grade_factory.update(self.sequence, only_if_higher=True)
                self.assert_grade(grade, *expected_grade)

        verify_update_if_higher((1, 2), (1, 2))  # previous value was non-existent
        verify_update_if_higher((2, 4), (2, 4))  # previous value was equivalent
        verify_update_if_higher((1, 4), (2, 4))  # previous value was greater
        verify_update_if_higher((3, 4), (3, 4))  # previous value was less

    @ddt.data(
        (0, None),
        (None, 3),
        (None, None),
        (0, 3),
    )
    @ddt.unpack
    def test_update_with_override(self, earned_graded_override, possible_graded_override):
        """
        Tests that when a PersistentSubsectionGradeOverride exists, the update()
        method returns a CreateSubsectionGrade with scores that account
        for the override.
        """
        # first, do an update to create a persistent grade
        with mock_get_score(2, 3):
            grade = self.subsection_grade_factory.update(self.sequence)
            self.assert_grade(grade, 2, 3)

        # there should only be one persistent grade
        persistent_grade = PersistentSubsectionGrade.objects.first()
        assert 2 == persistent_grade.earned_graded
        assert 3 == persistent_grade.possible_graded

        # Now create the override
        PersistentSubsectionGradeOverride.update_or_create_override(
            UserFactory(),
            persistent_grade,
            earned_graded_override=earned_graded_override,
            earned_all_override=earned_graded_override,
            possible_graded_override=possible_graded_override,
            feature=GradeOverrideFeatureEnum.gradebook,
        )

        # Now, even if the problem scores interface gives us a 2/3,
        # the subsection grade returned should be 0/3 due to the override.
        with mock_get_score(2, 3):
            grade = self.subsection_grade_factory.update(self.sequence)
            expected_earned = earned_graded_override
            if earned_graded_override is None:
                expected_earned = persistent_grade.earned_graded
            expected_possible = possible_graded_override
            if possible_graded_override is None:
                expected_possible = persistent_grade.possible_graded
            self.assert_grade(grade, expected_earned, expected_possible)

    def test_display_name_not_escaped(self):
        """Confirm that we don't escape the display name - downstream consumers will do that instead"""
        # first, do an update to create a persistent grade
        grade = self.subsection_grade_factory.update(self.sequence)
        assert grade.display_name == 'Test Sequential X with an & Ampersand'
