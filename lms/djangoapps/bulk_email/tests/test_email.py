"""
Unit tests for sending course email
"""


import json
import os
from unittest import skipIf
from unittest.mock import Mock, patch

import ddt
from django.conf import settings
from django.core import mail
from django.core.mail.message import forbid_multi_line_headers
from django.core.management import call_command
from django.test.utils import override_settings
from django.urls import reverse
from django.utils.translation import get_language
from edx_toggles.toggles.testutils import override_waffle_flag
from markupsafe import escape

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import (
    CourseEnrollmentFactory,
    InstructorFactory,
    StaffFactory,
    UserFactory,
)
from lms.djangoapps.bulk_email.messages import ACEEmail
from lms.djangoapps.bulk_email.tasks import _get_course_email_context, _get_source_address
from lms.djangoapps.instructor.toggles import LEGACY_INSTRUCTOR_DASHBOARD
from lms.djangoapps.instructor_task.subtasks import update_subtask_status
from openedx.core.djangoapps.course_groups.cohorts import add_user_to_cohort
from openedx.core.djangoapps.course_groups.models import CourseCohort
from openedx.core.djangoapps.enrollments.api import update_enrollment
from xmodule.modulestore import ModuleStoreEnum  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order

from ..models import BulkEmailFlag, Optout

STAFF_COUNT = 3
STUDENT_COUNT = 10
LARGE_NUM_EMAILS = 137


class MockCourseEmailResult:
    """
    A small closure-like class to keep count of emails sent over all tasks, recorded
    by mock object side effects
    """
    emails_sent = 0

    def get_mock_update_subtask_status(self):
        """Wrapper for mock email function."""
        def mock_update_subtask_status(entry_id, current_task_id, new_subtask_status):
            """Increments count of number of emails sent."""
            self.emails_sent += new_subtask_status.succeeded
            return update_subtask_status(entry_id, current_task_id, new_subtask_status)
        return mock_update_subtask_status


# Tests for legacy views. When DEPR-38432 is picked up, these tests will require the following changes:
# Either remove or leave the specific parts that reference the legacy instructor dashboard,
# and remove the override_waffle_flag for LEGACY_INSTRUCTOR_DASHBOARD.
@override_waffle_flag(LEGACY_INSTRUCTOR_DASHBOARD, active=True)
class EmailSendFromDashboardTestCase(SharedModuleStoreTestCase):
    """
    Test that emails send correctly.
    """

    def create_staff_and_instructor(self):
        """
        Creates one instructor and several course staff for self.course. Assigns
        them to self.instructor (single user) and self.staff (list of users),
        respectively.
        """
        self.instructor = InstructorFactory(course_key=self.course.id)

        self.staff = [
            StaffFactory(course_key=self.course.id) for __ in range(STAFF_COUNT)
        ]

    def create_students(self):
        """
        Creates users and enrolls them in self.course. Assigns these users to
        self.students.
        """
        self.students = [UserFactory() for _ in range(STUDENT_COUNT)]
        for student in self.students:
            CourseEnrollmentFactory.create(user=student, course_id=self.course.id)

    def login_as_user(self, user):
        """
        Log in self.client as user.
        """
        self.client.login(username=user.username, password=self.TEST_PASSWORD)

    def goto_instructor_dash_email_view(self):
        """
        Goes to the instructor dashboard to verify that the email section is
        there.
        """
        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.course.id)})
        # Response loads the whole instructor dashboard, so no need to explicitly
        # navigate to a particular email section
        response = self.client.get(url)
        email_section = '<div class="vert-left send-email" id="section-send-email">'
        # If this fails, it is likely because bulk_email.api.is_bulk_email_feature_enabled is set to False
        self.assertContains(response, email_section)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        course_title = "ẗëṡẗ title ｲ乇丂ｲ ﾶ乇丂丂ﾑg乇 ｷo尺 ﾑﾚﾚ тэѕт мэѕѕаБэ"
        cls.course = CourseFactory.create(
            display_name=course_title,
            default_store=ModuleStoreEnum.Type.split
        )

    def setUp(self):
        super().setUp()
        BulkEmailFlag.objects.create(enabled=True, require_course_email_auth=False)
        self.create_staff_and_instructor()
        self.create_students()

        # load initial content (since we don't run migrations as part of tests):
        call_command("loaddata", "course_email_template.json")

        self.login_as_user(self.instructor)

        # Pulling up the instructor dash email view here allows us to test sending emails in tests
        self.goto_instructor_dash_email_view()
        self.send_mail_url = reverse(
            'send_email', kwargs={'course_id': str(self.course.id)}
        )
        self.success_content = {
            'course_id': str(self.course.id),
            'success': True,
        }

    def tearDown(self):
        super().tearDown()
        BulkEmailFlag.objects.all().delete()


