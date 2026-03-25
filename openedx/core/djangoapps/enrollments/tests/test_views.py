# pylint: disable=missing-docstring,redefined-outer-name
"""
Tests for user enrollment.
"""

import datetime
import itertools
import json
from unittest.mock import patch
from urllib.parse import quote

import ddt
import httpretty
import pytest
from zoneinfo import ZoneInfo
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.wsgi import WSGIRequest
from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse
from freezegun import freeze_time
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import AdminFactory, SuperuserFactory, UserFactory
from common.djangoapps.util.models import RateLimitConfiguration
from common.djangoapps.util.testing import UrlResetMixin
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.course_groups import cohorts
from openedx.core.djangoapps.embargo.models import Country, CountryAccessRule, RestrictedCourse
from openedx.core.djangoapps.embargo.test_utils import restrict_course
from openedx.core.djangoapps.enrollments import api, data
from openedx.core.djangoapps.enrollments.errors import CourseEnrollmentError
from openedx.core.djangoapps.enrollments.views import EnrollmentUserThrottle
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.user_api.models import RetirementState, UserOrgTag, UserRetirementStatus
from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.core.lib.django_test_client_utils import get_absolute_url
from openedx.features.enterprise_support.tests import FAKE_ENTERPRISE_CUSTOMER
from openedx.features.enterprise_support.tests.mixins.enterprise import EnterpriseServiceMockMixin
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, check_mongo_calls_range


class EnrollmentTestMixin:
    """ Mixin with methods useful for testing enrollments. """
    API_KEY = "i am a key"

    def assert_enrollment_status(
        self,
        course_id=None,
        username=None,
        expected_status=status.HTTP_200_OK,
        email_opt_in=None,
        as_server=False,
        mode=CourseMode.DEFAULT_MODE_SLUG,
        is_active=None,
        enrollment_attributes=None,
        min_mongo_calls=0,
        max_mongo_calls=0,
        linked_enterprise_customer=None,
        cohort=None,
        force_enrollment=False,
    ):
        """
        Enroll in the course and verify the response's status code. If the expected status is 200, also validates
        the response content.

        Returns
            Response
        """
        course_id = course_id or str(self.course.id)
        username = username or self.user.username

        data = {
            'mode': mode,
            'course_details': {
                'course_id': course_id
            },
            'user': username,
            'force_enrollment': force_enrollment,
            'enrollment_attributes': enrollment_attributes
        }

        if is_active is not None:
            data['is_active'] = is_active

        if email_opt_in is not None:
            data['email_opt_in'] = email_opt_in

        if linked_enterprise_customer is not None:
            data['linked_enterprise_customer'] = linked_enterprise_customer

        if cohort is not None:
            data['cohort'] = cohort

        extra = {}
        if as_server:
            extra['HTTP_X_EDX_API_KEY'] = self.API_KEY

        # Verify that the modulestore is queried as expected.
        with check_mongo_calls_range(min_finds=min_mongo_calls, max_finds=max_mongo_calls):
            with patch('openedx.core.djangoapps.enrollments.views.audit_log') as mock_audit_log:
                url = reverse('courseenrollments')
                response = self.client.post(url, json.dumps(data), content_type='application/json', **extra)
                assert response.status_code == expected_status

                if expected_status == status.HTTP_200_OK:
                    data = json.loads(response.content.decode('utf-8'))
                    assert course_id == data['course_details']['course_id']

                    if mode is not None:
                        assert mode == data['mode']

                    if is_active is not None:
                        assert is_active == data['is_active']
                    else:
                        assert data['is_active']

                    if as_server:
                        # Verify that an audit message was logged.
                        assert mock_audit_log.called

                        # If multiple enrollment calls are made in the scope of a
                        # single test, we want to validate that audit messages are
                        # logged for each call.
                        mock_audit_log.reset_mock()

        return response

    def assert_enrollment_activation(self, expected_activation, expected_mode, as_server=True):
        """Change an enrollment's activation and verify its activation and mode are as expected."""
        self.assert_enrollment_status(
            as_server=as_server,
            mode=expected_mode,
            is_active=expected_activation,
            expected_status=status.HTTP_200_OK
        )
        actual_mode, actual_activation = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert actual_activation == expected_activation
        assert actual_mode == expected_mode

    def _get_enrollments(self):
        """Retrieve the enrollment list for the current user. """
        resp = self.client.get(reverse("courseenrollments"))
        return json.loads(resp.content.decode('utf-8'))


