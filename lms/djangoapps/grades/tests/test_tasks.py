"""
Tests for the functionality and infrastructure of grades tasks.
"""


import itertools
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import ddt
import pytz
from django.db.utils import IntegrityError
from django.utils import timezone
from edx_toggles.toggles.testutils import override_waffle_flag
from stevedore.extension import Extension, ExtensionManager

from common.djangoapps.student.models import CourseEnrollment, anonymous_id_for_user
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.track.event_transaction_utils import create_new_event_transaction_id, get_event_transaction_id
from common.djangoapps.util.date_utils import to_timestamp
from lms.djangoapps.courseware.tests.test_group_access import MemoryUserPartitionScheme
from lms.djangoapps.grades import tasks
from lms.djangoapps.grades.config.waffle import ENFORCE_FREEZE_GRADE_AFTER_COURSE_END
from lms.djangoapps.grades.constants import ScoreDatabaseTableEnum
from lms.djangoapps.grades.models import PersistentCourseGrade, PersistentSubsectionGrade
from lms.djangoapps.grades.signals.signals import PROBLEM_WEIGHTED_SCORE_CHANGED
from lms.djangoapps.grades.tasks import (
    RECALCULATE_GRADE_DELAY_SECONDS,
    _course_task_args,
    compute_all_grades_for_course,
    compute_grades_for_course,
    compute_grades_for_course_v2,
    recalculate_subsection_grade_v3
)
from openedx.core.djangoapps.content.block_structure.exceptions import BlockStructureNotFound
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, check_mongo_calls
from xmodule.partitions.partitions import USER_PARTITION_SCHEME_NAMESPACE, Group, UserPartition

from .utils import mock_get_score


class HasCourseWithProblemsMixin:
    """
    Mixin to provide tests with a sample course with graded subsections
    """
    def set_up_course(self, create_multiple_subsections=False, course_end=None):
        """
        Configures the course for this test.
        """
        self.course = CourseFactory.create(
            org='edx',
            name='course',
            run='run',
            end=course_end
        )

        self.chapter = BlockFactory.create(parent=self.course, category="chapter", display_name="Chapter")
        self.sequential = BlockFactory.create(parent=self.chapter, category='sequential', display_name="Sequential1")
        self.problem = BlockFactory.create(parent=self.sequential, category='problem', display_name='Problem')

        if create_multiple_subsections:
            seq2 = BlockFactory.create(parent=self.chapter, category='sequential')
            BlockFactory.create(parent=seq2, category='problem')

        self.frozen_now_datetime = datetime.now().replace(tzinfo=pytz.UTC)
        self.frozen_now_timestamp = to_timestamp(self.frozen_now_datetime)

        self.problem_weighted_score_changed_kwargs = OrderedDict([
            ('weighted_earned', 1.0),
            ('weighted_possible', 2.0),
            ('user_id', self.user.id),
            ('anonymous_user_id', 5),
            ('course_id', str(self.course.id)),
            ('usage_id', str(self.problem.location)),
            ('only_if_higher', None),
            ('modified', self.frozen_now_datetime),
            ('score_db_table', ScoreDatabaseTableEnum.courseware_student_module),
        ])

        create_new_event_transaction_id()

        self.recalculate_subsection_grade_kwargs = OrderedDict([
            ('user_id', self.user.id),
            ('course_id', str(self.course.id)),
            ('usage_id', str(self.problem.location)),
            ('anonymous_user_id', 5),
            ('only_if_higher', None),
            ('expected_modified_time', self.frozen_now_timestamp),
            ('score_deleted', False),
            ('event_transaction_id', str(get_event_transaction_id())),
            ('event_transaction_type', 'edx.grades.problem.submitted'),
            ('score_db_table', ScoreDatabaseTableEnum.courseware_student_module),
        ])

        # this call caches the anonymous id on the user object, saving 4 queries in all happy path tests
        _ = anonymous_id_for_user(self.user, self.course.id)
        # pylint: enable=attribute-defined-outside-init,no-member