class SendEmailWithMockedUgettextMixin:
    """
    Mock uggetext for EmailSendFromDashboardTestCase.
    """
    def send_email(self):
        """
        Sends a dummy email to check the `from_addr` translation.
        """
        test_email = {
            'action': 'send',
            'send_to': '["myself"]',
            'subject': 'test subject for myself',
            'message': 'test message for myself'
        }

        def mock_ugettext(text):
            """
            Mocks ugettext to return the lang code with the original string.

            e.g.

            >>> mock_ugettext('Hello') == 'AR Hello'
            """
            return '{lang} {text}'.format(  # noqa: UP032
                lang=get_language().upper(),
                text=text,
            )

        with patch('lms.djangoapps.bulk_email.tasks._', side_effect=mock_ugettext):
            self.client.post(self.send_mail_url, test_email)

        return mail.outbox[0]


@patch.dict(settings.FEATURES, {'ENABLE_INSTRUCTOR_EMAIL': True, 'REQUIRE_COURSE_EMAIL_AUTH': False})
@ddt.ddt
class LocalizedFromAddressPlatformLangTestCase(SendEmailWithMockedUgettextMixin, EmailSendFromDashboardTestCase):
    """
    Tests to ensure that the bulk email has the "From" address localized according to LANGUAGE_CODE.
    """
    @ddt.data(
        ('en', True, False),
        ('eo', True, False),
        ('en', True, True),
        ('eo', True, True),
    )
    @ddt.unpack
    def test_english_platform(self, language_code, enable_use_corse_id_in_from, ace_enabled):
        """
        Ensures that the source-code language (English) works well.
        """
        assert self.course.language is None
        # Sanity check
        with override_settings(
            LANGUAGE_CODE=language_code,
            EMAIL_USE_COURSE_ID_FROM_FOR_BULK=enable_use_corse_id_in_from,
            BULK_EMAIL_SEND_USING_EDX_ACE=ace_enabled
        ):
            message = self.send_email()
            self.assertRegex(message.from_email, f'{language_code.upper()} .* Course Staff')  # noqa: PT009


@patch.dict(settings.FEATURES, {'ENABLE_INSTRUCTOR_EMAIL': True, 'REQUIRE_COURSE_EMAIL_AUTH': False})
@ddt.ddt
class AceEmailTestCase(SendEmailWithMockedUgettextMixin, EmailSendFromDashboardTestCase):
    """
    Tests to ensure that the bulk email is sent using edx-ace when BULK_EMAIL_SEND_USING_EDX_ACE toggle is enabled.
    """
    @ddt.data(
        (True, True),
        (False, False),
    )
    @ddt.unpack
    @patch.object(ACEEmail, 'send')
    def test_ace_eanbled_toggle(self, ace_enabled, email_sent_with_ace, mock_ace_email_send):
        """
        Ensures that the email message is sent via edx-ace when BULK_EMAIL_SEND_USING_EDX_ACE toggle is enabled.
        """
        mock_ace_email_send.return_value = None
        test_email = {
            'action': 'Send email',
            'send_to': '["myself"]',
            'subject': 'test subject for myself',
            'message': 'test message for myself'
        }

        with override_settings(
            BULK_EMAIL_SEND_USING_EDX_ACE=ace_enabled
        ):
            response = self.client.post(self.send_mail_url, test_email)  # noqa: F841
            self.assertEqual(email_sent_with_ace, mock_ace_email_send.called)  # noqa: PT009

    def test_keyword_substitution_in_message_body(self):
        """
        Make sure keywords like `%%USER_FULLNAME%%`, `%%COURSE_DISPLAY_NAME%%` are substituted in message body
        """
        test_email = {
            'action': 'Send email',
            'send_to': '["staff"]',
            'subject': 'test subject for staff',
            'message': 'Hi %%USER_FULLNAME%%, Welcome to %%COURSE_DISPLAY_NAME%% course'
        }
        self.client.post(self.send_mail_url, test_email)
        text_message_body = mail.outbox[0].body
        html_message_body = ''
        for content, mimetype in mail.outbox[0].alternatives:
            if mimetype == 'text/html':
                html_message_body = content
                break

        self.assertNotIn('%%USER_FULLNAME%%', text_message_body)  # noqa: PT009
        self.assertNotIn('%%COURSE_DISPLAY_NAME%%', text_message_body)  # noqa: PT009
        self.assertNotIn('%%USER_FULLNAME%%', html_message_body)  # noqa: PT009
        self.assertNotIn('%%COURSE_DISPLAY_NAME%%', html_message_body)  # noqa: PT009
        self.assertIn(f'Hi {self.instructor.get_full_name()}', text_message_body)  # noqa: PT009
        self.assertIn(f'Welcome to {self.course.display_name}', text_message_body)  # noqa: PT009
        self.assertIn(f'Hi {self.instructor.get_full_name()}', html_message_body)  # noqa: PT009
        self.assertIn(f'Welcome to {self.course.display_name}', html_message_body)  # noqa: PT009