@override_settings(EDX_API_KEY="i am a key")
@ddt.ddt
@skip_unless_lms
class EnrollmentTest(EnrollmentTestMixin, ModuleStoreTestCase, APITestCase, EnterpriseServiceMockMixin):
    """
    Test user enrollment, especially with different course modes.
    """
    USERNAME = "Bob"
    EMAIL = "bob@example.com"
    PASSWORD = "edx"

    OTHER_USERNAME = "Jane"
    OTHER_EMAIL = "jane@example.com"

    ENABLED_CACHES = ['default', 'mongo_metadata_inheritance', 'loc_cache']
    ENABLED_SIGNALS = ['course_published']

    def setUp(self):
        """ Create a course and user, then log in. """
        super().setUp()

        self.rate_limit_config = RateLimitConfiguration.current()
        self.rate_limit_config.enabled = False
        self.rate_limit_config.save()

        throttle = EnrollmentUserThrottle()
        self.rate_limit, __ = throttle.parse_rate(throttle.rate)

        # Pass emit_signals when creating the course so it would be cached
        # as a CourseOverview. Enrollments require a cached CourseOverview.
        self.course = CourseFactory.create(emit_signals=True)

        self.user = UserFactory.create(
            username=self.USERNAME,
            email=self.EMAIL,
            password=self.PASSWORD,
        )
        self.other_user = UserFactory.create(
            username=self.OTHER_USERNAME,
            email=self.OTHER_EMAIL,
            password=self.PASSWORD,
        )
        self.client.login(username=self.USERNAME, password=self.PASSWORD)

    @ddt.data(
        # Default (no course modes in the database)
        # Expect that users are automatically enrolled as the default
        ([], CourseMode.DEFAULT_MODE_SLUG),

        # Audit / Verified
        # We should always go to the "choose your course" page.
        # We should also be enrolled as the default.
        ([CourseMode.VERIFIED, CourseMode.AUDIT], CourseMode.DEFAULT_MODE_SLUG),
    )
    @ddt.unpack
    def test_enroll(self, course_modes, enrollment_mode):
        # Create the course modes (if any) required for this test case
        for mode_slug in course_modes:
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode_slug,
                mode_display_name=mode_slug,
            )

        # Create an enrollment
        resp = self.assert_enrollment_status()

        # Verify that the response contains the correct course_name
        data = json.loads(resp.content.decode('utf-8'))
        assert self.course.display_name_with_default == data['course_details']['course_name']

        # Verify that the enrollment was created correctly
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == enrollment_mode

    def test_enroll_with_email_staff(self):
        # Create enrollments with email are allowed if you are staff.

        self.client.logout()
        AdminFactory.create(username='global_staff', email='global_staff@example.com', password=self.PASSWORD)
        self.client.login(username="global_staff", password=self.PASSWORD)

        resp = self.client.post(
            reverse('courseenrollments'),
            {
                'course_details': {
                    'course_id': str(self.course.id)
                },
                'email': self.user.email
            },
            format='json'
        )
        assert resp.status_code == status.HTTP_200_OK

    @patch('openedx.core.djangoapps.enrollments.views.EnrollmentListView.has_api_key_permissions')
    def test_enroll_with_email_server(self, has_api_key_permissions_mock):
        # Create enrollments with email are allowed if it is a server-to-server request.

        has_api_key_permissions_mock.return_value = True
        resp = self.client.post(
            reverse('courseenrollments'),
            {
                'course_details': {
                    'course_id': str(self.course.id)
                },
                'email': self.user.email
            },
            format='json'
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_enroll_with_email_without_staff(self):
        # If you are not staff or server request you can't create enrollments with email.

        resp = self.client.post(
            reverse('courseenrollments'),
            {
                'course_details': {
                    'course_id': str(self.course.id)
                },
                'email': self.other_user.email
            },
            format='json'
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_enroll_with_user_and_email(self):
        # Creating enrollments the user has priority over the email.
        resp = self.client.post(
            reverse('courseenrollments'),
            {
                'course_details': {
                    'course_id': str(self.course.id)
                },
                'user': self.user.username,
                'email': self.other_user.email
            },
            format='json'
        )
        self.assertContains(resp, self.user.username, status_code=status.HTTP_200_OK)

    def test_enroll_with_user_without_permissions_and_email(self):
        resp = self.client.post(
            reverse('courseenrollments'),
            {
                'course_details': {
                    'course_id': str(self.course.id)
                },
                'user': self.other_user.username,
                'email': self.user.email
            },
            format='json'
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_enroll_with_user_as_self_user(self):
        resp = self.client.post(
            reverse('courseenrollments'),
            {
                'course_details': {
                    'course_id': str(self.course.id)
                },
                'user': self.user.username
            },
            format='json'
        )
        self.assertContains(resp, self.user.username, status_code=status.HTTP_200_OK)

    def test_enroll_without_user(self):
        # To check if it takes the request.user.
        resp = self.client.post(
            reverse('courseenrollments'),
            {
                'course_details': {
                    'course_id': str(self.course.id)
                }
            },
            format='json'
        )
        self.assertContains(resp, self.user.username, status_code=status.HTTP_200_OK)

    @ddt.data(
        # Default (no course modes in the database)
        # Expect that users are automatically enrolled as the default
        ([CourseMode.VERIFIED], CourseMode.VERIFIED, False),

        # Audit / Verified
        # We should always go to the "choose your course" page.
        # We should also be enrolled as the default.
        ([CourseMode.VERIFIED], CourseMode.VERIFIED, True),
    )
    @ddt.unpack
    def test_force_enrollment(self, course_modes, enrollment_mode, force_enrollment):
        # Create the course modes (if any) required for this test case
        start_date = datetime.datetime(2021, 12, 1, 5, 0, 0, tzinfo=ZoneInfo("UTC"))
        end_date = datetime.datetime(2022, 12, 1, 5, 0, 0, tzinfo=ZoneInfo("UTC"))
        self.course = CourseFactory.create(
            emit_signals=True,
            start=start_date,
            end=end_date,
            enrollment_start=start_date,
            enrollment_end=end_date,
        )
        for mode_slug in course_modes:
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode_slug,
                mode_display_name=mode_slug,
            )

        # If a user enroll himself in expired course
        # whether force_enrollmet is True or False
        self.assert_enrollment_status(
            mode=CourseMode.VERIFIED,
            expected_status=status.HTTP_403_FORBIDDEN,
            force_enrollment=force_enrollment,
        )

        self.client.logout()
        AdminFactory.create(username='global_staff', email='global_staff@example.com', password=self.PASSWORD)
        self.client.login(username="global_staff", password=self.PASSWORD)

        if force_enrollment:
            # Create an enrollment
            resp = self.assert_enrollment_status(
                username=self.USERNAME,
                mode=CourseMode.VERIFIED,
                force_enrollment=force_enrollment,
            )

            # Verify that the response contains the correct course_name
            data = json.loads(resp.content.decode('utf-8'))
            assert self.course.display_name_with_default == data['course_details']['course_name']

            # Verify that the enrollment was created correctly
            assert CourseEnrollment.is_enrolled(self.user, self.course.id)
            course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
            assert is_active
            assert course_mode == enrollment_mode
        else:
            # If a staff user enroll other user in expired course
            # This will raise the CourseEnrollmentClosedError excecption
            # and return status will be 400
            self.assert_enrollment_status(
                username=self.USERNAME,
                mode=CourseMode.VERIFIED,
                expected_status=status.HTTP_400_BAD_REQUEST,
                force_enrollment=force_enrollment,
            )

    def test_check_enrollment(self):
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug=CourseMode.DEFAULT_MODE_SLUG,
            mode_display_name=CourseMode.DEFAULT_MODE_SLUG,
        )
        # Create an enrollment
        self.assert_enrollment_status()
        resp = self.client.get(
            reverse(
                'courseenrollment',
                kwargs={'username': self.user.username, "course_id": str(self.course.id)},
            )
        )
        assert resp.status_code == status.HTTP_200_OK
        data = json.loads(resp.content.decode('utf-8'))
        assert str(self.course.id) == data['course_details']['course_id']
        assert self.course.display_name_with_default == data['course_details']['course_name']
        assert CourseMode.DEFAULT_MODE_SLUG == data['mode']
        assert data['is_active']

    @ddt.data(
        (True, "True"),
        (False, "False"),
        (None, None)
    )
    @ddt.unpack
    def test_email_opt_in_true(self, opt_in, pref_value):
        """
        Verify that the email_opt_in parameter sets the underlying flag.
        And that if the argument is not present, then it does not affect the flag
        """

        def _assert_no_opt_in_set():
            """ Check the tag doesn't exit"""
            with pytest.raises(UserOrgTag.DoesNotExist):
                UserOrgTag.objects.get(user=self.user, org=self.course.id.org, key="email-optin")

        _assert_no_opt_in_set()
        self.assert_enrollment_status(email_opt_in=opt_in)
        if opt_in is None:
            _assert_no_opt_in_set()
        else:
            preference = UserOrgTag.objects.get(user=self.user, org=self.course.id.org, key="email-optin")
            assert preference.value == pref_value

    def test_enroll_prof_ed(self):
        # Create the prod ed mode.
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='professional',
            mode_display_name='Professional Education',
        )

        # Enroll in the course, this will fail if the mode is not explicitly professional.
        resp = self.assert_enrollment_status(expected_status=status.HTTP_400_BAD_REQUEST)

        # While the enrollment wrong is invalid, the response content should have
        # all the valid enrollment modes.
        data = json.loads(resp.content.decode('utf-8'))
        assert str(self.course.id) == data['course_details']['course_id']
        assert 1 == len(data['course_details']['course_modes'])
        assert 'professional' == data['course_details']['course_modes'][0]['slug']

    def test_user_not_specified(self):
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug=CourseMode.DEFAULT_MODE_SLUG,
            mode_display_name=CourseMode.DEFAULT_MODE_SLUG,
        )
        # Create an enrollment
        self.assert_enrollment_status()
        resp = self.client.get(
            reverse('courseenrollment', kwargs={"course_id": str(self.course.id)})
        )
        assert resp.status_code == status.HTTP_200_OK
        data = json.loads(resp.content.decode('utf-8'))
        assert str(self.course.id) == data['course_details']['course_id']
        assert CourseMode.DEFAULT_MODE_SLUG == data['mode']
        assert data['is_active']

    def test_user_not_authenticated(self):
        # Log out, so we're no longer authenticated
        self.client.logout()

        # Try to enroll, this should fail.
        self.assert_enrollment_status(expected_status=status.HTTP_401_UNAUTHORIZED)

    def test_user_not_activated(self):
        # Log out the default user, Bob.
        self.client.logout()

        # Create a user account
        self.user = UserFactory.create(
            username="inactive",
            email="inactive@example.com",
            password=self.PASSWORD,
            is_active=True
        )

        # Log in with the unactivated account
        self.client.login(username="inactive", password=self.PASSWORD)

        # Deactivate the user. Has to be done after login to get the user into the
        # request and properly logged in.
        self.user.is_active = False
        self.user.save()

        # Enrollment should succeed, even though we haven't authenticated.
        self.assert_enrollment_status()

    def test_user_does_not_match_url(self):
        # Try to enroll a user that is not the authenticated user.
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug=CourseMode.DEFAULT_MODE_SLUG,
            mode_display_name=CourseMode.DEFAULT_MODE_SLUG,
        )
        self.assert_enrollment_status(username=self.other_user.username, expected_status=status.HTTP_404_NOT_FOUND)
        # Verify that the server still has access to this endpoint.
        self.client.logout()
        self.assert_enrollment_status(username=self.other_user.username, as_server=True)

    def _assert_enrollments_visible_in_list(self, courses, use_server_key=False):
        """
        Check that the list of enrollments of self.user returned for the currently logged in user
        matches the list of courses passed in in 'courses'.
        """
        kwargs = {}
        if use_server_key:
            kwargs.update(HTTP_X_EDX_API_KEY=self.API_KEY)
        response = self.client.get(reverse('courseenrollments'), {'user': self.user.username}, **kwargs)
        assert response.status_code == status.HTTP_200_OK
        data = json.loads(response.content.decode('utf-8'))
        self.assertCountEqual(
            [(datum['course_details']['course_id'], datum['course_details']['course_name']) for datum in data],
            [(str(course.id), course.display_name_with_default) for course in courses]
        )

    def test_enrollment_list_permissions(self):
        """
        Test that the correct list of enrollments is returned, depending on the permissions of the
        requesting user.
        """
        # Create another course, and enroll self.user in both courses.
        other_course = CourseFactory.create(emit_signals=True)
        for course in self.course, other_course:
            CourseModeFactory.create(
                course_id=str(course.id),
                mode_slug=CourseMode.DEFAULT_MODE_SLUG,
                mode_display_name=CourseMode.DEFAULT_MODE_SLUG,
            )
            self.assert_enrollment_status(
                course_id=str(course.id),
                max_mongo_calls=0,
            )
        # Verify the user himself can see both of his enrollments.
        self._assert_enrollments_visible_in_list([self.course, other_course])
        # Verify that self.other_user can't see any of the enrollments.
        self.client.login(username=self.OTHER_USERNAME, password=self.PASSWORD)
        self._assert_enrollments_visible_in_list([])
        # Create a staff user for self.course (but nor for other_course) and log her in.
        staff_user = UserFactory.create(username='staff', email='staff@example.com', password=self.PASSWORD)
        CourseStaffRole(self.course.id).add_users(staff_user)
        self.client.login(username='staff', password=self.PASSWORD)
        # Verify that she can see only the enrollment in the course she has staff privileges for.
        self._assert_enrollments_visible_in_list([self.course])
        # Create a global staff user, and verify she can see all enrollments.
        AdminFactory(username='global_staff', email='global_staff@example.com', password=self.PASSWORD)
        self.client.login(username='global_staff', password=self.PASSWORD)
        self._assert_enrollments_visible_in_list([self.course, other_course])
        # Verify the server can see all enrollments.
        self.client.logout()
        self._assert_enrollments_visible_in_list([self.course, other_course], use_server_key=True)

    def test_user_does_not_match_param(self):
        """
        The view should return status 404 if the enrollment username does not match the username of the user
        making the request, unless the request is made by a staff user or with a server API key.
        """
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug=CourseMode.HONOR,
            mode_display_name=CourseMode.HONOR,
        )
        url = reverse('courseenrollment',
                      kwargs={'username': self.other_user.username, "course_id": str(self.course.id)})

        response = self.client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

        # Verify that the server still has access to this endpoint.
        self.client.logout()
        response = self.client.get(url, **{'HTTP_X_EDX_API_KEY': self.API_KEY})
        assert response.status_code == status.HTTP_200_OK

        # Verify staff have access to this endpoint
        staff_user = UserFactory.create(password=self.PASSWORD, is_staff=True)
        self.client.login(username=staff_user.username, password=self.PASSWORD)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK

    def test_get_course_details(self):
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug=CourseMode.HONOR,
            mode_display_name=CourseMode.HONOR,
            sku='123',
            bulk_sku="BULK123"
        )
        resp = self.client.get(
            reverse('courseenrollmentdetails', kwargs={"course_id": str(self.course.id)})
        )
        assert resp.status_code == status.HTTP_200_OK

        data = json.loads(resp.content.decode('utf-8'))
        assert str(self.course.id) == data['course_id']
        assert self.course.display_name_with_default == data['course_name']
        mode = data['course_modes'][0]
        assert mode['slug'] == CourseMode.HONOR
        assert mode['sku'] == '123'
        assert mode['bulk_sku'] == 'BULK123'
        assert mode['name'] == CourseMode.HONOR

    def test_get_course_details_with_credit_course(self):
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug=CourseMode.CREDIT_MODE,
            mode_display_name=CourseMode.CREDIT_MODE,
        )
        resp = self.client.get(
            reverse('courseenrollmentdetails', kwargs={"course_id": str(self.course.id)})
        )
        assert resp.status_code == status.HTTP_200_OK

        data = json.loads(resp.content.decode('utf-8'))
        assert str(self.course.id) == data['course_id']
        mode = data['course_modes'][0]
        assert mode['slug'] == CourseMode.CREDIT_MODE
        assert mode['name'] == CourseMode.CREDIT_MODE

    @ddt.data(
        # NOTE: Studio requires a start date, but this is not
        # enforced at the data layer, so we need to handle the case
        # in which no dates are specified.
        (None, None, None, None),
        (datetime.datetime(2015, 1, 2, 3, 4, 5, tzinfo=ZoneInfo("UTC")), None, "2015-01-02T03:04:05Z", None),
        (None, datetime.datetime(2015, 1, 2, 3, 4, 5, tzinfo=ZoneInfo("UTC")), None, "2015-01-02T03:04:05Z"),
        (
            datetime.datetime(2014, 6, 7, 8, 9, 10, tzinfo=ZoneInfo("UTC")),
            datetime.datetime(2015, 1, 2, 3, 4, 5, tzinfo=ZoneInfo("UTC")),
            "2014-06-07T08:09:10Z",
            "2015-01-02T03:04:05Z",
        ),
    )
    @ddt.unpack
    def test_get_course_details_course_dates(self, start_datetime, end_datetime, expected_start, expected_end):
        course = CourseFactory.create(start=start_datetime, end=end_datetime)
        # Load a CourseOverview. This initial load should result in a cache
        # miss; the modulestore is queried and course metadata is cached.
        __ = CourseOverview.get_from_id(course.id)

        self.assert_enrollment_status(course_id=str(course.id))

        # Check course details
        url = reverse('courseenrollmentdetails', kwargs={"course_id": str(course.id)})
        resp = self.client.get(url)
        assert resp.status_code == status.HTTP_200_OK

        data = json.loads(resp.content.decode('utf-8'))
        assert data['course_start'] == expected_start
        assert data['course_end'] == expected_end

        # Check enrollment course details
        url = reverse('courseenrollment', kwargs={"course_id": str(course.id)})
        resp = self.client.get(url)
        assert resp.status_code == status.HTTP_200_OK

        data = json.loads(resp.content.decode('utf-8'))
        assert data['course_details']['course_start'] == expected_start
        assert data['course_details']['course_end'] == expected_end

        # Check enrollment list course details
        resp = self.client.get(reverse('courseenrollments'))
        assert resp.status_code == status.HTTP_200_OK

        data = json.loads(resp.content.decode('utf-8'))
        assert data[0]['course_details']['course_start'] == expected_start
        assert data[0]['course_details']['course_end'] == expected_end

    def test_with_invalid_course_id(self):
        self.assert_enrollment_status(
            course_id='entirely/fake/course',
            expected_status=status.HTTP_400_BAD_REQUEST,
            min_mongo_calls=2,
            max_mongo_calls=3
        )

    def test_get_enrollment_details_bad_course(self):
        resp = self.client.get(
            reverse('courseenrollmentdetails', kwargs={"course_id": "some/fake/course"})
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    @patch.object(api, "get_enrollment")
    def test_get_enrollment_internal_error(self, mock_get_enrollment):
        mock_get_enrollment.side_effect = CourseEnrollmentError("Something bad happened.")
        resp = self.client.get(
            reverse(
                'courseenrollment',
                kwargs={'username': self.user.username, "course_id": str(self.course.id)},
            )
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_enrollment_already_enrolled(self):
        response = self.assert_enrollment_status()
        response_json = json.loads(response.content.decode('utf-8'))

        repeat_response = self.assert_enrollment_status(expected_status=status.HTTP_200_OK)
        repeat_json = json.loads(repeat_response.content.decode('utf-8'))

        assert response_json == repeat_json

    def test_get_enrollment_with_invalid_key(self):
        resp = self.client.post(
            reverse('courseenrollments'),
            {
                'course_details': {
                    'course_id': 'invalidcourse'
                },
                'user': self.user.username
            },
            format='json'
        )
        self.assertContains(resp, "No course ", status_code=status.HTTP_400_BAD_REQUEST)

    def test_enrollment_throttle_for_user(self):
        """Make sure a user requests do not exceed the maximum number of requests"""
        self.rate_limit_config.enabled = True
        self.rate_limit_config.save()
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug=CourseMode.DEFAULT_MODE_SLUG,
            mode_display_name=CourseMode.DEFAULT_MODE_SLUG,
        )

        for attempt in range(self.rate_limit + 2):
            expected_status = status.HTTP_429_TOO_MANY_REQUESTS if attempt >= self.rate_limit else status.HTTP_200_OK
            self.assert_enrollment_status(expected_status=expected_status)

    @ddt.data('staff', 'user')
    def test_enrollment_throttle_is_set_correctly(self, user_scope):
        """ Make sure throttle rate is set correctly for different user scopes. """
        self.rate_limit_config.enabled = True
        self.rate_limit_config.save()

        throttle = EnrollmentUserThrottle()
        throttle.scope = user_scope
        try:
            throttle.parse_rate(throttle.get_rate())
        except ImproperlyConfigured:
            self.fail(f"No throttle rate set for {user_scope}")

    def test_create_enrollment_with_cohort(self):
        """Enroll in the course, and also add to a cohort."""
        # Create a cohort
        cohort_name = 'masters'
        cohorts.set_course_cohorted(self.course.id, True)
        cohorts.add_cohort(self.course.id, cohort_name, 'test')
        # Create an enrollment

        self.assert_enrollment_status(cohort=cohort_name)
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        _, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert cohorts.get_cohort(self.user, self.course.id, assign=False).name == cohort_name

    def test_create_enrollment_with_wrong_cohort(self):
        """Enroll in the course, and also add to a cohort."""
        # Create a cohort
        cohorts.set_course_cohorted(self.course.id, True)
        cohorts.add_cohort(self.course.id, 'masters', 'test')
        # Create an enrollment
        self.assert_enrollment_status(cohort='missing', expected_status=status.HTTP_400_BAD_REQUEST)

    def test_create_enrollment_with_mode(self):
        """With the right API key, create a new enrollment with a mode set other than the default."""
        # Create a professional ed course mode.
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='professional',
            mode_display_name='professional',
        )

        # Create an enrollment
        self.assert_enrollment_status(as_server=True, mode='professional')

        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == 'professional'

    def test_enrollment_includes_expired_verified(self):
        """With the right API key, request that expired course verifications are still returned. """
        # Create a honor mode for a course.
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug=CourseMode.HONOR,
            mode_display_name=CourseMode.HONOR,
        )

        # Create a verified mode for a course.
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug=CourseMode.VERIFIED,
            mode_display_name=CourseMode.VERIFIED,
            expiration_datetime='1970-01-01 05:00:00Z'
        )

        # Passes the include_expired parameter to the API call
        v_response = self.client.get(
            reverse(
                'courseenrollmentdetails',
                kwargs={"course_id": str(self.course.id)}
            ),
            {'include_expired': True},
        )
        v_data = json.loads(v_response.content.decode('utf-8'))

        # Ensure that both course modes are returned
        assert len(v_data['course_modes']) == 2

        # Omits the include_expired parameter from the API call
        h_response = self.client.get(
            reverse('courseenrollmentdetails', kwargs={"course_id": str(self.course.id)}),
        )
        h_data = json.loads(h_response.content.decode('utf-8'))

        # Ensure that only one course mode is returned and that it is honor
        assert len(h_data['course_modes']) == 1
        assert h_data['course_modes'][0]['slug'] == CourseMode.HONOR

    def test_update_enrollment_with_mode(self):
        """With the right API key, update an existing enrollment with a new mode. """
        # Create an honor and verified mode for a course. This allows an update.
        for mode in [CourseMode.DEFAULT_MODE_SLUG, CourseMode.VERIFIED]:
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode,
                mode_display_name=mode,
            )

        # Create an enrollment
        self.assert_enrollment_status(as_server=True)

        # Check that the enrollment is default.
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == CourseMode.DEFAULT_MODE_SLUG

        # Check that the enrollment upgraded to verified.
        self.assert_enrollment_status(as_server=True, mode=CourseMode.VERIFIED, expected_status=status.HTTP_200_OK)
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == CourseMode.VERIFIED

    def test_enrollment_with_credit_mode(self):
        """With the right API key, update an existing enrollment with credit
        mode and set enrollment attributes.
        """
        for mode in [CourseMode.DEFAULT_MODE_SLUG, CourseMode.CREDIT_MODE]:
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode,
                mode_display_name=mode,
            )

        # Create an enrollment
        self.assert_enrollment_status(as_server=True)

        # Check that the enrollment is the default.
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == CourseMode.DEFAULT_MODE_SLUG

        # Check that the enrollment upgraded to credit.
        enrollment_attributes = [{
            "namespace": "credit",
            "name": "provider_id",
            "value": "hogwarts",
        }]
        self.assert_enrollment_status(
            as_server=True,
            mode=CourseMode.CREDIT_MODE,
            expected_status=status.HTTP_200_OK,
            enrollment_attributes=enrollment_attributes
        )
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == CourseMode.CREDIT_MODE

    def test_enrollment_with_invalid_attr(self):
        """Check response status is bad request when invalid enrollment
        attributes are passed
        """
        for mode in [CourseMode.DEFAULT_MODE_SLUG, CourseMode.CREDIT_MODE]:
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode,
                mode_display_name=mode,
            )

        # Create an enrollment
        self.assert_enrollment_status(as_server=True)

        # Check that the enrollment is the default.
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == CourseMode.DEFAULT_MODE_SLUG

        # Check that the enrollment upgraded to credit.
        enrollment_attributes = [{
            "namespace": "credit",
            "name": "invalid",
            "value": "hogwarts",
        }]
        self.assert_enrollment_status(
            as_server=True,
            mode=CourseMode.CREDIT_MODE,
            expected_status=status.HTTP_400_BAD_REQUEST,
            enrollment_attributes=enrollment_attributes
        )
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == CourseMode.DEFAULT_MODE_SLUG

    def test_downgrade_enrollment_with_mode(self):
        """With the right API key, downgrade an existing enrollment with a new mode. """
        # Create an honor and verified mode for a course. This allows an update.
        for mode in [CourseMode.DEFAULT_MODE_SLUG, CourseMode.VERIFIED]:
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode,
                mode_display_name=mode,
            )

        # Create a 'verified' enrollment
        self.assert_enrollment_status(as_server=True, mode=CourseMode.VERIFIED)

        # Check that the enrollment is verified.
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == CourseMode.VERIFIED

        # Check that the enrollment was downgraded to the default mode.
        self.assert_enrollment_status(
            as_server=True,
            mode=CourseMode.DEFAULT_MODE_SLUG,
            expected_status=status.HTTP_200_OK
        )
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == CourseMode.DEFAULT_MODE_SLUG

    @ddt.data(
        ((CourseMode.DEFAULT_MODE_SLUG,), CourseMode.DEFAULT_MODE_SLUG),
        ((CourseMode.DEFAULT_MODE_SLUG, CourseMode.VERIFIED), CourseMode.DEFAULT_MODE_SLUG),
        ((CourseMode.DEFAULT_MODE_SLUG, CourseMode.VERIFIED), CourseMode.VERIFIED),
        ((CourseMode.PROFESSIONAL,), CourseMode.PROFESSIONAL),
        ((CourseMode.NO_ID_PROFESSIONAL_MODE,), CourseMode.NO_ID_PROFESSIONAL_MODE),
        ((CourseMode.VERIFIED, CourseMode.CREDIT_MODE), CourseMode.VERIFIED),
        ((CourseMode.VERIFIED, CourseMode.CREDIT_MODE), CourseMode.CREDIT_MODE),
    )
    @ddt.unpack
    def test_deactivate_enrollment(self, configured_modes, selected_mode):
        """With the right API key, deactivate (i.e., unenroll from) an existing enrollment."""
        # Configure a set of modes for the course.
        for mode in configured_modes:
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode,
                mode_display_name=mode,
            )

        # Create an enrollment with the selected mode.
        self.assert_enrollment_status(as_server=True, mode=selected_mode)

        # Check that the enrollment has the correct mode and is active.
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == selected_mode

        # Verify that a non-Boolean enrollment status is treated as invalid.
        self.assert_enrollment_status(
            as_server=True,
            mode=None,
            is_active='foo',
            expected_status=status.HTTP_400_BAD_REQUEST
        )

        # Verify that the enrollment has been deactivated, and that the mode is unchanged.
        self.assert_enrollment_activation(False, selected_mode)

        # Verify that enrollment deactivation is idempotent.
        self.assert_enrollment_activation(False, selected_mode)

        # Verify that omitting the mode returns 400 for course configurations
        # in which the default mode doesn't exist.
        expected_status = (
            status.HTTP_200_OK
            if CourseMode.DEFAULT_MODE_SLUG in configured_modes
            else status.HTTP_400_BAD_REQUEST
        )
        self.assert_enrollment_status(
            as_server=True,
            is_active=False,
            expected_status=expected_status,
        )

    def test_deactivate_enrollment_with_global_staff(self):
        """Without API key but Staff staff permissions, deactivate (i.e., unenroll from) an existing enrollment."""
        # Configure a mode for the course.
        mode = CourseMode.VERIFIED
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug=mode,
            mode_display_name=mode,
        )

        # Create an enrollment with the selected mode.
        self.assert_enrollment_status(as_server=True, mode=mode)

        # Check that the enrollment has the correct mode and is active.
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == mode

        username = 'global_staff'
        AdminFactory(username=username, email='global_staff@example.com', password=self.PASSWORD)

        self.client.login(username=username, password=self.PASSWORD)
        # Verify that the enrollment has been deactivated, and the mode is
        # unchanged even by passing the as_server=false which means no API-KEY
        self.assert_enrollment_activation(False, mode, as_server=False)

    def test_deactivate_enrollment_expired_mode(self):
        """Verify that an enrollment in an expired mode can be deactivated."""
        for mode in (CourseMode.HONOR, CourseMode.VERIFIED):
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode,
                mode_display_name=mode,
            )

        # Create verified enrollment.
        self.assert_enrollment_status(as_server=True, mode=CourseMode.VERIFIED)

        # Change verified mode expiration.
        mode = CourseMode.objects.get(course_id=self.course.id, mode_slug=CourseMode.VERIFIED)
        mode.expiration_datetime = datetime.datetime(year=1970, month=1, day=1, tzinfo=ZoneInfo("UTC"))
        mode.save()

        # Deactivate enrollment.
        self.assert_enrollment_activation(False, CourseMode.VERIFIED)

    def test_change_mode_from_user(self):
        """Users should not be able to alter the enrollment mode on an enrollment. """
        # Create a default and a verified mode for a course. This allows an update.
        for mode in [CourseMode.DEFAULT_MODE_SLUG, CourseMode.VERIFIED]:
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode,
                mode_display_name=mode,
            )

        # Create an enrollment
        self.assert_enrollment_status()

        # Check that the enrollment is honor.
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == CourseMode.DEFAULT_MODE_SLUG

        # Get a 403 response when trying to upgrade yourself.
        self.assert_enrollment_status(mode=CourseMode.VERIFIED, expected_status=status.HTTP_403_FORBIDDEN)
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == CourseMode.DEFAULT_MODE_SLUG

    @ddt.data(*itertools.product(
        (CourseMode.HONOR, CourseMode.VERIFIED),
        (CourseMode.HONOR, CourseMode.VERIFIED),
        (True, False),
        (True, False),
    ))
    @ddt.unpack
    def test_change_mode_from_server(self, old_mode, new_mode, old_is_active, new_is_active):
        """
        Server-to-server calls should be allowed to change the mode of any
        enrollment, as long as the enrollment is not being deactivated during
        the same call (this is assumed to be an error on the client's side).
        """
        for mode in [CourseMode.HONOR, CourseMode.VERIFIED]:
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode,
                mode_display_name=mode,
            )

        # Set up the initial enrollment
        self.assert_enrollment_status(as_server=True, mode=old_mode, is_active=old_is_active)
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active == old_is_active
        assert course_mode == old_mode

        expected_status = status.HTTP_400_BAD_REQUEST if (
            old_mode != new_mode and
            old_is_active != new_is_active and
            not new_is_active
        ) else status.HTTP_200_OK

        # simulate the server-server api call under test
        response = self.assert_enrollment_status(
            as_server=True,
            mode=new_mode,
            is_active=new_is_active,
            expected_status=expected_status,
        )

        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        if expected_status == status.HTTP_400_BAD_REQUEST:
            # nothing should have changed
            assert is_active == old_is_active
            assert course_mode == old_mode
            # error message should contain specific text.  Otto checks for this text in the message.
            self.assertRegex(
                json.loads(response.content.decode('utf-8'))['message'],
                'Enrollment mode mismatch'
            )
        else:
            # call should have succeeded
            assert is_active == new_is_active
            assert course_mode == new_mode

    def test_change_mode_invalid_user(self):
        """
        Attempts to change an enrollment for a non-existent user should result in an HTTP 404 for non-server users,
        and HTTP 406 for server users.
        """
        self.assert_enrollment_status(username='fake-user', expected_status=status.HTTP_404_NOT_FOUND, as_server=False)
        self.assert_enrollment_status(username='fake-user', expected_status=status.HTTP_406_NOT_ACCEPTABLE,
                                      as_server=True)

    @ddt.data(
        (True, CourseMode.VERIFIED),
        (False, CourseMode.DEFAULT_MODE_SLUG)
    )
    @ddt.unpack
    def test_update_enrollment_with_expired_mode(self, using_api_key, updated_mode):
        """Verify that if verified mode is expired than it's enrollment cannot be updated. """
        for mode in [CourseMode.DEFAULT_MODE_SLUG, CourseMode.VERIFIED]:
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode,
                mode_display_name=mode,
            )

        # Create an enrollment
        self.assert_enrollment_status(as_server=True)

        # Check that the enrollment is the default.
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == CourseMode.DEFAULT_MODE_SLUG

        # Change verified mode expiration.
        mode = CourseMode.objects.get(course_id=self.course.id, mode_slug=CourseMode.VERIFIED)
        mode.expiration_datetime = datetime.datetime(year=1970, month=1, day=1, tzinfo=ZoneInfo("UTC"))
        mode.save()
        self.assert_enrollment_status(
            as_server=using_api_key,
            mode=CourseMode.VERIFIED,
            expected_status=status.HTTP_200_OK if using_api_key else status.HTTP_403_FORBIDDEN
        )
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == updated_mode

    @ddt.data(
        (True, status.HTTP_200_OK),
        (False, status.HTTP_404_NOT_FOUND)
    )
    @ddt.unpack
    def test_enrollment_with_global_staff_permissions(self, using_global_staff_user, http_status):
        """Verify no audit enrollments for user different than requesting user and without
        API_KEY should be done by the users having global staff permissions. """

        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug=CourseMode.VERIFIED,
            mode_display_name=CourseMode.VERIFIED,
        )

        username = self.OTHER_USERNAME
        if using_global_staff_user:
            username = 'global_staff'
            AdminFactory(username=username, email='global_staff@example.com', password=self.PASSWORD)
        self.client.login(username=username, password=self.PASSWORD)

        # Create an enrollment
        self.assert_enrollment_status(
            as_server=False,
            mode=CourseMode.VERIFIED,
            expected_status=http_status
        )

        if using_global_staff_user:
            course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
            assert is_active
            assert course_mode == CourseMode.VERIFIED
        self.client.logout()

    @httpretty.activate
    @override_settings(ENTERPRISE_SERVICE_WORKER_USERNAME='enterprise_worker',
                       FEATURES=dict(ENABLE_ENTERPRISE_INTEGRATION=True))
    @patch('openedx.features.enterprise_support.api.enterprise_customer_from_api')
    def test_enterprise_course_enrollment_with_ec_uuid(self, mock_enterprise_customer_from_api):
        """Verify that the enrollment completes when the EnterpriseCourseEnrollment creation succeeds. """
        UserFactory.create(
            username='enterprise_worker',
            email=self.EMAIL,
            password=self.PASSWORD,
        )
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug=CourseMode.DEFAULT_MODE_SLUG,
            mode_display_name=CourseMode.DEFAULT_MODE_SLUG,
        )
        consent_kwargs = {
            'username': self.user.username,
            'course_id': str(self.course.id),
            'ec_uuid': 'this-is-a-real-uuid'
        }
        mock_enterprise_customer_from_api.return_value = FAKE_ENTERPRISE_CUSTOMER
        self.mock_enterprise_course_enrollment_post_api()
        self.mock_consent_missing(**consent_kwargs)
        self.mock_consent_post(**consent_kwargs)
        self.assert_enrollment_status(
            expected_status=status.HTTP_200_OK,
            as_server=True,
            username='enterprise_worker',
            linked_enterprise_customer='this-is-a-real-uuid',
        )
        assert httpretty.last_request().path == '/consent/api/v1/data_sharing_consent'  # pylint: disable=no-member
        assert httpretty.last_request().method == httpretty.POST

    def test_enrollment_attributes_always_written(self):
        """ Enrollment attributes should always be written, regardless of whether
        the enrollment is being created or updated.
        """
        course_key = self.course.id
        for mode in [CourseMode.DEFAULT_MODE_SLUG, CourseMode.VERIFIED]:
            CourseModeFactory.create(
                course_id=course_key,
                mode_slug=mode,
                mode_display_name=mode,
            )

        # Creating a new enrollment should write attributes
        order_number = 'EDX-1000'
        enrollment_attributes = [{
            'namespace': 'order',
            'name': 'order_number',
            'value': order_number,
        }]
        mode = CourseMode.VERIFIED
        self.assert_enrollment_status(
            as_server=True,
            is_active=True,
            mode=mode,
            enrollment_attributes=enrollment_attributes
        )
        enrollment = CourseEnrollment.objects.get(user=self.user, course_id=course_key)
        assert enrollment.is_active
        assert enrollment.mode == CourseMode.VERIFIED
        assert enrollment.attributes.get(namespace='order', name='order_number').value == order_number

        # Updating an enrollment should update attributes
        order_number = 'EDX-2000'
        enrollment_attributes = [{
            'namespace': 'order',
            'name': 'order_number',
            'value': order_number,
        }]
        mode = CourseMode.DEFAULT_MODE_SLUG
        self.assert_enrollment_status(
            as_server=True,
            mode=mode,
            enrollment_attributes=enrollment_attributes
        )
        enrollment.refresh_from_db()
        assert enrollment.is_active
        assert enrollment.mode == mode
        assert enrollment.attributes.get(namespace='order', name='order_number').value == order_number


