"""
Unit tests for LMS instructor-initiated background tasks.

Runs tasks on answers to course problems to validate that code
paths actually work.
"""


import json
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import ddt
import pytest
from celery.states import FAILURE, SUCCESS
from django.utils.translation import gettext_noop
from opaque_keys.edx.keys import i4xEncoder

from common.djangoapps.course_modes.models import CourseMode
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.courseware.tests.factories import StudentModuleFactory
from lms.djangoapps.instructor_task.exceptions import UpdateProblemModuleStateError
from lms.djangoapps.instructor_task.models import InstructorTask
from lms.djangoapps.instructor_task.tasks import (
    delete_problem_state,
    export_ora2_data,
    export_ora2_submission_files,
    export_ora2_summary,
    generate_certificates,
    override_problem_score,
    rescore_problem,
    reset_problem_attempts,
)
from lms.djangoapps.instructor_task.tests.factories import InstructorTaskFactory
from lms.djangoapps.instructor_task.tests.test_base import InstructorTaskModuleTestCase
from xmodule.modulestore.exceptions import ItemNotFoundError  # lint-amnesty, pylint: disable=wrong-import-order

PROBLEM_URL_NAME = "test_urlname"


class TestTaskFailure(Exception):
    """
    An example exception to indicate failure of a mocked task.
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


class TestInstructorTasks(InstructorTaskModuleTestCase):
    """
    Ensure tasks behave as expected.
    """

    def setUp(self):
        super().setUp()
        self.initialize_course()
        self.instructor = self.create_instructor('instructor')
        self.location = self.problem_location(PROBLEM_URL_NAME)

    def _create_input_entry(
            self, student_ident=None, use_problem_url=True, course_id=None, only_if_higher=False, score=None
    ):
        """Creates a InstructorTask entry for testing."""
        task_id = str(uuid4())
        task_input = {'only_if_higher': only_if_higher}
        if use_problem_url:
            task_input['problem_url'] = self.location
        if student_ident is not None:
            task_input['student'] = student_ident
        if score is not None:
            task_input['score'] = score

        course_id = course_id or self.course.id
        instructor_task = InstructorTaskFactory.create(
            course_id=course_id,
            requester=self.instructor,
            task_input=json.dumps(task_input, cls=i4xEncoder),
            task_key='dummy value',
            task_id=task_id
        )
        return instructor_task

    def _get_block_instance_args(self):
        """
        Calculate dummy values for parameters needed for instantiating xmodule instances.
        """
        return {
            'request_info': {
                'username': 'dummy_username',
                'user_id': 'dummy_id',
            },
        }

    def _run_task_with_mock_celery(self, task_class, entry_id, task_id, expected_failure_message=None):
        """Submit a task and mock how celery provides a current_task."""
        self.current_task = Mock()  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        self.current_task.request = Mock()
        self.current_task.request.id = task_id
        self.current_task.update_state = Mock()
        if expected_failure_message is not None:
            self.current_task.update_state.side_effect = TestTaskFailure(expected_failure_message)
        task_args = [entry_id, self._get_block_instance_args()]

        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task') as mock_get_task:
            mock_get_task.return_value = self.current_task
            return task_class.apply(task_args, task_id=task_id).get()

    def _test_missing_current_task(self, task_class):
        """Check that a task_class fails when celery doesn't provide a current_task."""
        task_entry = self._create_input_entry()
        with pytest.raises(ValueError):
            task_class(task_entry.id, self._get_block_instance_args())

    def _test_undefined_course(self, task_class):
        """Run with celery, but with no course defined."""
        task_entry = self._create_input_entry(course_id="bogus/course/id")
        with pytest.raises(ItemNotFoundError):
            self._run_task_with_mock_celery(task_class, task_entry.id, task_entry.task_id)

    def _test_undefined_problem(self, task_class):
        """Run with celery, but no problem defined."""
        task_entry = self._create_input_entry()
        with pytest.raises(ItemNotFoundError):
            self._run_task_with_mock_celery(task_class, task_entry.id, task_entry.task_id)

    def _test_run_with_task(self, task_class, action_name, expected_num_succeeded,
                            expected_num_skipped=0, expected_attempted=0, expected_total=0):
        """Run a task and check the number of StudentModules processed."""
        task_entry = self._create_input_entry()
        status = self._run_task_with_mock_celery(task_class, task_entry.id, task_entry.task_id)
        expected_attempted = expected_attempted \
            if expected_attempted else expected_num_succeeded + expected_num_skipped
        expected_total = expected_total \
            if expected_total else expected_num_succeeded + expected_num_skipped
        # check return value
        assert status.get('attempted') == expected_attempted
        assert status.get('succeeded') == expected_num_succeeded
        assert status.get('skipped') == expected_num_skipped
        assert status.get('total') == expected_total
        assert status.get('action_name') == action_name
        assert status.get('duration_ms') > 0
        # compare with entry in table:
        entry = InstructorTask.objects.get(id=task_entry.id)
        assert json.loads(entry.task_output) == status
        assert entry.task_state == SUCCESS

    def _test_run_with_no_state(self, task_class, action_name):
        """Run with no StudentModules defined for the current problem."""
        self.define_option_problem(PROBLEM_URL_NAME)
        self._test_run_with_task(task_class, action_name, 0)

    def _create_students_with_state(self, num_students, state=None, grade=0, max_grade=1):
        """Create students, a problem, and StudentModule objects for testing"""
        self.define_option_problem(PROBLEM_URL_NAME)
        enrolled_students = self._create_and_enroll_students(num_students)

        for student in enrolled_students:
            StudentModuleFactory.create(
                course_id=self.course.id,
                module_state_key=self.location,
                student=student,
                grade=grade,
                max_grade=max_grade,
                state=state
            )
        return enrolled_students

    def _create_and_enroll_students(self, num_students, mode=CourseMode.DEFAULT_MODE_SLUG):
        """Create & enroll students for testing"""
        return [
            self.create_student(username='robot%d' % i, email='robot+test+%d@edx.org' % i, mode=mode)
            for i in range(num_students)
        ]

    def _create_students_with_no_state(self, num_students):
        """Create students and a problem for testing"""
        self.define_option_problem(PROBLEM_URL_NAME)
        enrolled_students = self._create_and_enroll_students(num_students)
        return enrolled_students

    def _assert_num_attempts(self, students, num_attempts):
        """Check the number attempts for all students is the same"""
        for student in students:
            module = StudentModule.objects.get(course_id=self.course.id,
                                               student=student,
                                               module_state_key=self.location)
            state = json.loads(module.state)
            assert state['attempts'] == num_attempts

    def _test_run_with_failure(self, task_class, expected_message):
        """Run a task and trigger an artificial failure with the given message."""
        task_entry = self._create_input_entry()
        self.define_option_problem(PROBLEM_URL_NAME)
        with pytest.raises(TestTaskFailure):
            self._run_task_with_mock_celery(task_class, task_entry.id, task_entry.task_id, expected_message)
        # compare with entry in table:
        entry = InstructorTask.objects.get(id=task_entry.id)
        assert entry.task_state == FAILURE
        output = json.loads(entry.task_output)
        assert output['exception'] == 'ExceptionWithTraceback'
        assert expected_message in output['message']

    def _test_run_with_long_error_msg(self, task_class):
        """
        Run with an error message that is so long it will require
        truncation (as well as the jettisoning of the traceback).
        """
        task_entry = self._create_input_entry()
        self.define_option_problem(PROBLEM_URL_NAME)
        expected_message = "x" * 1500
        with pytest.raises(TestTaskFailure):
            self._run_task_with_mock_celery(task_class, task_entry.id, task_entry.task_id, expected_message)
        # compare with entry in table:
        entry = InstructorTask.objects.get(id=task_entry.id)
        assert entry.task_state == FAILURE
        assert 1023 > len(entry.task_output)
        output = json.loads(entry.task_output)
        assert output['exception'] == 'ExceptionWithTraceback'
        assert (
            expected_message[:(len(output['message']) - 7)] + '\n"""...'
        ) == output['message']
        assert 'traceback' not in output

    def _test_run_with_short_error_msg(self, task_class):
        """
        Run with an error message that is short enough to fit
        in the output, but long enough that the traceback won't.
        Confirm that the traceback is truncated.
        """
        task_entry = self._create_input_entry()
        self.define_option_problem(PROBLEM_URL_NAME)
        expected_message = "x" * 900
        with pytest.raises(TestTaskFailure):
            self._run_task_with_mock_celery(task_class, task_entry.id, task_entry.task_id, expected_message)
        # compare with entry in table:
        entry = InstructorTask.objects.get(id=task_entry.id)
        assert entry.task_state == FAILURE
        assert 1023 > len(entry.task_output)
        output = json.loads(entry.task_output)
        assert output['exception'] == 'ExceptionWithTraceback'
        assert (expected_message[:(len(output['message']) - 7)] + '\n"""...') == output['message']
        assert output['message'][(- 3):] == '...'


