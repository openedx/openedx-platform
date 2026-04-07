"""
Test utilities for mobile API tests:

  MobileAPITestCase - Common base class with helper methods and common functionality.
     No tests are implemented in this base class.

  Test Mixins to be included by concrete test classes and provide implementation of common test methods:
     MobileAuthTestMixin - tests for APIs with mobile_view and is_user=False.
     MobileAuthUserTestMixin - tests for APIs with mobile_view and is_user=True.
     MobileCourseAccessTestMixin - tests for APIs with mobile_course_access.
"""
# pylint: disable=no-member


import datetime
from unittest.mock import patch

import ddt
import pytz
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from opaque_keys.edx.keys import CourseKey
from rest_framework.test import APITestCase

from common.djangoapps.student import auth
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.access_response import MobileAvailabilityError, StartDateError, VisibilityError
from lms.djangoapps.mobile_api.models import IgnoreMobileAvailableFlagConfig
from lms.djangoapps.mobile_api.tests.test_milestones import MobileAPIMilestonesMixin
from lms.djangoapps.mobile_api.utils import API_V1
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class MobileAPITestCase(ModuleStoreTestCase, APITestCase):
    """
    Base class for testing Mobile APIs.
    Subclasses are expected to define REVERSE_INFO to be used for django reverse URL, of the form:
       REVERSE_INFO = {'name': <django reverse name>, 'params': [<list of params in the URL>]}
    They may also override any of the methods defined in this class to control the behavior of the TestMixins.
    """
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            mobile_available=True,
            static_asset_path="needed_for_split",
            end=datetime.datetime.now(pytz.UTC),
            certificate_available_date=datetime.datetime.now(pytz.UTC)
        )
        self.user = UserFactory.create()
        self.password = self.TEST_PASSWORD
        self.username = self.user.username
        self.api_version = API_V1
        IgnoreMobileAvailableFlagConfig(enabled=False).save()

    def tearDown(self):
        super().tearDown()
        self.logout()

    def login(self):
        """Login test user."""
        self.client.login(username=self.username, password=self.password)

    def logout(self):
        """Logout test user."""
        self.client.logout()

    def enroll(self, course_id=None):
        """Enroll test user in test course."""
        CourseEnrollment.enroll(self.user, course_id or self.course.id)

    def unenroll(self, course_id=None):
        """Unenroll test user in test course."""
        CourseEnrollment.unenroll(self.user, course_id or self.course.id)

    def login_and_enroll(self, course_id=None):
        """Shortcut for both login and enrollment of the user."""
        self.login()
        self.enroll(course_id)

    def api_response(self, reverse_args=None, expected_response_code=200, data=None, **kwargs):
        """
        Helper method for calling endpoint, verifying and returning response.
        If expected_response_code is None, doesn't verify the response' status_code.
        """
        url = self.reverse_url(reverse_args, **kwargs)
        response = self.url_method(url, data=data, **kwargs)
        if expected_response_code is not None:
            assert response.status_code == expected_response_code
        return response

    def reverse_url(self, reverse_args=None, **kwargs):
        """Base implementation that returns URL for endpoint that's being tested."""
        reverse_args = reverse_args or {}
        if 'course_id' in self.REVERSE_INFO['params']:
            reverse_args.update({'course_id': str(kwargs.get('course_id', self.course.id))})
        if 'username' in self.REVERSE_INFO['params']:
            reverse_args.update({'username': kwargs.get('username', self.user.username)})
        if 'api_version' in self.REVERSE_INFO['params']:
            reverse_args.update({'api_version': kwargs.get('api_version', self.api_version)})
        return reverse(self.REVERSE_INFO['name'], kwargs=reverse_args)

    def url_method(self, url, data=None, **kwargs):  # pylint: disable=unused-argument
        """Base implementation that returns response from the GET method of the URL."""
        return self.client.get(url, data=data)


class MobileAuthTestMixin:
    """
    Test Mixin for testing APIs decorated with mobile_view.
    """
    def test_no_auth(self):
        self.logout()
        self.api_response(expected_response_code=401)