@skip_unless_lms
class EnrollmentEmbargoTest(EnrollmentTestMixin, UrlResetMixin, ModuleStoreTestCase):
    """Test that enrollment is blocked from embargoed countries. """

    USERNAME = "Bob"
    EMAIL = "bob@example.com"
    PASSWORD = "edx"

    URLCONF_MODULES = ['openedx.core.djangoapps.embargo']

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    def setUp(self):
        """ Create a course and user, then log in. """
        super().setUp()

        self.course = CourseFactory.create()
        # Load a CourseOverview. This initial load should result in a cache
        # miss; the modulestore is queried and course metadata is cached.
        __ = CourseOverview.get_from_id(self.course.id)

        self.user = UserFactory.create(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)
        self.client.login(username=self.USERNAME, password=self.PASSWORD)
        self.url = reverse('courseenrollments')

    def _generate_data(self):
        return json.dumps({
            'course_details': {
                'course_id': str(self.course.id)
            },
            'user': self.user.username
        })

    def assert_access_denied(self, user_message_path):
        """
        Verify that the view returns HTTP status 403 and includes a URL in the response, and no enrollment is created.
        """
        data = self._generate_data()
        response = self.client.post(self.url, data, content_type='application/json')

        # Expect an error response
        assert response.status_code == 403

        # Expect that the redirect URL is included in the response
        resp_data = json.loads(response.content.decode('utf-8'))
        user_message_url = get_absolute_url(user_message_path)
        assert resp_data['user_message_url'] == user_message_url

        # Verify that we were not enrolled
        assert self._get_enrollments() == []

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    def test_embargo_change_enrollment_restrict_geoip(self):
        """ Validates that enrollment changes are blocked if the request originates from an embargoed country. """

        # Use the helper to setup the embargo and simulate a request from a blocked IP address.
        with restrict_course(self.course.id) as redirect_path:
            self.assert_access_denied(redirect_path)

    def _setup_embargo(self):
        restricted_course = RestrictedCourse.objects.create(course_key=self.course.id)

        restricted_country = Country.objects.create(country='US')
        unrestricted_country = Country.objects.create(country='CA')

        CountryAccessRule.objects.create(
            rule_type=CountryAccessRule.BLACKLIST_RULE,
            restricted_course=restricted_course,
            country=restricted_country
        )

        # Clear the cache to remove the effects of previous embargo tests
        cache.clear()

        return unrestricted_country, restricted_country

    @override_settings(EDX_API_KEY=EnrollmentTestMixin.API_KEY)
    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    def test_embargo_change_enrollment_restrict_user_profile(self):
        """ Validates that enrollment changes are blocked if the user's profile is linked to an embargoed country. """

        __, restricted_country = self._setup_embargo()

        # Update the user's profile, linking the user to the embargoed country.
        self.user.profile.country = restricted_country.country
        self.user.profile.save()

        path = reverse('embargo:blocked_message', kwargs={'access_point': 'enrollment', 'message_key': 'default'})
        self.assert_access_denied(path)

    @override_settings(EDX_API_KEY=EnrollmentTestMixin.API_KEY)
    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    def test_embargo_change_enrollment_allow_user_profile(self):
        """
        Validates that enrollment changes are allowed if the user's profile is NOT linked to an embargoed country.
        """

        # Setup the embargo
        unrestricted_country, __ = self._setup_embargo()

        # Verify that users without black-listed country codes *can* be enrolled
        self.user.profile.country = unrestricted_country.country
        self.user.profile.save()
        self.assert_enrollment_status()

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    def test_embargo_change_enrollment_allow(self):
        self.assert_enrollment_status()

        # Verify that we were enrolled
        assert len(self._get_enrollments()) == 1