@ddt.ddt
class RecalculateSubsectionGradeTest(HasCourseWithProblemsMixin, ModuleStoreTestCase):
    """
    Ensures that the recalculate subsection grade task functions as expected when run.
    """
    ENABLED_SIGNALS = ['course_published', 'pre_publish']

    def setUp(self):
        super().setUp()
        self.user = UserFactory()

    @contextmanager
    def mock_csm_get_score(self, score=MagicMock(grade=1.0, max_grade=2.0)):
        """
        Mocks the scores needed by the SCORE_PUBLISHED signal
        handler. By default, sets the returned score to 1/2.
        """
        with patch("lms.djangoapps.grades.tasks.get_score", return_value=score):
            yield

    def test_triggered_by_problem_weighted_score_change(self):
        """
        Ensures that the PROBLEM_WEIGHTED_SCORE_CHANGED signal enqueues the correct task.
        """
        self.set_up_course()
        send_args = self.problem_weighted_score_changed_kwargs
        local_task_args = self.recalculate_subsection_grade_kwargs.copy()
        local_task_args['event_transaction_type'] = 'edx.grades.problem.submitted'
        local_task_args['force_update_subsections'] = False
        with self.mock_csm_get_score() and patch(
            'lms.djangoapps.grades.tasks.recalculate_subsection_grade_v3.apply_async',
            return_value=None
        ) as mock_task_apply:
            PROBLEM_WEIGHTED_SCORE_CHANGED.send(sender=None, **send_args)
            mock_task_apply.assert_called_once_with(countdown=RECALCULATE_GRADE_DELAY_SECONDS, kwargs=local_task_args)

    @patch('lms.djangoapps.grades.signals.signals.SUBSECTION_SCORE_CHANGED.send')
    def test_triggers_subsection_score_signal(self, mock_subsection_signal):
        """
        Ensures that a subsection grade recalculation triggers a signal.
        """
        self.set_up_course()
        self._apply_recalculate_subsection_grade()
        assert mock_subsection_signal.called

    def test_block_structure_created_only_once(self):
        self.set_up_course()
        with patch(
            'openedx.core.djangoapps.content.block_structure.factory.BlockStructureFactory.create_from_store',
            side_effect=BlockStructureNotFound(self.course.location),
        ) as mock_block_structure_create:
            self._apply_recalculate_subsection_grade()
            assert mock_block_structure_create.call_count == 1

    @ddt.data(
        (ModuleStoreEnum.Type.split, 1, 42, True),
        (ModuleStoreEnum.Type.split, 1, 42, False),
    )
    @ddt.unpack
    def test_query_counts(self, default_store, num_mongo_calls, num_sql_calls, create_multiple_subsections):
        with self.store.default_store(default_store):
            self.set_up_course(create_multiple_subsections=create_multiple_subsections)
            with check_mongo_calls(num_mongo_calls):
                with self.assertNumQueries(num_sql_calls):
                    self._apply_recalculate_subsection_grade()

    @ddt.data(
        (ModuleStoreEnum.Type.split, 1, 42),
    )
    @ddt.unpack
    def test_query_counts_dont_change_with_more_content(self, default_store, num_mongo_calls, num_sql_calls):
        with self.store.default_store(default_store):
            self.set_up_course(create_multiple_subsections=True)

            num_problems = 10
            for _ in range(num_problems):
                BlockFactory.create(parent=self.sequential, category='problem')

            num_sequentials = 10
            for _ in range(num_sequentials):
                BlockFactory.create(parent=self.chapter, category='sequential')

            with check_mongo_calls(num_mongo_calls):
                with self.assertNumQueries(num_sql_calls):
                    self._apply_recalculate_subsection_grade()

    @patch('lms.djangoapps.grades.signals.signals.SUBSECTION_SCORE_CHANGED.send')
    def test_other_inaccessible_subsection(self, mock_subsection_signal):
        self.set_up_course()
        accessible_seq = BlockFactory.create(parent=self.chapter, category='sequential')
        inaccessible_seq = BlockFactory.create(parent=self.chapter, category='sequential', visible_to_staff_only=True)

        # Update problem to have 2 additional sequential parents.
        # So in total, 3 sequential parents, with one inaccessible.
        for sequential in (accessible_seq, inaccessible_seq):
            sequential.children = [self.problem.location]
            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, self.course.id):
                self.store.update_item(sequential, self.user.id)

        # Make sure the signal is sent for only the 1 accessible sequentials.
        # Update: draft branch content shouldn't be accessible
        self._apply_recalculate_subsection_grade()
        assert mock_subsection_signal.call_count == 1
        sequentials_signalled = {
            args[1]['subsection_grade'].location
            for args in mock_subsection_signal.call_args_list
        }
        self.assertSetEqual(
            sequentials_signalled,
            {self.sequential.location},
        )

    @patch('lms.djangoapps.grades.signals.signals.SUBSECTION_SCORE_CHANGED.send')
    def test_problem_block_with_restricted_access(self, mock_subsection_signal):
        """
        Test that `SUBSECTION_SCORE_CHANGED` is sent for a restricted problem block.
        """
        self.set_up_course()

        UserPartition.scheme_extensions = ExtensionManager.make_test_instance(
            [
                Extension(
                    "memory",
                    USER_PARTITION_SCHEME_NAMESPACE,
                    MemoryUserPartitionScheme(),
                    None
                )
            ],
            namespace=USER_PARTITION_SCHEME_NAMESPACE
        )
        verified_group = Group(60, 'verified')
        verified_partition = UserPartition(
            0,
            'Verified Partition',
            'Verified Learners',
            [verified_group],
            scheme=UserPartition.get_scheme("memory"),
        )

        accessible_seq = BlockFactory.create(parent=self.chapter, category='sequential')
        restricted_problem = BlockFactory.create(
            parent=accessible_seq,
            category='problem',
            display_name='Restricted Problem',
            group_access={verified_partition.id: [verified_group.id]}
        )

        self.recalculate_subsection_grade_kwargs['usage_id'] = str(restricted_problem.location)
        verified_partition.scheme.set_group_for_user(self.user, verified_partition, verified_group)

        self._apply_recalculate_subsection_grade()
        assert mock_subsection_signal.call_count == 1
        UserPartition.scheme_extensions = None

    @ddt.data(
        (ModuleStoreEnum.Type.split, 1, 42),
    )
    @ddt.unpack
    def test_persistent_grades_on_course(self, default_store, num_mongo_queries, num_sql_queries):
        with self.store.default_store(default_store):
            self.set_up_course()
            with check_mongo_calls(num_mongo_queries):
                with self.assertNumQueries(num_sql_queries):
                    self._apply_recalculate_subsection_grade()
            assert PersistentCourseGrade.read(self.user.id, self.course.id) is not None
            assert len(PersistentSubsectionGrade.bulk_read_grades(self.user.id, self.course.id)) > 0

    @patch('lms.djangoapps.grades.signals.signals.SUBSECTION_SCORE_CHANGED.send')
    @patch('lms.djangoapps.grades.subsection_grade_factory.SubsectionGradeFactory.update')
    def test_retry_first_time_only(self, mock_update, mock_course_signal):
        """
        Ensures that a task retry completes after a one-time failure.
        """
        self.set_up_course()
        mock_update.side_effect = [IntegrityError("WHAMMY"), None]
        self._apply_recalculate_subsection_grade()
        assert mock_course_signal.call_count == 1

    @patch('lms.djangoapps.grades.tasks.recalculate_subsection_grade_v3.retry')
    @patch('lms.djangoapps.grades.subsection_grade_factory.SubsectionGradeFactory.update')
    def test_retry_on_integrity_error(self, mock_update, mock_retry):
        """
        Ensures that tasks will be retried if IntegrityErrors are encountered.
        """
        self.set_up_course()
        mock_update.side_effect = IntegrityError("WHAMMY")
        self._apply_recalculate_subsection_grade()
        self._assert_retry_called(mock_retry)

    @ddt.data(ScoreDatabaseTableEnum.courseware_student_module, ScoreDatabaseTableEnum.submissions,
              ScoreDatabaseTableEnum.overrides)
    @patch('lms.djangoapps.grades.tasks.recalculate_subsection_grade_v3.retry')
    @patch('lms.djangoapps.grades.tasks.log')
    def test_retry_when_db_not_updated(self, score_db_table, mock_log, mock_retry):
        self.set_up_course()
        self.recalculate_subsection_grade_kwargs['score_db_table'] = score_db_table
        modified_datetime = datetime.utcnow().replace(tzinfo=pytz.UTC) - timedelta(days=1)
        if score_db_table == ScoreDatabaseTableEnum.submissions:
            with patch('lms.djangoapps.grades.tasks.sub_api.get_score') as mock_sub_score:
                mock_sub_score.return_value = {
                    'created_at': modified_datetime
                }
                self._apply_recalculate_subsection_grade(
                    mock_score=MagicMock(module_type='any_block_type')
                )
        elif score_db_table == ScoreDatabaseTableEnum.courseware_student_module:
            self._apply_recalculate_subsection_grade(
                mock_score=MagicMock(modified=modified_datetime)
            )
        else:
            with patch('lms.djangoapps.grades.api') as mock_grade_service:
                mock_grade_service.get_subsection_grade_override = MagicMock(
                    return_value=MagicMock(modified=modified_datetime)
                )
                recalculate_subsection_grade_v3.apply(kwargs=self.recalculate_subsection_grade_kwargs)

        self._assert_retry_called(mock_retry)
        assert 'Grades: tasks._has_database_updated_with_new_score is False.' in mock_log.info.call_args_list[0][0][0]

    @ddt.data(
        *itertools.product(
            (True, False),
            (ScoreDatabaseTableEnum.courseware_student_module, ScoreDatabaseTableEnum.submissions,
             ScoreDatabaseTableEnum.overrides),
        )
    )
    @ddt.unpack
    @patch('lms.djangoapps.grades.tasks.recalculate_subsection_grade_v3.retry')
    @patch('lms.djangoapps.grades.tasks.log')
    def test_when_no_score_found(self, score_deleted, score_db_table, mock_log, mock_retry):
        self.set_up_course()
        self.recalculate_subsection_grade_kwargs['score_deleted'] = score_deleted
        self.recalculate_subsection_grade_kwargs['score_db_table'] = score_db_table

        if score_db_table == ScoreDatabaseTableEnum.submissions:
            with patch('lms.djangoapps.grades.tasks.sub_api.get_score') as mock_sub_score:
                mock_sub_score.return_value = None
                self._apply_recalculate_subsection_grade(
                    mock_score=MagicMock(module_type='any_block_type')
                )
        elif score_db_table == ScoreDatabaseTableEnum.overrides:
            with patch('lms.djangoapps.grades.api') as mock_grade_service:
                mock_grade_service.get_subsection_grade_override.return_value = None
                recalculate_subsection_grade_v3.apply(kwargs=self.recalculate_subsection_grade_kwargs)
        else:
            self._apply_recalculate_subsection_grade(mock_score=None)

        if score_deleted:
            self._assert_retry_not_called(mock_retry)
        else:
            self._assert_retry_called(mock_retry)
            assert 'Grades: tasks._has_database_updated_with_new_score is False.'\
                   in mock_log.info.call_args_list[0][0][0]

    @patch('lms.djangoapps.grades.tasks.log')
    @patch('lms.djangoapps.grades.tasks.recalculate_subsection_grade_v3.retry')
    @patch('lms.djangoapps.grades.subsection_grade_factory.SubsectionGradeFactory.update')
    def test_log_unknown_error(self, mock_update, mock_retry, mock_log):
        """
        Ensures that unknown errors are logged before a retry.
        """
        self.set_up_course()
        mock_update.side_effect = Exception("General exception with no further detail!")
        self._apply_recalculate_subsection_grade()
        assert 'General exception with no further detail!' in mock_log.info.call_args[0][0]
        self._assert_retry_called(mock_retry)

    @patch('lms.djangoapps.grades.tasks.log')
    @patch('lms.djangoapps.grades.tasks.recalculate_subsection_grade_v3.retry')
    @patch('lms.djangoapps.grades.subsection_grade_factory.SubsectionGradeFactory.update')
    def test_no_log_known_error(self, mock_update, mock_retry, mock_log):
        """
        Ensures that known errors are not logged before a retry.
        """
        self.set_up_course()
        mock_update.side_effect = IntegrityError("race condition oh noes")
        self._apply_recalculate_subsection_grade()
        assert not mock_log.info.called
        self._assert_retry_called(mock_retry)

    def _apply_recalculate_subsection_grade(
            self,
            mock_score=MagicMock(
                modified=datetime.utcnow().replace(tzinfo=pytz.UTC) + timedelta(days=1),
                grade=1.0,
                max_grade=2.0,
            )
    ):
        """
        Calls the recalculate_subsection_grade task with necessary
        mocking in place.
        """
        with self.mock_csm_get_score(mock_score):
            with mock_get_score(1, 2):
                recalculate_subsection_grade_v3.apply(kwargs=self.recalculate_subsection_grade_kwargs)

    def _assert_retry_called(self, mock_retry):
        """
        Verifies the task was retried and with the correct
        number of arguments.
        """
        assert mock_retry.called
        assert len(mock_retry.call_args[1]['kwargs']) == len(self.recalculate_subsection_grade_kwargs)

    def _assert_retry_not_called(self, mock_retry):
        """
        Verifies the task was not retried.
        """
        assert not mock_retry.called