class TestOverrideScoreInstructorTask(TestInstructorTasks):
    """Tests instructor task to override learner's problem score"""
    def assert_task_output(self, output, **expected_output):
        """
        Check & compare output of the task
        """
        assert output.get('total') == expected_output.get('total')
        assert output.get('attempted') == expected_output.get('attempted')
        assert output.get('succeeded') == expected_output.get('succeeded')
        assert output.get('skipped') == expected_output.get('skipped')
        assert output.get('failed') == expected_output.get('failed')
        assert output.get('action_name') == expected_output.get('action_name')
        assert output.get('duration_ms') > expected_output.get('duration_ms', 0)

    def get_task_output(self, task_id):
        """Get and load instructor task output"""
        entry = InstructorTask.objects.get(id=task_id)
        return json.loads(entry.task_output)

    def test_override_missing_current_task(self):
        self._test_missing_current_task(override_problem_score)

    def test_override_undefined_course(self):
        """Tests that override problem score raises exception with undefined course"""
        self._test_undefined_course(override_problem_score)

    def test_override_undefined_problem(self):
        """Tests that override problem score raises exception with undefined problem"""
        self._test_undefined_problem(override_problem_score)

    def test_override_with_no_state(self):
        """Tests override score with no problem state in StudentModule"""
        self._test_run_with_no_state(override_problem_score, 'overridden')

    def test_override_with_failure(self):
        self._test_run_with_failure(override_problem_score, 'We expected this to fail')

    def test_override_with_long_error_msg(self):
        self._test_run_with_long_error_msg(override_problem_score)

    def test_override_with_short_error_msg(self):
        self._test_run_with_short_error_msg(override_problem_score)

    def test_overriding_non_scorable(self):
        """
        Tests that override problem score raises an error if block has not `set_score` method.
        """
        input_state = json.dumps({'done': True})
        num_students = 1
        self._create_students_with_state(num_students, input_state)
        task_entry = self._create_input_entry(score=0)
        mock_instance = MagicMock()
        del mock_instance.set_score
        with patch(
                'lms.djangoapps.instructor_task.tasks_helper.module_state.get_block_for_descriptor'
        ) as mock_get_block:
            mock_get_block.return_value = mock_instance
            with pytest.raises(UpdateProblemModuleStateError):
                self._run_task_with_mock_celery(override_problem_score, task_entry.id, task_entry.task_id)
        # check values stored in table:
        entry = InstructorTask.objects.get(id=task_entry.id)
        output = json.loads(entry.task_output)
        assert output['exception'] == 'ExceptionWithTraceback'
        assert 'Scores cannot be overridden for this problem type.' in output['message']

    def test_overriding_unaccessable(self):
        """
        Tests score override for a problem in a course, for all students fails if user has answered a
        problem to which user does not have access to.
        """
        input_state = json.dumps({'done': True})
        num_students = 1
        self._create_students_with_state(num_students, input_state)
        task_entry = self._create_input_entry(score=0)
        with patch('lms.djangoapps.instructor_task.tasks_helper.module_state.get_block_for_descriptor',
                   return_value=None):
            self._run_task_with_mock_celery(override_problem_score, task_entry.id, task_entry.task_id)

        self.assert_task_output(
            output=self.get_task_output(task_entry.id),
            total=num_students,
            attempted=num_students,
            succeeded=0,
            skipped=0,
            failed=num_students,
            action_name='overridden'
        )

    def test_overriding_success(self):
        """
        Tests score override for a problem in a course, for all students succeeds.
        """
        mock_instance = MagicMock()
        getattr(mock_instance, 'override_problem_score').return_value = None  # lint-amnesty, pylint: disable=literal-used-as-attribute

        num_students = 10
        self._create_students_with_state(num_students)
        task_entry = self._create_input_entry(score=0)
        with patch(
                'lms.djangoapps.instructor_task.tasks_helper.module_state.get_block_for_descriptor'
        ) as mock_get_block:
            mock_get_block.return_value = mock_instance
            mock_instance.max_score = MagicMock(return_value=99999.0)
            mock_instance.weight = 99999.0
            self._run_task_with_mock_celery(override_problem_score, task_entry.id, task_entry.task_id)

        self.assert_task_output(
            output=self.get_task_output(task_entry.id),
            total=num_students,
            attempted=num_students,
            succeeded=num_students,
            skipped=0,
            failed=0,
            action_name='overridden'
        )

    def test_overriding_success_with_no_state(self):
        """
        Tests that score override is successful for a learner when they have no state.
        """
        num_students = 1
        enrolled_students = self._create_students_with_no_state(num_students=num_students)
        task_entry = self._create_input_entry(score=1, student_ident=enrolled_students[0].username)

        self._run_task_with_mock_celery(override_problem_score, task_entry.id, task_entry.task_id)
        self.assert_task_output(
            output=self.get_task_output(task_entry.id),
            total=num_students,
            attempted=num_students,
            succeeded=num_students,
            skipped=0,
            failed=0,
            action_name='overridden'
        )


