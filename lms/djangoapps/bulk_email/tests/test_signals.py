"""
Unit tests for student optouts from course email
"""


import json
from unittest.mock import Mock, patch

from django.core import mail
from django.core.management import call_command
from django.urls import reverse

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import AdminFactory, CourseEnrollmentFactory, UserFactory
from lms.djangoapps.bulk_email.models import BulkEmailFlag, Optout
from lms.djangoapps.bulk_email.signals import force_optout_all
from opaque_keys.edx.keys import CourseKey
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@patch('lms.djangoapps.bulk_email.models.html_to_text', Mock(return_value='Mocking CourseEmail.text_message', autospec=True))  # lint-amnesty, pylint: disable=line-too-long
class TestOptoutCourseEmailsBySignal(ModuleStoreTestCase):
    """
    Tests that the force_optout_all signal receiver opts the user out of course emails
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(run='testcourse1', display_name="Test Course Title")
        self.instructor = AdminFactory.create()
        self.student = UserFactory.create()
        self.enrollment = CourseEnrollmentFactory.create(user=self.student, course_id=self.course.id)

        # load initial content (since we don't run migrations as part of tests):
        call_command("loaddata", "course_email_template.json")

        self.client.login(username=self.student.username, password=self.TEST_PASSWORD)

        self.send_mail_url = reverse('send_email', kwargs={'course_id': str(self.course.id)})
        self.success_content = {
            'course_id': str(self.course.id),
            'success': True,
        }
        BulkEmailFlag.objects.create(enabled=True, require_course_email_auth=False)

    def test_optout_row_created_on_signal(self):
        """
        Make sure the correct row is created for a user enrolled in a course
        """
        force_optout_all(sender=self.__class__, user=self.student)
        assert Optout.objects.filter(user=self.student, course_id=self.course.id).count() == 1

    def send_test_email(self):
        """
        Navigate to the instructor dash's email view to send bulk email
        """
        # Pull up email view on instructor dashboard
        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.course.id)})
        response = self.client.get(url)
        email_section = '<div class="vert-left send-email" id="section-send-email">'

        # If this fails, it is likely because BulkEmailFlag.is_enabled() is set to False
        self.assertContains(response, email_section)

        test_email = {
            'action': 'Send email',
            'send_to': '["myself", "staff", "learners"]',
            'subject': 'test subject for all',
            'message': 'test message for all'
        }
        response = self.client.post(self.send_mail_url, test_email)
        assert json.loads(response.content.decode('utf-8')) == self.success_content

    def test_optout_course(self):
        """
        Make sure student does not receive course email after being opted out
        """
        # Use the signal receiver to for the opt-out
        force_optout_all(sender=self.__class__, user=self.student)

        # Try to send a bulk course email
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)
        self.send_test_email()

        # Assert that self.student.email not in mail.to, outbox should only contain "myself" target
        assert len(mail.outbox) == 1
        assert len(mail.outbox[0].to) == 1
        assert mail.outbox[0].to[0] == self.instructor.email

    @patch('lms.djangoapps.bulk_email.signals.log.warning')
    def test_optout_handles_missing_course_overview(self, mock_log_warning):
        """
        Test that force_optout_all gracefully handles CourseEnrollments
        with missing CourseOverview records
        """
        # Create a course key for a course that doesn't exist in CourseOverview
        nonexistent_course_key = CourseKey.from_string('course-v1:TestX+Missing+2023')

        # Create an enrollment with a course_id that doesn't have a CourseOverview
        CourseEnrollment.objects.create(
            user=self.student,
            course_id=nonexistent_course_key,
            mode='honor'
        )

        # Verify the orphaned enrollment exists
        assert CourseEnrollment.objects.filter(
            user=self.student,
            course_id=nonexistent_course_key
        ).exists()

        force_optout_all(sender=self.__class__, user=self.student)

        # Verify that a warning was logged for the missing CourseOverview
        mock_log_warning.assert_called()
        call_args = mock_log_warning.call_args[0][0]
        assert "CourseOverview not found for enrollment" in call_args
        assert f"user: {self.student.id}" in call_args
        assert "skipping optout creation" in call_args

        # Verify that optouts were created for valid courses only
        valid_course_optouts = Optout.objects.filter(user=self.student, course_id=self.course.id)
        missing_course_optouts = Optout.objects.filter(user=self.student, course_id=nonexistent_course_key)

        assert valid_course_optouts.count() == 1
        assert missing_course_optouts.count() == 0
