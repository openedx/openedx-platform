"""
Tests for the Instructor Task `api_helper.py` functions.
"""
import datetime
import hashlib
import json
from unittest.mock import patch
from uuid import uuid4

from celery.states import FAILURE
from testfixtures import LogCapture

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.bulk_email.api import create_course_email
from lms.djangoapps.bulk_email.data import BulkEmailTargetChoices
from lms.djangoapps.instructor_task.api_helper import QueueConnectionError, schedule_task, submit_scheduled_task
from lms.djangoapps.instructor_task.data import InstructorTaskTypes
from lms.djangoapps.instructor_task.models import SCHEDULED, InstructorTask, InstructorTaskSchedule
from lms.djangoapps.instructor_task.tests.factories import InstructorTaskFactory, InstructorTaskScheduleFactory
from lms.djangoapps.instructor_task.tests.test_base import InstructorTaskCourseTestCase

LOG_PATH = "lms.djangoapps.instructor_task.api_helper"


class ScheduledBulkEmailInstructorTaskTests(InstructorTaskCourseTestCase):
    """
    Tests for the `schedule_task` functionality, with a focus on the scheduled bulk email tasks.
    """
    class FakeRequest:
        """
        Test class reflecting a portion of the properties expected in a WSGIRequest. We use data from the originating
        web request during execution of Instructor Tasks.
        """
        def __init__(self, user):
            self.user = user
            self.META = {
                "REMOTE_ADDR": "127.0.0.1",
                'HTTP_USER_AGENT': 'test_agent',
                'SERVER_NAME': 'test_server_name',
            }

    def setUp(self):
        super().setUp()

        self.initialize_course()
        self.instructor = UserFactory.create(username="instructor", email="instructor@edx.org")
        self.request = self.FakeRequest(self.instructor)
        self.targets = [BulkEmailTargetChoices.SEND_TO_MYSELF]
        self.course_email = self._create_course_email(self.targets)
        self.schedule = datetime.datetime.now(datetime.timezone.utc)
        self.task_type = InstructorTaskTypes.BULK_COURSE_EMAIL
        self.task_input = json.dumps(self._generate_bulk_email_task_input(self.course_email, self.targets))
        self.task_key = hashlib.md5(str(self.course_email.id).encode('utf-8')).hexdigest()

    def _create_course_email(self, targets):
        """
        Create CourseEmail object for testing.
        """
        course_email = create_course_email(
            self.course.id,
            self.instructor,
            targets,
            "Test Subject",
            "<p>Test message.</p>"
        )

        return course_email

    def _generate_bulk_email_task_input(self, course_email, targets):
        return {
            "email_id": course_email.id,
            "to_option": targets
        }

    def _verify_log_messages(self, expected_messages, log):
        for index, message in enumerate(expected_messages):
            assert message in log.records[index].getMessage()

    def test_create_scheduled_instructor_task(self):
        """
        Happy path test for the `schedule_task` function. Verifies that we create an InstructorTask instance and an
        associated InstructorTaskSchedule instance as expected.
        """
        with LogCapture() as log:
            schedule_task(self.request, self.task_type, self.course.id, self.task_input, self.task_key, self.schedule)

        # get the task instance and its associated schedule for verifications
        task = InstructorTask.objects.get(course_id=self.course.id, task_key=self.task_key)
        task_schedule = InstructorTaskSchedule.objects.get(task=task)
        expected_task_args = {
            "request_info": {
                "username": self.instructor.username,
                "user_id": self.instructor.id,
                "ip": "127.0.0.1",
                "agent": "test_agent",
                "host": "test_server_name",
            },
            "task_id": task.task_id
        }
        expected_messages = [
            f"Creating a scheduled instructor task of type '{self.task_type}' for course '{self.course.id}' requested "
            f"by user with id '{self.request.user.id}'",
            f"Creating a task schedule associated with instructor task '{task.id}' and due after '{self.schedule}'",
            f"Updating task state of instructor task '{task.id}' to '{SCHEDULED}'"
        ]
        # convert from text back to JSON before comparison
        actual_task_args = json.loads(task_schedule.task_args)

        # verify the task has the correct state
        assert task.task_state == SCHEDULED
        # verify that the schedule is associated with the correct task_id (UUID)
        assert task_schedule.task_id == task.id
        # verify that the schedule is the expected date and time
        assert task_schedule.task_due == self.schedule
        # verify the task_arguments are as expected
        assert expected_task_args == actual_task_args
        self._verify_log_messages(expected_messages, log)

    @patch("lms.djangoapps.instructor_task.api_helper._get_xblock_instance_args", side_effect=Exception("boom!"))
    def test_create_scheduled_instructor_task_expect_failure(self, mock_get_xblock_instance_args):
        """
        A test to verify that we will mark a task as `FAILED` if a failure occurs during the creation of the task
        schedule.
        """
        expected_messages = [
            f"Creating a scheduled instructor task of type '{self.task_type}' for course '{self.course.id}' requested "
            f"by user with id '{self.request.user.id}'",
            "Error occurred during task or schedule creation: boom!",
        ]

        with self.assertRaises(QueueConnectionError):
            with LogCapture() as log:
                schedule_task(
                    self.request, self.task_type, self.course.id, self.task_input, self.task_key, self.schedule
                )

        task = InstructorTask.objects.get(course_id=self.course.id, task_key=self.task_key)
        assert task.task_state == FAILURE
        self._verify_log_messages(expected_messages, log)