@ddt.ddt
class TestRescoreInstructorTask(TestInstructorTasks):
    """Tests problem-rescoring instructor task."""

    def assert_task_output(self, output, **expected_output):
        """
        Check & compare output of the task
        """
        assert output.get('total') == expected_output.get('total')
        assert output.get('attempted') == expected_output.get('attempted')
        assert output.get('succeeded') == expected_output.get('succeeded')
        assert output.get('skipped') == expected_output.get('skipped')
        assert output.get('failed') == expected_output.get('failed')
        assert output.get('action_name') == expected_output.get('action_name')
        assert output.get('duration_ms') > expected_output.get('duration_ms', 0)

    def get_task_output(self, task_id):
        """Get and load instructor task output"""
        entry = InstructorTask.objects.get(id=task_id)
        return json.loads(entry.task_output)

    def test_rescore_missing_current_task(self):
        self._test_missing_current_task(rescore_problem)

    def test_rescore_undefined_course(self):
        self._test_undefined_course(rescore_problem)

    def test_rescore_undefined_problem(self):
        self._test_undefined_problem(rescore_problem)

    def test_rescore_with_no_state(self):
        self._test_run_with_no_state(rescore_problem, 'rescored')

    def test_rescore_with_failure(self):
        self._test_run_with_failure(rescore_problem, 'We expected this to fail')

    def test_rescore_with_long_error_msg(self):
        self._test_run_with_long_error_msg(rescore_problem)

    def test_rescore_with_short_error_msg(self):
        self._test_run_with_short_error_msg(rescore_problem)

    def test_rescoring_unrescorable(self):
        input_state = json.dumps({'done': True})
        num_students = 1
        self._create_students_with_state(num_students, input_state)
        task_entry = self._create_input_entry()
        mock_instance = MagicMock()
        del mock_instance.rescore_problem
        del mock_instance.rescore
        with patch('lms.djangoapps.instructor_task.tasks_helper.module_state.get_block_for_descriptor') as mock_get_block:  # lint-amnesty, pylint: disable=line-too-long
            mock_get_block.return_value = mock_instance
            with pytest.raises(UpdateProblemModuleStateError):
                self._run_task_with_mock_celery(rescore_problem, task_entry.id, task_entry.task_id)
        # check values stored in table:
        entry = InstructorTask.objects.get(id=task_entry.id)
        output = json.loads(entry.task_output)
        assert output['exception'] == 'ExceptionWithTraceback'
        assert 'Specified module {} of type {} does not support rescoring.'.format(
            self.location,
            mock_instance.__class__,
        ) in output['message']

    def test_rescoring_unaccessable(self):
        """
        Tests rescores a problem in a course, for all students fails if user has answered a
        problem to which user does not have access to.
        """
        input_state = json.dumps({'done': True})
        num_students = 1
        self._create_students_with_state(num_students, input_state)
        task_entry = self._create_input_entry()
        with patch('lms.djangoapps.instructor_task.tasks_helper.module_state.get_block_for_descriptor', return_value=None):  # lint-amnesty, pylint: disable=line-too-long
            self._run_task_with_mock_celery(rescore_problem, task_entry.id, task_entry.task_id)

        self.assert_task_output(
            output=self.get_task_output(task_entry.id),
            total=num_students,
            attempted=num_students,
            succeeded=0,
            skipped=0,
            failed=num_students,
            action_name='rescored'
        )

    def test_rescoring_success(self):
        """
        Tests rescores a problem in a course, for all students succeeds.
        """
        mock_instance = MagicMock()
        getattr(mock_instance, 'rescore').return_value = None  # lint-amnesty, pylint: disable=literal-used-as-attribute
        mock_instance.has_submitted_answer.return_value = True
        del mock_instance.done  # old CAPA code used to use this value so we delete it here to be sure

        num_students = 10
        self._create_students_with_state(num_students)
        task_entry = self._create_input_entry()
        with patch(
                'lms.djangoapps.instructor_task.tasks_helper.module_state.get_block_for_descriptor'
        ) as mock_get_block:
            mock_get_block.return_value = mock_instance
            self._run_task_with_mock_celery(rescore_problem, task_entry.id, task_entry.task_id)

        self.assert_task_output(
            output=self.get_task_output(task_entry.id),
            total=num_students,
            attempted=num_students,
            succeeded=num_students,
            skipped=0,
            failed=0,
            action_name='rescored'
        )


