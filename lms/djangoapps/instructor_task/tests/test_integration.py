"""
Integration Tests for LMS instructor-initiated background tasks.

Runs tasks on answers to course problems to validate that code
paths actually work.

"""


import json
import logging
import textwrap
from collections import namedtuple
from unittest.mock import patch

import pytest
import ddt
from celery.states import FAILURE, SUCCESS
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.test.utils import override_settings
from django.urls import reverse

from xmodule.capa.responsetypes import StudentInputError
from xmodule.capa.tests.response_xml_factory import CodeResponseXMLFactory, CustomResponseXMLFactory
from xmodule.capa.tests.test_util import use_unsafe_codejail
from lms.djangoapps.courseware.model_data import StudentModule
from lms.djangoapps.grades.api import CourseGradeFactory
from lms.djangoapps.instructor_task.api import (
    submit_delete_problem_state_for_all_students,
    submit_rescore_problem_for_all_students,
    submit_rescore_problem_for_student,
    submit_reset_problem_attempts_for_all_students
)
from lms.djangoapps.instructor_task.data import InstructorTaskTypes
from lms.djangoapps.instructor_task.models import InstructorTask
from lms.djangoapps.instructor_task.tasks_helper.grades import CourseGradeReport
from lms.djangoapps.instructor_task.tests.test_base import (
    OPTION_1,
    OPTION_2,
    InstructorTaskModuleTestCase,
    TestReportMixin
)
from openedx.core.djangoapps.util.testing import TestConditionalContent
from openedx.core.lib.url_utils import quote_slashes
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import BlockFactory  # lint-amnesty, pylint: disable=wrong-import-order
from common.test.utils import assert_dict_contains_subset

log = logging.getLogger(__name__)


class TestIntegrationTask(InstructorTaskModuleTestCase):
    """
    Base class to provide general methods used for "integration" testing of particular tasks.
    """

    def _assert_task_failure(self, entry_id, task_type, problem_url_name, expected_message):
        """Confirm that expected values are stored in InstructorTask on task failure."""
        instructor_task = InstructorTask.objects.get(id=entry_id)
        assert instructor_task.task_state == FAILURE
        assert instructor_task.requester.username == 'instructor'
        assert instructor_task.task_type == task_type
        task_input = json.loads(instructor_task.task_input)
        assert 'student' not in task_input
        assert task_input['problem_url'] == str(InstructorTaskModuleTestCase.problem_location(problem_url_name))
        status = json.loads(instructor_task.task_output)
        assert status['exception'] == 'ExceptionWithTraceback'
        assert expected_message in status['message']
        # check status returned:
        status = InstructorTaskModuleTestCase.get_task_status(instructor_task.task_id)
        assert expected_message in status['message']


