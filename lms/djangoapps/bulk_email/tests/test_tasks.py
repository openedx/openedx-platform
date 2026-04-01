"""
Unit tests for LMS instructor-initiated background tasks.

Runs tasks on answers to course problems to validate that code
paths actually work.

"""


import json  # lint-amnesty, pylint: disable=wrong-import-order
from datetime import datetime
from itertools import chain, cycle, repeat  # lint-amnesty, pylint: disable=wrong-import-order
from smtplib import (  # lint-amnesty, pylint: disable=wrong-import-order
    SMTPAuthenticationError,
    SMTPConnectError,
    SMTPDataError,
    SMTPSenderRefused,
    SMTPServerDisconnected
)
from unittest.mock import Mock, patch  # lint-amnesty, pylint: disable=wrong-import-order
from uuid import uuid4  # lint-amnesty, pylint: disable=wrong-import-order

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from celery.states import FAILURE, SUCCESS
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.management import call_command
from django.test.utils import override_settings
from opaque_keys.edx.locator import CourseLocator

from lms.djangoapps.bulk_email.tasks import _get_course_email_context
from lms.djangoapps.instructor_task.models import InstructorTask
from lms.djangoapps.instructor_task.subtasks import SubtaskStatus, update_subtask_status
from lms.djangoapps.instructor_task.tasks import send_bulk_course_email
from lms.djangoapps.instructor_task.tests.factories import InstructorTaskFactory
from lms.djangoapps.instructor_task.tests.test_base import InstructorTaskCourseTestCase
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..models import SEND_TO_LEARNERS, SEND_TO_MYSELF, SEND_TO_STAFF, CourseEmail, Optout


class TestTaskFailure(Exception):
    """Dummy exception used for unit tests."""
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


def my_update_subtask_status(entry_id, current_task_id, new_subtask_status):
    """
    Check whether a subtask has been updated before really updating.

    Check whether a subtask which has been retried
    has had the retry already write its results here before the code
    that was invoking the retry had a chance to update this status.

    This is the norm in "eager" mode (used by tests) where the retry is called
    and run to completion before control is returned to the code that
    invoked the retry.  If the retries eventually end in failure (e.g. due to
    a maximum number of retries being attempted), the "eager" code will return
    the error for each retry as it is popped off the stack.  We want to just ignore
    the later updates that are called as the result of the earlier retries.

    This should not be an issue in production, where status is updated before
    a task is retried, and is then updated afterwards if the retry fails.
    """
    entry = InstructorTask.objects.get(pk=entry_id)
    subtask_dict = json.loads(entry.subtasks)
    subtask_status_info = subtask_dict['status']
    current_subtask_status = SubtaskStatus.from_dict(subtask_status_info[current_task_id])
    current_retry_count = current_subtask_status.get_retry_count()
    new_retry_count = new_subtask_status.get_retry_count()
    if current_retry_count <= new_retry_count:
        update_subtask_status(entry_id, current_task_id, new_subtask_status)


