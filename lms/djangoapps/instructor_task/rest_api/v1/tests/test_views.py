"""
Tests for the instructor_task app's REST API v1 views.
"""
import datetime
import json
from uuid import uuid4

import ddt
import pytz
from celery.states import REVOKED
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from common.djangoapps.student.tests.factories import GlobalStaffFactory, InstructorFactory, StaffFactory, UserFactory
from lms.djangoapps.bulk_email.api import create_course_email, get_course_email
from lms.djangoapps.bulk_email.data import BulkEmailTargetChoices
from lms.djangoapps.instructor_task.data import InstructorTaskTypes
from lms.djangoapps.instructor_task.models import PROGRESS, SCHEDULED, InstructorTask, InstructorTaskSchedule
from lms.djangoapps.instructor_task.tests.factories import InstructorTaskFactory, InstructorTaskScheduleFactory
from openedx.core.lib.html_to_text import html_to_text
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

User = get_user_model()


@ddt.ddt
class TestScheduledBulkEmailAPIViews(APITestCase, ModuleStoreTestCase):
    """
    Tests for the ListScheduledBulkEmailInstructorTasks and DeleteScheduledBulkEmailInstructorTask views.
    """

    def setUp(self):
        super().setUp()
        self.course1 = CourseFactory()
        self.course2 = CourseFactory()
        self.global_staff = GlobalStaffFactory(username="globalstaff")
        self.instructor_course1 = InstructorFactory.create(
            course_key=self.course1.id,
            username="instructor_course1")
        self.instructor_course2 = InstructorFactory.create(
            course_key=self.course2.id,
            username="instructor_course2")
        self.staff = StaffFactory.create(
            course_key=self.course1.id,
            username="staff")
        self.user = UserFactory(username="user")

    def tearDown(self):
        super().tearDown()
        self.client.logout()

    def _build_api_url(self, course_id):
        """
        Utility function to build the URL to retrieve scheduled bulk email tasks from a given course-id.
        """
        return f"/api/instructor_task/v1/schedules/{course_id}/bulk_email/"

    def _create_course_email(self, course_id, author, targets, subject, message):
        """
        Utility function to create CourseEmail objects for the test suite.
        """
        return create_course_email(course_id, author, targets, subject, message)

    def _create_scheduled_course_emails_for_course(self, course_id, email_author, task_status, num_emails):
        """
        Utility function to create (bulk course email) scheduled instructor tasks for the test suite.
        """
        for i in range(1, (num_emails + 1)):
            # create the course email instance
            email = self._create_course_email(
                course_id,
                email_author,
                [BulkEmailTargetChoices.SEND_TO_MYSELF],
                f"Test Subject{i}",
                "<p>Test message.</p>"
            )
            # associate the course_email instance with the task
            task_input = {'email_id': email.id}
            task_id = str(uuid4())
            task = InstructorTaskFactory.create(
                course_id=course_id,
                requester=email_author,
                task_id=task_id,
                task_input=json.dumps(task_input),
                task_key='dummy value',
                task_state=task_status,
                task_type=InstructorTaskTypes.BULK_COURSE_EMAIL
            )
            # associate the task with an instructor task schedule
            task_args = {
                "request_info": {
                    "username": self.instructor_course1.username,
                    "user_id": self.instructor_course1.id,
                    "ip": "192.168.1.100",
                    "agent": "Mozilla",
                    "host": "localhost:18000"
                },
                "task_id": task.task_id
            }
            InstructorTaskScheduleFactory.create(
                task=task,
                task_args=json.dumps(task_args),
            )

    def _assert_error_code_and_message(self, response, expected_error_code, expected_error_msg):
        """
        Utility function to verify expected error code and messages from a response.
        """
        assert response.status_code == expected_error_code
        assert response.data.get("detail") == expected_error_msg

    @ddt.data(
        ("globalstaff", 200),
        ("instructor_course1", 200),
        ("instructor_course2", 403),
        ("staff", 200),
        ("user", 403)
    )
    @ddt.unpack
    def test_list_tasks_basic_permissions(self, username, expected_status):
        """
        Test case that verifies the permissions of the ListScheduledBulkEmailInstructorTasks view. A user without staff,
        instructor, or GlobalStaff access should not be able to retrieve the bulk email schedules for a course.
        """
        self.client.login(username=username, password=self.TEST_PASSWORD)
        response = self.client.get(self._build_api_url(self.course1.id))
        assert response.status_code == expected_status

    def test_list_tasks(self):
        """
        Test case that verifies the functionality of the ListScheduledBulkEmailInstructorTasks view.

        This test verifies that the results returned are for scheduled tasks awaiting execution and also confirms that
        the email data associated with the results belong to the correct task and schedule.
        """
        self._create_scheduled_course_emails_for_course(self.course1.id, self.instructor_course1, SCHEDULED, 3)
        # add a "In Progress" task which shouldn't be returned in our results
        self._create_scheduled_course_emails_for_course(self.course1.id, self.instructor_course1, PROGRESS, 1)

        self.client.login(username=self.instructor_course1.username, password=self.TEST_PASSWORD)
        response = self.client.get(self._build_api_url(self.course1.id))
        results = response.data.get("results")

        assert response.status_code == 200
        assert len(results) == 3
        # Verify the serializer is returning the correct data
        for result in results:
            # Get the full task associated with the individual response instance, then verify that the email referenced
            # in the response instance belongs to the apporpriate task.
            email_data = result.get("course_email")
            task = InstructorTask.objects.get(id=result.get("task"))
            task_input = json.loads(task.task_input)
            task_email_id = task_input.get("email_id")
            assert email_data.get("id") == task_email_id

    def test_delete_schedules(self):
        """
        Test case that verifies the functionality of the ModifyScheduledBulkEmailInstructorTask view.

        This test verifies that, when a task schedule is cancelled, the data is in the correct state and that the task
        is no longer returned with GET requests.
        """
        expected_task_output_msg = "Task revoked before running"

        self._create_scheduled_course_emails_for_course(self.course1.id, self.instructor_course1, SCHEDULED, 3)
        self.client.login(username=self.instructor_course1.username, password=self.TEST_PASSWORD)
        response = self.client.get(self._build_api_url(self.course1.id))
        results = response.data.get("results")

        assert len(results) == 3

        # cancel/delete the first scheduled bulk email task in the list
        schedule_id = results[0].get("id")
        task_id = results[0].get("task")
        delete_response = self.client.delete(f"{self._build_api_url(self.course1.id)}{schedule_id}")
        assert delete_response.status_code == 204

        # get the list of scheduled tasks again and verify the one we deleted doesn't appear in the results
        response = self.client.get(self._build_api_url(self.course1.id))
        results = response.data.get("results")
        assert len(results) == 2
        for result in results:
            assert not result.get("id") == schedule_id

        # verify the task status is REVOKED for the scheduled we cancelled
        task = InstructorTask.objects.get(id=task_id)
        assert task.task_state == REVOKED
        assert json.loads(task.task_output).get("message") == expected_task_output_msg

    def test_delete_schedules_schedule_does_not_exist(self):
        """
        Test case that verifies the functionality of the ModifyScheduledBulkEmailInstructorTask view.

        This test verifies the response received when we try to delete a task schedule that does not exist.
        """
        self.client.login(username=self.instructor_course1.username, password=self.TEST_PASSWORD)
        response = self.client.delete(f"{self._build_api_url(self.course1.id)}123456789")
        assert response.status_code == 404

    def test_delete_schedule_task_already_processed(self):
        """
        Test case that verifies the functionality of the ModifyScheduledBulkEmailInstructorTask view.

        This test verifies the response received when we try to delete a schedule for a task that has already been
        processed by Celery.
        """
        self._create_scheduled_course_emails_for_course(self.course1.id, self.instructor_course1, PROGRESS, 1)
        task = InstructorTask.objects.get(course_id=self.course1.id)
        schedule = InstructorTaskSchedule.objects.get(task=task)

        self.client.login(username=self.instructor_course1.username, password=self.TEST_PASSWORD)
        response = self.client.delete(f"{self._build_api_url(self.course1.id)}{schedule.id}")
        assert response.status_code == 400

    def test_update_schedule_new_date(self):
        """
        Test case that verifies the functionality of the ModifyScheduledBulkEmailInstructorTask view.

        This test verifies the ability to update the date and time of a schedule.
        """
        self._create_scheduled_course_emails_for_course(self.course1.id, self.instructor_course1, SCHEDULED, 1)
        task = InstructorTask.objects.get(course_id=self.course1.id)
        task_schedule = InstructorTaskSchedule.objects.get(task=task)
        schedule_datetime = datetime.datetime(3000, 6, 1, 17, 15, 0, tzinfo=pytz.utc)
        data = {
            "schedule": schedule_datetime.strftime('%Y-%m-%dT%H:%M:%SZ'),
            "browser_timezone": "UTC"
        }
        self.client.login(username=self.instructor_course1.username, password=self.TEST_PASSWORD)
        response = self.client.patch(
            f"{self._build_api_url(self.course1.id)}{task_schedule.id}",
            data=json.dumps(data),
            content_type="application/json"
        )

        updated_schedule = InstructorTaskSchedule.objects.get(task=task)
        assert response.status_code == 200
        assert updated_schedule.task_due == schedule_datetime

    def test_update_schedule_edit_email(self):
        """
        Test case that verifies the functionality of the ModifyScheduledBulkEmailInstructorTask view.

        This test verifies the ability to modify the contents of an email after it has been scheduled.
        """
        self._create_scheduled_course_emails_for_course(self.course1.id, self.instructor_course1, SCHEDULED, 1)
        task = InstructorTask.objects.get(course_id=self.course1.id)
        task_schedule = InstructorTaskSchedule.objects.get(task=task)
        email_id = json.loads(task.task_input).get("email_id")
        data = {
            "email": {
                "id": email_id,
                "targets": ["myself", "staff"],
                "subject": "UpdatedSubject",
                "message": "<p>UpdatedMessage!!!</p>"
            }
        }

        self.client.login(username=self.instructor_course1.username, password=self.TEST_PASSWORD)
        response = self.client.patch(
            f"{self._build_api_url(self.course1.id)}{task_schedule.id}",
            data=json.dumps(data),
            content_type="application/json"
        )

        course_email = get_course_email(email_id)
        targets_list = course_email.targets.values_list("target_type", flat=True)
        assert response.status_code == 200
        assert course_email.subject == data.get("email").get("subject")
        assert course_email.html_message == data.get("email").get("message")
        assert course_email.text_message == html_to_text(data.get("email").get("message"))
        assert len(targets_list) == 2
        assert targets_list[0] == BulkEmailTargetChoices.SEND_TO_MYSELF
        assert targets_list[1] == BulkEmailTargetChoices.SEND_TO_STAFF

    def test_update_schedule_bad_date(self):
        """
        Test case that verifies the functionality of the ModifyScheduledBulkEmailInstructorTask view.

        This test ensures that we don't accept dates in the past when updating a task schedule.
        """
        self._create_scheduled_course_emails_for_course(self.course1.id, self.instructor_course1, SCHEDULED, 1)
        task = InstructorTask.objects.get(course_id=self.course1.id)
        task_schedule = InstructorTaskSchedule.objects.get(task=task)
        data = {
            "schedule": "1999-05-10T17:15:00.000Z",
            "browser_timezone": "UTC"
        }
        expected_error_msg = (
            f"Cannot update instructor task schedule '{task_schedule.id}', the updated schedule occurs in the past"
        )

        self.client.login(username=self.instructor_course1.username, password=self.TEST_PASSWORD)
        response = self.client.patch(
            f"{self._build_api_url(self.course1.id)}{task_schedule.id}",
            data=json.dumps(data),
            content_type="application/json"
        )

        self._assert_error_code_and_message(response, 400, expected_error_msg)

    def test_update_schedule_bad_email_target_data(self):
        """
        Test case that verifies the functionality of the ModifyScheduledBulkEmailInstructorTask view.

        This test verifies error handling by simulating bad email data (bad target data).
        """
        self._create_scheduled_course_emails_for_course(self.course1.id, self.instructor_course1, SCHEDULED, 1)
        task = InstructorTask.objects.get(course_id=self.course1.id)
        task_schedule = InstructorTaskSchedule.objects.get(task=task)
        email_id = json.loads(task.task_input).get("email_id")
        data = {
            "email": {
                "id": email_id,
                "targets": ["pizzasteve"],
                "subject": f"Test Subject{email_id}",
                "message": "<p>Test message.</p>"
            }
        }
        expected_error_msg = (
            f"Course email being sent to an unrecognized target: 'pizzasteve' for '{self.course1.id}', subject 'Test "
            f"Subject{email_id}'"
        )

        self.client.login(username=self.instructor_course1.username, password=self.TEST_PASSWORD)
        response = self.client.patch(
            f"{self._build_api_url(self.course1.id)}{task_schedule.id}",
            data=json.dumps(data),
            content_type="application/json"
        )

        self._assert_error_code_and_message(response, 400, expected_error_msg)

    def test_update_schedule_mismatched_email_id(self):
        """
        Test case that verifies the functionality of the ModifyScheduledBulkEmailInstructorTask view.

        This test ensures that we will not update an email if it is not associated with the correct task/schedule.
        """
        self._create_scheduled_course_emails_for_course(self.course1.id, self.instructor_course1, SCHEDULED, 1)
        task = InstructorTask.objects.get(course_id=self.course1.id)
        task_schedule = InstructorTaskSchedule.objects.get(task=task)
        data = {
            "email": {
                "id": 8663863102,
                "targets": ["myself"],
                "subject": "Test Subject!",
                "message": "<p>Test message.</p>"
            }
        }
        expected_error_msg = (
            f"Cannot update instructor task '{task.id} for course '{self.course1.id}', the email id '8663863102' "
            "specified in the request does not match the email id associated with the task"
        )

        self.client.login(username=self.instructor_course1.username, password=self.TEST_PASSWORD)
        response = self.client.patch(
            f"{self._build_api_url(self.course1.id)}{task_schedule.id}",
            data=json.dumps(data),
            content_type="application/json"
        )

        self._assert_error_code_and_message(response, 400, expected_error_msg)

    def test_update_schedule_dne(self):
        """
        Test case that verifies the functionality of the ModifyScheduledBulkEmailInstructorTask view.

        This test ensures behavior when trying to update a task schedule that does not exist.
        """
        data = {
            "schedule": "3000-05-10T17:15:00.000Z",
            "browser_timezone": "UTC"
        }

        self.client.login(username=self.instructor_course1.username, password=self.TEST_PASSWORD)
        response = self.client.patch(
            f"{self._build_api_url(self.course1.id)}8675309",
            data=json.dumps(data),
            content_type="application/json"
        )
        expected_error_msg = (
            "Cannot update instructor task schedule '8675309', a schedule with this ID does not exist"
        )

        self._assert_error_code_and_message(response, 404, expected_error_msg)