@ddt.ddt
@override_settings(RATELIMIT_ENABLE=False)
@use_unsafe_codejail()
class TestRescoringTask(TestIntegrationTask):
    """
    Integration-style tests for rescoring problems in a background task.

    Exercises real problems with a minimum of patching.
    """

    def setUp(self):
        super().setUp()

        self.initialize_course()
        self.create_instructor('instructor')
        self.user1 = self.create_student('u1')
        self.user2 = self.create_student('u2')
        self.user3 = self.create_student('u3')
        self.user4 = self.create_student('u4')
        self.users = [self.user1, self.user2, self.user3, self.user4]
        self.logout()

        # set up test user for performing test operations
        self.setup_user()

    def render_problem(self, username, problem_url_name):
        """
        Use ajax interface to request html for a problem.
        """
        # make sure that the requested user is logged in, so that the ajax call works
        # on the right problem:
        self.login_username(username)
        # make ajax call:
        modx_url = reverse('xblock_handler', kwargs={
            'course_id': str(self.course.id),
            'usage_id': quote_slashes(str(InstructorTaskModuleTestCase.problem_location(problem_url_name))),
            'handler': 'xmodule_handler',
            'suffix': 'problem_get',
        })
        resp = self.client.post(modx_url, {})
        return resp

    def check_state(self, user, block, expected_score, expected_max_score, expected_attempts=1):
        """
        Check that the StudentModule state contains the expected values.

        The student module is found for the test course, given the `username` and problem `block`.

        Values checked include the number of attempts, the score, and the max score for a problem.
        """
        module = self.get_student_module(user.username, block)
        assert module.grade == expected_score
        assert module.max_grade == expected_max_score
        state = json.loads(module.state)
        attempts = state['attempts']
        assert attempts == expected_attempts
        if attempts > 0:
            assert 'correct_map' in state
            assert 'student_answers' in state
            assert len(state['correct_map']) > 0
            assert len(state['student_answers']) > 0

        # assume only one problem in the subsection and the grades
        # are in sync.
        expected_subsection_grade = expected_score

        course_grade = CourseGradeFactory().read(user, self.course)
        grade = course_grade.graded_subsections_by_format()['Homework'][self.problem_section.location].graded_total
        assert grade.earned == expected_subsection_grade

    def submit_rescore_all_student_answers(self, instructor, problem_url_name, only_if_higher=False):
        """Submits the particular problem for rescoring"""
        return submit_rescore_problem_for_all_students(
            self.create_task_request(instructor),
            InstructorTaskModuleTestCase.problem_location(problem_url_name),
            only_if_higher,
        )

    def submit_rescore_one_student_answer(self, instructor, problem_url_name, student, only_if_higher=False):
        """Submits the particular problem for rescoring for a particular student"""
        return submit_rescore_problem_for_student(
            self.create_task_request(instructor),
            InstructorTaskModuleTestCase.problem_location(problem_url_name),
            student,
            only_if_higher,
        )

    def verify_rescore_results(self, problem_edit, new_expected_scores, new_expected_max, rescore_if_higher):
        """
        Common helper to verify the results of rescoring for a single
        student and all students are as expected.
        """
        # get block:
        problem_url_name = 'H1P1'
        self.define_option_problem(problem_url_name)
        location = InstructorTaskModuleTestCase.problem_location(problem_url_name)
        block = self.module_store.get_item(location)

        # first store answers for each of the separate users:
        self.submit_student_answer('u1', problem_url_name, [OPTION_1, OPTION_1])
        self.submit_student_answer('u2', problem_url_name, [OPTION_1, OPTION_2])
        self.submit_student_answer('u3', problem_url_name, [OPTION_2, OPTION_1])
        self.submit_student_answer('u4', problem_url_name, [OPTION_2, OPTION_2])

        # verify each user's grade
        expected_original_scores = (2, 1, 1, 0)
        expected_original_max = 2
        for i, user in enumerate(self.users):
            self.check_state(user, block, expected_original_scores[i], expected_original_max)

        # update the data in the problem definition so the answer changes.
        self.redefine_option_problem(problem_url_name, **problem_edit)

        # confirm that simply rendering the problem again does not change the grade
        self.render_problem('u1', problem_url_name)
        self.check_state(self.user1, block, expected_original_scores[0], expected_original_max)

        # rescore the problem for only one student -- only that student's grade should change:
        self.submit_rescore_one_student_answer('instructor', problem_url_name, self.user1, rescore_if_higher)
        self.check_state(self.user1, block, new_expected_scores[0], new_expected_max)
        for i, user in enumerate(self.users[1:], start=1):  # everyone other than user1
            self.check_state(user, block, expected_original_scores[i], expected_original_max)

        # rescore the problem for all students
        self.submit_rescore_all_student_answers('instructor', problem_url_name, rescore_if_higher)
        for i, user in enumerate(self.users):
            self.check_state(user, block, new_expected_scores[i], new_expected_max)

    RescoreTestData = namedtuple('RescoreTestData', 'edit, new_expected_scores, new_expected_max')

    @ddt.data(
        RescoreTestData(edit=dict(correct_answer=OPTION_2), new_expected_scores=(0, 1, 1, 2), new_expected_max=2),
        RescoreTestData(edit=dict(num_inputs=2), new_expected_scores=(2, 1, 1, 0), new_expected_max=4),
        RescoreTestData(edit=dict(num_inputs=4), new_expected_scores=(2, 1, 1, 0), new_expected_max=8),
        RescoreTestData(edit=dict(num_responses=4), new_expected_scores=(2, 1, 1, 0), new_expected_max=4),
        RescoreTestData(edit=dict(num_inputs=2, num_responses=4), new_expected_scores=(2, 1, 1, 0), new_expected_max=8),
    )
    @ddt.unpack
    def test_rescoring_option_problem(self, problem_edit, new_expected_scores, new_expected_max):
        """
        Run rescore scenario on option problem.
        Verify rescoring updates grade after content change.
        Original problem definition has:
            num_inputs = 1
            num_responses = 2
            correct_answer = OPTION_1
        """
        self.verify_rescore_results(
            problem_edit, new_expected_scores, new_expected_max, rescore_if_higher=False,
        )

    @ddt.data(
        RescoreTestData(edit={}, new_expected_scores=(2, 1, 1, 0), new_expected_max=2),
        RescoreTestData(edit=dict(correct_answer=OPTION_2), new_expected_scores=(2, 1, 1, 2), new_expected_max=2),
    )
    @ddt.unpack
    def test_rescoring_if_higher(self, problem_edit, new_expected_scores, new_expected_max):
        self.verify_rescore_results(
            problem_edit, new_expected_scores, new_expected_max, rescore_if_higher=True,
        )

    def test_rescoring_if_higher_scores_equal(self):
        """
        Specifically tests rescore when the previous and new raw scores are equal. In this case, the scores should
        be updated.
        """
        problem_edit = dict(num_inputs=2)  # this change to the problem means the problem will now have a max score of 4
        unchanged_max = 2
        new_max = 4
        problem_url_name = 'H1P1'
        self.define_option_problem(problem_url_name)
        location = InstructorTaskModuleTestCase.problem_location(problem_url_name)
        block = self.module_store.get_item(location)

        # first store answers for each of the separate users:
        self.submit_student_answer('u1', problem_url_name, [OPTION_1, OPTION_1])
        self.submit_student_answer('u2', problem_url_name, [OPTION_2, OPTION_2])

        # verify each user's grade
        self.check_state(self.user1, block, 2, 2)  # user 1 has a 2/2
        self.check_state(self.user2, block, 0, 2)  # user 2 has a 0/2

        # update the data in the problem definition so the answer changes.
        self.redefine_option_problem(problem_url_name, **problem_edit)

        # confirm that simply rendering the problem again does not change the grade
        self.render_problem('u1', problem_url_name)
        self.check_state(self.user1, block, 2, 2)
        self.check_state(self.user2, block, 0, 2)

        # rescore the problem for all students
        self.submit_rescore_all_student_answers('instructor', problem_url_name, True)

        # user 1's score would go down, so it remains 2/2. user 2's score was 0/2, which is equivalent to the new score
        # of 0/4, so user 2's score changes to 0/4.
        self.check_state(self.user1, block, 2, unchanged_max)
        self.check_state(self.user2, block, 0, new_max)

    def test_rescoring_failure(self):
        """Simulate a failure in rescoring a problem"""
        problem_url_name = 'H1P1'
        self.define_option_problem(problem_url_name)
        self.submit_student_answer('u1', problem_url_name, [OPTION_1, OPTION_1])

        expected_message = "bad things happened"
        with patch('xmodule.capa.capa_problem.LoncapaProblem.get_grade_from_current_answers') as mock_rescore:
            mock_rescore.side_effect = ZeroDivisionError(expected_message)
            instructor_task = self.submit_rescore_all_student_answers('instructor', problem_url_name)
        self._assert_task_failure(
            instructor_task.id, InstructorTaskTypes.RESCORE_PROBLEM, problem_url_name, expected_message
        )

    def test_rescoring_bad_unicode_input(self):
        """Generate a real failure in rescoring a problem, with an answer including unicode"""
        # At one point, the student answers that resulted in StudentInputErrors were being
        # persisted (even though they were not counted as an attempt).  That is not possible
        # now, so it's harder to generate a test for how such input is handled.
        problem_url_name = 'H1P1'
        # set up an option problem -- doesn't matter really what problem it is, but we need
        # it to have an answer.
        self.define_option_problem(problem_url_name)
        self.submit_student_answer('u1', problem_url_name, [OPTION_1, OPTION_1])

        # return an input error as if it were a numerical response, with an embedded unicode character:
        expected_message = "Could not interpret '2/3\u03a9' as a number"
        with patch('xmodule.capa.capa_problem.LoncapaProblem.get_grade_from_current_answers') as mock_rescore:
            mock_rescore.side_effect = StudentInputError(expected_message)
            instructor_task = self.submit_rescore_all_student_answers('instructor', problem_url_name)

        # check instructor_task returned
        instructor_task = InstructorTask.objects.get(id=instructor_task.id)
        assert instructor_task.task_state == 'SUCCESS'
        assert instructor_task.requester.username == 'instructor'
        assert instructor_task.task_type == InstructorTaskTypes.RESCORE_PROBLEM
        task_input = json.loads(instructor_task.task_input)
        assert 'student' not in task_input
        assert task_input['problem_url'] == str(InstructorTaskModuleTestCase.problem_location(problem_url_name))
        status = json.loads(instructor_task.task_output)
        assert status['attempted'] == 1
        assert status['succeeded'] == 0
        assert status['total'] == 1

    def define_code_response_problem(self, problem_url_name):
        """
        Define an arbitrary code-response problem.

        We'll end up mocking its evaluation later.
        """
        factory = CodeResponseXMLFactory()
        grader_payload = json.dumps({"grader": "ps04/grade_square.py"})
        problem_xml = factory.build_xml(initial_display="def square(x):",
                                        answer_display="answer",
                                        grader_payload=grader_payload,
                                        num_responses=2)
        BlockFactory.create(parent_location=self.problem_section.location,
                            category="problem",
                            display_name=str(problem_url_name),
                            data=problem_xml)

    def test_rescoring_code_problem(self):
        """Run rescore scenario on problem with code submission"""
        problem_url_name = 'H1P2'
        self.define_code_response_problem(problem_url_name)
        # we fully create the CodeResponse problem, but just pretend that we're queuing it:
        with patch('xmodule.capa.xqueue_interface.XQueueInterface.send_to_queue') as mock_send_to_queue:
            mock_send_to_queue.return_value = (0, "Successfully queued")
            self.submit_student_answer('u1', problem_url_name, ["answer1", "answer2"])

        instructor_task = self.submit_rescore_all_student_answers('instructor', problem_url_name)

        instructor_task = InstructorTask.objects.get(id=instructor_task.id)
        assert instructor_task.task_state == FAILURE
        status = json.loads(instructor_task.task_output)
        assert status['exception'] == 'ExceptionWithTraceback'
        expected_message = "Problem's definition does not support rescoring."
        assert expected_message in status['message']

        status = InstructorTaskModuleTestCase.get_task_status(instructor_task.task_id)
        assert expected_message in status['message']

    def define_randomized_custom_response_problem(self, problem_url_name, redefine=False):
        """
        Defines a custom response problem that uses a random value to determine correctness.

        Generated answer is also returned as the `msg`, so that the value can be used as a
        correct answer by a test.

        If the `redefine` flag is set, then change the definition of correctness (from equals
        to not-equals).
        """
        factory = CustomResponseXMLFactory()
        script = textwrap.dedent("""
                def check_func(expect, answer_given):
                    expected = str(random.randint(0, 100))
                    return {'ok': answer_given %s expected, 'msg': expected}
            """ % ('!=' if redefine else '=='))
        problem_xml = factory.build_xml(script=script, cfn="check_func", expect="42", num_responses=1)
        if redefine:
            block = self.module_store.get_item(
                InstructorTaskModuleTestCase.problem_location(problem_url_name)
            )
            block.data = problem_xml
            with self.module_store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, block.location.course_key):  # lint-amnesty, pylint: disable=line-too-long
                self.module_store.update_item(block, self.user.id)
                self.module_store.publish(block.location, self.user.id)
        else:
            # Use "per-student" rerandomization so that check-problem can be called more than once.
            # Using "always" means we cannot check a problem twice, but we want to call once to get the
            # correct answer, and call a second time with that answer to confirm it's graded as correct.
            # Per-student rerandomization will at least generate different seeds for different users, so
            # we get a little more test coverage.
            BlockFactory.create(parent_location=self.problem_section.location,
                                category="problem",
                                display_name=str(problem_url_name),
                                data=problem_xml,
                                metadata={"rerandomize": "per_student"})

    def test_rescoring_randomized_problem(self):
        """Run rescore scenario on custom problem that uses randomize"""
        # First define the custom response problem:
        problem_url_name = 'H1P1'
        self.define_randomized_custom_response_problem(problem_url_name)
        location = InstructorTaskModuleTestCase.problem_location(problem_url_name)
        block = self.module_store.get_item(location)
        # run with more than one user
        for user in self.users:
            # first render the problem, so that a seed will be created for this user
            self.render_problem(user.username, problem_url_name)
            # submit a bogus answer, in order to get the problem to tell us its real answer
            dummy_answer = "1000"
            self.submit_student_answer(user.username, problem_url_name, [dummy_answer, dummy_answer])
            # we should have gotten the problem wrong, since we're way out of range:
            self.check_state(user, block, 0, 1, expected_attempts=1)
            # dig the correct answer out of the problem's message
            module = self.get_student_module(user.username, block)
            state = json.loads(module.state)
            correct_map = state['correct_map']
            log.info("Correct Map: %s", correct_map)
            # only one response, so pull it out:
            answer = list(correct_map.values())[0]['msg']
            self.submit_student_answer(user.username, problem_url_name, [answer, answer])
            # we should now get the problem right, with a second attempt:
            self.check_state(user, block, 1, 1, expected_attempts=2)

        # redefine the problem (as stored in Mongo) so that the definition of correct changes
        self.define_randomized_custom_response_problem(problem_url_name, redefine=True)
        # confirm that simply rendering the problem again does not result in a change
        # in the grade (or the attempts):
        self.render_problem('u1', problem_url_name)
        self.check_state(self.user1, block, 1, 1, expected_attempts=2)

        # rescore the problem for only one student -- only that student's grade should change
        # (and none of the attempts):
        self.submit_rescore_one_student_answer('instructor', problem_url_name, User.objects.get(username='u1'))
        for user in self.users:
            expected_score = 0 if user.username == 'u1' else 1
            self.check_state(user, block, expected_score, 1, expected_attempts=2)

        # rescore the problem for all students
        self.submit_rescore_all_student_answers('instructor', problem_url_name)

        # all grades should change to being wrong (with no change in attempts)
        for user in self.users:
            self.check_state(user, block, 0, 1, expected_attempts=2)


