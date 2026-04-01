"""
Test for LMS instructor background task queue management
"""

import datetime
import json
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import pytest
import pytz
import ddt
from testfixtures import LogCapture
from celery.states import FAILURE, SUCCESS
from xmodule.modulestore.exceptions import ItemNotFoundError

from common.djangoapps.student.tests.factories import UserFactory
from common.test.utils import normalize_repr
from lms.djangoapps.bulk_email.api import create_course_email
from lms.djangoapps.bulk_email.data import BulkEmailTargetChoices
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.api import get_certificate_generation_history
from lms.djangoapps.instructor_task.api import (
    SpecificStudentIdMissingError,
    generate_anonymous_ids,
    generate_certificates_for_students,
    get_instructor_task_history,
    get_running_instructor_tasks,
    process_scheduled_instructor_tasks,
    regenerate_certificates,
    submit_bulk_course_email,
    submit_calculate_may_enroll_csv,
    submit_calculate_problem_responses_csv,
    submit_calculate_students_features_csv,
    submit_cohort_students,
    submit_course_survey_report,
    submit_delete_entrance_exam_state_for_student,
    submit_delete_problem_state_for_all_students,
    submit_export_ora2_data,
    submit_export_ora2_submission_files,
    submit_override_score,
    submit_rescore_entrance_exam_for_student,
    submit_rescore_problem_for_all_students,
    submit_rescore_problem_for_student,
    submit_reset_problem_attempts_for_all_students,
    submit_reset_problem_attempts_in_entrance_exam,
)
from lms.djangoapps.instructor_task.api_helper import AlreadyRunningError, QueueConnectionError
from lms.djangoapps.instructor_task.data import InstructorTaskTypes
from lms.djangoapps.instructor_task.models import PROGRESS, SCHEDULED, InstructorTask
from lms.djangoapps.instructor_task.tasks import (
    export_ora2_data,
    export_ora2_submission_files,
    generate_anonymous_ids_for_course,
)
from lms.djangoapps.instructor_task.tests.factories import InstructorTaskFactory, InstructorTaskScheduleFactory
from lms.djangoapps.instructor_task.tests.test_base import (
    TEST_COURSE_KEY,
    InstructorTaskCourseTestCase,
    InstructorTaskModuleTestCase,
    InstructorTaskTestCase,
    TestReportMixin
)

LOG_PATH = 'lms.djangoapps.instructor_task.api'


class InstructorTaskReportTest(InstructorTaskTestCase):
    """
    Tests API methods that involve the reporting of status for background tasks.
    """

    def test_get_running_instructor_tasks(self):
        # when fetching running tasks, we get all running tasks, and only running tasks
        for _ in range(1, 5):
            self._create_failure_entry()
            self._create_success_entry()
        progress_task_ids = [self._create_progress_entry().task_id for _ in range(1, 5)]
        task_ids = [instructor_task.task_id for instructor_task in get_running_instructor_tasks(TEST_COURSE_KEY)]
        assert set(task_ids) == set(progress_task_ids)

    def test_get_instructor_task_history(self):
        # when fetching historical tasks, we get all tasks, including running tasks
        expected_ids = []
        for _ in range(1, 5):
            expected_ids.append(self._create_failure_entry().task_id)
            expected_ids.append(self._create_success_entry().task_id)
            expected_ids.append(self._create_progress_entry().task_id)
        task_ids = [instructor_task.task_id for instructor_task
                    in get_instructor_task_history(TEST_COURSE_KEY, usage_key=self.problem_url)]
        assert set(task_ids) == set(expected_ids)
        # make the same call using explicit task_type:
        task_ids = [instructor_task.task_id for instructor_task
                    in get_instructor_task_history(
                        TEST_COURSE_KEY,
                        usage_key=self.problem_url,
                        task_type=InstructorTaskTypes.RESCORE_PROBLEM
                    )]
        assert set(task_ids) == set(expected_ids)
        # make the same call using a non-existent task_type:
        task_ids = [instructor_task.task_id for instructor_task
                    in get_instructor_task_history(
                        TEST_COURSE_KEY,
                        usage_key=self.problem_url,
                        task_type='dummy_type'
                    )]
        assert set(task_ids) == set()