@ddt.ddt
class ComputeGradesForCourseTest(HasCourseWithProblemsMixin, ModuleStoreTestCase):
    """
    Test compute_grades_for_course_v2 task.
    """

    ENABLED_SIGNALS = ['course_published', 'pre_publish']

    def setUp(self):
        super().setUp()
        self.users = [UserFactory.create() for _ in range(12)]
        self.set_up_course()
        for user in self.users:
            CourseEnrollment.enroll(user, self.course.id)

    @ddt.data(*range(0, 12, 3))
    def test_behavior(self, batch_size):
        with mock_get_score(1, 2):
            result = compute_grades_for_course_v2.delay(
                course_key=str(self.course.id),
                batch_size=batch_size,
                offset=4,
            )
        assert result.successful
        assert PersistentCourseGrade.objects.filter(course_id=self.course.id).count() == min(batch_size, 8)
        assert PersistentSubsectionGrade.objects.filter(course_id=self.course.id).count() == min(batch_size, 8)

    @ddt.data(*range(1, 12, 3))
    def test_course_task_args(self, test_batch_size):
        offset_expected = 0
        for course_key, offset, batch_size in _course_task_args(
            batch_size=test_batch_size, course_key=self.course.id, from_settings=False
        ):
            assert course_key == str(self.course.id)
            assert batch_size == test_batch_size
            assert offset == offset_expected
            offset_expected += test_batch_size