def cross_domain_config(func):
    """Decorator for configuring a cross-domain request. """
    feature_flag_decorator = patch.dict(settings.FEATURES, {
        'ENABLE_CORS_HEADERS': True,
        'ENABLE_CROSS_DOMAIN_CSRF_COOKIE': True
    })
    settings_decorator = override_settings(
        CORS_ORIGIN_WHITELIST=["https://www.edx.org"],
        CROSS_DOMAIN_CSRF_COOKIE_NAME="prod-edx-csrftoken",
        CROSS_DOMAIN_CSRF_COOKIE_DOMAIN=".edx.org"
    )
    is_secure_decorator = patch.object(WSGIRequest, 'is_secure', return_value=True)

    return feature_flag_decorator(
        settings_decorator(
            is_secure_decorator(func)
        )
    )


@skip_unless_lms
class EnrollmentCrossDomainTest(ModuleStoreTestCase):
    """Test cross-domain calls to the enrollment end-points. """

    USERNAME = "Bob"
    EMAIL = "bob@example.com"
    PASSWORD = "edx"
    REFERER = "https://www.edx.org"

    def setUp(self):
        """ Create a course and user, then log in. """
        super().setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)

        self.client = Client(enforce_csrf_checks=True)
        self.client.login(username=self.USERNAME, password=self.PASSWORD)

    @cross_domain_config
    def test_cross_domain_change_enrollment(self, *args):  # pylint: disable=unused-argument
        csrf_cookie = self._get_csrf_cookie()
        resp = self._cross_domain_post(csrf_cookie)

        # Expect that the request gets through successfully,
        # passing the CSRF checks (including the referer check).
        assert resp.status_code == 200

    @cross_domain_config
    def test_cross_domain_missing_csrf(self, *args):  # pylint: disable=unused-argument
        resp = self._cross_domain_post('invalid_csrf_token')
        assert resp.status_code == 403

    def _get_csrf_cookie(self):
        """Retrieve the cross-domain CSRF cookie. """
        url = reverse('courseenrollment', kwargs={
            'course_id': str(self.course.id)
        })
        resp = self.client.get(url, HTTP_REFERER=self.REFERER)
        assert resp.status_code == 200
        assert 'prod-edx-csrftoken' in resp.cookies
        return resp.cookies['prod-edx-csrftoken'].value

    def _cross_domain_post(self, csrf_cookie):
        """Perform a cross-domain POST request. """
        url = reverse('courseenrollments')
        params = json.dumps({
            'course_details': {
                'course_id': str(self.course.id),
            },
            'user': self.user.username
        })
        return self.client.post(
            url, params, content_type='application/json',
            HTTP_REFERER=self.REFERER,
            HTTP_X_CSRFTOKEN=csrf_cookie
        )


