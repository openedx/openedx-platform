"""
Unit tests for email feature flag in student dashboard. Additionally tests
that bulk email is always disabled for non-Mongo backed courses, regardless
of email feature flag, and that the view is conditionally available when
Course Auth is turned on.
"""

from django.urls import reverse

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory

# This import is for an lms djangoapp.
# Its testcases are only run under lms.
from lms.djangoapps.bulk_email.api import is_bulk_email_feature_enabled
from lms.djangoapps.bulk_email.models import BulkEmailFlag, CourseAuthorization
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@skip_unless_lms
class TestStudentDashboardEmailView(SharedModuleStoreTestCase):
    """
    Check for email view displayed with flag
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()

        # Create student account
        student = UserFactory.create()
        CourseEnrollmentFactory.create(user=student, course_id=self.course.id)
        self.client.login(username=student.username, password=self.TEST_PASSWORD)

        self.url = reverse('dashboard')
        # URL for email settings modal
        self.email_modal_link = (
            '<a href="#email-settings-modal" class="action action-email-settings" rel="leanModal" '
            'data-course-id="course-v1:{org}+{num}+{name}" data-course-number="{num}" '
            'data-dashboard-index="0" data-optout="False">Email Settings</a>'
        ).format(
            org=self.course.org,
            num=self.course.number,
            name=self.course.display_name.replace(' ', '_'),
        )

    def tearDown(self):
        super().tearDown()
        BulkEmailFlag.objects.all().delete()

    def test_email_flag_true(self):
        BulkEmailFlag.objects.create(enabled=True, require_course_email_auth=False)
        # Assert that the URL for the email view is in the response
        response = self.client.get(self.url)
        self.assertContains(response, self.email_modal_link)

    def test_email_flag_false(self):
        BulkEmailFlag.objects.create(enabled=False)
        # Assert that the URL for the email view is not in the response
        response = self.client.get(self.url)
        self.assertNotContains(response, self.email_modal_link)

    def test_email_unauthorized(self):
        BulkEmailFlag.objects.create(enabled=True, require_course_email_auth=True)
        # Assert that instructor email is not enabled for this course
        assert not is_bulk_email_feature_enabled(self.course.id)
        # Assert that the URL for the email view is not in the response
        # if this course isn't authorized
        response = self.client.get(self.url)
        self.assertNotContains(response, self.email_modal_link)

    def test_email_authorized(self):
        BulkEmailFlag.objects.create(enabled=True, require_course_email_auth=True)
        # Authorize the course to use email
        cauth = CourseAuthorization(course_id=self.course.id, email_enabled=True)
        cauth.save()
        # Assert that instructor email is enabled for this course
        assert is_bulk_email_feature_enabled(self.course.id)
        # Assert that the URL for the email view is not in the response
        # if this course isn't authorized
        response = self.client.get(self.url)
        self.assertContains(response, self.email_modal_link)
