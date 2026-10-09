"""
Tests of the SubsectionGrade classes.
"""


from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from ddt import data, ddt, unpack
from django.db import DatabaseError, connection
from django.test import override_settings
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyMasteryStatus,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    MasteryStatus,
    RuleType,
    StudentCompetencyCriteriaStatus,
)
from openedx_tagging.models import ObjectTag, Tag

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.track.event_transaction_utils import get_event_transaction_id, get_event_transaction_type
from lms.djangoapps.grades.tasks import roll_up_competency_statuses_for_user
from xmodule.graders import AggregatedScore

from ..models import PersistentSubsectionGrade, PersistentSubsectionGradeOverride
from ..subsection_grade import CreateSubsectionGrade, ReadSubsectionGrade
from ..subsection_grade_factory import SubsectionGradeFactory
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


RECORD_PATH = 'lms.djangoapps.grades.subsection_grade.record_graded_object_statuses'
EMIT_PATH = 'lms.djangoapps.grades.subsection_grade.events.subsection_grade_calculated'
ENQUEUE_PATH = 'lms.djangoapps.grades.tasks.roll_up_competency_statuses_for_user.apply_async'


class SubsectionGradeFixtureMixin:
    """
    Helpers shared by the competency test classes; expects to be mixed into a GradeTestBase.
    """

    def _create_grade(self, subsection=None):
        """
        Returns an unsaved CreateSubsectionGrade for the given subsection (default: the first sequence).
        """
        factory = SubsectionGradeFactory(self.request.user, self.course, self.course_structure)
        return factory.create(subsection or self.sequence, read_only=True, force_calculate=True)