@ddt.ddt
@skip_unless_lms
class UnenrollmentTest(EnrollmentTestMixin, ModuleStoreTestCase):
    """
    Tests unenrollment functionality. The API being tested is intended to
    unenroll a learner from all of their courses.g
    """
    USERNAME = "Bob"
    EMAIL = "bob@example.com"
    PASSWORD = "edx"

    ENABLED_CACHES = ['default', 'mongo_metadata_inheritance', 'loc_cache']
    ENABLED_SIGNALS = ['course_published']

    def setUp(self):
        """ Create a course and user, then log in. """
        super().setUp()
        self.superuser = SuperuserFactory()
        self.superuser_client = Client()
        # Pass emit_signals when creating the course so it would be cached
        # as a CourseOverview. Enrollments require a cached CourseOverview.
        self.first_org_course = CourseFactory.create(emit_signals=True, org="org", course="course", run="run")
        self.other_first_org_course = CourseFactory.create(emit_signals=True, org="org", course="course2", run="run2")
        self.second_org_course = CourseFactory.create(emit_signals=True, org="org2", course="course3", run="run3")
        self.third_org_course = CourseFactory.create(emit_signals=True, org="org3", course="course4", run="run4")

        self.courses = [
            self.first_org_course, self.other_first_org_course, self.second_org_course, self.third_org_course
        ]

        self.orgs = {"org", "org2", "org3"}

        for course in self.courses:
            CourseModeFactory.create(
                course_id=str(course.id),
                mode_slug=CourseMode.DEFAULT_MODE_SLUG,
                mode_display_name=CourseMode.DEFAULT_MODE,
            )

        self.user = UserFactory.create(
            username=self.USERNAME,
            email=self.EMAIL,
            password=self.PASSWORD,
        )
        self.client.login(username=self.USERNAME, password=self.PASSWORD)
        for course in self.courses:
            self.assert_enrollment_status(course_id=str(course.id), username=self.USERNAME, is_active=True)

    def _create_test_retirement(self, user=None):
        """
        Helper method to create a RetirementStatus with useful defaults
        """
        RetirementState.objects.create(
            state_name='PENDING',
            state_execution_order=1,
            is_dead_end_state=False,
            required=False
        )
        if user is None:
            user = UserFactory()
        return UserRetirementStatus.create_retirement(user)

    def build_jwt_headers(self, user):
        """
        Helper function for creating headers for the JWT authentication.
        """
        token = create_jwt_for_user(user)
        headers = {'HTTP_AUTHORIZATION': 'JWT ' + token}

        return headers

    def test_deactivate_enrollments(self):
        self._assert_active()
        self._create_test_retirement(self.user)
        response = self._submit_unenroll(self.user.username)
        assert response.status_code == status.HTTP_200_OK
        data = json.loads(response.content.decode('utf-8'))
        # order doesn't matter so compare sets
        assert set(data) == self.orgs
        self._assert_inactive()

    def test_deactivate_enrollments_no_retirement_status(self):
        self._assert_active()
        response = self._submit_unenroll(self.user.username)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_deactivate_enrollments_unauthorized(self):
        self._assert_active()
        response = self._submit_unenroll(self.user.username, submitting_user=self.user, client=self.client)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        self._assert_active()

    def test_deactivate_enrollments_no_username(self):
        self._assert_active()
        response = self._submit_unenroll(None)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = json.loads(response.content.decode('utf-8'))
        assert data == 'Username not specified.'
        self._assert_active()

    def test_deactivate_enrollments_empty_username(self):
        self._assert_active()
        self._create_test_retirement(self.user)
        response = self._submit_unenroll("")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        self._assert_active()

    def test_deactivate_enrollments_invalid_username(self):
        self._assert_active()
        self._create_test_retirement(self.user)
        response = self._submit_unenroll("a made up username")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        self._assert_active()

    def test_deactivate_enrollments_called_twice(self):
        self._assert_active()
        self._create_test_retirement(self.user)
        response = self._submit_unenroll(self.user.username)
        assert response.status_code == status.HTTP_200_OK
        response = self._submit_unenroll(self.user.username)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert response.content.decode('utf-8') == ''
        self._assert_inactive()

    def _assert_active(self):
        for course in self.courses:
            assert CourseEnrollment.is_enrolled(self.user, course.id)
            _, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, course.id)
            assert is_active

    def _assert_inactive(self):
        for course in self.courses:
            _, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, course.id)
            assert not is_active

    def _submit_unenroll(self, unenrolling_username, submitting_user=None, client=None):
        """ Submit enrollment, by default as superuser. """
        # Provide both or neither of the overrides
        assert (submitting_user is None) == (client is None)

        # Avoid mixing cookies between two users
        client = client or self.superuser_client
        submitting_user = submitting_user or self.superuser

        data = {}
        if unenrolling_username is not None:
            data['username'] = unenrolling_username

        url = reverse('unenrollment')
        headers = self.build_jwt_headers(submitting_user)
        return client.post(url, json.dumps(data), content_type='application/json', **headers)