@override_settings(RATELIMIT_ENABLE=False)
class TestResetAttemptsTask(TestIntegrationTask):
    """
    Integration-style tests for resetting problem attempts in a background task.

    Exercises real problems with a minimum of patching.
    """
    userlist = ['u1', 'u2', 'u3', 'u4']

    def setUp(self):
        super().setUp()
        self.initialize_course()
        self.create_instructor('instructor')
        for username in self.userlist:
            self.create_student(username)
        self.logout()

    def get_num_attempts(self, username, block):
        """returns number of attempts stored for `username` on problem `block` for test course"""
        module = self.get_student_module(username, block)
        state = json.loads(module.state)
        return state['attempts']

    def reset_problem_attempts(self, instructor, location):
        """Submits the current problem for resetting"""
        return submit_reset_problem_attempts_for_all_students(self.create_task_request(instructor),
                                                              location)

    def test_reset_attempts_on_problem(self):
        """Run reset-attempts scenario on option problem"""
        # get block:
        problem_url_name = 'H1P1'
        self.define_option_problem(problem_url_name)
        location = InstructorTaskModuleTestCase.problem_location(problem_url_name)
        block = self.module_store.get_item(location)
        num_attempts = 3
        # first store answers for each of the separate users:
        for _ in range(num_attempts):
            for username in self.userlist:
                self.submit_student_answer(username, problem_url_name, [OPTION_1, OPTION_1])

        for username in self.userlist:
            assert self.get_num_attempts(username, block) == num_attempts

        self.reset_problem_attempts('instructor', location)

        for username in self.userlist:
            assert self.get_num_attempts(username, block) == 0

    def test_reset_failure(self):
        """Simulate a failure in resetting attempts on a problem"""
        problem_url_name = 'H1P1'
        location = InstructorTaskModuleTestCase.problem_location(problem_url_name)
        self.define_option_problem(problem_url_name)
        self.submit_student_answer('u1', problem_url_name, [OPTION_1, OPTION_1])

        expected_message = "bad things happened"
        with patch('lms.djangoapps.courseware.models.StudentModule.save') as mock_save:
            mock_save.side_effect = ZeroDivisionError(expected_message)
            instructor_task = self.reset_problem_attempts('instructor', location)
        self._assert_task_failure(
            instructor_task.id, InstructorTaskTypes.RESET_PROBLEM_ATTEMPTS, problem_url_name, expected_message
        )

    def test_reset_non_problem(self):
        """confirm that a non-problem can still be successfully reset"""
        location = self.problem_section.location
        instructor_task = self.reset_problem_attempts('instructor', location)
        instructor_task = InstructorTask.objects.get(id=instructor_task.id)
        assert instructor_task.task_state == SUCCESS