class TestResetAttemptsInstructorTask(TestInstructorTasks):
    """Tests instructor task that resets problem attempts."""

    def test_reset_missing_current_task(self):
        self._test_missing_current_task(reset_problem_attempts)

    def test_reset_undefined_course(self):
        self._test_undefined_course(reset_problem_attempts)

    def test_reset_undefined_problem(self):
        self._test_undefined_problem(reset_problem_attempts)

    def test_reset_with_no_state(self):
        self._test_run_with_no_state(reset_problem_attempts, 'reset')

    def test_reset_with_failure(self):
        self._test_run_with_failure(reset_problem_attempts, 'We expected this to fail')

    def test_reset_with_long_error_msg(self):
        self._test_run_with_long_error_msg(reset_problem_attempts)

    def test_reset_with_short_error_msg(self):
        self._test_run_with_short_error_msg(reset_problem_attempts)

    def test_reset_with_some_state(self):
        initial_attempts = 3
        input_state = json.dumps({'attempts': initial_attempts})
        num_students = 10
        students = self._create_students_with_state(num_students, input_state)
        # check that entries were set correctly
        self._assert_num_attempts(students, initial_attempts)
        # run the task
        self._test_run_with_task(reset_problem_attempts, 'reset', num_students)
        # check that entries were reset
        self._assert_num_attempts(students, 0)

    def test_reset_with_zero_attempts(self):
        initial_attempts = 0
        input_state = json.dumps({'attempts': initial_attempts})
        num_students = 10
        students = self._create_students_with_state(num_students, input_state)
        # check that entries were set correctly
        self._assert_num_attempts(students, initial_attempts)
        # run the task
        self._test_run_with_task(reset_problem_attempts, 'reset', 0, expected_num_skipped=num_students)
        # check that entries were reset
        self._assert_num_attempts(students, 0)

    def _test_reset_with_student(self, use_email):
        """Run a reset task for one student, with several StudentModules for the problem defined."""
        num_students = 10
        initial_attempts = 3
        input_state = json.dumps({'attempts': initial_attempts})
        students = self._create_students_with_state(num_students, input_state)
        # check that entries were set correctly
        for student in students:
            module = StudentModule.objects.get(course_id=self.course.id,
                                               student=student,
                                               module_state_key=self.location)
            state = json.loads(module.state)
            assert state['attempts'] == initial_attempts

        if use_email:
            student_ident = students[3].email
        else:
            student_ident = students[3].username
        task_entry = self._create_input_entry(student_ident)

        status = self._run_task_with_mock_celery(reset_problem_attempts, task_entry.id, task_entry.task_id)
        # check return value
        assert status.get('attempted') == 1
        assert status.get('succeeded') == 1
        assert status.get('total') == 1
        assert status.get('action_name') == 'reset'
        assert status.get('duration_ms') > 0

        # compare with entry in table:
        entry = InstructorTask.objects.get(id=task_entry.id)
        assert json.loads(entry.task_output) == status
        assert entry.task_state == SUCCESS
        # check that the correct entry was reset
        for index, student in enumerate(students):
            module = StudentModule.objects.get(course_id=self.course.id,
                                               student=student,
                                               module_state_key=self.location)
            state = json.loads(module.state)
            if index == 3:
                assert state['attempts'] == 0
            else:
                assert state['attempts'] == initial_attempts

    def test_reset_with_student_username(self):
        self._test_reset_with_student(False)

    def test_reset_with_student_email(self):
        self._test_reset_with_student(True)