@ddt.ddt
@skip_unless_lms
class UserRoleTest(ModuleStoreTestCase):
    """
    Tests the API call to list user roles.
    """
    USERNAME = "Bob"
    EMAIL = "bob@example.com"
    STAFF_USERNAME = "Bobstaff"
    STAFF_EMAIL = "bobStaff@example.com"
    PASSWORD = "edx"

    ENABLED_CACHES = ['default']

    def setUp(self):
        """ Create a course and user, then log in. """
        super().setUp()
        self.course1 = CourseFactory.create(emit_signals=True, org="org1", course="course1", run="run1")
        self.course2 = CourseFactory.create(emit_signals=True, org="org2", course="course2", run="run2")
        self.user = UserFactory.create(
            username=self.USERNAME,
            email=self.EMAIL,
            password=self.PASSWORD,
        )
        self.staff_user = UserFactory.create(
            username=self.STAFF_USERNAME,
            email=self.STAFF_EMAIL,
            password=self.PASSWORD,
            is_staff=True,
        )
        self.client.login(username=self.USERNAME, password=self.PASSWORD)

    def _create_expected_role_dict(self, course, role):
        """ Creates the expected role dict object that the view should return """
        return {
            'course_id': str(course.id),
            'org': course.org,
            'role': role.ROLE,
        }

    def _assert_roles(self, expected_roles, is_staff, course_id=None):
        """ Asserts that the api call is successful and returns the expected roles """
        if course_id is not None:
            response = self.client.get(reverse('roles'), {'course_id': course_id})
        else:
            response = self.client.get(reverse('roles'))
        assert response.status_code == status.HTTP_200_OK
        response_data = json.loads(response.content.decode('utf-8'))
        sort_by_role_id = lambda r: r['course_id']
        response_data['roles'] = sorted(response_data['roles'], key=sort_by_role_id)
        expected_roles = sorted(expected_roles, key=sort_by_role_id)
        expected = {'roles': expected_roles, 'is_staff': is_staff}
        assert response_data == expected

    def _login(self, is_staff):
        """ If is_staff is true, logs in the staff user. Otherwise, logs in the non-staff user """
        logged_in_user = self.staff_user if is_staff else self.user
        self.client.login(username=logged_in_user.username, password=self.PASSWORD)
        return logged_in_user

    def test_not_logged_in(self):
        self.client.logout()
        response = self.client.get(reverse('roles'))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @ddt.data(True, False)
    def test_roles_no_roles(self, is_staff):
        self._login(is_staff)
        self._assert_roles([], is_staff)

    @ddt.data(True, False)
    def test_roles(self, is_staff):
        logged_in_user = self._login(is_staff)
        role1 = CourseStaffRole(self.course1.id)
        role1.add_users(logged_in_user)
        expected_role1 = self._create_expected_role_dict(self.course1, role1)
        expected_roles = [expected_role1]
        self._assert_roles(expected_roles, is_staff)
        role2 = CourseStaffRole(self.course2.id)
        role2.add_users(logged_in_user)
        expected_role2 = self._create_expected_role_dict(self.course2, role2)
        expected_roles.append(expected_role2)
        self._assert_roles(expected_roles, is_staff)

    def test_roles_filter(self):
        role1 = CourseStaffRole(self.course1.id)
        role1.add_users(self.user)
        expected_role1 = self._create_expected_role_dict(self.course1, role1)
        role2 = CourseStaffRole(self.course2.id)
        role2.add_users(self.user)
        expected_role2 = self._create_expected_role_dict(self.course2, role2)
        self._assert_roles([expected_role1], False, course_id=str(self.course1.id))
        self._assert_roles([expected_role2], False, course_id=str(self.course2.id))

    def test_roles_exception(self):
        with patch('openedx.core.djangoapps.enrollments.api.get_user_roles') as mock_get_user_roles:
            mock_get_user_roles.side_effect = Exception()
            response = self.client.get(reverse('roles'))
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            expected_response = {
                "message": (
                    "An error occurred while retrieving roles for user '{username}"
                ).format(username=self.user.username)
            }
            response_data = json.loads(response.content.decode('utf-8'))
            assert response_data == expected_response