class MobileAuthUserTestMixin(MobileAuthTestMixin):
    """
    Test Mixin for testing APIs related to users: mobile_view with is_user=True.
    """
    def test_invalid_user(self):
        self.login_and_enroll()
        self.api_response(expected_response_code=403, username='no_user')

    def test_other_user(self):
        # login and enroll as the test user
        self.login_and_enroll()
        self.logout()

        # login and enroll as another user
        other = UserFactory.create()
        self.client.login(username=other.username, password='test')
        self.enroll()
        self.logout()

        # now login and call the API as the test user
        self.login()
        self.api_response(expected_response_code=403, username=other.username)


@ddt.ddt
class MobileCourseAccessTestMixin(MobileAPIMilestonesMixin):
    """
    Test Mixin for testing APIs marked with mobile_course_access.
    Subclasses are expected to inherit from MobileAPITestCase.
    Subclasses can override verify_success, verify_failure, and init_course_access methods.
    """
    ALLOW_ACCESS_TO_UNRELEASED_COURSE = False
    ALLOW_ACCESS_TO_NON_VISIBLE_COURSE = False

    def verify_success(self, response):
        """Base implementation of verifying a successful response."""
        assert response.status_code == 200

    def verify_failure(self, response, error_type=None):
        """Base implementation of verifying a failed response."""
        assert response.status_code == 404
        if error_type:
            assert response.data == error_type.to_json()

    def init_course_access(self, course_id=None):
        """Base implementation of initializing the user for each test."""
        self.login_and_enroll(course_id)

    @patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': True})
    def test_success(self):
        self.init_course_access()

        response = self.api_response(expected_response_code=None)
        self.verify_success(response)  # allow subclasses to override verification

    def test_course_not_found(self):
        non_existent_course_id = CourseKey.from_string('course-v1:a+b+c')
        self.init_course_access(course_id=non_existent_course_id)

        response = self.api_response(expected_response_code=None, course_id=non_existent_course_id)
        self.verify_failure(response)  # allow subclasses to override verification

    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False, 'ENABLE_MKTG_SITE': True})
    def test_unreleased_course(self):
        # ensure the course always starts in the future
        self.course = CourseFactory.create(mobile_available=True, static_asset_path="needed_for_split")
        self.course.start = timezone.now() + datetime.timedelta(days=365)
        self.init_course_access()
        self._verify_response(self.ALLOW_ACCESS_TO_UNRELEASED_COURSE, StartDateError(self.course.start))

    # A tuple of Role Types and Boolean values that indicate whether access should be given to that role.
    @ddt.data(
        (auth.CourseBetaTesterRole, True),
        (auth.CourseStaffRole, True),
        (auth.CourseInstructorRole, True),
        (None, False)
    )
    @ddt.unpack
    @patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': True})
    def test_non_mobile_available(self, role, should_succeed):
        """
        Tests that the MobileAvailabilityError() is raised for certain user
        roles when trying to access course content. Also verifies that if
        the IgnoreMobileAvailableFlagConfig is enabled,
        MobileAvailabilityError() will not be raised for all user roles.
        """
        self.init_course_access()
        # set mobile_available to False for the test course
        self.course.mobile_available = False
        self.course = self.update_course(self.course, self.user.id)
        self._verify_response(should_succeed, MobileAvailabilityError(), role)

        IgnoreMobileAvailableFlagConfig(enabled=True).save()
        self._verify_response(True, MobileAvailabilityError(), role)

    def test_unenrolled_user(self):
        self.login()
        self.unenroll()
        response = self.api_response(expected_response_code=None)
        self.verify_failure(response)

    @ddt.data(
        (auth.CourseStaffRole, True),
        (None, False)
    )
    @ddt.unpack
    @patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': True})
    def test_visible_to_staff_only_course(self, role, should_succeed):
        self.init_course_access()
        self.course.visible_to_staff_only = True
        self.course = self.update_course(self.course, self.user.id)
        if self.ALLOW_ACCESS_TO_NON_VISIBLE_COURSE:
            should_succeed = True
        self._verify_response(should_succeed, VisibilityError(), role)

    def _verify_response(self, should_succeed, error_type, role=None):
        """
        Calls API and verifies the response
        """
        # set user's role in the course
        if role:
            role(self.course.id).add_users(self.user)

        response = self.api_response(expected_response_code=None)

        if should_succeed:
            self.verify_success(response)
        else:
            self.verify_failure(response, error_type)