class TestDeleteStateInstructorTask(TestInstructorTasks):
    """Tests instructor task that deletes problem state."""

    def test_delete_missing_current_task(self):
        self._test_missing_current_task(delete_problem_state)

    def test_delete_undefined_course(self):
        self._test_undefined_course(delete_problem_state)

    def test_delete_undefined_problem(self):
        self._test_undefined_problem(delete_problem_state)

    def test_delete_with_no_state(self):
        self._test_run_with_no_state(delete_problem_state, 'deleted')

    def test_delete_with_failure(self):
        self._test_run_with_failure(delete_problem_state, 'We expected this to fail')

    def test_delete_with_long_error_msg(self):
        self._test_run_with_long_error_msg(delete_problem_state)

    def test_delete_with_short_error_msg(self):
        self._test_run_with_short_error_msg(delete_problem_state)

    def test_delete_with_some_state(self):
        # This will create StudentModule entries -- we don't have to worry about
        # the state inside them.
        num_students = 10
        students = self._create_students_with_state(num_students)
        # check that entries were created correctly
        for student in students:
            StudentModule.objects.get(course_id=self.course.id,
                                      student=student,
                                      module_state_key=self.location)
        self._test_run_with_task(delete_problem_state, 'deleted', num_students)
        # confirm that no state can be found anymore:
        for student in students:
            with pytest.raises(StudentModule.DoesNotExist):
                StudentModule.objects.get(course_id=self.course.id,
                                          student=student,
                                          module_state_key=self.location)