@patch.dict(settings.FEATURES, {'ENABLE_INSTRUCTOR_EMAIL': True, 'REQUIRE_COURSE_EMAIL_AUTH': False})
@ddt.ddt
class LocalizedFromAddressCourseLangTestCase(SendEmailWithMockedUgettextMixin, EmailSendFromDashboardTestCase):
    """
    Test if the bulk email "From" address uses the course.language if present instead of LANGUAGE_CODE.

    This is similiar to LocalizedFromAddressTestCase but creating a different test case to allow
    changing the class-wide course object.
    """

    @classmethod
    def setUpClass(cls):
        """
        Creates a different course.
        """
        super().setUpClass()
        course_title = "ẗëṡẗ ｲэ"
        cls.course = CourseFactory.create(
            display_name=course_title,
            language='ar',
            default_store=ModuleStoreEnum.Type.split
        )

    @override_settings(LANGUAGE_CODE='eo', EMAIL_USE_COURSE_ID_FROM_FOR_BULK=True)
    def test_esperanto_platform_arabic_course(self):
        """
        The course language should override the platform's.
        """
        message = self.send_email()
        self.assertRegex(message.from_email, 'AR .* Course Staff')  # noqa: PT009


@patch('lms.djangoapps.bulk_email.models.html_to_text', Mock(return_value='Mocking CourseEmail.text_message', autospec=True))  # pylint: disable=line-too-long
class TestEmailSendFromDashboardMockedHtmlToText(EmailSendFromDashboardTestCase):
    """
    Tests email sending with mocked html_to_text.
    """
    def test_email_disabled(self):
        """
        Test response when email is disabled for course.
        """
        BulkEmailFlag.objects.create(enabled=True, require_course_email_auth=True)
        test_email = {
            'action': 'Send email',
            'send_to': '["myself"]',
            'subject': 'test subject for myself',
            'message': 'test message for myself'
        }
        response = self.client.post(self.send_mail_url, test_email)
        # We should get back a HttpResponseForbidden (status code 403)
        self.assertContains(response, "Email is not enabled for this course.", status_code=403)

    @override_settings(EMAIL_USE_COURSE_ID_FROM_FOR_BULK=True)
    @patch('lms.djangoapps.bulk_email.models.html_to_text', Mock(return_value='Mocking CourseEmail.text_message', autospec=True))  # pylint: disable=line-too-long
    def test_send_to_self(self):
        """
        Make sure email send to myself goes to myself.
        """
        test_email = {
            'action': 'send',
            'send_to': '["myself"]',
            'subject': 'test subject for myself',
            'message': 'test message for myself'
        }
        response = self.client.post(self.send_mail_url, test_email)
        assert json.loads(response.content.decode('utf-8')) == self.success_content

        # Check that outbox is as expected
        assert len(mail.outbox) == 1
        assert len(mail.outbox[0].to) == 1
        assert mail.outbox[0].to[0] == self.instructor.email
        assert mail.outbox[0].subject == 'test subject for myself'
        assert mail.outbox[0].from_email == \
               f'"{self.course.display_name}" Course Staff <{self.course.id.course}-no-reply@example.com>'

    def test_send_to_staff(self):
        """
        Make sure email send to staff and instructors goes there.
        """
        test_email = {
            'action': 'Send email',
            'send_to': '["staff"]',
            'subject': 'test subject for staff',
            'message': 'test message for subject'
        }
        response = self.client.post(self.send_mail_url, test_email)
        assert json.loads(response.content.decode('utf-8')) == self.success_content

        # the 1 is for the instructor in this test and others
        assert len(mail.outbox) == (1 + len(self.staff))
        assert len([e.to[0] for e in mail.outbox]) == len([self.instructor.email] + [s.email for s in self.staff])

    @override_settings(DEFAULT_FROM_EMAIL='test@example.com', BULK_EMAIL_DEFAULT_FROM_EMAIL=None, EMAIL_USE_COURSE_ID_FROM_FOR_BULK=False)  # pylint: disable=line-too-long
    def test_email_from_address(self):
        """
        Make sure the from_address should be the DEFAULT_FROM_EMAIL when corresponding flag is enabled.
        """
        test_email = {
            'action': 'Send email',
            'send_to': '["staff"]',
            'subject': 'test subject for staff',
            'message': 'test message for subject'
        }
        self.client.post(self.send_mail_url, test_email)
        from_email = mail.outbox[0].from_email
        self.assertEqual(  # noqa: PT009
            from_email,
            'test@example.com'
        )

    def test_send_to_cohort(self):
        """
        Make sure email sent to a cohort goes there.
        """
        cohort = CourseCohort.create(cohort_name='test cohort', course_id=self.course.id)
        for student in self.students:
            add_user_to_cohort(cohort.course_user_group, student.username)
        test_email = {
            'action': 'Send email',
            'send_to': f'["cohort:{cohort.course_user_group.name}"]',
            'subject': 'test subject for cohort',
            'message': 'test message for cohort'
        }
        response = self.client.post(self.send_mail_url, test_email)
        assert json.loads(response.content.decode('utf-8')) == self.success_content

        assert len([e.to[0] for e in mail.outbox]) == len([s.email for s in self.students])

    def test_send_to_cohort_unenrolled(self):
        """
        Make sure email sent to a cohort does not go to unenrolled members of the cohort.
        """
        self.students.append(UserFactory())  # user will be added to cohort, but not enrolled in course
        cohort = CourseCohort.create(cohort_name='test cohort', course_id=self.course.id)
        for student in self.students:
            add_user_to_cohort(cohort.course_user_group, student.username)
        test_email = {
            'action': 'Send email',
            'send_to': f'["cohort:{cohort.course_user_group.name}"]',
            'subject': 'test subject for cohort',
            'message': 'test message for cohort'
        }
        response = self.client.post(self.send_mail_url, test_email)
        assert json.loads(response.content.decode('utf-8')) == self.success_content

        assert len(mail.outbox) == (len(self.students) - 1)
        assert self.students[(- 1)].email not in [e.to[0] for e in mail.outbox]

    def test_send_to_track(self):
        """
        Make sure email sent to a registration track goes there.
        """
        CourseMode.objects.create(mode_slug='test', course_id=self.course.id)
        for student in self.students:
            update_enrollment(student, str(self.course.id), 'test')
        test_email = {
            'action': 'Send email',
            'send_to': '["track:test"]',
            'subject': 'test subject for test track',
            'message': 'test message for test track',
        }
        response = self.client.post(self.send_mail_url, test_email)
        assert json.loads(response.content.decode('utf-8')) == self.success_content

        assert len([e.to[0] for e in mail.outbox]) == len([s.email for s in self.students])

    def test_send_to_track_other_enrollments(self):
        """
        Failing test for EDUCATOR-217: verifies that emails are only sent to
        users in a specific track if they're in that track in the course the
        email is being sent from.
        """
        # Create a mode and designate an enrolled user to be placed in that mode
        CourseMode.objects.create(mode_slug='test_mode', course_id=self.course.id)
        test_mode_student = self.students[0]
        update_enrollment(test_mode_student, str(self.course.id), 'test_mode')

        # Take another user already enrolled in the course, then enroll them in
        # another course but in that same test mode
        test_mode_student_other_course = self.students[1]
        other_course = CourseFactory.create()
        CourseMode.objects.create(mode_slug='test_mode', course_id=other_course.id)
        CourseEnrollmentFactory.create(
            user=test_mode_student_other_course,
            course_id=other_course.id
        )
        update_enrollment(test_mode_student_other_course, str(other_course.id), 'test_mode')

        # Send the emails...
        test_email = {
            'action': 'Send email',
            'send_to': '["track:test_mode"]',
            'subject': 'test subject for test_mode track',
            'message': 'test message for test_mode track',
        }
        response = self.client.post(self.send_mail_url, test_email)
        assert json.loads(response.content.decode('utf-8')) == self.success_content

        # Only the the student in the test mode in the course the email was
        # sent from should receive an email
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to[0] == test_mode_student.email

    def test_send_to_all(self):
        """
        Make sure email send to all goes there.
        """

        test_email = {
            'action': 'Send email',
            'send_to': '["myself", "staff", "learners"]',
            'subject': 'test subject for all',
            'message': 'test message for all'
        }
        response = self.client.post(self.send_mail_url, test_email)
        assert json.loads(response.content.decode('utf-8')) == self.success_content

        # the 1 is for the instructor
        assert len(mail.outbox) == ((1 + len(self.staff)) + len(self.students))
        assert len([e.to[0] for e in mail.outbox]) == \
               len([self.instructor.email] + [s.email for s in self.staff] + [s.email for s in self.students])

    @override_settings(BULK_EMAIL_JOB_SIZE_THRESHOLD=1)
    def test_send_to_all_high_queue(self):
        """
        Test that email is still sent when the high priority queue is used
        """
        self.test_send_to_all()

    def test_no_duplicate_emails_staff_instructor(self):
        """
        Test that no duplicate emails are sent to a course instructor that is
        also course staff
        """
        CourseStaffRole(self.course.id).add_users(self.instructor)
        self.test_send_to_all()

    def test_no_duplicate_emails_enrolled_staff(self):
        """
        Test that no duplicate emails are sent to a course instructor that is
        also enrolled in the course
        """
        CourseEnrollment.enroll(self.instructor, self.course.id)
        self.test_send_to_all()

    def test_no_duplicate_emails_unenrolled_staff(self):
        """
        Test that no duplicate emails are sent to a course staff that is
        not enrolled in the course, but is enrolled in other courses
        """
        course_1 = CourseFactory.create()
        course_2 = CourseFactory.create()
        # make sure self.instructor isn't enrolled in the course
        assert not CourseEnrollment.is_enrolled(self.instructor, self.course.id)
        CourseEnrollment.enroll(self.instructor, course_1.id)
        CourseEnrollment.enroll(self.instructor, course_2.id)
        self.test_send_to_all()

    def test_unicode_subject_send_to_all(self):
        """
        Make sure email (with Unicode characters) send to all goes there.
        """

        uni_subject = 'téśt śúbjéćt főŕ áĺĺ'
        test_email = {
            'action': 'Send email',
            'send_to': '["myself", "staff", "learners"]',
            'subject': uni_subject,
            'message': 'test message for all'
        }
        response = self.client.post(self.send_mail_url, test_email)
        assert json.loads(response.content.decode('utf-8')) == self.success_content

        assert len(mail.outbox) == ((1 + len(self.staff)) + len(self.students))
        assert len([e.to[0] for e in mail.outbox]) ==\
               len([self.instructor.email] + [s.email for s in self.staff] + [s.email for s in self.students])
        assert mail.outbox[0].subject == uni_subject

    def test_unicode_students_send_to_all(self):
        """
        Make sure email (with Unicode characters) send to all goes there.
        """

        # Create a student with Unicode in their first & last names
        unicode_user = UserFactory(first_name='Ⓡⓞⓑⓞⓣ', last_name='ՇﻉรՇ')
        CourseEnrollmentFactory.create(user=unicode_user, course_id=self.course.id)
        self.students.append(unicode_user)

        test_email = {
            'action': 'Send email',
            'send_to': '["myself", "staff", "learners"]',
            'subject': 'test subject for all',
            'message': 'test message for all'
        }
        response = self.client.post(self.send_mail_url, test_email)
        assert json.loads(response.content.decode('utf-8')) == self.success_content

        assert len(mail.outbox) == ((1 + len(self.staff)) + len(self.students))

        assert len([e.to[0] for e in mail.outbox]) ==\
               len([self.instructor.email] + [s.email for s in self.staff] + [s.email for s in self.students])

    @override_settings(BULK_EMAIL_DEFAULT_FROM_EMAIL="no-reply@courseupdates.edx.org", EMAIL_USE_COURSE_ID_FROM_FOR_BULK=True)  # pylint: disable=line-too-long
    def test_long_course_display_name(self):
        """
        This test tests that courses with exorbitantly large display names
        can still send emails, since it appears that 320 appears to be the
        character length limit of from emails for Amazon SES.
        """
        test_email = {
            'action': 'Send email',
            'send_to': '["myself", "staff", "learners"]',
            'subject': 'test subject for self',
            'message': 'test message for self'
        }

        # make display_name that's longer than 320 characters when encoded
        # to ascii and escaped, but shorter than 320 unicode characters
        long_name = "Финансовое программирование и политика, часть 1: макроэкономические счета и анализ"

        course = CourseFactory.create(
            display_name=long_name,
            org="IMF",
            number="FPP.1x",
            run="2016",
        )
        instructor = InstructorFactory(course_key=course.id)

        unexpected_from_addr = _get_source_address(
            course.id, course.display_name, course_language=None, truncate=False
        )
        __, encoded_unexpected_from_addr = forbid_multi_line_headers(
            "from", unexpected_from_addr, 'utf-8'
        )
        escaped_encoded_unexpected_from_addr = escape(encoded_unexpected_from_addr)

        # it's shorter than 320 characters when just encoded
        assert len(encoded_unexpected_from_addr) == 318
        # escaping it brings it over that limit
        assert len(escaped_encoded_unexpected_from_addr) == 324
        # when not escaped or encoded, it's well below 320 characters
        assert len(unexpected_from_addr) == 137

        self.login_as_user(instructor)
        send_mail_url = reverse('send_email', kwargs={'course_id': str(course.id)})
        response = self.client.post(send_mail_url, test_email)
        assert json.loads(response.content.decode('utf-8'))['success']

        assert len(mail.outbox) == 1
        from_email = mail.outbox[0].from_email

        expected_from_addr = (  # noqa: UP032
            '"{course_name}" Course Staff <{course_name}-no-reply@courseupdates.edx.org>'
        ).format(course_name=course.id.course)

        assert from_email == expected_from_addr
        assert len(from_email) == 61

    @override_settings(BULK_EMAIL_EMAILS_PER_TASK=3)
    @patch('lms.djangoapps.bulk_email.tasks.update_subtask_status')
    def test_chunked_queries_send_numerous_emails(self, email_mock):
        """
        Test sending a large number of emails, to test the chunked querying
        """
        mock_factory = MockCourseEmailResult()
        email_mock.side_effect = mock_factory.get_mock_update_subtask_status()
        added_users = []
        for _ in range(LARGE_NUM_EMAILS):
            user = UserFactory()
            added_users.append(user)
            CourseEnrollmentFactory.create(user=user, course_id=self.course.id)

        optouts = []
        for i in [1, 3, 9, 10, 18]:  # 5 random optouts
            user = added_users[i]
            optouts.append(user)
            optout = Optout(user=user, course_id=self.course.id)
            optout.save()

        test_email = {
            'action': 'Send email',
            'send_to': '["myself", "staff", "learners"]',
            'subject': 'test subject for all',
            'message': 'test message for all'
        }
        response = self.client.post(self.send_mail_url, test_email)
        assert json.loads(response.content.decode('utf-8')) == self.success_content

        assert mock_factory.emails_sent == \
               ((((1 + len(self.staff)) + len(self.students)) + LARGE_NUM_EMAILS) - len(optouts))
        outbox_contents = [e.to[0] for e in mail.outbox]
        should_send_contents = ([self.instructor.email] +
                                [s.email for s in self.staff] +
                                [s.email for s in self.students] +
                                [s.email for s in added_users if s not in optouts])
        assert len(outbox_contents) == len(should_send_contents)

    def test_unsubscribe_link_in_email(self):
        """
        Make sure opt out link is present in email.
        """

        test_email = {
            'action': 'Send email',
            'send_to': '["learners"]',
            'subject': 'Checking unsubscribe link in email',
            'message': 'test message for all'
        }
        response = self.client.post(self.send_mail_url, test_email)
        assert json.loads(response.content.decode('utf-8')) == self.success_content

        # check unsubscribe link in template
        for m in mail.outbox:
            plain_template = m.body
            html_template = m.alternatives[0][0]

            assert 'bulk_email/email/optout/' in plain_template
            assert 'bulk_email/email/optout/' in html_template