@override_settings(ENABLE_COMPETENCY_MASTERY_TRACKING=True)
@ddt
class SubsectionGradeCompetencyStatusTest(SubsectionGradeFixtureMixin, GradeTestBase):
    """
    Tests that persisting a subsection grade passes the right scores to the competency API in the grade's transaction.
    """

    def test_single_path_records_fraction(self):
        with mock_get_score(1, 2), patch(RECORD_PATH) as mock_record:
            self._create_grade().update_or_create_model(self.request.user)

        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs['user_id'] == self.request.user.id
        scores = mock_record.call_args.kwargs['scores']
        assert [(score.object_id, score.fraction) for score in scores] == [
            (str(self.sequence.location), Decimal('0.5')),
        ]

    def test_staff_override_uses_override_adjusted_fraction(self):
        with mock_get_score(1, 4), patch(RECORD_PATH):
            self.subsection_grade_factory.update(self.sequence)
        model = PersistentSubsectionGrade.read_grade(self.request.user.id, self.sequence.location)
        PersistentSubsectionGradeOverride.update_or_create_override(
            UserFactory(), model, earned_graded_override=4.0, possible_graded_override=4.0,
        )

        with mock_get_score(1, 4), patch(RECORD_PATH) as mock_record:
            self.subsection_grade_factory.update(self.sequence)

        assert mock_record.call_args.kwargs['scores'][0].fraction == Decimal('1')

    def test_record_failure_rolls_back_grade_and_skips_event(self):
        with mock_get_score(1, 2), patch(RECORD_PATH, side_effect=DatabaseError), patch(EMIT_PATH) as mock_emit:
            with pytest.raises(DatabaseError):
                self._create_grade().update_or_create_model(self.request.user)

        assert PersistentSubsectionGrade.objects.count() == 0
        mock_emit.assert_not_called()

    def test_event_is_emitted_after_the_atomic_block(self):
        # An open atomic block adds a savepoint id, so the depth tells whether a call ran inside the block.
        depths = {}
        with mock_get_score(1, 2):
            grade = self._create_grade()
            with patch(RECORD_PATH, side_effect=lambda **_: depths.update(record=len(connection.savepoint_ids))):
                with patch(EMIT_PATH, side_effect=lambda _: depths.update(emit=len(connection.savepoint_ids))):
                    grade.update_or_create_model(self.request.user)

        assert depths['record'] == depths['emit'] + 1

    def test_zero_possible_passes_no_score(self):
        with mock_get_score(0, 0), patch(RECORD_PATH) as mock_record:
            self._create_grade().update_or_create_model(self.request.user, force_update_subsections=True)

        mock_record.assert_not_called()

    def test_unattempted_passes_no_score(self):
        with mock_get_score(1, 2, first_attempted=None), patch(RECORD_PATH) as mock_record:
            self._create_grade().update_or_create_model(self.request.user, force_update_subsections=True)

        mock_record.assert_not_called()

    @override_settings(ENABLE_COMPETENCY_MASTERY_TRACKING=False)
    def test_setting_off_saves_grades_without_calling_core(self):
        with mock_get_score(1, 2), patch(RECORD_PATH) as mock_record:
            self._create_grade().update_or_create_model(self.request.user)
            CreateSubsectionGrade.bulk_create_models(
                UserFactory(), [self._create_grade(self.sequence2)], self.course.id,
            )

        mock_record.assert_not_called()
        assert PersistentSubsectionGrade.objects.count() == 2

    def test_bulk_path_records_each_qualifying_subsection(self):
        with mock_get_score(1, 2), patch(RECORD_PATH) as mock_record, patch(EMIT_PATH) as mock_emit:
            grades = CreateSubsectionGrade.bulk_create_models(
                self.request.user,
                [self._create_grade(self.sequence), self._create_grade(self.sequence2)],
                self.course.id,
            )

        mock_record.assert_called_once()
        scores = mock_record.call_args.kwargs['scores']
        assert {(score.object_id, score.fraction) for score in scores} == {
            (str(self.sequence.location), Decimal('0.5')),
            (str(self.sequence2.location), Decimal('0.5')),
        }
        assert mock_emit.call_count == len(grades) == 2

    def test_bulk_path_omits_zero_possible_subsections(self):
        with mock_get_score(0, 0), patch(RECORD_PATH) as mock_record:
            CreateSubsectionGrade.bulk_create_models(
                self.request.user, [self._create_grade(self.sequence)], self.course.id,
            )

        mock_record.assert_not_called()

    def test_bulk_path_failure_rolls_back_grades_and_skips_events(self):
        with mock_get_score(1, 2), patch(RECORD_PATH, side_effect=DatabaseError), patch(EMIT_PATH) as mock_emit:
            with pytest.raises(DatabaseError):
                CreateSubsectionGrade.bulk_create_models(
                    self.request.user, [self._create_grade(self.sequence)], self.course.id,
                )

        assert PersistentSubsectionGrade.objects.count() == 0
        mock_emit.assert_not_called()

    def test_single_path_enqueues_rollup_once_after_commit(self):
        with mock_get_score(1, 2), patch(RECORD_PATH, return_value=1), patch(ENQUEUE_PATH) as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                self._create_grade().update_or_create_model(self.request.user)
            assert len(callbacks) == 1

        mock_enqueue.assert_called_once_with(
            kwargs={'user_id': self.request.user.id, 'object_ids': [str(self.sequence.location)]},
        )

    def test_single_path_does_not_enqueue_before_commit(self):
        with mock_get_score(1, 2), patch(RECORD_PATH, return_value=1), patch(ENQUEUE_PATH) as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=False):
                self._create_grade().update_or_create_model(self.request.user)

        mock_enqueue.assert_not_called()

    def test_single_path_does_not_enqueue_when_no_rows_changed(self):
        with mock_get_score(1, 2), patch(RECORD_PATH, return_value=0), patch(ENQUEUE_PATH) as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                self._create_grade().update_or_create_model(self.request.user)

        mock_enqueue.assert_not_called()

    def test_single_path_does_not_enqueue_when_record_fails(self):
        with mock_get_score(1, 2), patch(RECORD_PATH, side_effect=DatabaseError), patch(ENQUEUE_PATH) as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                with pytest.raises(DatabaseError):
                    self._create_grade().update_or_create_model(self.request.user)

        mock_enqueue.assert_not_called()

    def test_single_path_enqueue_failure_does_not_skip_event_or_raise(self):
        with mock_get_score(1, 2), patch(RECORD_PATH, return_value=1), patch(EMIT_PATH) as mock_emit:
            with patch(ENQUEUE_PATH, side_effect=OSError('broker down')):
                with self.captureOnCommitCallbacks(execute=True):
                    self._create_grade().update_or_create_model(self.request.user)

        mock_emit.assert_called_once()

    def test_single_path_enqueue_kwargs_are_what_the_task_reads(self):
        with mock_get_score(1, 2), patch(RECORD_PATH, return_value=1), patch(ENQUEUE_PATH) as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                self._create_grade().update_or_create_model(self.request.user)
        captured = mock_enqueue.call_args.kwargs['kwargs']

        with patch('lms.djangoapps.grades.tasks.roll_up_competency_statuses') as mock_roll_up:
            roll_up_competency_statuses_for_user.apply(kwargs=captured)

        mock_roll_up.assert_called_once_with(
            user_id=self.request.user.id, object_ids=[str(self.sequence.location)],
        )

    @override_settings(ENABLE_COMPETENCY_MASTERY_TRACKING=False)
    def test_setting_off_does_not_enqueue(self):
        with mock_get_score(1, 2), patch(RECORD_PATH, return_value=1), patch(ENQUEUE_PATH) as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                self._create_grade().update_or_create_model(self.request.user)
                CreateSubsectionGrade.bulk_create_models(
                    UserFactory(), [self._create_grade(self.sequence2)], self.course.id,
                )

        mock_enqueue.assert_not_called()

    def test_bulk_path_enqueues_one_rollup_with_every_scored_object(self):
        with mock_get_score(1, 2), patch(RECORD_PATH, return_value=2), patch(ENQUEUE_PATH) as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                CreateSubsectionGrade.bulk_create_models(
                    self.request.user,
                    [self._create_grade(self.sequence), self._create_grade(self.sequence2)],
                    self.course.id,
                )

        mock_enqueue.assert_called_once()
        kwargs = mock_enqueue.call_args.kwargs['kwargs']
        assert kwargs['user_id'] == self.request.user.id
        assert sorted(kwargs['object_ids']) == sorted([str(self.sequence.location), str(self.sequence2.location)])

    def test_bulk_path_does_not_enqueue_when_no_rows_changed(self):
        with mock_get_score(1, 2), patch(RECORD_PATH, return_value=0), patch(ENQUEUE_PATH) as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                CreateSubsectionGrade.bulk_create_models(
                    self.request.user, [self._create_grade(self.sequence)], self.course.id,
                )

        mock_enqueue.assert_not_called()

    def test_bulk_path_does_not_enqueue_when_record_fails(self):
        with mock_get_score(1, 2), patch(RECORD_PATH, side_effect=DatabaseError), patch(ENQUEUE_PATH) as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                with pytest.raises(DatabaseError):
                    CreateSubsectionGrade.bulk_create_models(
                        self.request.user, [self._create_grade(self.sequence)], self.course.id,
                    )

        mock_enqueue.assert_not_called()

    def test_bulk_path_enqueue_failure_does_not_skip_events_or_raise(self):
        with mock_get_score(1, 2), patch(RECORD_PATH, return_value=1), patch(EMIT_PATH) as mock_emit:
            with patch(ENQUEUE_PATH, side_effect=OSError('broker down')):
                with self.captureOnCommitCallbacks(execute=True):
                    CreateSubsectionGrade.bulk_create_models(
                        self.request.user, [self._create_grade(self.sequence)], self.course.id,
                    )

        mock_emit.assert_called_once()

    def test_bulk_path_enqueue_omits_zero_possible_subsections(self):
        with mock_get_score(1, 2):
            scored = self._create_grade(self.sequence)
        with mock_get_score(0, 0):
            unscorable = self._create_grade(self.sequence2)

        with patch(RECORD_PATH, return_value=1), patch(ENQUEUE_PATH) as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                CreateSubsectionGrade.bulk_create_models(
                    self.request.user, [scored, unscorable], self.course.id,
                )

        assert mock_enqueue.call_args.kwargs['kwargs']['object_ids'] == [str(self.sequence.location)]

    def test_bulk_path_empty_input_returns_none_without_transaction_or_record(self):
        with patch(RECORD_PATH) as mock_record, patch(
            'lms.djangoapps.grades.subsection_grade.transaction.atomic'
        ) as mock_atomic:
            assert CreateSubsectionGrade.bulk_create_models(self.request.user, [], self.course.id) is None

        mock_record.assert_not_called()
        mock_atomic.assert_not_called()

    @data((3, 2, '1'), (-1, 2, '0'), (0.7, 1.0, '0.7'), (1, 4, '0.25'))
    @unpack
    def test_fraction_is_clamped_and_exact(self, earned, possible, expected):
        with mock_get_score(earned, possible):
            score = self._create_grade().graded_object_score()

        assert score.fraction == Decimal(expected)

    def test_fraction_absorbs_summation_noise(self):
        with mock_get_score(1, 2):
            grade = self._create_grade()
        noisy_sum = 0.0
        for _ in range(8):
            noisy_sum += 0.1
        assert noisy_sum != 0.8
        grade.graded_total = AggregatedScore(
            noisy_sum, 1.0, True, first_attempted=datetime(2000, 1, 1, tzinfo=UTC),
        )

        fraction = grade.graded_object_score().fraction

        assert fraction == Decimal('0.8')
        assert fraction >= Decimal('0.8')


