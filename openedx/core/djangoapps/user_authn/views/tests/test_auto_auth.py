""" Tests for auto auth. """


import json
from unittest.mock import Mock, patch

import ddt
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.test import TestCase
from django.test.client import Client
from opaque_keys.edx.locator import CourseLocator

from common.djangoapps.student.models import CourseAccessRole, CourseEnrollment, UserProfile, anonymous_id_for_user
from common.djangoapps.util.testing import UrlResetMixin
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_MODERATOR,
    FORUM_ROLE_STUDENT,
    Role
)
from openedx.core.djangoapps.django_comment_common.utils import seed_permissions_roles
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from common.test.utils import assert_dict_contains_subset


class AutoAuthTestCase(UrlResetMixin, TestCase):
    """
    Base class for AutoAuth Tests that properly resets the urls.py
    """
    URLCONF_MODULES = ['openedx.core.djangoapps.user_authn.urls_common', 'openedx.core.djangoapps.user_authn.urls']


@ddt.ddt
class AutoAuthEnabledTestCase(AutoAuthTestCase, ModuleStoreTestCase):
    """
    Tests for the Auto auth view that we have for load testing.
    """
    COURSE_ID_MONGO = 'edX/Test101/2014_Spring'
    COURSE_ID_SPLIT = 'course-v1:edX+Test101+2014_Spring'
    COURSE_IDS_DDT = (
        (COURSE_ID_MONGO, CourseLocator.from_string(COURSE_ID_MONGO)),
        (COURSE_ID_SPLIT, CourseLocator.from_string(COURSE_ID_SPLIT)),
    )

    @patch.dict("django.conf.settings.FEATURES", {"AUTOMATIC_AUTH_FOR_TESTING": True})
    def setUp(self):
        # Patching the settings.FEATURES['AUTOMATIC_AUTH_FOR_TESTING']
        # value affects the contents of urls.py,
        # so we need to call super.setUp() which reloads urls.py (because
        # of the UrlResetMixin)

        self.CREATE_USER = False  # no need to add a user from modulestore setup
        super().setUp()
        self.url = '/auto_auth'
        self.client = Client()

    def test_create_user(self):
        """
        Test that user gets created when visiting the page.
        """
        self._auto_auth()
        assert User.objects.count() == 1
        user = User.objects.all()[0]
        assert user.is_active
        assert not user.profile.requires_parental_consent()

    @patch.dict("django.conf.settings.FEATURES", {'RESTRICT_AUTOMATIC_AUTH': False})
    def test_create_same_user(self):
        self._auto_auth({'username': 'test'})
        self._auto_auth({'username': 'test'})
        assert User.objects.count() == 1

    def test_create_multiple_users(self):
        """
        Test to make sure multiple users are created.
        """
        self._auto_auth()
        self.client.logout()
        self._auto_auth()
        assert User.objects.all().count() == 2

    def test_create_defined_user(self):
        """
        Test that the user gets created with the correct attributes
        when they are passed as parameters on the auto-auth page.
        """
        self._auto_auth({
            'username': 'robot', 'password': 'test',
            'email': 'robot@edx.org', 'full_name': "Robot Name"
        })

        # Check that the user has the correct info
        user = User.objects.get(username='robot')
        assert user.username == 'robot'
        assert user.check_password('test')
        assert user.email == 'robot@edx.org'

        # Check that the user has a profile
        user_profile = UserProfile.objects.get(user=user)
        assert user_profile.name == 'Robot Name'

        # By default, the user should not be global staff
        assert not user.is_staff

    @patch.dict("django.conf.settings.FEATURES", {'RESTRICT_AUTOMATIC_AUTH': False})
    def test_create_staff_user(self):

        # Create a staff user
        self._auto_auth({'username': 'test', 'staff': 'true'})
        user = User.objects.get(username='test')
        assert user.is_staff

        # Revoke staff privileges
        self._auto_auth({'username': 'test', 'staff': 'false'})
        user = User.objects.get(username='test')
        assert not user.is_staff

    @ddt.data(*COURSE_IDS_DDT)
    @ddt.unpack
    def test_course_enrollment(self, course_id, course_key):

        # Create a user and enroll in a course
        self._auto_auth({'username': 'test', 'course_id': course_id})

        # Check that a course enrollment was created for the user
        assert CourseEnrollment.objects.count() == 1
        enrollment = CourseEnrollment.objects.get(course_id=course_key)
        assert enrollment.user.username == 'test'

    @ddt.data(*COURSE_IDS_DDT)
    @ddt.unpack
    @patch.dict("django.conf.settings.FEATURES", {'RESTRICT_AUTOMATIC_AUTH': False})
    def test_double_enrollment(self, course_id, course_key):

        # Create a user and enroll in a course
        self._auto_auth({'username': 'test', 'course_id': course_id})

        # Make the same call again, re-enrolling the student in the same course
        self._auto_auth({'username': 'test', 'course_id': course_id})

        # Check that only one course enrollment was created for the user
        assert CourseEnrollment.objects.count() == 1
        enrollment = CourseEnrollment.objects.get(course_id=course_key)
        assert enrollment.user.username == 'test'

    @ddt.data(*COURSE_IDS_DDT)
    @ddt.unpack
    def test_set_roles(self, course_id, course_key):
        seed_permissions_roles(course_key)
        course_roles = {r.name: r for r in Role.objects.filter(course_id=course_key)}
        assert len(course_roles) == 5
        # sanity check

        # Student role is assigned by default on course enrollment.
        self._auto_auth({'username': 'a_student', 'course_id': course_id})
        user = User.objects.get(username='a_student')
        user_roles = user.roles.all()
        assert len(user_roles) == 1
        assert user_roles[0] == course_roles[FORUM_ROLE_STUDENT]

        self.client.logout()
        self._auto_auth({'username': 'a_moderator', 'course_id': course_id, 'roles': 'Moderator'})
        user = User.objects.get(username='a_moderator')
        user_roles = user.roles.all()
        assert set(user_roles) == {course_roles[FORUM_ROLE_STUDENT], course_roles[FORUM_ROLE_MODERATOR]}

        # check multiple roles work.
        self.client.logout()
        self._auto_auth({
            'username': 'an_admin', 'course_id': course_id,
            'roles': f'{FORUM_ROLE_MODERATOR},{FORUM_ROLE_ADMINISTRATOR}'
        })
        user = User.objects.get(username='an_admin')
        user_roles = user.roles.all()
        assert set(user_roles) == {course_roles[FORUM_ROLE_STUDENT], course_roles[FORUM_ROLE_MODERATOR],
                                   course_roles[FORUM_ROLE_ADMINISTRATOR]}

    def test_json_response(self):
        """ The view should return JSON. """
        response = self._auto_auth()
        response_data = json.loads(response.content.decode('utf-8'))
        for key in ['created_status', 'username', 'email', 'password', 'user_id', 'anonymous_id']:
            assert key in response_data
        user = User.objects.get(username=response_data['username'])
        assert_dict_contains_subset(
            self,
            {
                'created_status': 'Logged in',
                'anonymous_id': anonymous_id_for_user(user, None),
            },
            response_data,
        )

    @ddt.data(*COURSE_IDS_DDT)
    @ddt.unpack
    def test_redirect_to_course(self, course_id, course_key):
        # Create a user and enroll in a course
        response = self._auto_auth({
            'username': 'test',
            'course_id': course_id,
            'redirect': True,
            'staff': 'true',
        }, status_code=302)

        # Check that a course enrollment was created for the user
        assert CourseEnrollment.objects.count() == 1
        enrollment = CourseEnrollment.objects.get(course_id=course_key)
        assert enrollment.user.username == 'test'

        # Check that the redirect was to the correct outline page for either lms or studio
        if settings.ROOT_URLCONF == 'lms.urls':
            expected_redirect_url = f'http://learning-mfe/course/{course_id}/home'
        else:
            expected_redirect_url = f'/course/{course_id}'

        assert response.url == expected_redirect_url

    def test_redirect_to_main(self):
        # Create user and redirect to 'home' (cms) or 'dashboard' (lms)
        response = self._auto_auth({
            'username': 'test',
            'redirect': True,
            'staff': 'true',
        }, status_code=302)

        # Check that the redirect was to either /dashboard or /home
        if settings.ROOT_URLCONF == 'lms.urls':
            url_pattern = '/dashboard'
        else:
            url_pattern = '/home'

        assert response.url.endswith(url_pattern)

    def test_redirect_to_specified(self):
        # Create user and redirect to specified url
        url_pattern = '/u/test#about_me'
        response = self._auto_auth({
            'username': 'test',
            'redirect_to': url_pattern,
            'staff': 'true',
        }, status_code=302)

        assert response.url.endswith(url_pattern)

    def _auto_auth(self, params=None, status_code=200, **kwargs):
        """
        Make a request to the auto-auth end-point and check
        that the response is successful.

        Arguments:
            params (dict): Dict of params to pass to the auto_auth view
            status_code (int): Expected response status code
            kwargs: Passed directly to the test client's get method.

        Returns:
            Response: The response object for the auto_auth page.
        """
        params = params or {}
        response = self.client.get(self.url, params, **kwargs)

        assert response.status_code == status_code

        # Check that session and CSRF are set in the response
        for cookie in ['csrftoken', 'sessionid']:
            assert cookie in response.cookies
            assert response.cookies[cookie].value

        return response

    @patch("openedx.core.djangoapps.site_configuration.helpers.get_value", Mock(return_value=False))
    def test_create_account_not_allowed(self):
        """
        Test case to check user creation is forbidden when ALLOW_PUBLIC_ACCOUNT_CREATION feature flag is turned off
        """
        response = self.client.get(self.url)
        assert response.status_code == 403

    def test_course_access_roles(self):
        """ Passing role names via the course_access_roles query string parameter should create CourseAccessRole
        objects associated with the user.
        """

        expected_roles = ['finance_admin', 'sales_admin']
        course_key = CourseLocator.from_string(self.COURSE_ID_SPLIT)
        params = {
            'course_id': str(course_key),
            'course_access_roles': ','.join(expected_roles)
        }

        response = self._auto_auth(params)
        user_info = json.loads(response.content.decode('utf-8'))

        for role in expected_roles:
            assert CourseAccessRole.objects\
                .filter(user__id=user_info['user_id'], course_id=course_key, org=course_key.org, role=role).exists()