@skipIf(os.environ.get("TRAVIS") == 'true', "Skip this test in Travis CI.")
class TestEmailSendFromDashboard(EmailSendFromDashboardTestCase):
    """
    Tests email sending without mocked html_to_text.

    Note that these tests are skipped on Travis because we can't use the
    function `html_to_text` as it is currently implemented on Travis.
    """

    def test_unicode_message_send_to_all(self):
        """
        Make sure email (with Unicode characters) send to all goes there.
        """

        uni_message = 'ẗëṡẗ ṁëṡṡäġë ḟöṛ äḷḷ ｲ乇丂ｲ ﾶ乇丂丂ﾑg乇 ｷo尺 ﾑﾚﾚ тэѕт мэѕѕаБэ fоѓ аll'
        test_email = {
            'action': 'Send email',
            'send_to': '["myself", "staff", "learners"]',
            'subject': 'test subject for all',
            'message': uni_message
        }
        response = self.client.post(self.send_mail_url, test_email)
        assert json.loads(response.content.decode('utf-8')) == self.success_content

        assert len(mail.outbox) == ((1 + len(self.staff)) + len(self.students))
        assert len([e.to[0] for e in mail.outbox]) ==\
               len([self.instructor.email] + [s.email for s in self.staff] + [s.email for s in self.students])

        message_body = mail.outbox[0].body
        assert uni_message in message_body