class SubsectionGradeEventTest(SubsectionGradeFixtureMixin, GradeTestBase):
    """
    Tests that persisting subsection grades through CreateSubsectionGrade emits the grade calculated event.
    """

    def test_update_or_create_model_emits_event(self):
        with mock_get_score(1, 2), patch('lms.djangoapps.grades.events.tracker') as tracker_mock:
            model = self._create_grade().update_or_create_model(self.request.user)

        self._assert_tracker_emitted_event(tracker_mock, model)

    def test_bulk_create_models_emits_event_for_each_grade(self):
        with mock_get_score(1, 2), patch('lms.djangoapps.grades.events.tracker') as tracker_mock:
            models = CreateSubsectionGrade.bulk_create_models(
                self.request.user,
                [self._create_grade(self.sequence), self._create_grade(self.sequence2)],
                self.course.id,
            )

        assert len(models) == 2
        for model in models:
            self._assert_tracker_emitted_event(tracker_mock, model)

    def _assert_tracker_emitted_event(self, tracker_mock, grade):
        """
        Ensures the mocked event tracker was called with the expected info based on the passed grade.
        """
        tracker_mock.emit.assert_any_call(
            'edx.grades.subsection.grade_calculated',
            {
                'user_id': str(grade.user_id),
                'course_id': str(grade.course_id),
                'block_id': str(grade.usage_key),
                'course_version': str(grade.course_version),
                'weighted_total_earned': grade.earned_all,
                'weighted_total_possible': grade.possible_all,
                'weighted_graded_earned': grade.earned_graded,
                'weighted_graded_possible': grade.possible_graded,
                'first_attempted': str(grade.first_attempted),
                'subtree_edited_timestamp': str(grade.subtree_edited_timestamp),
                'event_transaction_id': str(get_event_transaction_id()),
                'event_transaction_type': str(get_event_transaction_type()),
                'visible_blocks_hash': str(grade.visible_blocks_id),
            }
        )


