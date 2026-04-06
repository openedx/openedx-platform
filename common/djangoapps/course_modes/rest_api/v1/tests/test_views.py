"""
Tests for the course modes API.
"""

import json

import ddt
from django.urls import reverse
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.rest_api.v1.views import CourseModesView
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.user_authn.tests.utils import JWT_AUTH_TYPES, AuthAndScopesTestMixin, AuthType
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)


@ddt.ddt
class CourseModesViewTestBase(AuthAndScopesTestMixin):
    """
    Tests for the course modes list/create API endpoints.
    """
    default_scopes = CourseModesView.required_scopes
    view_name = ''

    @classmethod
    def setUpClass(cls):
        cls.course_key = CourseKey.from_string('course-v1:edX+DemoX+Demo_Course')
        cls.course = CourseOverviewFactory.create(id=cls.course_key)
        cls.audit_mode = CourseModeFactory.create(
            course_id=cls.course_key,
            mode_slug='audit',
            mode_display_name='Audit',
            min_price=0,
        )
        cls.verified_mode = CourseModeFactory.create(
            course_id=cls.course_key,
            mode_slug='verified',
            mode_display_name='Verified',
            min_price=25,
        )
        # use these to make sure we don't fetch data for other courses
        cls.other_course_key = CourseKey.from_string('course-v1:edX+DemoX+Other_Course')
        cls.other_course = CourseOverviewFactory.create(id=cls.other_course_key)
        cls.other_mode = CourseModeFactory.create(
            course_id=cls.other_course_key,
            mode_slug='other-audit',
            mode_display_name='Other Audit',
            min_price=0,
        )

    @classmethod
    def tearDownClass(cls):
        cls.course.delete()
        cls.audit_mode.delete()
        cls.verified_mode.delete()
        cls.other_course.delete()
        cls.other_mode.delete()

    def setUp(self):
        super().setUp()
        # overwrite self.student to be a staff member, since only staff
        # should be able to access the course_modes API endpoints.
        # This is needed to make a handful of tests inherited from AuthAndScopesTestMixin pass.
        # Note that we also inherit here self.global_staff (a staff user)
        # and self.other_student, which remains a non-staff user.
        self.student = UserFactory.create(password=self.user_password, is_staff=True)

    def assert_success_response_for_student(self, response):
        """
        Required method to implement AuthAndScopesTestMixin.
        """
        pass  # pylint: disable=unnecessary-pass

    @ddt.data(*JWT_AUTH_TYPES)
    def test_jwt_on_behalf_of_user(self, auth_type):
        """
        We have to override this super method due to this API
        being restricted to staff users only.
        """
        jwt_token = self._create_jwt_token(self.student, auth_type, include_me_filter=True)
        # include_me_filter=True means a JWT filter will require the username
        # of the requesting user to be in the requested URL
        url = self.get_url(self.student) + f'?username={self.student.username}'

        resp = self.get_response(AuthType.jwt, token=jwt_token, url=url)
        assert status.HTTP_200_OK == resp.status_code