class ScheduledInstructorTaskSubmissionTests(InstructorTaskCourseTestCase):
    """
    Unit tests scheduled instructor task functionality. Verifies behavior around retrieving and submission of instructor
    tasks due for execution.
    """
    def setUp(self):
        super().setUp()
        self.initialize_course()
        self.instructor = UserFactory.create(username="instructor", email="instructor@edx.org")
        # create an instructor task instance
        task_id = str(uuid4())
        self.task = InstructorTaskFactory.create(
            task_type=InstructorTaskTypes.BULK_COURSE_EMAIL,
            course_id=self.course.id,
            task_input="{'email_id': 41, 'to_option': ['myself']}",
            task_key="3416a75f4cea9109507cacd8e2f2aefc",
            task_id=task_id,
            task_state=SCHEDULED,
            task_output=None,
            requester=self.instructor
        )
        # associate the task with a instructor task schedule instance
        task_args = {
            "request_info": {
                "username": self.instructor.username,
                "user_id": self.instructor.id,
                "ip": "192.168.1.100",
                "agent": "Mozilla",
                "host": "localhost:18000"
            },
            "task_id": self.task.task_id
        }
        self.task_schedule = InstructorTaskScheduleFactory.create(
            task=self.task,
            task_args=json.dumps(task_args),
        )

    @patch("lms.djangoapps.instructor_task.api_helper.send_bulk_course_email.apply_async")
    def test_submit_scheduled_instructor_task(self, mock_task_execution):
        """
        A test that verifies the behavior of submitting a scheduled instructor task for execution.
        """
        schedule = InstructorTaskSchedule.objects.get(task=self.task.id)
        expected_task_arguments = json.loads(schedule.task_args)
        expected_task_args = [schedule.task.id, expected_task_arguments]
        expected_messages = [
            f"Submitting scheduled task '{schedule.task.id}' for processing",
        ]

        with LogCapture() as log:
            submit_scheduled_task(schedule)

        mock_task_execution.assert_called_once()
        mock_task_execution.assert_called_with(expected_task_args, task_id=schedule.task.task_id)
        log.check_present((LOG_PATH, "INFO", expected_messages[0]))

    def test_submit_scheduled_instructor_task_bad_task_class(self):
        """
        A test that verifies behavior when we can't determine the task class to use when submitting our scheduled task
        for execution.
        """
        schedule = InstructorTaskSchedule.objects.get(task=self.task.id)
        task = schedule.task
        task.task_type = "task_without_scheduling_support"
        task.save()
        expected_messages = [
            f"Could not submit scheduled instructor task with id '{schedule.task.id}' and task type "
            f"'{task.task_type}'. Could not determine the task class for the request.",
        ]

        with LogCapture() as log:
            submit_scheduled_task(schedule)

        log.check_present((LOG_PATH, "WARNING", expected_messages[0]))

    def test_submit_scheduled_instructor_task_with_error(self):
        """
        A test that verifies our task handling if an error occurs during task submission. Verifies that the task failure
        is correctly handled.
        """
        schedule = InstructorTaskSchedule.objects.get(task=self.task.id)
        schedule.task_args = "{malformed JSON data}"
        schedule.save()
        expected_messages = [
            f"Error submitting scheduled task '{schedule.task.id}' to Celery: Expecting property name enclosed in "
            "double quotes: line 1 column 2 (char 1)",
        ]

        with self.assertRaises(QueueConnectionError):
            with LogCapture() as log:
                submit_scheduled_task(schedule)

        assert schedule.task.task_state == FAILURE
        log.check_present((LOG_PATH, "ERROR", expected_messages[0]))