class TestCourseEmailContext(SharedModuleStoreTestCase):
    """
    Test the course email context hash used to send bulk emails.
    """

    @classmethod
    def setUpClass(cls):
        """
        Create a course shared by all tests.
        """
        super().setUpClass()
        cls.course_title = "Финансовое программирование и политика, часть 1: макроэкономические счета и анализ"
        cls.course_org = 'IMF'
        cls.course_number = "FPP.1x"
        cls.course_run = "2016"
        cls.course = CourseFactory.create(
            display_name=cls.course_title,
            org=cls.course_org,
            number=cls.course_number,
            run=cls.course_run,
        )

    def verify_email_context(self, email_context, scheme):
        """
        This test tests that the bulk email context uses http or https urls as appropriate.
        """
        course_id_fragment = f'{self.course_org}+{self.course_number}+{self.course_run}'
        assert email_context['platform_name'] == settings.PLATFORM_NAME
        assert email_context['course_title'] == self.course_title
        assert email_context['course_url'] == \
               f'{scheme}://edx.org/courses/course-v1:{course_id_fragment}/'
        assert email_context['course_image_url'] == \
               f'{scheme}://edx.org/asset-v1:{course_id_fragment}+type@asset+block@images_course_image.jpg'
        assert email_context['email_settings_url'] == f'{scheme}://edx.org/dashboard'
        assert email_context['account_settings_url'] == settings.ACCOUNT_MICROFRONTEND_URL

    @override_settings(LMS_ROOT_URL="http://edx.org")
    def test_insecure_email_context(self):
        """
        This test tests that the bulk email context uses http urls
        """
        email_context = _get_course_email_context(self.course)
        self.verify_email_context(email_context, 'http')

    @override_settings(LMS_ROOT_URL="https://edx.org")
    def test_secure_email_context(self):
        """
        This test tests that the bulk email context uses https urls
        """
        email_context = _get_course_email_context(self.course)
        self.verify_email_context(email_context, 'https')