@skip_unless_lms
class TestCourseModesListViews(CourseModesViewTestBase, ModuleStoreTestCase, APITestCase):
    """
    Tests for the course modes list/create API endpoints.
    """
    view_name = 'course_modes_api:v1:course_modes_list'

    # pylint: disable=unused-argument
    def get_url(self, username=None, course_id=None):
        """
        Required method to implement AuthAndScopesTestMixin.
        """
        kwargs = {
            'course_id': str(course_id or self.course_key)
        }
        return reverse(self.view_name, kwargs=kwargs)

    def test_list_course_modes_student_forbidden(self):
        self.client.login(username=self.other_student.username, password=self.user_password)
        url = self.get_url(course_id=self.course_key)

        response = self.client.get(url)

        assert status.HTTP_403_FORBIDDEN == response.status_code

    def test_list_course_modes_happy_path(self):
        self.client.login(username=self.global_staff.username, password=self.user_password)
        url = self.get_url(course_id=self.course_key)

        response = self.client.get(url)

        assert status.HTTP_200_OK == response.status_code
        actual_results = self._sorted_results(response)
        expected_results = [
            {
                'course_id': str(self.course_key),
                'mode_slug': 'audit',
                'mode_display_name': 'Audit',
                'min_price': 0,
                'currency': 'usd',
                'expiration_datetime': None,
                'expiration_datetime_is_explicit': False,
                'description': None,
                'sku': None,
                'bulk_sku': None,
            },
            {
                'course_id': str(self.course_key),
                'mode_slug': 'verified',
                'mode_display_name': 'Verified',
                'min_price': 25,
                'currency': 'usd',
                'expiration_datetime': None,
                'expiration_datetime_is_explicit': False,
                'description': None,
                'sku': None,
                'bulk_sku': None,
            },
        ]
        assert expected_results == actual_results

        # Now test the "other" course
        url = self.get_url(course_id=self.other_course_key)

        other_response = self.client.get(url)

        assert status.HTTP_200_OK == other_response.status_code
        other_actual_results = self._sorted_results(other_response)
        other_expected_results = [
            {
                'course_id': str(self.other_course_key),
                'mode_slug': 'other-audit',
                'mode_display_name': 'Other Audit',
                'min_price': 0,
                'currency': 'usd',
                'expiration_datetime': None,
                'expiration_datetime_is_explicit': False,
                'description': None,
                'sku': None,
                'bulk_sku': None,
            },
        ]
        assert other_expected_results == other_actual_results

    @staticmethod
    def _sorted_results(response):
        return sorted(
            [dict(item) for item in response.data],
            key=lambda item: item['mode_slug'],
        )

    def test_post_course_mode_forbidden(self):
        self.client.login(username=self.other_student.username, password=self.user_password)
        url = self.get_url(course_id=self.course_key)

        response = self.client.post(url, data={'it': 'does not matter'})

        assert status.HTTP_403_FORBIDDEN == response.status_code

    def test_post_course_mode_happy_path(self):
        self.client.login(username=self.global_staff.username, password=self.user_password)
        url = self.get_url(course_id=self.course_key)

        request_payload = {
            'course_id': str(self.course_key),
            'mode_slug': 'masters',
            'mode_display_name': 'Masters',
            'currency': 'usd',
        }

        response = self.client.post(url, data=request_payload)

        assert status.HTTP_201_CREATED == response.status_code
        new_mode = CourseMode.objects.get(course_id=self.course_key, mode_slug='masters')
        assert self.course_key == new_mode.course_id
        assert 'masters' == new_mode.mode_slug
        assert 'Masters' == new_mode.mode_display_name
        assert 0 == new_mode.min_price  # 0 is the default defined on the models.CourseMode.currency field
        assert 'usd' == new_mode.currency

    def test_post_course_mode_fails_when_missing_required_fields(self):
        self.client.login(username=self.global_staff.username, password=self.user_password)
        url = self.get_url(course_id=self.course_key)

        request_payload = {
            'course_id': str(self.course_key),
            'mode_slug': 'phd',
        }

        response = self.client.post(url, data=request_payload)

        assert status.HTTP_400_BAD_REQUEST == response.status_code
        expected_data = {
            'currency': [
                'This field is required.'
            ],
            'mode_display_name': [
                'This field is required.'
            ]
        }
        assert expected_data == response.data
        assert 0 == CourseMode.objects.filter(course_id=self.course_key, mode_slug='phd').count()