@patch('lms.djangoapps.bulk_email.models.html_to_text', Mock(return_value='Mocking CourseEmail.text_message', autospec=True))  # lint-amnesty, pylint: disable=line-too-long
class TestBulkEmailInstructorTask(InstructorTaskCourseTestCase):
    """Tests instructor task that send bulk email."""

    def setUp(self):
        super().setUp()
        self.initialize_course()
        self.instructor = self.create_instructor('instructor')

        # load initial content (since we don't run migrations as part of tests):
        call_command("loaddata", "course_email_template.json")

    def _create_input_entry(self, course_id=None):
        """
        Creates a InstructorTask entry for testing.

        Overrides the base class version in that this creates CourseEmail.
        """
        targets = [SEND_TO_MYSELF, SEND_TO_STAFF, SEND_TO_LEARNERS]
        course_id = course_id or self.course.id
        course_email = CourseEmail.create(
            course_id, self.instructor, targets, "Test Subject", "<p>This is a test message</p>"
        )
        task_input = {'email_id': course_email.id}
        task_id = str(uuid4())
        instructor_task = InstructorTaskFactory.create(
            course_id=course_id,
            requester=self.instructor,
            task_input=json.dumps(task_input),
            task_key='dummy value',
            task_id=task_id,
        )
        return instructor_task

    def _run_task_with_mock_celery(self, task_class, entry_id, task_id):
        """Mock was not needed for some tests, testing to see if it's needed at all."""
        task_args = [entry_id, {}]
        return task_class.apply(task_args, task_id=task_id).get()

    def test_email_missing_current_task(self):
        task_entry = self._create_input_entry()
        with pytest.raises(ValueError):
            send_bulk_course_email(task_entry.id, {})

    def test_email_undefined_course(self):
        # Check that we fail when passing in a course that doesn't exist.
        task_entry = self._create_input_entry(course_id=CourseLocator("bogus", "course", "id"))
        with pytest.raises(ValueError):
            self._run_task_with_mock_celery(send_bulk_course_email, task_entry.id, task_entry.task_id)

    def test_bad_task_id_on_update(self):
        task_entry = self._create_input_entry()

        def dummy_update_subtask_status(entry_id, _current_task_id, new_subtask_status):
            """Passes a bad value for task_id to test update_subtask_status"""
            bogus_task_id = "this-is-bogus"
            update_subtask_status(entry_id, bogus_task_id, new_subtask_status)

        with pytest.raises(ValueError):
            with patch('lms.djangoapps.bulk_email.tasks.update_subtask_status', dummy_update_subtask_status):
                send_bulk_course_email(task_entry.id, {})

    def _create_students(self, num_students):
        """Create students for testing"""
        return [self.create_student('robot%d' % i) for i in range(num_students)]

    def _assert_single_subtask_status(self, entry, succeeded, failed=0, skipped=0, retried_nomax=0, retried_withmax=0):
        """Compare counts with 'subtasks' entry in InstructorTask table."""
        subtask_info = json.loads(entry.subtasks)
        # verify subtask-level counts:
        assert subtask_info.get('total') == 1
        assert subtask_info.get('succeeded') == (1 if (succeeded > 0) else 0)
        assert subtask_info.get('failed') == (0 if (succeeded > 0) else 1)
        # verify individual subtask status:
        subtask_status_info = subtask_info.get('status')
        task_id_list = list(subtask_status_info.keys())
        assert len(task_id_list) == 1
        task_id = task_id_list[0]
        subtask_status = subtask_status_info.get(task_id)
        print(f"Testing subtask status: {subtask_status}")
        assert subtask_status.get('task_id') == task_id
        assert subtask_status.get('attempted') == (succeeded + failed)
        assert subtask_status.get('succeeded') == succeeded
        assert subtask_status.get('skipped') == skipped
        assert subtask_status.get('failed') == failed
        assert subtask_status.get('retried_nomax') == retried_nomax
        assert subtask_status.get('retried_withmax') == retried_withmax
        assert subtask_status.get('state') == (SUCCESS if (succeeded > 0) else FAILURE)

    def _test_run_with_task(
            self, task_class, action_name, total, succeeded,
            failed=0, skipped=0, retried_nomax=0, retried_withmax=0):
        """Run a task and check the number of emails processed."""
        task_entry = self._create_input_entry()
        parent_status = self._run_task_with_mock_celery(task_class, task_entry.id, task_entry.task_id)

        # check return value
        assert parent_status.get('total') == total
        assert parent_status.get('action_name') == action_name

        # compare with task_output entry in InstructorTask table:
        entry = InstructorTask.objects.get(id=task_entry.id)
        status = json.loads(entry.task_output)
        assert status.get('attempted') == (succeeded + failed)
        assert status.get('succeeded') == succeeded
        assert status.get('skipped') == skipped
        assert status.get('failed') == failed
        assert status.get('total') == total
        assert status.get('action_name') == action_name
        assert status.get('duration_ms') > 0
        assert entry.task_state == SUCCESS
        self._assert_single_subtask_status(entry, succeeded, failed, skipped, retried_nomax, retried_withmax)
        return entry

    def test_successful(self):
        # Select number of emails to fit into a single subtask.
        num_emails = settings.BULK_EMAIL_EMAILS_PER_TASK
        # We also send email to the instructor:
        self._create_students(num_emails - 1)
        with patch('lms.djangoapps.bulk_email.tasks.get_connection', autospec=True) as get_conn:
            get_conn.return_value.send_messages.side_effect = cycle([None])
            self._test_run_with_task(send_bulk_course_email, 'emailed', num_emails, num_emails)

    def test_successful_twice(self):
        # Select number of emails to fit into a single subtask.
        num_emails = settings.BULK_EMAIL_EMAILS_PER_TASK
        # We also send email to the instructor:
        self._create_students(num_emails - 1)
        with patch('lms.djangoapps.bulk_email.tasks.get_connection', autospec=True) as get_conn:
            get_conn.return_value.send_messages.side_effect = cycle([None])
            task_entry = self._test_run_with_task(send_bulk_course_email, 'emailed', num_emails, num_emails)

        # submit the same task a second time, and confirm that it is not run again.
        with patch('lms.djangoapps.bulk_email.tasks.get_connection', autospec=True) as get_conn:
            get_conn.return_value.send_messages.side_effect = cycle([Exception("This should not happen!")])
            parent_status = self._run_task_with_mock_celery(send_bulk_course_email, task_entry.id, task_entry.task_id)
        assert parent_status.get('total') == num_emails
        assert parent_status.get('succeeded') == num_emails
        assert parent_status.get('failed') == 0

    def test_unactivated_user(self):
        # Select number of emails to fit into a single subtask.
        num_emails = settings.BULK_EMAIL_EMAILS_PER_TASK
        # We also send email to the instructor:
        students = self._create_students(num_emails - 1)
        # mark a student as not yet having activated their email:
        student = students[0]
        student.is_active = False
        student.save()
        with patch('lms.djangoapps.bulk_email.tasks.get_connection', autospec=True) as get_conn:
            get_conn.return_value.send_messages.side_effect = cycle([None])
            self._test_run_with_task(send_bulk_course_email, 'emailed', num_emails - 1, num_emails - 1)

    def test_skipped(self):
        # Select number of emails to fit into a single subtask.
        num_emails = settings.BULK_EMAIL_EMAILS_PER_TASK
        # We also send email to the instructor:
        students = self._create_students(num_emails - 1)
        # have every fourth student optout:
        expected_skipped = int((num_emails + 3) / 4.0)
        expected_succeeds = num_emails - expected_skipped
        for index in range(0, num_emails, 4):
            Optout.objects.create(user=students[index], course_id=self.course.id)
        # mark some students as opting out
        with patch('lms.djangoapps.bulk_email.tasks.get_connection', autospec=True) as get_conn:
            get_conn.return_value.send_messages.side_effect = cycle([None])
            self._test_run_with_task(
                send_bulk_course_email, 'emailed', num_emails, expected_succeeds, skipped=expected_skipped
            )

    def _test_email_address_failures(self, exception):
        """Test that celery handles bad address errors by failing and not retrying."""
        # Select number of emails to fit into a single subtask.
        num_emails = settings.BULK_EMAIL_EMAILS_PER_TASK
        # We also send email to the instructor:
        self._create_students(num_emails - 1)
        expected_fails = int((num_emails + 3) / 4.0)
        expected_succeeds = num_emails - expected_fails
        with patch('lms.djangoapps.bulk_email.tasks.get_connection', autospec=True) as get_conn:
            # have every fourth email fail due to some address failure:
            get_conn.return_value.send_messages.side_effect = cycle([exception, None, None, None])
            self._test_run_with_task(
                send_bulk_course_email, 'emailed', num_emails, expected_succeeds, failed=expected_fails
            )

    def test_smtp_blacklisted_user(self):
        # Test that celery handles permanent SMTPDataErrors by failing and not retrying.
        self._test_email_address_failures(SMTPDataError(554, "Email address is blacklisted"))

    def test_ses_blacklisted_user(self):
        # Test that celery handles permanent SMTPDataErrors by failing and not retrying.

        operation_name = ''
        parsed_response = {'Error': {'Code': 'MessageRejected', 'Message': 'Error Uploading'}}
        self._test_email_address_failures(ClientError(parsed_response, operation_name))

    def test_ses_illegal_address(self):
        # Test that celery handles permanent SMTPDataErrors by failing and not retrying.
        operation_name = ''
        parsed_response = {'Error': {'Code': 'MailFromDomainNotVerifiedException', 'Message': 'Error Uploading'}}
        self._test_email_address_failures(ClientError(parsed_response, operation_name))

    def test_ses_domain_ends_with_dot(self):
        # Test that celery handles permanent SMTPDataErrors by failing and not retrying.
        operation_name = ''
        parsed_response = {'Error': {'Code': 'MailFromDomainNotVerifiedException', 'Message': 'invalid domain'}}
        self._test_email_address_failures(ClientError(parsed_response, operation_name))

    def test_bulk_email_skip_with_non_ascii_emails(self):
        """
        Tests that bulk email skips the email address containing non-ASCII characters
        and does not fail.
        """
        num_emails = 10
        emails_with_non_ascii_chars = 3
        num_of_course_instructors = 1

        students = [self.create_student('robot%d' % i) for i in range(num_emails)]
        for student in students[:emails_with_non_ascii_chars]:
            student.email = f'{student.username}@tesá.com'
            student.save()

        total = num_emails + num_of_course_instructors
        expected_succeeds = num_emails - emails_with_non_ascii_chars + num_of_course_instructors
        expected_fails = emails_with_non_ascii_chars

        with patch('lms.djangoapps.bulk_email.tasks.get_connection', autospec=True) as get_conn:
            get_conn.return_value.send_messages.side_effect = cycle([None])
            self._test_run_with_task(
                task_class=send_bulk_course_email,
                action_name='emailed',
                total=total,
                succeeded=expected_succeeds,
                failed=expected_fails
            )

    def _test_retry_after_limited_retry_error(self, exception):
        """Test that celery handles connection failures by retrying."""
        # If we want the batch to succeed, we need to send fewer emails
        # than the max retries, so that the max is not triggered.
        num_emails = settings.BULK_EMAIL_MAX_RETRIES
        # We also send email to the instructor:
        self._create_students(num_emails - 1)
        expected_fails = 0
        expected_succeeds = num_emails
        with patch('lms.djangoapps.bulk_email.tasks.get_connection', autospec=True) as get_conn:
            # Have every other mail attempt fail due to disconnection.
            get_conn.return_value.send_messages.side_effect = cycle([exception, None])
            self._test_run_with_task(
                send_bulk_course_email,
                'emailed',
                num_emails,
                expected_succeeds,
                failed=expected_fails,
                retried_withmax=num_emails
            )

    def _test_max_retry_limit_causes_failure(self, exception):
        """Test that celery can hit a maximum number of retries."""
        # Doesn't really matter how many recipients, since we expect
        # to fail on the first.
        num_emails = 10
        # We also send email to the instructor:
        self._create_students(num_emails - 1)
        expected_fails = num_emails
        expected_succeeds = 0
        with patch('lms.djangoapps.bulk_email.tasks.get_connection', autospec=True) as get_conn:
            # always fail to connect, triggering repeated retries until limit is hit:
            get_conn.return_value.send_messages.side_effect = cycle([exception])
            with patch('lms.djangoapps.bulk_email.tasks.update_subtask_status', my_update_subtask_status):
                self._test_run_with_task(
                    send_bulk_course_email,
                    'emailed',
                    num_emails,
                    expected_succeeds,
                    failed=expected_fails,
                    retried_withmax=(settings.BULK_EMAIL_MAX_RETRIES + 1)
                )

    def test_retry_after_smtp_disconnect(self):
        self._test_retry_after_limited_retry_error(SMTPServerDisconnected(425, "Disconnecting"))

    def test_max_retry_after_smtp_disconnect(self):
        self._test_max_retry_limit_causes_failure(SMTPServerDisconnected(425, "Disconnecting"))

    def test_retry_after_smtp_connect_error(self):
        self._test_retry_after_limited_retry_error(SMTPConnectError(424, "Bad Connection"))

    def test_max_retry_after_smtp_connect_error(self):
        self._test_max_retry_limit_causes_failure(SMTPConnectError(424, "Bad Connection"))

    def test_retry_after_aws_connect_error(self):
        self._test_retry_after_limited_retry_error(
            EndpointConnectionError(endpoint_url="Could not connect to the endpoint URL:")
        )

    def test_max_retry_after_aws_connect_error(self):
        self._test_max_retry_limit_causes_failure(
            EndpointConnectionError(endpoint_url="Could not connect to the endpoint URL:")
        )

    def test_retry_after_general_error(self):
        self._test_retry_after_limited_retry_error(Exception("This is some random exception."))

    def test_max_retry_after_general_error(self):
        self._test_max_retry_limit_causes_failure(Exception("This is some random exception."))

    def _test_retry_after_unlimited_retry_error(self, exception):
        """Test that celery handles throttling failures by retrying."""
        num_emails = 8
        # We also send email to the instructor:
        self._create_students(num_emails - 1)
        expected_fails = 0
        expected_succeeds = num_emails
        # Note that because celery in eager mode will call retries synchronously,
        # each retry will increase the stack depth.  It turns out that there is a
        # maximum depth at which a RuntimeError is raised ("maximum recursion depth
        # exceeded").  The maximum recursion depth is 90, so
        # num_emails * expected_retries < 90.
        expected_retries = 10
        with patch('lms.djangoapps.bulk_email.tasks.get_connection', autospec=True) as get_conn:
            # Cycle through N throttling errors followed by a success.
            get_conn.return_value.send_messages.side_effect = cycle(
                chain(repeat(exception, expected_retries), [None])
            )
            self._test_run_with_task(
                send_bulk_course_email,
                'emailed',
                num_emails,
                expected_succeeds,
                failed=expected_fails,
                retried_nomax=(expected_retries * num_emails)
            )

    def test_retry_after_smtp_throttling_error(self):
        self._test_retry_after_unlimited_retry_error(SMTPDataError(455, "Throttling: Sending rate exceeded"))

    def test_retry_after_smtp_sender_refused_error(self):
        self._test_retry_after_unlimited_retry_error(
            SMTPSenderRefused(421, "Throttling: Sending rate exceeded", self.instructor.email)
        )

    def _test_immediate_failure(self, exception):
        """Test that celery can hit a maximum number of retries."""
        # Doesn't really matter how many recipients, since we expect
        # to fail on the first.
        num_emails = 10
        # We also send email to the instructor:
        self._create_students(num_emails - 1)
        expected_fails = num_emails
        expected_succeeds = 0
        with patch('lms.djangoapps.bulk_email.tasks.get_connection', autospec=True) as get_conn:
            # always fail to connect, triggering repeated retries until limit is hit:
            get_conn.return_value.send_messages.side_effect = cycle([exception])
            self._test_run_with_task(
                send_bulk_course_email,
                'emailed',
                num_emails,
                expected_succeeds,
                failed=expected_fails,
            )

    def test_failure_on_unhandled_smtp(self):
        self._test_immediate_failure(SMTPAuthenticationError(403, "That password doesn't work!"))

    def test_bulk_emails_with_unicode_course_image_name(self):
        # Test bulk email with unicode characters in course image name
        course_image = '在淡水測試.jpg'
        self.course = CourseFactory.create(course_image=course_image)

        num_emails = 2
        # We also send email to the instructor:
        self._create_students(num_emails - 1)

        with patch('lms.djangoapps.bulk_email.tasks.get_connection', autospec=True) as get_conn:
            get_conn.return_value.send_messages.side_effect = cycle([None])
            self._test_run_with_task(send_bulk_course_email, 'emailed', num_emails, num_emails)

    def test_get_course_email_context_has_correct_keys(self):
        result = _get_course_email_context(self.course)
        assert 'course_title' in result
        assert 'course_root' in result
        assert 'course_language' in result
        assert 'course_url' in result
        assert 'course_image_url' in result
        assert 'course_end_date' in result
        assert 'account_settings_url' in result
        assert 'email_settings_url' in result
        assert 'platform_name' in result

    def test_account_settings_url_uses_site_config_value(self):
        """
        If site configuration defines ACCOUNT_MICROFRONTEND_URL, the email context
        should use that value.
        """
        siteconf_url = "https://accounts.siteconf.example"

        with patch(
            "lms.djangoapps.bulk_email.tasks.configuration_helpers.get_value",
            side_effect=lambda key, default=None, *a, **k:
                siteconf_url if key == "ACCOUNT_MICROFRONTEND_URL" else default,
        ):
            ctx = _get_course_email_context(self.course)

        assert ctx["account_settings_url"] == siteconf_url

    def test_account_settings_url_falls_back_to_settings(self):
        """
        If site configuration does not override, fall back to settings.ACCOUNT_MICROFRONTEND_URL.
        """
        fallback = "https://accounts.settings.example"

        with override_settings(ACCOUNT_MICROFRONTEND_URL=fallback):
            with patch(
                "lms.djangoapps.bulk_email.tasks.configuration_helpers.get_value",
                side_effect=lambda key, default=None, *a, **k: default,
            ):
                ctx = _get_course_email_context(self.course)

                assert ctx["account_settings_url"] == fallback
                assert ctx["account_settings_url"] == settings.ACCOUNT_MICROFRONTEND_URL

    @override_settings(BULK_COURSE_EMAIL_LAST_LOGIN_ELIGIBILITY_PERIOD=1)
    def test_ineligible_recipients_filtered_by_last_login(self):
        """
        Test that verifies active and enrolled students with last_login dates beyond the set threshold are not sent bulk
        course emails.
        """
        # create students and enrollments for test, then update the last_login dates to fit the scenario under test
        students = self._create_students(2)
        students[0].last_login = datetime.now()
        students[1].last_login = datetime.now() - relativedelta(months=2)

        with patch('lms.djangoapps.bulk_email.tasks.get_connection', autospec=True) as get_conn:
            get_conn.return_value.send_messages.side_effect = cycle([None])
            # we should expect only one email to be sent as the other learner is not eligible to receive the message
            # based on their last_login date
            self._test_run_with_task(send_bulk_course_email, 'emailed', 1, 1)

    def test_email_is_not_sent_to_disabled_user(self):
        """
        Tests if disabled user are skipped when sending bulk email
        """
        user_1 = self.create_student(username="user1", email="user1@example.com")
        user_1.set_unusable_password()
        user_1.save()
        self.create_student(username="user2", email="user2@example.com")
        with patch('lms.djangoapps.bulk_email.tasks.get_connection', autospec=True) as get_conn:
            get_conn.return_value.send_messages.side_effect = cycle([None])
            self._test_run_with_task(send_bulk_course_email, 'emailed', 3, 2, skipped=1)