class RecalculateGradesForUserTest(HasCourseWithProblemsMixin, ModuleStoreTestCase):
    """
    Test recalculate_course_and_subsection_grades_for_user task.
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.set_up_course()
        CourseEnrollment.enroll(self.user, self.course.id)

    def test_recalculation_happy_path(self):
        with patch('lms.djangoapps.grades.tasks.CourseGradeFactory') as mock_factory:
            factory = mock_factory.return_value
            factory.read.return_value = MagicMock(attempted=True)

            kwargs = {
                'user_id': self.user.id,
                'course_key': str(self.course.id),
            }

            task_result = tasks.recalculate_course_and_subsection_grades_for_user.apply_async(kwargs=kwargs)
            task_result.get()

            factory.read.assert_called_once_with(self.user, course_key=self.course.id)
            factory.update.assert_called_once_with(
                user=self.user,
                course_key=self.course.id,
                force_update_subsections=True,
            )

    def test_recalculation_doesnt_happen_if_not_previously_attempted(self):
        with patch('lms.djangoapps.grades.tasks.CourseGradeFactory') as mock_factory:
            factory = mock_factory.return_value
            factory.read.return_value = MagicMock(attempted=False)

            kwargs = {
                'user_id': self.user.id,
                'course_key': str(self.course.id),
            }

            task_result = tasks.recalculate_course_and_subsection_grades_for_user.apply_async(kwargs=kwargs)
            task_result.get()

            factory.read.assert_called_once_with(self.user, course_key=self.course.id)
            assert not factory.update.called


@ddt.ddt
class FreezeGradingAfterCourseEndTest(HasCourseWithProblemsMixin, ModuleStoreTestCase):
    """
    Test enforce_freeze_grade_after_course_end waffle flag controlling grading tasks.
    """
    def setUp(self):
        super().setUp()
        self.users = [UserFactory.create() for _ in range(12)]
        self.user = self.users[0]
        self.freeze_grade_flag = ENFORCE_FREEZE_GRADE_AFTER_COURSE_END

    def _assert_log(self, mock_log, method_name):
        assert mock_log.info.called
        log_message = f"Attempted {method_name} for course '%s', but grades are frozen."
        assert log_message in mock_log.info.call_args_list[0][0][0]

    def _assert_for_freeze_grade_flag(  # lint-amnesty, pylint: disable=missing-function-docstring
        self,
        result,
        freeze_flag_value,
        end_date_adjustment,
        mock_log,
        mock_call,
        task_name
    ):
        assert result.successful
        if freeze_flag_value and end_date_adjustment > 30:
            mock_call.assert_not_called()
            self._assert_log(mock_log, task_name)
        else:
            mock_call.assert_called_once()

    @ddt.data(
        *itertools.product(
            (True, False),
            (29, 31)
        )
    )
    @ddt.unpack
    @patch('lms.djangoapps.grades.tasks.log')
    def test_compute_all_grades_for_course(self, freeze_flag_value, end_date_adjustment, mock_log):
        self.set_up_course(course_end=timezone.now() - timedelta(end_date_adjustment))
        for user in self.users:
            CourseEnrollment.enroll(user, self.course.id)

        with override_waffle_flag(self.freeze_grade_flag, active=freeze_flag_value):
            with patch(
                'lms.djangoapps.grades.tasks.compute_grades_for_course_v2.apply_async',
                return_value=None
            ) as mock_compute_grades:
                result = compute_all_grades_for_course.apply_async(
                    kwargs={
                        'course_key': str(self.course.id)
                    }
                )
                self._assert_for_freeze_grade_flag(
                    result,
                    freeze_flag_value,
                    end_date_adjustment,
                    mock_log,
                    mock_compute_grades,
                    'compute_all_grades_for_course'
                )

    @ddt.data(
        *itertools.product(
            (True, False),
            (29, 31)
        )
    )
    @ddt.unpack
    @patch('lms.djangoapps.grades.tasks.log')
    def test_compute_grades_for_course(self, freeze_flag_value, end_date_adjustment, mock_log):
        self.set_up_course(course_end=timezone.now() - timedelta(end_date_adjustment))
        for user in self.users:
            CourseEnrollment.enroll(user, self.course.id)

        with override_waffle_flag(self.freeze_grade_flag, active=freeze_flag_value):
            with patch('lms.djangoapps.grades.tasks.CourseGradeFactory') as mock_factory:
                factory = mock_factory.return_value
                with mock_get_score(1, 2):
                    result = compute_grades_for_course.apply_async(
                        kwargs={
                            'course_key': str(self.course.id),
                            'batch_size': 2,
                            'offset': 4,
                        }
                    )
                    self._assert_for_freeze_grade_flag(
                        result,
                        freeze_flag_value,
                        end_date_adjustment,
                        mock_log,
                        factory.iter,
                        'compute_grades_for_course'
                    )

    @ddt.data(
        *itertools.product(
            (True, False),
            (29, 31)
        )
    )
    @ddt.unpack
    @patch('lms.djangoapps.grades.tasks.log')
    def test_recalculate_course_and_subsection_grades(self, freeze_flag_value, end_date_adjustment, mock_log):
        self.set_up_course(course_end=timezone.now() - timedelta(end_date_adjustment))
        CourseEnrollment.enroll(self.user, self.course.id)
        with override_waffle_flag(self.freeze_grade_flag, active=freeze_flag_value):
            with patch('lms.djangoapps.grades.tasks.CourseGradeFactory') as mock_factory:
                factory = mock_factory.return_value
                kwargs = {
                    'user_id': self.user.id,
                    'course_key': str(self.course.id),
                }

                result = tasks.recalculate_course_and_subsection_grades_for_user.apply_async(kwargs=kwargs)
                self._assert_for_freeze_grade_flag(
                    result,
                    freeze_flag_value,
                    end_date_adjustment,
                    mock_log,
                    factory.read,
                    'recalculate_course_and_subsection_grades_for_user'
                )

    @ddt.data(
        *itertools.product(
            (True, False),
            (29, 31)
        )
    )
    @ddt.unpack
    @patch('lms.djangoapps.grades.tasks.log')
    def test_recalculate_subsection_grade_v3(self, freeze_flag_value, end_date_adjustment, mock_log):
        self.set_up_course(course_end=timezone.now() - timedelta(end_date_adjustment))
        for user in self.users:
            CourseEnrollment.enroll(user, self.course.id)

        with override_waffle_flag(self.freeze_grade_flag, active=freeze_flag_value):
            modified_datetime = datetime.utcnow().replace(tzinfo=pytz.UTC) - timedelta(days=1)  # lint-amnesty, pylint: disable=unused-variable
            with patch('lms.djangoapps.grades.tasks._has_db_updated_with_new_score') as mock_has_db_updated:
                result = recalculate_subsection_grade_v3.apply_async(kwargs=self.recalculate_subsection_grade_kwargs)
                self._assert_for_freeze_grade_flag(
                    result,
                    freeze_flag_value,
                    end_date_adjustment,
                    mock_log,
                    mock_has_db_updated,
                    '_recalculate_subsection_grade'
                )