@skip_unless_lms
class TestCourseModesDetailViews(CourseModesViewTestBase, APITestCase):
    """
    Tests for the course modes retrieve/update/delete API endpoints.
    """
    view_name = 'course_modes_api:v1:course_modes_detail'

    # pylint: disable=unused-argument
    def get_url(self, username=None, course_id=None, mode_slug=None):
        """
        Required method to implement AuthAndScopesTestMixin.
        """
        kwargs = {
            'course_id': str(course_id or self.course_key),
            'mode_slug': mode_slug or 'audit',
        }
        return reverse(self.view_name, kwargs=kwargs)

    def test_retrieve_course_mode_student_forbidden(self):
        self.client.login(username=self.other_student.username, password=self.user_password)
        url = self.get_url(mode_slug='audit')

        response = self.client.get(url)

        assert status.HTTP_403_FORBIDDEN == response.status_code

    def test_retrieve_course_mode_does_not_exist(self):
        self.client.login(username=self.global_staff.username, password=self.user_password)
        url = self.get_url(mode_slug='does-not-exist')

        response = self.client.get(url)

        assert status.HTTP_404_NOT_FOUND == response.status_code

    def test_retrieve_course_mode_happy_path(self):
        self.client.login(username=self.global_staff.username, password=self.user_password)
        url = self.get_url(mode_slug='audit')

        response = self.client.get(url)

        assert status.HTTP_200_OK == response.status_code
        actual_data = dict(response.data)
        expected_data = {
            'course_id': str(self.course_key),
            'mode_slug': 'audit',
            'mode_display_name': 'Audit',
            'min_price': 0,
            'currency': 'usd',
            'expiration_datetime': None,
            'expiration_datetime_is_explicit': False,
            'description': None,
            'sku': None,
            'bulk_sku': None,
        }
        assert expected_data == actual_data

    def test_update_course_mode_student_forbidden(self):
        self.client.login(username=self.other_student.username, password=self.user_password)
        url = self.get_url(mode_slug='audit')

        response = self.client.patch(
            url,
            content_type='application/merge-patch+json',
            data=json.dumps({'it': 'does not matter'}),
        )

        assert status.HTTP_403_FORBIDDEN == response.status_code

    def test_update_course_mode_does_not_exist(self):
        self.client.login(username=self.global_staff.username, password=self.user_password)
        url = self.get_url(mode_slug='does-not-exist')

        response = self.client.patch(
            url,
            data=json.dumps({'it': 'does not matter'}),
            content_type='application/merge-patch+json',
        )

        assert status.HTTP_404_NOT_FOUND == response.status_code

    def test_update_course_mode_happy_path(self):
        new_mode = CourseModeFactory.create(
            course_id=self.course_key,
            mode_slug='prof-ed',
            mode_display_name='Professional Education',
            min_price=100,
            currency='jpy',
        )
        self.client.login(username=self.global_staff.username, password=self.user_password)
        url = self.get_url(mode_slug='prof-ed')

        response = self.client.patch(
            url,
            data=json.dumps({
                'min_price': 222,
                'mode_display_name': 'Something Else',
            }),
            content_type='application/merge-patch+json',
        )

        assert status.HTTP_204_NO_CONTENT == response.status_code
        updated_mode = CourseMode.objects.get(course_id=self.course_key, mode_slug='prof-ed')
        assert 222 == updated_mode.min_price
        assert 'Something Else' == updated_mode.mode_display_name
        assert 'jpy' == updated_mode.currency
        self.addCleanup(lambda mode: mode.delete(), new_mode)

    def test_update_course_mode_fails_when_updating_static_fields(self):
        self.client.login(username=self.global_staff.username, password=self.user_password)
        url = self.get_url(mode_slug='audit')

        response = self.client.patch(
            url,
            data=json.dumps({
                'course_id': 'course-v1:edX+DemoX+Demo_Course2',
                'mode_slug': 'audit-2',
            }),
            content_type='application/merge-patch+json',
        )

        assert status.HTTP_400_BAD_REQUEST == response.status_code
        expected_data = {
            'course_id': [
                'This field cannot be modified.'
            ],
            'mode_slug': [
                'This field cannot be modified.'
            ]
        }
        assert expected_data == response.data
        assert 'audit' == self.audit_mode.mode_slug
        assert self.course_key == self.audit_mode.course_id

    def test_delete_course_mode_student_forbidden(self):
        self.client.login(username=self.other_student.username, password=self.user_password)
        url = self.get_url(mode_slug='audit')

        response = self.client.delete(url)

        assert status.HTTP_403_FORBIDDEN == response.status_code

    def test_delete_course_mode_does_not_exist(self):
        self.client.login(username=self.global_staff.username, password=self.user_password)
        url = self.get_url(mode_slug='does-not-exist')

        response = self.client.delete(url)

        assert status.HTTP_404_NOT_FOUND == response.status_code

    def test_delete_course_mode_happy_path(self):
        new_mode = CourseModeFactory.create(
            course_id=self.course_key,
            mode_slug='bachelors',
            mode_display_name='Bachelors',
            min_price=1000,
        )
        self.client.login(username=self.global_staff.username, password=self.user_password)
        url = self.get_url(mode_slug='bachelors')

        response = self.client.delete(url)

        assert status.HTTP_204_NO_CONTENT == response.status_code
        assert 0 == CourseMode.objects.filter(course_id=self.course_key, mode_slug='bachelors').count()
        self.addCleanup(lambda mode: mode.delete(), new_mode)