@ddt.ddt
class InstructorTaskModuleSubmitTest(InstructorTaskModuleTestCase):
    """Tests API methods that involve the submission of module-based background tasks."""

    def setUp(self):
        super().setUp()

        self.initialize_course()
        self.student = UserFactory.create(username="student", email="student@edx.org")
        self.instructor = UserFactory.create(username="instructor", email="instructor@edx.org")

    def test_submit_nonexistent_modules(self):
        # confirm that a rescore of a non-existent module returns an exception
        problem_url = InstructorTaskModuleTestCase.problem_location("NonexistentProblem")
        request = None
        with pytest.raises(ItemNotFoundError):
            submit_rescore_problem_for_student(request, problem_url, self.student)
        with pytest.raises(ItemNotFoundError):
            submit_rescore_problem_for_all_students(request, problem_url)
        with pytest.raises(ItemNotFoundError):
            submit_reset_problem_attempts_for_all_students(request, problem_url)
        with pytest.raises(ItemNotFoundError):
            submit_delete_problem_state_for_all_students(request, problem_url)

    def test_submit_nonrescorable_modules(self):
        # confirm that a rescore of an existent but unscorable module returns an exception
        # (Note that it is easier to test a scoreable but non-rescorable module in test_tasks,
        # where we are creating real modules.)
        problem_url = self.problem_section.location
        request = None
        with pytest.raises(NotImplementedError):
            submit_rescore_problem_for_student(request, problem_url, self.student)
        with pytest.raises(NotImplementedError):
            submit_rescore_problem_for_all_students(request, problem_url)

    @ddt.data(
        (normalize_repr(submit_rescore_problem_for_all_students), InstructorTaskTypes.RESCORE_PROBLEM),
        (
            normalize_repr(submit_rescore_problem_for_all_students),
            InstructorTaskTypes.RESCORE_PROBLEM_IF_HIGHER,
            {'only_if_higher': True}
        ),
        (normalize_repr(submit_rescore_problem_for_student), InstructorTaskTypes.RESCORE_PROBLEM, {'student': True}),
        (
            normalize_repr(submit_rescore_problem_for_student),
            InstructorTaskTypes.RESCORE_PROBLEM_IF_HIGHER,
            {'student': True, 'only_if_higher': True}
        ),
        (normalize_repr(submit_reset_problem_attempts_for_all_students), InstructorTaskTypes.RESET_PROBLEM_ATTEMPTS),
        (normalize_repr(submit_delete_problem_state_for_all_students), InstructorTaskTypes.DELETE_PROBLEM_STATE),
        (
            normalize_repr(submit_rescore_entrance_exam_for_student),
            InstructorTaskTypes.RESCORE_PROBLEM,
            {'student': True}
        ),
        (
            normalize_repr(submit_rescore_entrance_exam_for_student),
            InstructorTaskTypes.RESCORE_PROBLEM_IF_HIGHER,
            {'student': True, 'only_if_higher': True}
        ),
        (
            normalize_repr(submit_reset_problem_attempts_in_entrance_exam),
            InstructorTaskTypes.RESET_PROBLEM_ATTEMPTS,
            {'student': True}
        ),
        (
            normalize_repr(submit_delete_entrance_exam_state_for_student),
            InstructorTaskTypes.DELETE_PROBLEM_STATE,
            {'student': True}
        ),
        (
            normalize_repr(submit_override_score),
            InstructorTaskTypes.OVERRIDE_PROBLEM_SCORE,
            {'student': True, 'score': 0}
        )
    )
    @ddt.unpack
    def test_submit_task(self, task_function, expected_task_type, params=None):
        """
        Tests submission of instructor task.
        """
        if params is None:
            params = {}
        if params.get('student'):
            params['student'] = self.student

        problem_url_name = 'H1P1'
        self.define_option_problem(problem_url_name)
        location = InstructorTaskModuleTestCase.problem_location(problem_url_name)

        # unsuccessful submission, exception raised while submitting.
        with patch('lms.djangoapps.instructor_task.tasks_base.BaseInstructorTask.apply_async') as apply_async:

            error = Exception()
            apply_async.side_effect = error

            with pytest.raises(QueueConnectionError):
                instructor_task = task_function(self.create_task_request(self.instructor), location, **params)

            most_recent_task = InstructorTask.objects.latest('id')
            assert most_recent_task.task_state == FAILURE

        # successful submission
        instructor_task = task_function(self.create_task_request(self.instructor), location, **params)
        assert instructor_task.task_type == expected_task_type

        # test resubmitting, by updating the existing record:
        instructor_task = InstructorTask.objects.get(id=instructor_task.id)
        instructor_task.task_state = PROGRESS
        instructor_task.save()

        with pytest.raises(AlreadyRunningError):
            task_function(self.create_task_request(self.instructor), location, **params)