@override_settings(ENABLE_COMPETENCY_MASTERY_TRACKING=True)
class SubsectionGradeCompetencyIntegrationTest(SubsectionGradeFixtureMixin, GradeTestBase):
    """
    Grades subsections against real competency criteria and checks the learner statuses stored with the grade.
    """

    def setUp(self):
        super().setUp()
        # The test database is built without migrations, so the seeded status rows are created here.
        for status in MasteryStatus:
            CompetencyMasteryStatus.objects.get_or_create(id=status.value, defaults={'status': status.label})

    def _add_criterion(self, subsection, threshold=0.75):
        """
        Tags the subsection the way Studio does (object_id is the usage key string) and attaches a criterion to it.
        """
        block_id = subsection.location.block_id
        taxonomy = CompetencyTaxonomy.objects.create(name=f'Taxonomy {block_id}', export_id=f'taxonomy-{block_id}')
        tag = Tag.objects.create(taxonomy=taxonomy, value=f'Competency {block_id}')
        object_tag = ObjectTag.objects.create(object_id=str(subsection.location), taxonomy=taxonomy, tag=tag)
        profile = CompetencyRuleProfile.objects.create(
            competency_taxonomy=taxonomy,
            rule_type=RuleType.GRADE,
            rule_payload={'op': 'gte', 'value': threshold, 'scale': 'percent'},
        )
        return CompetencyCriterion.objects.create(
            group=CompetencyCriteriaGroup.objects.create(tag=tag), object_tag=object_tag, rule_profile=profile,
        )

    def _status(self, criterion):
        """
        Returns the learner's stored status id for the criterion.
        """
        return StudentCompetencyCriteriaStatus.objects.get(user=self.request.user, criterion=criterion).status_id

    def _grade(self, earned, possible, subsection=None):
        """
        Persists the subsection grade through the factory with the given graded score.
        """
        with mock_get_score(earned, possible):
            self.subsection_grade_factory.update(subsection or self.sequence)

    def _override_to_pass(self):
        """
        Creates a staff override that sets the first sequence's graded score to 0.8.
        """
        model = PersistentSubsectionGrade.read_grade(self.request.user.id, self.sequence.location)
        PersistentSubsectionGradeOverride.update_or_create_override(
            UserFactory(), model, earned_graded_override=4.0, possible_graded_override=5.0,
        )

    def test_grade_meeting_threshold_demonstrates_criterion(self):
        criterion = self._add_criterion(self.sequence)

        self._grade(4, 5)

        assert self._status(criterion) == MasteryStatus.DEMONSTRATED

    def test_grade_below_threshold_is_attempted_not_demonstrated(self):
        criterion = self._add_criterion(self.sequence)

        self._grade(1, 4)

        assert self._status(criterion) == MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED

    def test_staff_override_raising_failing_score_demonstrates_criterion(self):
        criterion = self._add_criterion(self.sequence)
        self._grade(1, 4)
        assert self._status(criterion) == MasteryStatus.ATTEMPTED_NOT_DEMONSTRATED

        self._override_to_pass()
        self._grade(1, 4)

        assert self._status(criterion) == MasteryStatus.DEMONSTRATED

    def test_withdrawn_override_keeps_criterion_demonstrated(self):
        criterion = self._add_criterion(self.sequence)
        self._grade(1, 4)
        self._override_to_pass()
        self._grade(1, 4)

        PersistentSubsectionGradeOverride.objects.all().delete()
        self._grade(1, 4)

        assert self._status(criterion) == MasteryStatus.DEMONSTRATED

    def test_subsection_without_criteria_writes_no_status(self):
        self._grade(4, 5)

        assert PersistentSubsectionGrade.objects.count() == 1
        assert not StudentCompetencyCriteriaStatus.objects.exists()

    def test_status_write_failure_leaves_no_grade(self):
        self._add_criterion(self.sequence)

        with mock_get_score(4, 5), patch.object(StudentCompetencyCriteriaStatus, 'save', side_effect=DatabaseError):
            with pytest.raises(DatabaseError):
                self.subsection_grade_factory.update(self.sequence)

        assert not PersistentSubsectionGrade.objects.exists()

    def test_each_updated_subsection_records_its_own_status(self):
        criterion = self._add_criterion(self.sequence)
        criterion2 = self._add_criterion(self.sequence2)

        with mock_get_score(4, 5):
            for subsection in (self.sequence, self.sequence2):
                self.subsection_grade_factory.update(subsection)

        assert self._status(criterion) == MasteryStatus.DEMONSTRATED
        assert self._status(criterion2) == MasteryStatus.DEMONSTRATED

    def test_factory_create_records_status(self):
        criterion = self._add_criterion(self.sequence)

        with mock_get_score(4, 5):
            SubsectionGradeFactory(self.request.user, self.course, self.course_structure).create(
                self.sequence, force_calculate=True,
            )

        assert self._status(criterion) == MasteryStatus.DEMONSTRATED

    def test_bulk_create_unsaved_records_status(self):
        criterion = self._add_criterion(self.sequence)

        with mock_get_score(4, 5):
            self.subsection_grade_factory.create(self.sequence, read_only=True, force_calculate=True)
            self.subsection_grade_factory.bulk_create_unsaved()

        assert self._status(criterion) == MasteryStatus.DEMONSTRATED