@ddt.ddt
@skip_unless_lms
class CourseEnrollmentsApiListTest(APITestCase, ModuleStoreTestCase):
    """
    Test the course enrollments list API.
    """
    CREATED_DATA = datetime.datetime(2018, 1, 1, 0, 0, 1, tzinfo=ZoneInfo("UTC"))

    def setUp(self):
        super().setUp()
        self.rate_limit_config = RateLimitConfiguration.current()
        self.rate_limit_config.enabled = False
        self.rate_limit_config.save()

        throttle = EnrollmentUserThrottle()
        self.rate_limit, __ = throttle.parse_rate(throttle.rate)

        self.course = CourseFactory.create(org='e', number='d', run='X', emit_signals=True)
        self.course2 = CourseFactory.create(org='x', number='y', run='Z', emit_signals=True)

        for mode_slug in ('honor', 'verified', 'audit'):
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode_slug,
                mode_display_name=mode_slug
            )

        self.staff_user = AdminFactory(
            username='staff',
            email='staff@example.com',
            password='edx'
        )

        self.student1 = UserFactory(
            username='student1',
            email='student1@example.com',
            password='edx'
        )

        self.student2 = UserFactory(
            username='student2',
            email='student2@example.com',
            password='edx'
        )

        self.student3 = UserFactory(
            username='student3',
            email='student3@example.com',
            password='edx'
        )

        with freeze_time(self.CREATED_DATA):
            data.create_course_enrollment(
                self.student1.username,
                str(self.course.id),
                'honor',
                True
            )
            data.create_course_enrollment(
                self.student2.username,
                str(self.course.id),
                'honor',
                True
            )
            data.create_course_enrollment(
                self.student3.username,
                str(self.course2.id),
                'verified',
                True
            )
            data.create_course_enrollment(
                self.student2.username,
                str(self.course2.id),
                'honor',
                True
            )
            data.create_course_enrollment(
                self.staff_user.username,
                str(self.course2.id),
                'verified',
                True
            )
        self.url = reverse('courseenrollmentsapilist')

    def _login_as_staff(self):
        self.client.login(username=self.staff_user.username, password='edx')

    def _make_request(self, query_params=None):
        return self.client.get(self.url, query_params)

    def _assert_list_of_enrollments(self, query_params=None, expected_status=status.HTTP_200_OK, error_fields=None):
        """
        Make a request to the CourseEnrolllmentApiList endpoint and run assertions on the response
        using the optional parameters 'query_params', 'expected_status' and 'error_fields'.
        """
        response = self._make_request(query_params)
        assert response.status_code == expected_status
        content = json.loads(response.content.decode('utf-8'))
        if error_fields is not None:
            assert 'field_errors' in content
            for error_field in error_fields:
                assert error_field in content['field_errors']
        return content

    def test_user_not_authenticated(self):
        self.client.logout()
        response = self.client.get(self.url, {'course_id': quote(str(self.course.id))})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_user_not_authorized(self):
        self.client.login(username=self.student1.username, password='edx')
        response = self.client.get(self.url, {'course_id': quote(str(self.course.id))})
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @ddt.data(
        ({'course_id': '1'}, ['course_id', ]),
        ({'course_id': '1', 'username': 'staff'}, ['course_id', ]),
        ({'username': '1*2'}, ['username', ]),
        ({'username': '1*2', 'course_id': 'org.0/course_0/Run_0'}, ['username', ]),
        ({'username': '1*2', 'course_id': '1'}, ['username', 'course_id']),
        ({'username': ','.join(str(x) for x in range(101))}, ['username', ])
    )
    @ddt.unpack
    def test_query_string_parameters_invalid_errors(self, query_params, error_fields):
        self._login_as_staff()
        self._assert_list_of_enrollments(query_params, status.HTTP_400_BAD_REQUEST, error_fields)

    @ddt.data(
        # Non-existent user
        ({'username': 'nobody'},),
        ({'username': 'nobody', 'course_id': 'e/d/X'},),

        # Non-existent course
        ({'course_id': 'a/b/c'},),
        ({'course_id': 'a/b/c', 'username': 'student1'},),

        # Non-existent course and user
        ({'course_id': 'a/b/c', 'username': 'dummy'},)
    )
    @ddt.unpack
    def test_non_existent_course_user(self, query_params):
        self._login_as_staff()
        content = self._assert_list_of_enrollments(query_params, status.HTTP_200_OK)
        assert len(content['results']) == 0

    @ddt.file_data('fixtures/course-enrollments-api-list-valid-data.json')
    @ddt.unpack
    def test_response_valid_queries(self, args):
        query_params = args[0]
        expected_results = args[1]

        self._login_as_staff()
        content = self._assert_list_of_enrollments(query_params, status.HTTP_200_OK)
        results = content['results']

        self.assertCountEqual(results, expected_results)