@patch('lms.djangoapps.bulk_email.models.html_to_text', Mock(return_value='Mocking CourseEmail.text_message', autospec=True))  # lint-amnesty, pylint: disable=line-too-long
class InstructorTaskCourseSubmitTest(TestReportMixin, InstructorTaskCourseTestCase):
    """Tests API methods that involve the submission of course-based background tasks."""

    def setUp(self):
        super().setUp()

        self.initialize_course()
        self.student = UserFactory.create(username="student", email="student@edx.org")
        self.instructor = UserFactory.create(username="instructor", email="instructor@edx.org")

    def _define_course_email(self):
        """Create CourseEmail object for testing."""
        email_recipient_groups = [
            BulkEmailTargetChoices.SEND_TO_MYSELF,
            BulkEmailTargetChoices.SEND_TO_STAFF,
            BulkEmailTargetChoices.SEND_TO_LEARNERS
        ]
        course_email = create_course_email(
            self.course.id,
            self.instructor,
            email_recipient_groups,
            "Test Subject",
            "<p>This is a test message</p>"
        )
        return course_email.id

    def _test_resubmission(self, api_call):
        """
        Tests the resubmission of an instructor task through the API.
        The call to the API is a lambda expression passed via
        `api_call`.  Expects that the API call returns the resulting
        InstructorTask object, and that its resubmission raises
        `AlreadyRunningError`.
        """
        instructor_task = api_call()
        instructor_task = InstructorTask.objects.get(id=instructor_task.id)
        instructor_task.task_state = PROGRESS
        instructor_task.save()
        with pytest.raises(AlreadyRunningError):
            api_call()

    def _generate_scheduled_task(self, task_state=None):
        return InstructorTaskFactory.create(
            task_type=InstructorTaskTypes.BULK_COURSE_EMAIL,
            course_id=self.course.id,
            task_input="{'email_id': 1, 'to_option': ['myself']}",
            task_key="3416a75f4cea9109507cacd8e2f2aefc",
            task_id=str(uuid4()),
            task_state=task_state if task_state else SCHEDULED,
            task_output=None,
            requester=self.instructor
        )

    def _generate_scheduled_task_schedule(self, task, due_date):
        return InstructorTaskScheduleFactory.create(
            task=task,
            task_args=json.dumps(self._generate_task_args()),
            task_due=due_date
        )

    def _generate_task_args(self):
        """
        Utility function that creates a sample `task_args` value for a scheduled task.
        """
        task_args = {
            "request_info": {
                "username": self.instructor.username,
                "user_id": self.instructor.id,
                "ip": "127.0.0.1",
                "agent": "Mozilla",
                "host": "localhost:18000"
            },
            "task_id": "622748b3-2831-432e-b519-4fde2706ca59"
        }
        return task_args

    def test_submit_bulk_email_all(self):
        email_id = self._define_course_email()
        api_call = lambda: submit_bulk_course_email(
            self.create_task_request(self.instructor),
            self.course.id,
            email_id
        )
        self._test_resubmission(api_call)

    def test_submit_calculate_problem_responses(self):
        api_call = lambda: submit_calculate_problem_responses_csv(
            self.create_task_request(self.instructor),
            self.course.id,
            problem_locations='',
        )
        self._test_resubmission(api_call)

    def test_submit_calculate_students_features(self):
        api_call = lambda: submit_calculate_students_features_csv(
            self.create_task_request(self.instructor),
            self.course.id,
            features=[]
        )
        self._test_resubmission(api_call)

    def test_submit_course_survey_report(self):
        api_call = lambda: submit_course_survey_report(
            self.create_task_request(self.instructor), self.course.id
        )
        self._test_resubmission(api_call)

    def test_submit_calculate_may_enroll(self):
        api_call = lambda: submit_calculate_may_enroll_csv(
            self.create_task_request(self.instructor),
            self.course.id,
            features=[]
        )
        self._test_resubmission(api_call)

    def test_submit_cohort_students(self):
        api_call = lambda: submit_cohort_students(
            self.create_task_request(self.instructor),
            self.course.id,
            file_name='filename.csv'
        )
        self._test_resubmission(api_call)

    def test_submit_ora2_request_task(self):
        request = self.create_task_request(self.instructor)

        with patch('lms.djangoapps.instructor_task.api.submit_task') as mock_submit_task:
            mock_submit_task.return_value = MagicMock()
            submit_export_ora2_data(request, self.course.id)

            mock_submit_task.assert_called_once_with(
                request, InstructorTaskTypes.EXPORT_ORA2_DATA, export_ora2_data, self.course.id, {}, '')

    def test_submit_export_ora2_submission_files(self):
        request = self.create_task_request(self.instructor)

        with patch('lms.djangoapps.instructor_task.api.submit_task') as mock_submit_task:
            mock_submit_task.return_value = MagicMock()
            submit_export_ora2_submission_files(request, self.course.id)

            mock_submit_task.assert_called_once_with(
                request,
                InstructorTaskTypes.EXPORT_ORA2_SUBMISSION_FILES,
                export_ora2_submission_files,
                self.course.id,
                {},
                ''
            )

    def test_submit_generate_certs_students(self):
        """
        Tests certificates generation task submission api
        """
        api_call = lambda: generate_certificates_for_students(
            self.create_task_request(self.instructor),
            self.course.id
        )
        self._test_resubmission(api_call)

    def test_regenerate_certificates(self):
        """
        Tests certificates regeneration task submission api
        """
        def api_call():
            """
            wrapper method for regenerate_certificates
            """
            return regenerate_certificates(
                self.create_task_request(self.instructor),
                self.course.id,
                [CertificateStatuses.downloadable, CertificateStatuses.generating]
            )
        self._test_resubmission(api_call)

    def test_certificate_generation_no_specific_student_id(self):
        """
        Raises ValueError when student_set is 'specific_student' and 'specific_student_id' is None.
        """
        with pytest.raises(SpecificStudentIdMissingError):
            generate_certificates_for_students(
                self.create_task_request(self.instructor),
                self.course.id,
                student_set='specific_student',
                specific_student_id=None
            )

    def test_certificate_generation_history(self):
        """
        Tests that a new record is added whenever certificate generation/regeneration task is submitted.
        """
        instructor_task = generate_certificates_for_students(
            self.create_task_request(self.instructor),
            self.course.id
        )
        cert_args = {
            "course_id": self.course.id,
            "generated_by": self.instructor,
            "instructor_task": instructor_task,
            "is_regeneration": False
        }
        certificate_generation_history = get_certificate_generation_history(**cert_args)

        # Validate that record was added to CertificateGenerationHistory
        assert certificate_generation_history.exists()

        instructor_task = regenerate_certificates(
            self.create_task_request(self.instructor),
            self.course.id,
            [CertificateStatuses.downloadable, CertificateStatuses.generating]
        )
        cert_args = {
            "course_id": self.course.id,
            "generated_by": self.instructor,
            "instructor_task": instructor_task,
            "is_regeneration": True
        }
        certificate_generation_history = get_certificate_generation_history(**cert_args)

        # Validate that record was added to CertificateGenerationHistory
        assert certificate_generation_history.exists()

    def test_submit_anonymized_id_report_generation(self):
        request = self.create_task_request(self.instructor)

        with patch('lms.djangoapps.instructor_task.api.submit_task') as mock_submit_task:
            mock_submit_task.return_value = MagicMock()
            generate_anonymous_ids(request, self.course.id)

            mock_submit_task.assert_called_once_with(
                request,
                InstructorTaskTypes.GENERATE_ANONYMOUS_IDS_FOR_COURSE,
                generate_anonymous_ids_for_course,
                self.course.id,
                {},
                ''
            )

    @patch("lms.djangoapps.instructor_task.api.schedule_task")
    @patch("lms.djangoapps.instructor_task.api.submit_task")
    def test_submit_bulk_course_email_with_schedule(self, mock_submit_task, mock_schedule_task):
        """
        A test to determine if the right helper function is being called when a scheduled task is being processed.
        """
        email_id = self._define_course_email()
        schedule = datetime.datetime(2030, 8, 15, 8, 15, 12, 0, pytz.utc)
        submit_bulk_course_email(
            self.create_task_request(self.instructor),
            self.course.id,
            email_id,
            schedule
        )
        mock_schedule_task.assert_called_once()
        mock_submit_task.assert_not_called()

    @patch("lms.djangoapps.instructor_task.api.submit_scheduled_task")
    def test_process_scheduled_tasks(self, mock_submit_scheduled_task):
        """
        A test to verify the functionality of the `process_scheduled_tasks` function. This function determines which
        scheduled instructor tasks are due for execution.

        This test generates three scheduled tasks; one that has been processed, one that is due for processing, and one
        that is due in the future. In this test, one only of these tasks should be eligible for processing.
        """
        base_date = datetime.datetime.now().replace(tzinfo=datetime.timezone.utc)
        executed_instructor_task = self._generate_scheduled_task(task_state=SUCCESS)
        executed_instructor_task_due_date = base_date - datetime.timedelta(days=5)
        self._generate_scheduled_task_schedule(executed_instructor_task, executed_instructor_task_due_date)

        due_instructor_task = self._generate_scheduled_task()
        due_instructor_task_due_date = base_date - datetime.timedelta(days=1)
        due_instructor_task_schedule = self._generate_scheduled_task_schedule(
            due_instructor_task,
            due_instructor_task_due_date
        )

        future_instructor_task = self._generate_scheduled_task()
        future_instructor_task_due_date = base_date + datetime.timedelta(days=15)
        self._generate_scheduled_task_schedule(future_instructor_task, future_instructor_task_due_date)

        expected_messages = [
            f"Attempting to queue scheduled task with id '{due_instructor_task.id}'"
        ]

        with LogCapture() as log:
            process_scheduled_instructor_tasks()

        mock_submit_scheduled_task.assert_called_once_with(due_instructor_task_schedule)
        log.check_present((LOG_PATH, "INFO", expected_messages[0]),)

    @patch("lms.djangoapps.instructor_task.api.submit_scheduled_task", side_effect=QueueConnectionError("blammo!"))
    def test_process_scheduled_tasks_expect_error(self, mock_scheduled_task):
        """
        A test that verifies the behavior of the `process_scheduled_tasks` function when there is an error processing
        the request.
        """
        base_date = datetime.datetime.now().replace(tzinfo=datetime.timezone.utc)
        due_instructor_task = self._generate_scheduled_task()
        due_instructor_task_due_date = base_date - datetime.timedelta(days=1)
        self._generate_scheduled_task_schedule(due_instructor_task, due_instructor_task_due_date)
        expected_messages = [
            f"Error processing scheduled task with task id '{due_instructor_task.id}': blammo!",
        ]

        with LogCapture() as log:
            process_scheduled_instructor_tasks()

        log.check_present((LOG_PATH, "ERROR", expected_messages[0]),)
