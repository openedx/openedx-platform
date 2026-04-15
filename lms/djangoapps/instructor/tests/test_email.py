"""
Unit tests for email feature flag in new instructor dashboard.
Additionally tests that bulk email is always disabled for
non-Mongo backed courses, regardless of email feature flag, and
that the view is conditionally available when Course Auth is turned on.
"""


from django.urls import reverse
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.tests.factories import AdminFactory
from lms.djangoapps.bulk_email.models import BulkEmailFlag, CourseAuthorization, DisabledCourse
from lms.djangoapps.bulk_email.models_api import (
    is_bulk_email_disabled_for_course,
    is_bulk_email_enabled_for_course,
    is_bulk_email_feature_enabled,
)
from xmodule.modulestore.tests.django_utils import (  # lint-amnesty, pylint: disable=wrong-import-order
    TEST_DATA_MIXED_MODULESTORE,
    SharedModuleStoreTestCase,
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class TestNewInstructorDashboardEmailViewMongoBacked(SharedModuleStoreTestCase):
    """
    Check for email view on the new instructor dashboard
    for Mongo-backed courses
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

        # URL for instructor dash
        cls.url = reverse('instructor_dashboard', kwargs={'course_id': str(cls.course.id)})
        # URL for email view
        cls.email_link = '<button type="button" class="btn-link send_email" data-section="send_email">Email</button>'

    def setUp(self):
        super().setUp()

        # Create instructor account
        instructor = AdminFactory.create()
        self.client.login(username=instructor.username, password=self.TEST_PASSWORD)

    def tearDown(self):
        super().tearDown()
        BulkEmailFlag.objects.all().delete()

    # In order for bulk email to work, we must have both the BulkEmailFlag.is_enabled()
    # set to True and for the course to be Mongo-backed.
    # The flag is enabled and the course is Mongo-backed (should work)
    def test_email_flag_true_mongo_true(self):
        BulkEmailFlag.objects.create(enabled=True, require_course_email_auth=False)
        # Assert that instructor email is enabled for this course - since REQUIRE_COURSE_EMAIL_AUTH is False,
        # all courses should be authorized to use email.
        assert is_bulk_email_feature_enabled(self.course.id)
        # Assert that the URL for the email view is in the response
        response = self.client.get(self.url)
        self.assertContains(response, self.email_link)

        send_to_label = '<div class="send_to_list">Send to:</div>'
        self.assertContains(response, send_to_label)
        assert response.status_code == 200

    # The course is Mongo-backed but the flag is disabled (should not work)
    def test_email_flag_false_mongo_true(self):
        BulkEmailFlag.objects.create(enabled=False)
        # Assert that the URL for the email view is not in the response
        response = self.client.get(self.url)
        self.assertNotContains(response, self.email_link)

    # Flag is enabled, but we require course auth and haven't turned it on for this course
    def test_course_not_authorized(self):
        BulkEmailFlag.objects.create(enabled=True, require_course_email_auth=True)
        # Assert that instructor email is not enabled for this course
        assert not is_bulk_email_feature_enabled(self.course.id)
        # Assert that the URL for the email view is not in the response
        response = self.client.get(self.url)
        self.assertNotContains(response, self.email_link)

    # Flag is enabled, we require course auth and turn it on for this course
    def test_course_authorized(self):
        BulkEmailFlag.objects.create(enabled=True, require_course_email_auth=True)
        # Assert that instructor email is not enabled for this course
        assert not is_bulk_email_feature_enabled(self.course.id)
        # Assert that the URL for the email view is not in the response
        response = self.client.get(self.url)
        self.assertNotContains(response, self.email_link)

        # Authorize the course to use email
        cauth = CourseAuthorization(course_id=self.course.id, email_enabled=True)
        cauth.save()

        # Assert that instructor email is enabled for this course
        assert is_bulk_email_feature_enabled(self.course.id)
        # Assert that the URL for the email view is in the response
        response = self.client.get(self.url)
        self.assertContains(response, self.email_link)

    def test_course_authorized_and_on_deny_list(self):
        BulkEmailFlag.objects.create(enabled=True, require_course_email_auth=True)
        # Authorize the course to use email
        cauth = CourseAuthorization(course_id=self.course.id, email_enabled=True)
        cauth.save()
        # Disabled the course to use email
        disabled_course = DisabledCourse(course_id=self.course.id)
        disabled_course.save()
        # Assert that instructor email is disabled for this course
        assert is_bulk_email_disabled_for_course(self.course.id)
        # Assert that the URL for the email view is not in the response
        response = self.client.get(self.url)
        self.assertNotContains(response, self.email_link)

    # Flag is disabled, but course is authorized
    def test_course_authorized_feature_off(self):
        BulkEmailFlag.objects.create(enabled=False, require_course_email_auth=True)
        # Authorize the course to use email
        cauth = CourseAuthorization(course_id=self.course.id, email_enabled=True)
        cauth.save()

        # Assert that this course is authorized for instructor email, but the feature is not enabled
        assert not is_bulk_email_feature_enabled(self.course.id)
        assert is_bulk_email_enabled_for_course(self.course.id)
        # Assert that the URL for the email view IS NOT in the response
        response = self.client.get(self.url)
        self.assertNotContains(response, self.email_link)


class TestNewInstructorDashboardEmailViewXMLBacked(SharedModuleStoreTestCase):
    """
    Check for email view on the new instructor dashboard
    """

    MODULESTORE = TEST_DATA_MIXED_MODULESTORE

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course_key = CourseKey.from_string('edX/toy/2012_Fall')

        # URL for instructor dash
        cls.url = reverse('instructor_dashboard', kwargs={'course_id': str(cls.course_key)})
        # URL for email view
        cls.email_link = '<button type="button" class="btn-link send_email" data-section="send_email">Email</button>'

    def setUp(self):
        super().setUp()

        # Create instructor account
        instructor = AdminFactory.create()
        self.client.login(username=instructor.username, password=self.TEST_PASSWORD)

        # URL for instructor dash
        self.url = reverse('instructor_dashboard', kwargs={'course_id': str(self.course_key)})
        # URL for email view
        self.email_link = '<button type="button" class="btn-link send_email" data-section="send_email">Email</button>'

    def tearDown(self):
        super().tearDown()
        BulkEmailFlag.objects.all().delete()

    # The flag is enabled, and since REQUIRE_COURSE_EMAIL_AUTH is False, all courses should
    # be authorized to use email. But the course is not Mongo-backed (should not work)
    def test_email_flag_true_mongo_false(self):
        BulkEmailFlag.objects.create(enabled=True, require_course_email_auth=False)
        response = self.client.get(self.url)
        self.assertNotContains(response, self.email_link, status_code=404)

    # The flag is disabled and the course is not Mongo-backed (should not work)
    def test_email_flag_false_mongo_false(self):
        BulkEmailFlag.objects.create(enabled=False, require_course_email_auth=False)
        response = self.client.get(self.url)
        self.assertNotContains(response, self.email_link, status_code=404)