class TestCertificateGenerationnstructorTask(TestInstructorTasks):
    """Tests instructor task that generates student certificates."""

    def test_generate_certificates_missing_current_task(self):
        """
        Test error is raised when certificate generation task run without current task
        """
        self._test_missing_current_task(generate_certificates)

    def test_generate_certificates_task_run(self):
        """
        Test certificate generation task run without any errors
        """
        self._test_run_with_task(
            generate_certificates,
            'certificates generated',
            0,
            0,
            expected_attempted=1,
            expected_total=1
        )


class TestOra2ResponsesInstructorTask(TestInstructorTasks):
    """Tests instructor task that fetches ora2 response data."""

    def test_ora2_missing_current_task(self):
        self._test_missing_current_task(export_ora2_data)

    def test_ora2_with_failure(self):
        self._test_run_with_failure(export_ora2_data, 'We expected this to fail')

    def test_ora2_with_long_error_msg(self):
        self._test_run_with_long_error_msg(export_ora2_data)

    def test_ora2_with_short_error_msg(self):
        self._test_run_with_short_error_msg(export_ora2_data)

    def test_ora2_runs_task(self):
        task_entry = self._create_input_entry()
        task_xblock_args = self._get_block_instance_args()

        with patch('lms.djangoapps.instructor_task.tasks.run_main_task') as mock_main_task:
            export_ora2_data(task_entry.id, task_xblock_args)
            action_name = gettext_noop('generated')

            assert mock_main_task.call_count == 1
            args = mock_main_task.call_args[0]
            assert args[0] == task_entry.id
            assert callable(args[1])
            assert args[2] == action_name