@ddt.ddt
@skip_unless_lms
class EnrollmentAllowedViewTest(APITestCase):
    """
    Test the view that allows the retrieval and creation of enrollment
    allowed for a given user email and course id.
    """

    def setUp(self):
        self.url = reverse('courseenrollmentallowed')
        self.staff_user = AdminFactory(
            username='staff',
            email='staff@example.com',
            password='edx'
        )
        self.student1 = UserFactory(
            username='student1',
            email='student1@example.com',
            password='edx'
        )
        self.data = {
            'email': 'new-student@example.com',
            'course_id': 'course-v1:edX+DemoX+Demo_Course'
        }
        self.staff_token = create_jwt_for_user(self.staff_user)
        self.student_token = create_jwt_for_user(self.student1)
        self.client.credentials(HTTP_AUTHORIZATION='JWT ' + self.staff_token)
        return super().setUp()

    @ddt.data(
        [{'email': 'new-student@example.com', 'course_id': 'course-v1:edX+DemoX+Demo_Course'}, status.HTTP_201_CREATED],
        [{'course_id': 'course-v1:edX+DemoX+Demo_Course'}, status.HTTP_400_BAD_REQUEST],
        [{'email': 'new-student@example.com'}, status.HTTP_400_BAD_REQUEST],
    )
    @ddt.unpack
    def test_post_enrollment_allowed(self, data, expected_result):
        """
        Expected results:
        - 201: If the request has email and course_id.
        - 400: If the request has not.
        """
        response = self.client.post(self.url, data)
        assert response.status_code == expected_result

    def test_post_enrollment_allowed_without_staff(self):
        """
        Expected result:
        - 403: Get when I am not staff.
        """
        self.client.credentials(HTTP_AUTHORIZATION='JWT ' + self.student_token)
        response = self.client.post(self.url, self.data)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_enrollment_allowed_empty(self):
        """
        Expected result:
        - Get the enrollment allowed from the request.user.
        """
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_200_OK

    def test_get_enrollment_allowed(self):
        """
        Expected result:
        - Get the course enrollment allows.
        """
        response = self.client.post(path=self.url, data=self.data)
        response = self.client.get(self.url, {"email": "new-student@example.com"})
        self.assertContains(response, 'new-student@example.com', status_code=status.HTTP_200_OK)

    def test_get_enrollment_allowed_without_staff(self):
        """
        Expected result:
        - 403: Get when I am not staff.
        """
        self.client.credentials(HTTP_AUTHORIZATION='JWT ' + self.student_token)
        response = self.client.get(self.url, {"email": "new-student@example.com"})
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @ddt.data(
        [{'email': 'new-student@example.com',
          'course_id': 'course-v1:edX+DemoX+Demo_Course'},
         status.HTTP_204_NO_CONTENT],
        [{'email': 'other-student@example.com',
          'course_id': 'course-v1:edX+DemoX+Demo_Course'},
         status.HTTP_404_NOT_FOUND],
        [{'course_id': 'course-v1:edX+DemoX+Demo_Course'},
         status.HTTP_400_BAD_REQUEST],
    )
    @ddt.unpack
    def test_delete_enrollment_allowed(self, delete_data, expected_result):
        """
        Expected results:
        - 204: Enrollment allowed deleted.
        - 404: Not found, the course enrollment allowed doesn't exists.
        - 400: Bad request, missing data.
        """
        self.client.post(self.url, self.data)
        response = self.client.delete(self.url, delete_data)
        assert response.status_code == expected_result