class AutoAuthDisabledTestCase(AutoAuthTestCase):
    """
    Test that the page is inaccessible with default settings
    """

    @patch.dict("django.conf.settings.FEATURES", {"AUTOMATIC_AUTH_FOR_TESTING": False})
    def setUp(self):
        # Patching the settings.FEATURES['AUTOMATIC_AUTH_FOR_TESTING']
        # value affects the contents of urls.py,
        # so we need to call super.setUp() which reloads urls.py (because
        # of the UrlResetMixin)
        super().setUp()
        self.url = '/auto_auth'
        self.client = Client()

    def test_auto_auth_disabled(self):
        """
        Make sure automatic authentication is disabled.
        """
        response = self.client.get(self.url)
        assert response.status_code == 404


class AutoAuthRestrictedTestCase(AutoAuthTestCase):
    """
    Test that the default security restrictions on automatic authentication
    work as intended.  These restrictions are in place for load tests.
    """

    @patch.dict('django.conf.settings.FEATURES', {'AUTOMATIC_AUTH_FOR_TESTING': True})
    def setUp(self):
        # Patching the settings.FEATURES['AUTOMATIC_AUTH_FOR_TESTING']
        # value affects the contents of urls.py,
        # so we need to call super.setUp() which reloads urls.py (because
        # of the UrlResetMixin)
        super().setUp()
        self.url = '/auto_auth'
        self.client = Client()

    @patch.dict("django.conf.settings.FEATURES", {'RESTRICT_AUTOMATIC_AUTH': True})
    def test_superuser(self):
        """
        Make sure that superusers cannot be created.
        """
        response = self.client.get(self.url, {'username': 'test', 'superuser': 'true'})
        assert response.status_code == 403

    @patch.dict("django.conf.settings.FEATURES", {'RESTRICT_AUTOMATIC_AUTH': True})
    def test_modify_user(self):
        """
        Make sure that existing users cannot be modified.
        """
        response = self.client.get(self.url, {'username': 'test'})
        assert response.status_code == 200
        response = self.client.get(self.url, {'username': 'test'})
        assert response.status_code == 403