class TestOra2ExportSubmissionFilesInstructorTask(TestInstructorTasks):
    """Tests instructor task that exports ora2 submission files archive."""

    def test_ora2_missing_current_task(self):
        self._test_missing_current_task(export_ora2_submission_files)

    def test_ora2_with_failure(self):
        self._test_run_with_failure(export_ora2_submission_files, 'We expected this to fail')

    def test_ora2_with_long_error_msg(self):
        self._test_run_with_long_error_msg(export_ora2_submission_files)

    def test_ora2_with_short_error_msg(self):
        self._test_run_with_short_error_msg(export_ora2_submission_files)

    def test_ora2_runs_task(self):
        task_entry = self._create_input_entry()
        task_xblock_args = self._get_block_instance_args()

        with patch('lms.djangoapps.instructor_task.tasks.run_main_task') as mock_main_task:
            export_ora2_submission_files(task_entry.id, task_xblock_args)
            action_name = gettext_noop('compressed')

            assert mock_main_task.call_count == 1
            args = mock_main_task.call_args[0]
            assert args[0] == task_entry.id
            assert callable(args[1])
            assert args[2] == action_name


class TestOra2SummaryInstructorTask(TestInstructorTasks):
    """Tests instructor task that fetches ora2 response summary."""

    def test_ora2_missing_current_task(self):
        self._test_missing_current_task(export_ora2_summary)

    def test_ora2_with_failure(self):
        self._test_run_with_failure(export_ora2_summary, 'We expected this to fail')

    def test_ora2_with_long_error_msg(self):
        self._test_run_with_long_error_msg(export_ora2_summary)

    def test_ora2_with_short_error_msg(self):
        self._test_run_with_short_error_msg(export_ora2_summary)

    def test_ora2_runs_task(self):
        task_entry = self._create_input_entry()
        task_xblock_args = self._get_block_instance_args()

        with patch('lms.djangoapps.instructor_task.tasks.run_main_task') as mock_main_task:
            export_ora2_summary(task_entry.id, task_xblock_args)
            action_name = gettext_noop('generated')

            assert mock_main_task.call_count == 1
            args = mock_main_task.call_args[0]
            assert args[0] == task_entry.id
            assert callable(args[1])
            assert args[2] == action_name