class TestDeleteProblemTask(TestIntegrationTask):
    """
    Integration-style tests for deleting problem state in a background task.

    Exercises real problems with a minimum of patching.
    """
    userlist = ['u1', 'u2', 'u3', 'u4']

    def setUp(self):
        super().setUp()

        self.initialize_course()
        self.create_instructor('instructor')
        for username in self.userlist:
            self.create_student(username)
        self.logout()

    def delete_problem_state(self, instructor, location):
        """Submits the current problem for deletion"""
        return submit_delete_problem_state_for_all_students(self.create_task_request(instructor), location)

    def test_delete_problem_state(self):
        """Run delete-state scenario on option problem"""
        # get block:
        problem_url_name = 'H1P1'
        self.define_option_problem(problem_url_name)
        location = InstructorTaskModuleTestCase.problem_location(problem_url_name)
        block = self.module_store.get_item(location)
        # first store answers for each of the separate users:
        for username in self.userlist:
            self.submit_student_answer(username, problem_url_name, [OPTION_1, OPTION_1])
        # confirm that state exists:
        for username in self.userlist:
            assert self.get_student_module(username, block) is not None
        # run delete task:
        self.delete_problem_state('instructor', location)
        # confirm that no state can be found:
        for username in self.userlist:
            with pytest.raises(StudentModule.DoesNotExist):
                self.get_student_module(username, block)

    def test_delete_failure(self):
        """Simulate a failure in deleting state of a problem"""
        problem_url_name = 'H1P1'
        location = InstructorTaskModuleTestCase.problem_location(problem_url_name)
        self.define_option_problem(problem_url_name)
        self.submit_student_answer('u1', problem_url_name, [OPTION_1, OPTION_1])

        expected_message = "bad things happened"
        with patch('lms.djangoapps.courseware.models.StudentModule.delete') as mock_delete:
            mock_delete.side_effect = ZeroDivisionError(expected_message)
            instructor_task = self.delete_problem_state('instructor', location)
        self._assert_task_failure(
            instructor_task.id, InstructorTaskTypes.DELETE_PROBLEM_STATE, problem_url_name, expected_message
        )

    def test_delete_non_problem(self):
        """confirm that a non-problem can still be successfully deleted"""
        location = self.problem_section.location
        instructor_task = self.delete_problem_state('instructor', location)
        instructor_task = InstructorTask.objects.get(id=instructor_task.id)
        assert instructor_task.task_state == SUCCESS


class TestGradeReportConditionalContent(TestReportMixin, TestConditionalContent, TestIntegrationTask):
    """
    Test grade report in cases where there are problems contained within split tests.
    """

    def verify_csv_task_success(self, task_result):
        """
        Verify that all students were successfully graded by
        `CourseGradeReport`.

        Arguments:
            task_result (dict): Return value of `CourseGradeReport.generate`.
        """
        assert_dict_contains_subset(self, {'attempted': 2, 'succeeded': 2, 'failed': 0}, task_result)

    def verify_grades_in_csv(self, students_grades, ignore_other_columns=False):
        """
        Verify that the grades CSV contains the expected grades data.

        Arguments:
            students_grades (iterable): An iterable of dictionaries,
                where each dict maps a student to another dict
                representing their grades we expect to see in the CSV.
                For example: [student_a: {'grade': 1.0, 'HW': 1.0}]
        """
        def merge_dicts(*dicts):
            """
            Return the union of dicts

            Arguments:
                dicts: tuple of dicts
            """
            return dict([item for d in dicts for item in d.items()])  # lint-amnesty, pylint: disable=consider-using-dict-comprehension

        def user_partition_group(user):
            """Return a dict having single key with value equals to students group in partition"""
            group_config_hdr_tpl = 'Experiment Group ({})'
            return {
                group_config_hdr_tpl.format(self.partition.name): self.partition.scheme.get_group_for_user(
                    self.course.id, user, self.partition
                ).name
            }

        self.verify_rows_in_csv(
            [
                merge_dicts(
                    {'Student ID': str(student.id), 'Username': student.username, 'Email': student.email},
                    grades,
                    user_partition_group(student)
                )
                for student_grades in students_grades for student, grades in student_grades.items()
            ],
            ignore_other_columns=ignore_other_columns,
        )

    def test_both_groups_problems(self):
        """
        Verify that grade export works when each user partition
        receives (different) problems.  Each user's grade on their
        particular problem should show up in the grade report.
        """
        problem_a_url = 'problem_a_url'
        problem_b_url = 'problem_b_url'
        self.define_option_problem(problem_a_url, parent=self.vertical_a)
        self.define_option_problem(problem_b_url, parent=self.vertical_b)
        # student A will get 100%, student B will get 50% because
        # OPTION_1 is the correct option, and OPTION_2 is the
        # incorrect option
        self.submit_student_answer(self.student_a.username, problem_a_url, [OPTION_1, OPTION_1])
        self.submit_student_answer(self.student_b.username, problem_b_url, [OPTION_1, OPTION_2])

        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            result = CourseGradeReport.generate(None, None, self.course.id, {}, 'graded')
            self.verify_csv_task_success(result)
            self.verify_grades_in_csv(
                [
                    {
                        self.student_a: {
                            'Grade': '1.0',
                            'Homework': '1.0',
                        }
                    },
                    {
                        self.student_b: {
                            'Grade': '0.5',
                            'Homework': '0.5',
                        }
                    },
                ],
                ignore_other_columns=True,
            )

    def test_one_group_problem(self):
        """
        Verify that grade export works when only the Group A user
        partition receives a problem.  We expect to see a column for
        the homework where student_a's entry includes their grade, and
        student b's entry shows a 0.
        """
        problem_a_url = 'problem_a_url'
        self.define_option_problem(problem_a_url, parent=self.vertical_a)

        self.submit_student_answer(self.student_a.username, problem_a_url, [OPTION_1, OPTION_1])

        with patch('lms.djangoapps.instructor_task.tasks_helper.runner._get_current_task'):
            result = CourseGradeReport.generate(None, None, self.course.id, {}, 'graded')
            self.verify_csv_task_success(result)
            self.verify_grades_in_csv(
                [
                    {
                        self.student_a: {
                            'Grade': '1.0',
                            'Homework': '1.0',
                        },
                    },
                    {
                        self.student_b: {
                            'Grade': '0.0',
                            'Homework': 'Not Attempted',
                        }
                    },
                ],
                ignore_other_columns=True
            )
