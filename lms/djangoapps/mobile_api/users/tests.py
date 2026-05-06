"""
Tests for users API
"""


import datetime
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import parse_qs

import ddt
import pytz
from completion.models import BlockCompletion
from completion.test_utils import CompletionWaffleTestMixin, submit_completions_for_testing
from django.conf import settings
from django.db import transaction
from django.template import defaultfilters
from django.test import RequestFactory, override_settings
from django.utils import timezone
from django.utils.timezone import now
from milestones.tests.utils import MilestonesTestCaseMixin
from opaque_keys.edx.keys import CourseKey
from rest_framework import status

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.util.milestones_helpers import set_prerequisite_courses
from common.djangoapps.util.testing import UrlResetMixin
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from lms.djangoapps.courseware.access_response import MilestoneAccessError, StartDateError, VisibilityError
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.mobile_api.models import MobileConfig
from lms.djangoapps.mobile_api.testutils import (
    MobileAPITestCase,
    MobileAuthTestMixin,
    MobileAuthUserTestMixin,
    MobileCourseAccessTestMixin,
)
from lms.djangoapps.mobile_api.users.enums import EnrollmentStatuses
from lms.djangoapps.mobile_api.utils import API_V05, API_V1, API_V2, API_V3, API_V4
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration
from openedx.core.lib.courses import course_image_url
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig
from openedx.features.course_experience.tests.views.helpers import add_course_mode
from xmodule.course_block import DEFAULT_START_DATE  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import (  # pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)

from .. import errors
from .serializers import CourseEnrollmentSerializer, CourseEnrollmentSerializerv05


@ddt.ddt
class TestUserDetailApi(MobileAPITestCase, MobileAuthUserTestMixin):
    """
    Tests for /api/mobile/{api_version}/users/<user_name>...
    """
    REVERSE_INFO = {'name': 'user-detail', 'params': ['username', 'api_version']}

    @ddt.data(API_V05, API_V1)
    def test_success(self, api_version):
        self.login()

        response = self.api_response(api_version=api_version)
        assert response.data['username'] == self.user.username
        assert response.data['email'] == self.user.email


@ddt.ddt
class TestUserInfoApi(MobileAPITestCase, MobileAuthTestMixin):
    """
    Tests for /api/mobile/{api_version}/my_user_info
    """
    REVERSE_INFO = {'name': 'user-info', 'params': ['api_version']}

    @ddt.data(API_V05, API_V1)
    def test_success(self, api_version):
        """Verify the endpoint redirects to the user detail endpoint"""
        self.login()

        response = self.api_response(expected_response_code=302, api_version=api_version)
        assert self.username in response['location']

    @ddt.data(API_V05, API_V1)
    def test_last_loggedin_updated(self, api_version):
        """Verify that a user's last logged in value updates after hitting the my_user_info endpoint"""
        self.login()

        self.user.refresh_from_db()
        last_login_before = self.user.last_login

        # just hit the api endpoint; we don't care about the response here (tested previously)
        self.api_response(expected_response_code=302, api_version=api_version)

        self.user.refresh_from_db()
        last_login_after = self.user.last_login
        assert last_login_after > last_login_before


@ddt.ddt
@override_settings(MKTG_URLS={'ROOT': 'dummy-root'})
class TestUserEnrollmentApi(UrlResetMixin, MobileAPITestCase, MobileAuthUserTestMixin,
                            MobileCourseAccessTestMixin, MilestonesTestCaseMixin):
    """
    Tests for /api/mobile/{api_version}/users/<user_name>/course_enrollments/
    """
    REVERSE_INFO = {'name': 'courseenrollment-detail', 'params': ['username', 'api_version']}
    ALLOW_ACCESS_TO_UNRELEASED_COURSE = True
    ALLOW_ACCESS_TO_MILESTONE_COURSE = True
    ALLOW_ACCESS_TO_NON_VISIBLE_COURSE = True
    NEXT_WEEK = datetime.datetime.now(pytz.UTC) + datetime.timedelta(days=7)
    LAST_WEEK = datetime.datetime.now(pytz.UTC) - datetime.timedelta(days=7)
    THREE_YEARS_AGO = now() - datetime.timedelta(days=(365 * 3))
    ADVERTISED_START = "Spring 2016"
    ENABLED_SIGNALS = ['course_published']
    DATES = {
        'next_week': NEXT_WEEK,
        'last_week': LAST_WEEK,
        'default_start_date': DEFAULT_START_DATE,
    }

    @patch.dict(settings.FEATURES, {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()

    def verify_success(self, response):
        """
        Verifies user course enrollment response for success
        """
        super().verify_success(response)
        courses = response.data
        assert len(courses) == 1

        found_course = courses[0]['course']
        assert f'courses/{self.course.id}/about' in found_course['course_about']
        assert f'course_info/{self.course.id}/updates' in found_course['course_updates']
        assert f'course_info/{self.course.id}/handouts' in found_course['course_handouts']
        assert found_course['id'] == str(self.course.id)
        assert courses[0]['mode'] == CourseMode.DEFAULT_MODE_SLUG
        assert courses[0]['course']['subscription_id'] == self.course.clean_id(padding_char='_')

        expected_course_image_url = course_image_url(self.course)
        assert expected_course_image_url is not None
        assert expected_course_image_url in found_course['course_image']
        assert expected_course_image_url in found_course['media']['course_image']['uri']

    def verify_failure(self, response, error_type=None):
        assert response.status_code == 200
        courses = response.data
        assert len(courses) == 0

    @patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': True})
    @ddt.data(API_V05, API_V1, API_V2)
    def test_sort_order(self, api_version):
        self.login()

        num_courses = 3
        courses = []
        for course_index in range(num_courses):
            courses.append(CourseFactory.create(mobile_available=True))
            self.enroll(courses[course_index].id)

        # verify courses are returned in the order of enrollment, with most recently enrolled first.
        response = self.api_response(api_version=api_version)
        enrollments = response.data['enrollments'] if api_version == API_V2 else response.data

        for course_index in range(num_courses):
            assert enrollments[course_index]['course']['id'] ==\
                   str(courses[((num_courses - course_index) - 1)].id)

    @ddt.data(API_V05, API_V1, API_V2)
    @patch.dict(settings.FEATURES, {
        'ENABLE_PREREQUISITE_COURSES': True,
        'DISABLE_START_DATES': False,
        'ENABLE_MKTG_SITE': True,
    })
    def test_courseware_access(self, api_version):
        self.login()

        course_with_prereq = CourseFactory.create(start=self.LAST_WEEK, mobile_available=True)
        prerequisite_course = CourseFactory.create()
        set_prerequisite_courses(course_with_prereq.id, [str(prerequisite_course.id)])

        # Create list of courses with various expected courseware_access responses and corresponding expected codes
        courses = [
            course_with_prereq,
            CourseFactory.create(start=self.NEXT_WEEK, mobile_available=True),
            CourseFactory.create(visible_to_staff_only=True, mobile_available=True),
            CourseFactory.create(start=self.LAST_WEEK, mobile_available=True, visible_to_staff_only=False),
        ]

        expected_error_codes = [
            MilestoneAccessError().error_code,  # 'unfulfilled_milestones'
            StartDateError(self.NEXT_WEEK).error_code,  # 'course_not_started'
            VisibilityError().error_code,  # 'not_visible_to_user'
            None,
        ]

        # Enroll in all the courses
        for course in courses:
            self.enroll(course.id)

        # Verify courses have the correct response through error code. Last enrolled course is first course in response
        response = self.api_response(api_version=api_version)
        enrollments = response.data['enrollments'] if api_version == API_V2 else response.data

        for course_index in range(len(courses)):
            result = enrollments[course_index]['course']['courseware_access']
            assert result['error_code'] == expected_error_codes[::(- 1)][course_index]

            if result['error_code'] is not None:
                assert not result['has_access']

    @ddt.data(
        ('next_week', ADVERTISED_START, ADVERTISED_START, "string", API_V05),
        ('next_week', ADVERTISED_START, ADVERTISED_START, "string", API_V1),
        ('next_week', ADVERTISED_START, ADVERTISED_START, "string", API_V2),
        ('next_week', None, defaultfilters.date(NEXT_WEEK, "DATE_FORMAT"), "timestamp", API_V05),
        ('next_week', None, defaultfilters.date(NEXT_WEEK, "DATE_FORMAT"), "timestamp", API_V1),
        ('next_week', None, defaultfilters.date(NEXT_WEEK, "DATE_FORMAT"), "timestamp", API_V2),
        ('next_week', '', defaultfilters.date(NEXT_WEEK, "DATE_FORMAT"), "timestamp", API_V05),
        ('next_week', '', defaultfilters.date(NEXT_WEEK, "DATE_FORMAT"), "timestamp", API_V1),
        ('next_week', '', defaultfilters.date(NEXT_WEEK, "DATE_FORMAT"), "timestamp", API_V2),
        ('default_start_date', ADVERTISED_START, ADVERTISED_START, "string", API_V05),
        ('default_start_date', ADVERTISED_START, ADVERTISED_START, "string", API_V1),
        ('default_start_date', ADVERTISED_START, ADVERTISED_START, "string", API_V2),
        ('default_start_date', '', None, "empty", API_V05),
        ('default_start_date', '', None, "empty", API_V1),
        ('default_start_date', '', None, "empty", API_V2),
        ('default_start_date', None, None, "empty", API_V05),
        ('default_start_date', None, None, "empty", API_V1),
        ('default_start_date', None, None, "empty", API_V2),
    )
    @ddt.unpack
    @patch.dict(settings.FEATURES, {'DISABLE_START_DATES': False, 'ENABLE_MKTG_SITE': True})
    def test_start_type_and_display(self, start, advertised_start, expected_display, expected_type, api_version):
        """
        Tests that the correct start_type and start_display are returned in the
        case the course has not started
        """
        self.login()
        course = CourseFactory.create(start=self.DATES[start], advertised_start=advertised_start, mobile_available=True)
        self.enroll(course.id)

        response = self.api_response(api_version=api_version)
        courses = response.data['enrollments'] if api_version == API_V2 else response.data
        assert courses[0]['course']['start_type'] == expected_type
        assert courses[0]['course']['start_display'] == expected_display

    @ddt.data(API_V05, API_V1, API_V2)
    @patch.dict(settings.FEATURES, {"ENABLE_DISCUSSION_SERVICE": True, 'ENABLE_MKTG_SITE': True})
    def test_discussion_url(self, api_version):
        self.login_and_enroll()

        response = self.api_response(api_version=api_version)
        courses = response.data['enrollments'] if api_version == API_V2 else response.data
        response_discussion_url = courses[0]['course']['discussion_url']
        assert f'/api/discussion/v1/courses/{self.course.id}' in response_discussion_url

    @ddt.data(API_V05, API_V1, API_V2)
    def test_org_query(self, api_version):
        self.login()

        # Create list of courses with various organizations
        courses = [
            CourseFactory.create(org='edX', mobile_available=True),
            CourseFactory.create(org='edX', mobile_available=True),
            CourseFactory.create(org='edX', mobile_available=True, visible_to_staff_only=True),
            CourseFactory.create(org='Proversity.org', mobile_available=True),
            CourseFactory.create(org='MITx', mobile_available=True),
            CourseFactory.create(org='HarvardX', mobile_available=True),
        ]

        # Enroll in all the courses
        for course in courses:
            self.enroll(course.id)

        response = self.api_response(data={'org': 'edX'}, api_version=api_version)
        courses = response.data['enrollments'] if api_version == API_V2 else response.data

        # Test for 3 expected courses
        assert len(courses) == 3

        # Verify only edX courses are returned
        for entry in courses:
            assert entry['course']['org'] == 'edX'

    @ddt.data(API_V05, API_V1, API_V2)
    @patch('lms.djangoapps.mobile_api.users.views.get_current_site_orgs', return_value=['edX'])
    def test_filter_by_current_site_orgs(self, api_version, get_current_site_orgs_mock):
        self.login()

        # Create list of courses with various organizations
        courses = [
            CourseFactory.create(org='edX', mobile_available=True),
            CourseFactory.create(org='edX', mobile_available=True),
            CourseFactory.create(org='edX', mobile_available=True, visible_to_staff_only=True),
            CourseFactory.create(org='Proversity.org', mobile_available=True),
            CourseFactory.create(org='MITx', mobile_available=True),
            CourseFactory.create(org='HarvardX', mobile_available=True),
        ]

        # Enroll in all the courses
        for course in courses:
            self.enroll(course.id)

        response = self.api_response(api_version=api_version)
        courses = response.data['enrollments'] if api_version == API_V2 else response.data

        # Test for 3 expected courses
        self.assertEqual(len(courses), 3)  # noqa: PT009

        # Verify only edX courses are returned
        for entry in courses:
            self.assertEqual(entry['course']['org'], 'edX')  # noqa: PT009

    def create_enrollment(self, expired):
        """
        Create an enrollment
        """
        if expired:
            course = CourseFactory.create(start=self.THREE_YEARS_AGO, mobile_available=True)
            enrollment = CourseEnrollmentFactory.create(
                user=self.user,
                course_id=course.id
            )
            enrollment.created = self.THREE_YEARS_AGO + datetime.timedelta(days=1)
            enrollment.save()
        else:
            course = CourseFactory.create(start=self.LAST_WEEK, mobile_available=True)
            self.enroll(course.id)

        add_course_mode(course, mode_slug=CourseMode.AUDIT)
        add_course_mode(course)

    def _get_enrollment_data(self, api_version, expired):
        """
        Login, Create enrollments and get data through enrollments api.
        """
        self.login()
        self.create_enrollment(expired)
        response = self.api_response(api_version=api_version).data
        result = response['enrollments'] if api_version == API_V2 else response

        return result

    def _assert_enrollment_results(self, api_version, courses, num_courses_returned, gating_enabled=True):  # pylint: disable=missing-function-docstring
        assert len(courses) == num_courses_returned

        if api_version == API_V05:
            if num_courses_returned:
                assert 'audit_access_expires' not in courses[0]
        else:
            assert 'audit_access_expires' in courses[0]

            for course_mode in courses[0]['course_modes']:
                assert 'android_sku' in course_mode
                assert 'ios_sku' in course_mode
                assert 'min_price' in course_mode

            if gating_enabled:
                assert courses[0].get('audit_access_expires') is not None

    @ddt.data(
        (API_V05, True, 0),
        (API_V05, False, 1),
        (API_V1, True, 1),
        (API_V1, False, 1),
        (API_V2, True, 1),
        (API_V2, False, 1),
    )
    @ddt.unpack
    def test_enrollment_with_gating(self, api_version, expired, num_courses_returned):
        """
        Test that expired courses are only returned in v1 of API
        when waffle flag enabled, and un-expired courses always returned
        """
        CourseDurationLimitConfig.objects.create(enabled=True, enabled_as_of=datetime.datetime(2015, 1, 1))
        courses = self._get_enrollment_data(api_version, expired)
        self._assert_enrollment_results(api_version, courses, num_courses_returned, True)

    @ddt.data(
        (API_V05, True, 1),
        (API_V05, False, 1),
        (API_V1, True, 1),
        (API_V1, False, 1),
        (API_V2, True, 1),
        (API_V2, False, 1),
    )
    @ddt.unpack
    def test_enrollment_no_gating(self, api_version, expired, num_courses_returned):
        """
        Test that expired and non-expired courses are returned if the waffle flag is disabled,
        regardless of the API version
        """
        CourseDurationLimitConfig.objects.create(enabled=False)
        courses = self._get_enrollment_data(api_version, expired)
        self._assert_enrollment_results(api_version, courses, num_courses_returned, False)

    def test_enrollment_with_configs(self):
        """
        Test that configs are returned in proper structure in enrollments api.
        """
        self.login_and_enroll()

        MobileConfig(name='simple config', value='simple').save()
        MobileConfig(name='iap_config', value='iap').save()
        MobileConfig(name='iap config', value='false iap').save()
        expected_result = {
            'iap_configs': {'iap_config': 'iap'},
            'simple config': 'simple',
            'iap config': 'false iap',
        }

        response = self.api_response(api_version=API_V2)
        self.assertDictEqual(response.data['configs'], expected_result)  # noqa: PT009
        assert 'enrollments' in response.data

    def test_pagination_enrollment(self):
        """
        Test pagination for UserCourseEnrollmentsList view v3
        for 3rd version of this view we use DefaultPagination

        Test for /api/mobile/{api_version}/users/<user_name>/course_enrollments/
        api_version = v3
        """
        self.login()
        # Create and enroll to 15 courses
        courses = [CourseFactory.create(org="my_org", mobile_available=True) for _ in range(15)]
        for course in courses:
            self.enroll(course.id)

        response = self.api_response(api_version=API_V3)
        assert response.status_code == 200
        assert response.data["enrollments"]["count"] == 15
        assert response.data["enrollments"]["num_pages"] == 2
        assert response.data["enrollments"]["current_page"] == 1
        assert len(response.data["enrollments"]["results"]) == 10
        assert "next" in response.data["enrollments"]
        assert "previous" in response.data["enrollments"]

    def test_student_dont_have_enrollments(self):
        """
        Testing modified `UserCourseEnrollmentsList` view with api_version == v4.
        """
        self.login()
        expected_result = {
            'configs': {
                'iap_configs': {}
            },
            'user_timezone': 'UTC',
            'enrollments': {
                'next': None,
                'previous': None,
                'count': 0,
                'num_pages': 1,
                'current_page': 1,
                'start': 0,
                'results': []
            }
        }

        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertDictEqual(expected_result, response.data)  # noqa: PT009
        self.assertNotIn('primary', response.data)  # noqa: PT009

    def test_student_have_one_enrollment(self):
        """
        Testing modified `UserCourseEnrollmentsList` view with api_version == v4.
        """
        self.login()
        course = CourseFactory.create(org="edx", mobile_available=True)
        self.enroll(course.id)
        expected_enrollments = {
            'next': None,
            'previous': None,
            'count': 0,
            'num_pages': 1,
            'current_page': 1,
            'start': 0,
            'results': []
        }

        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertDictEqual(expected_enrollments, response.data['enrollments'])  # noqa: PT009
        self.assertIn('primary', response.data)  # noqa: PT009
        self.assertEqual(str(course.id), response.data['primary']['course']['id'])  # noqa: PT009

    def test_student_have_two_enrollments(self):
        """
        Testing modified `UserCourseEnrollmentsList` view with api_version == v4.
        """
        self.login()
        course_first = CourseFactory.create(org="edx", mobile_available=True)
        course_second = CourseFactory.create(org="edx", mobile_available=True)
        self.enroll(course_first.id)
        self.enroll(course_second.id)

        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(len(response.data['enrollments']['results']), 1)  # noqa: PT009
        self.assertEqual(response.data['enrollments']['count'], 1)  # noqa: PT009
        self.assertEqual(response.data['enrollments']['results'][0]['course']['id'], str(course_first.id))  # noqa: PT009  # pylint: disable=line-too-long
        self.assertIn('primary', response.data)  # noqa: PT009
        self.assertEqual(response.data['primary']['course']['id'], str(course_second.id))  # noqa: PT009

    def test_student_have_more_then_ten_enrollments(self):
        """
        Testing modified `UserCourseEnrollmentsList` view with api_version == v4.
        """
        self.login()
        courses = [CourseFactory.create(org="edx", mobile_available=True) for _ in range(15)]
        for course in courses:
            self.enroll(course.id)
        latest_enrolment = CourseFactory.create(org="edx", mobile_available=True)
        self.enroll(latest_enrolment.id)

        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(response.data['enrollments']['count'], 15)  # noqa: PT009
        self.assertEqual(response.data['enrollments']['num_pages'], 3)  # noqa: PT009
        self.assertEqual(len(response.data['enrollments']['results']), 5)  # noqa: PT009
        self.assertIn('primary', response.data)  # noqa: PT009
        self.assertEqual(response.data['primary']['course']['id'], str(latest_enrolment.id))  # noqa: PT009

    def test_student_have_progress_in_old_course_and_enroll_newest_course(self):
        """
        Testing modified `UserCourseEnrollmentsList` view with api_version == v4.
        """
        self.login()
        old_course = CourseFactory.create(org="edx", mobile_available=True)
        self.enroll(old_course.id)
        courses = [CourseFactory.create(org="edx", mobile_available=True) for _ in range(5)]
        for course in courses:
            self.enroll(course.id)
        new_course = CourseFactory.create(org="edx", mobile_available=True)
        self.enroll(new_course.id)

        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(response.data['enrollments']['count'], 6)  # noqa: PT009
        self.assertEqual(len(response.data['enrollments']['results']), 5)  # noqa: PT009
        # check that we have the new_course in primary section
        self.assertIn('primary', response.data)  # noqa: PT009
        self.assertEqual(response.data['primary']['course']['id'], str(new_course.id))  # noqa: PT009

        # doing progress in the old_course
        StudentModule.objects.create(
            student=self.user,
            course_id=old_course.id,
            module_state_key=old_course.location,
        )
        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(response.data['enrollments']['count'], 6)  # noqa: PT009
        self.assertEqual(len(response.data['enrollments']['results']), 5)  # noqa: PT009
        # check that now we have the old_course in primary section
        self.assertIn('primary', response.data)  # noqa: PT009
        self.assertEqual(response.data['primary']['course']['id'], str(old_course.id))  # noqa: PT009

        # enroll to the newest course
        newest_course = CourseFactory.create(org="edx", mobile_available=True)
        self.enroll(newest_course.id)

        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(response.data['enrollments']['count'], 7)  # noqa: PT009
        self.assertEqual(len(response.data['enrollments']['results']), 5)  # noqa: PT009
        # check that now we have the newest_course in primary section
        self.assertIn('primary', response.data)  # noqa: PT009
        self.assertEqual(response.data['primary']['course']['id'], str(newest_course.id))  # noqa: PT009

    def test_student_enrolled_only_not_mobile_available_courses(self):
        """
        Testing modified `UserCourseEnrollmentsList` view with api_version == v4.
        """
        self.login()
        courses = [CourseFactory.create(org="edx", mobile_available=False) for _ in range(3)]
        for course in courses:
            self.enroll(course.id)
        expected_result = {
            "configs": {
                "iap_configs": {}
            },
            "user_timezone": "UTC",
            "enrollments": {
                "next": None,
                "previous": None,
                "count": 0,
                "num_pages": 1,
                "current_page": 1,
                "start": 0,
                "results": []
            }
        }

        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertDictEqual(expected_result, response.data)  # noqa: PT009
        self.assertNotIn('primary', response.data)  # noqa: PT009

    def test_do_progress_in_not_mobile_available_course(self):
        """
        Testing modified `UserCourseEnrollmentsList` view with api_version == v4.
        """
        self.login()
        not_mobile_available = CourseFactory.create(org="edx", mobile_available=False)
        self.enroll(not_mobile_available.id)
        courses = [CourseFactory.create(org="edx", mobile_available=True) for _ in range(5)]
        for course in courses:
            self.enroll(course.id)
        new_course = CourseFactory.create(org="edx", mobile_available=True)
        self.enroll(new_course.id)

        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(response.data['enrollments']['count'], 5)  # noqa: PT009
        self.assertEqual(len(response.data['enrollments']['results']), 5)  # noqa: PT009
        # check that we have the new_course in primary section
        self.assertIn('primary', response.data)  # noqa: PT009
        self.assertEqual(response.data['primary']['course']['id'], str(new_course.id))  # noqa: PT009

        # doing progress in the not_mobile_available course
        StudentModule.objects.create(
            student=self.user,
            course_id=not_mobile_available.id,
            module_state_key=not_mobile_available.location,
        )
        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(response.data['enrollments']['count'], 5)  # noqa: PT009
        self.assertEqual(len(response.data['enrollments']['results']), 5)  # noqa: PT009
        # check that we have the new_course in primary section in the same way
        self.assertIn('primary', response.data)  # noqa: PT009
        self.assertEqual(response.data['primary']['course']['id'], str(new_course.id))  # noqa: PT009

    def test_pagination_for_user_enrollments_api_v4(self):
        """
        Tests `UserCourseEnrollmentsV4Pagination`, api_version == v4.
        """
        self.login()
        courses = [CourseFactory.create(org="my_org", mobile_available=True) for _ in range(15)]
        for course in courses:
            self.enroll(course.id)

        response = self.api_response(api_version=API_V4)
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(response.data['enrollments']['count'], 14)  # noqa: PT009
        self.assertEqual(response.data['enrollments']['num_pages'], 3)  # noqa: PT009
        self.assertEqual(response.data['enrollments']['current_page'], 1)  # noqa: PT009
        self.assertEqual(len(response.data['enrollments']['results']), 5)  # noqa: PT009
        self.assertIn('next', response.data['enrollments'])  # noqa: PT009
        self.assertIn('previous', response.data['enrollments'])  # noqa: PT009
        self.assertIn('primary', response.data)  # noqa: PT009

    def test_course_status_in_primary_obj_when_student_doesnt_have_progress(self):
        """
        Testing modified `UserCourseEnrollmentsList` view with api_version == v4.
        """
        self.login()
        course = CourseFactory.create(org="edx", mobile_available=True)
        self.enroll(course.id)

        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(response.data['primary']['course_status'], None)  # noqa: PT009

    @patch('lms.djangoapps.mobile_api.users.serializers.get_key_to_last_completed_block')
    def test_course_status_in_primary_obj_when_student_have_progress(
        self,
        get_last_completed_block_mock: MagicMock,
    ):
        """
        Testing modified `UserCourseEnrollmentsList` view with api_version == v4.
        """
        self.login()
        # create test course structure
        course = CourseFactory.create(org="edx", mobile_available=True)
        section = BlockFactory.create(
            parent=course,
            category="chapter",
            display_name="section",
        )
        subsection = BlockFactory.create(
            parent=section,
            category="sequential",
            display_name="subsection",
        )
        vertical = BlockFactory.create(
            parent=subsection,
            category="vertical",
            display_name="test unit",
        )
        problem = BlockFactory.create(
            parent=vertical,
            category="problem",
            display_name="problem",
        )
        self.enroll(course.id)
        get_last_completed_block_mock.return_value = problem.location
        expected_course_status = {
            'last_visited_module_id': str(subsection.location),
            'last_visited_module_path': [
                str(course.location),
                str(section.location),
                str(subsection.location),
            ],
            'last_visited_block_id': str(problem.location),
            'last_visited_unit_display_name': vertical.display_name,
        }

        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(response.data['primary']['course_status'], expected_course_status)  # noqa: PT009
        get_last_completed_block_mock.assert_called_once_with(self.user, course.id)

    def test_user_enrollment_api_v4_in_progress_status(self):
        """
        Testing
        """
        self.login()
        old_course = CourseFactory.create(
            org="edx",
            mobile_available=True,
            start=self.THREE_YEARS_AGO,
            end=self.LAST_WEEK
        )
        actual_course = CourseFactory.create(
            org="edx",
            mobile_available=True,
            start=self.LAST_WEEK,
            end=self.NEXT_WEEK
        )
        infinite_course = CourseFactory.create(
            org="edx",
            mobile_available=True,
            start=self.LAST_WEEK,
            end=None
        )

        self.enroll(old_course.id)
        self.enroll(actual_course.id)
        self.enroll(infinite_course.id)

        response = self.api_response(api_version=API_V4, data={'status': EnrollmentStatuses.IN_PROGRESS.value})
        enrollments = response.data['enrollments']

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(enrollments['count'], 2)  # noqa: PT009
        self.assertEqual(enrollments['results'][1]['course']['id'], str(actual_course.id))  # noqa: PT009
        self.assertEqual(enrollments['results'][0]['course']['id'], str(infinite_course.id))  # noqa: PT009
        self.assertNotIn('primary', response.data)  # noqa: PT009

    def test_user_enrollment_api_v4_completed_status(self):
        """
        Testing
        """
        self.login()
        old_course = CourseFactory.create(
            org="edx",
            mobile_available=True,
            start=self.THREE_YEARS_AGO,
            end=self.LAST_WEEK
        )
        actual_course = CourseFactory.create(
            org="edx",
            mobile_available=True,
            start=self.LAST_WEEK,
            end=self.NEXT_WEEK
        )
        infinite_course = CourseFactory.create(
            org="edx",
            mobile_available=True,
            start=self.LAST_WEEK,
            end=None
        )
        GeneratedCertificateFactory.create(
            user=self.user,
            course_id=infinite_course.id,
            status=CertificateStatuses.downloadable,
            mode='verified',
        )

        self.enroll(old_course.id)
        self.enroll(actual_course.id)
        self.enroll(infinite_course.id)

        response = self.api_response(api_version=API_V4, data={'status': EnrollmentStatuses.COMPLETED.value})
        enrollments = response.data['enrollments']

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(enrollments['count'], 1)  # noqa: PT009
        self.assertEqual(enrollments['results'][0]['course']['id'], str(infinite_course.id))  # noqa: PT009
        self.assertNotIn('primary', response.data)  # noqa: PT009

    def test_user_enrollment_api_v4_expired_status(self):
        """
        Testing
        """
        self.login()
        old_course = CourseFactory.create(
            org="edx",
            mobile_available=True,
            start=self.THREE_YEARS_AGO,
            end=self.LAST_WEEK
        )
        actual_course = CourseFactory.create(
            org="edx",
            mobile_available=True,
            start=self.LAST_WEEK,
            end=self.NEXT_WEEK
        )
        infinite_course = CourseFactory.create(
            org="edx",
            mobile_available=True,
            start=self.LAST_WEEK,
            end=None
        )
        self.enroll(old_course.id)
        self.enroll(actual_course.id)
        self.enroll(infinite_course.id)

        response = self.api_response(api_version=API_V4, data={'status': EnrollmentStatuses.EXPIRED.value})
        enrollments = response.data['enrollments']

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(enrollments['count'], 1)  # noqa: PT009
        self.assertEqual(enrollments['results'][0]['course']['id'], str(old_course.id))  # noqa: PT009
        self.assertNotIn('primary', response.data)  # noqa: PT009

    def test_user_enrollment_api_v4_expired_course_with_certificate(self):
        """
        Testing that the API returns a course with
        an expiration date in the past if the user has a certificate for this course.
        """
        self.login()
        expired_course = CourseFactory.create(
            org="edx",
            mobile_available=True,
            start=self.THREE_YEARS_AGO,
            end=self.LAST_WEEK
        )
        expired_course_with_cert = CourseFactory.create(
            org="edx",
            mobile_available=True,
            start=self.THREE_YEARS_AGO,
            end=self.LAST_WEEK
        )
        GeneratedCertificateFactory.create(
            user=self.user,
            course_id=expired_course_with_cert.id,
            status=CertificateStatuses.downloadable,
            mode='verified',
        )

        self.enroll(expired_course_with_cert.id)
        self.enroll(expired_course.id)

        response = self.api_response(api_version=API_V4, data={'status': EnrollmentStatuses.COMPLETED.value})
        enrollments = response.data['enrollments']

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(enrollments['count'], 1)  # noqa: PT009
        self.assertEqual(enrollments['results'][0]['course']['id'], str(expired_course_with_cert.id))  # noqa: PT009
        self.assertNotIn('primary', response.data)  # noqa: PT009

    def test_user_enrollment_api_v4_status_all(self):
        """
        Testing
        """
        self.login()
        old_course = CourseFactory.create(
            org="edx",
            mobile_available=True,
            start=self.THREE_YEARS_AGO,
            end=self.LAST_WEEK
        )
        actual_course = CourseFactory.create(
            org="edx",
            mobile_available=True,
            start=self.LAST_WEEK,
            end=self.NEXT_WEEK
        )
        infinite_course = CourseFactory.create(
            org="edx",
            mobile_available=True,
            start=self.LAST_WEEK,
            end=None
        )
        GeneratedCertificateFactory.create(
            user=self.user,
            course_id=infinite_course.id,
            status=CertificateStatuses.downloadable,
            mode='verified',
        )

        self.enroll(old_course.id)
        self.enroll(actual_course.id)
        self.enroll(infinite_course.id)

        response = self.api_response(api_version=API_V4, data={'status': EnrollmentStatuses.ALL.value})
        enrollments = response.data['enrollments']

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(enrollments['count'], 3)  # noqa: PT009
        self.assertEqual(enrollments['results'][0]['course']['id'], str(infinite_course.id))  # noqa: PT009
        self.assertEqual(enrollments['results'][1]['course']['id'], str(actual_course.id))  # noqa: PT009
        self.assertEqual(enrollments['results'][2]['course']['id'], str(old_course.id))  # noqa: PT009
        self.assertNotIn('primary', response.data)  # noqa: PT009

    def test_response_contains_primary_enrollment_assignments_info(self):
        self.login()
        course = CourseFactory.create(org='edx', mobile_available=True)
        self.enroll(course.id)

        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn('course_assignments', response.data['primary'])  # noqa: PT009
        self.assertIn('past_assignments', response.data['primary']['course_assignments'])  # noqa: PT009
        self.assertIn('future_assignments', response.data['primary']['course_assignments'])  # noqa: PT009
        self.assertListEqual(response.data['primary']['course_assignments']['past_assignments'], [])  # noqa: PT009
        self.assertListEqual(response.data['primary']['course_assignments']['future_assignments'], [])  # noqa: PT009

    @patch('lms.djangoapps.courseware.courses.get_course_assignments', return_value=[])
    def test_course_progress_in_primary_enrollment_with_no_assignments(
        self,
        get_course_assignment_mock: MagicMock,
    ) -> None:
        self.login()
        course = CourseFactory.create(org='edx', mobile_available=True)
        self.enroll(course.id)
        expected_course_progress = {'total_assignments_count': 0, 'assignments_completed': 0}

        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn('course_progress', response.data['primary'])  # noqa: PT009
        self.assertDictEqual(response.data['primary']['course_progress'], expected_course_progress)  # noqa: PT009

    @patch(
        'lms.djangoapps.mobile_api.users.serializers.CourseEnrollmentSerializerModifiedForPrimary'
        '.get_course_assignments'
    )
    @patch('lms.djangoapps.courseware.courses.get_course_assignments')
    def test_course_progress_in_primary_enrollment_with_assignments(
        self,
        get_course_assignment_mock: MagicMock,
        assignments_mock: MagicMock,
    ) -> None:
        self.login()
        course = CourseFactory.create(org='edx', mobile_available=True)
        self.enroll(course.id)
        course_assignments_mock = [
            Mock(complete=False), Mock(complete=False), Mock(complete=True), Mock(complete=True), Mock(complete=True)
        ]
        get_course_assignment_mock.return_value = course_assignments_mock
        student_assignments_mock = {
            'future_assignments': [],
            'past_assignments': [],
        }
        assignments_mock.return_value = student_assignments_mock
        expected_course_progress = {'total_assignments_count': 5, 'assignments_completed': 3}

        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn('course_progress', response.data['primary'])  # noqa: PT009
        self.assertDictEqual(response.data['primary']['course_progress'], expected_course_progress)  # noqa: PT009

    @patch('lms.djangoapps.courseware.courses.get_course_assignments')
    def test_course_progress_for_secondary_enrollments_no_query_param(
        self,
        get_course_assignment_mock: MagicMock,
    ) -> None:
        self.login()
        courses = [CourseFactory.create(org='edx', mobile_available=True) for _ in range(5)]
        for course in courses:
            self.enroll(course.id)

        response = self.api_response(api_version=API_V4)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        for enrollment in response.data['enrollments']['results']:
            self.assertNotIn('course_progress', enrollment)  # noqa: PT009

    @patch('lms.djangoapps.courseware.courses.get_course_assignments')
    def test_course_progress_for_secondary_enrollments_with_query_param(
        self,
        get_course_assignment_mock: MagicMock,
    ) -> None:
        self.login()
        courses = [CourseFactory.create(org='edx', mobile_available=True) for _ in range(5)]
        for course in courses:
            self.enroll(course.id)
        expected_course_progress = {'total_assignments_count': 0, 'assignments_completed': 0}

        response = self.api_response(api_version=API_V4, data={'requested_fields': 'course_progress'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        for enrollment in response.data['enrollments']['results']:
            self.assertIn('course_progress', enrollment)  # noqa: PT009
            self.assertDictEqual(enrollment['course_progress'], expected_course_progress)  # noqa: PT009

    @patch(
        'lms.djangoapps.mobile_api.users.serializers.CourseEnrollmentSerializerModifiedForPrimary'
        '.get_course_assignments'
    )
    @patch('lms.djangoapps.courseware.courses.get_course_assignments')
    def test_course_progress_for_secondary_enrollments_with_query_param_and_assignments(
        self,
        get_course_assignment_mock: MagicMock,
        assignments_mock: MagicMock,
    ) -> None:
        self.login()
        courses = [CourseFactory.create(org='edx', mobile_available=True) for _ in range(2)]
        for course in courses:
            self.enroll(course.id)
        course_assignments_mock = [
            Mock(complete=False), Mock(complete=False), Mock(complete=True), Mock(complete=True), Mock(complete=True)
        ]
        get_course_assignment_mock.return_value = course_assignments_mock
        student_assignments_mock = {
            'future_assignments': [],
            'past_assignments': [],
        }
        assignments_mock.return_value = student_assignments_mock
        expected_course_progress = {'total_assignments_count': 5, 'assignments_completed': 3}

        response = self.api_response(api_version=API_V4, data={'requested_fields': 'course_progress'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn('course_progress', response.data['primary'])  # noqa: PT009
        self.assertDictEqual(response.data['primary']['course_progress'], expected_course_progress)  # noqa: PT009
        self.assertIn('course_progress', response.data['enrollments']['results'][0])  # noqa: PT009
        self.assertDictEqual(response.data['enrollments']['results'][0]['course_progress'], expected_course_progress)  # noqa: PT009  # pylint: disable=line-too-long


@override_settings(MKTG_URLS={'ROOT': 'dummy-root'})
class TestUserEnrollmentCertificates(UrlResetMixin, MobileAPITestCase, MilestonesTestCaseMixin):
    """
    Tests for /api/mobile/{api_version}/users/<user_name>/course_enrollments/
    """
    REVERSE_INFO = {'name': 'courseenrollment-detail', 'params': ['username', 'api_version']}
    ENABLED_SIGNALS = ['course_published']

    def verify_pdf_certificate(self):
        """
        Verifies the correct URL is returned in the response
        for PDF certificates.
        """
        self.login_and_enroll()

        certificate_url = "https://test_certificate_url"
        GeneratedCertificateFactory.create(
            user=self.user,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode='verified',
            download_url=certificate_url,
        )

        response = self.api_response()
        certificate_data = response.data[0]['certificate']
        assert certificate_data['url'] == certificate_url

    @patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': True})
    def test_no_certificate(self):
        self.login_and_enroll()

        response = self.api_response()
        certificate_data = response.data[0]['certificate']
        self.assertDictEqual(certificate_data, {})  # noqa: PT009

    @patch.dict(settings.FEATURES, {'CERTIFICATES_HTML_VIEW': False, 'ENABLE_MKTG_SITE': True})
    def test_pdf_certificate_with_html_cert_disabled(self):
        """
        Tests PDF certificates with CERTIFICATES_HTML_VIEW set to True.
        """
        self.verify_pdf_certificate()

    @patch.dict(settings.FEATURES, {'CERTIFICATES_HTML_VIEW': True, 'ENABLE_MKTG_SITE': True})
    def test_pdf_certificate_with_html_cert_enabled(self):
        """
        Tests PDF certificates with CERTIFICATES_HTML_VIEW set to True.
        """
        self.verify_pdf_certificate()

    @patch.dict(settings.FEATURES, {'CERTIFICATES_HTML_VIEW': True, 'ENABLE_MKTG_SITE': True})
    def test_web_certificate(self):
        self.login_and_enroll()

        GeneratedCertificateFactory.create(
            user=self.user,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable
        )

        response = self.api_response()
        certificate_data = response.data[0]['certificate']
        self.assertRegex(  # noqa: PT009
            certificate_data['url'],
            r'http.*/certificates/[0-9a-f]{32}'
        )


class CourseStatusAPITestCase(MobileAPITestCase):
    """
    Base test class for /api/mobile/{api_version}/users/<user_name>/course_status_info/{course_id}
    """
    REVERSE_INFO = {'name': 'user-course-status', 'params': ['username', 'course_id', 'api_version']}

    def setUp(self):
        """
        Creates a basic course structure for our course
        """
        super().setUp()

        self.section = BlockFactory.create(
            parent=self.course,
            category='chapter',
        )
        self.sub_section = BlockFactory.create(
            parent=self.section,
            category='sequential',
        )
        self.unit = BlockFactory.create(
            parent=self.sub_section,
            category='vertical',
        )
        self.other_sub_section = BlockFactory.create(
            parent=self.section,
            category='sequential',
        )
        self.other_unit = BlockFactory.create(
            parent=self.other_sub_section,
            category='vertical',
        )


class TestCourseStatusGET(CourseStatusAPITestCase, MobileAuthUserTestMixin,
                          MobileCourseAccessTestMixin, MilestonesTestCaseMixin, CompletionWaffleTestMixin):
    """
    Tests for GET of /api/mobile/v<version_number>/users/<user_name>/course_status_info/{course_id}
    """

    def test_success_v0(self):
        self.login_and_enroll()

        response = self.api_response(api_version=API_V05)
        assert response.data['last_visited_module_id'] == str(self.sub_section.location)
        assert response.data['last_visited_module_path'] == [str(block.location) for block in
                                                             [self.sub_section, self.section, self.course]]

    def test_success_v1(self):
        self.override_waffle_switch(True)
        self.login_and_enroll()
        submit_completions_for_testing(self.user, [self.unit.location])
        response = self.api_response(api_version=API_V1)
        assert response.data['last_visited_block_id'] == str(self.unit.location)

    # Since we are testing an non atomic view in atomic test case, therefore we are expecting error on failures
    def api_atomic_response(self, reverse_args=None, data=None, **kwargs):
        """
        Same as the api_response from MobileAPITestCase, but handles the view as an atomic transaction.
        """
        url = self.reverse_url(reverse_args, **kwargs)
        with transaction.atomic():
            self.url_method(url, data=data, **kwargs)

    def test_invalid_user(self):
        self.login_and_enroll()
        self.api_atomic_response(username='no_user')

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
        self.api_atomic_response(username=other.username)

    def test_course_not_found(self):
        non_existent_course_id = CourseKey.from_string('a/b/c')
        self.init_course_access(course_id=non_existent_course_id)

        self.api_atomic_response(course_id=non_existent_course_id)

    def test_unenrolled_user(self):
        self.login()
        self.unenroll()
        self.api_atomic_response(expected_response_code=None)

    def test_no_auth(self):
        self.logout()
        self.api_atomic_response()


class TestCourseStatusPATCH(CourseStatusAPITestCase, MobileAuthUserTestMixin,
                            MobileCourseAccessTestMixin, MilestonesTestCaseMixin):
    """
    Tests for PATCH of /api/mobile/v0.5/users/<user_name>/course_status_info/{course_id}
    """
    def url_method(self, url, **kwargs):  # pylint: disable=arguments-differ
        # override implementation to use PATCH method.
        return self.client.patch(url, data=kwargs.get('data', None))

    def test_success(self):
        self.login_and_enroll()
        response = self.api_response(data={"last_visited_module_id": str(self.other_unit.location)})
        assert response.data['last_visited_module_id'] == str(self.other_sub_section.location)

    def test_invalid_block(self):
        self.login_and_enroll()
        response = self.api_response(data={"last_visited_module_id": "abc"}, expected_response_code=400)
        assert response.data == errors.ERROR_INVALID_MODULE_ID

    def test_nonexistent_block(self):
        self.login_and_enroll()
        non_existent_key = self.course.id.make_usage_key('video', 'non-existent')
        response = self.api_response(data={"last_visited_module_id": non_existent_key}, expected_response_code=400)
        assert response.data == errors.ERROR_INVALID_MODULE_ID

    def test_no_timezone(self):
        self.login_and_enroll()
        past_date = datetime.datetime.now()
        response = self.api_response(
            data={
                "last_visited_module_id": str(self.other_unit.location),
                "modification_date": past_date.isoformat()
            },
            expected_response_code=400
        )
        assert response.data == errors.ERROR_INVALID_MODIFICATION_DATE

    def _date_sync(self, date, initial_unit, update_unit, expected_subsection):
        """
        Helper for test cases that use a modification to decide whether
        to update the course status
        """
        self.login_and_enroll()

        # save something so we have an initial date
        self.api_response(data={"last_visited_module_id": str(initial_unit.location)})

        # now actually update it
        response = self.api_response(
            data={
                "last_visited_module_id": str(update_unit.location),
                "modification_date": date.isoformat()
            }
        )
        assert response.data['last_visited_module_id'] == str(expected_subsection.location)

    def test_old_date(self):
        self.login_and_enroll()
        date = timezone.now() + datetime.timedelta(days=-100)
        self._date_sync(date, self.unit, self.other_unit, self.sub_section)

    def test_new_date(self):
        self.login_and_enroll()
        date = timezone.now() + datetime.timedelta(days=100)
        self._date_sync(date, self.unit, self.other_unit, self.other_sub_section)

    def test_no_initial_date(self):
        self.login_and_enroll()
        response = self.api_response(
            data={
                "last_visited_module_id": str(self.other_unit.location),
                "modification_date": timezone.now().isoformat()
            }
        )
        assert response.data['last_visited_module_id'] == str(self.other_sub_section.location)

    def test_invalid_date(self):
        self.login_and_enroll()
        response = self.api_response(data={"modification_date": "abc"}, expected_response_code=400)
        assert response.data == errors.ERROR_INVALID_MODIFICATION_DATE


@ddt.ddt
@patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': True})
@override_settings(MKTG_URLS={'ROOT': 'dummy-root'})
class TestCourseEnrollmentSerializer(MobileAPITestCase, MilestonesTestCaseMixin):
    """
    Test the course enrollment serializer
    """
    ENABLED_SIGNALS = ['course_published']

    def setUp(self):
        super().setUp()
        self.login_and_enroll()
        self.request = RequestFactory().get('/')
        self.request.user = self.user

    def get_serialized_data(self, api_version):
        """
        Return data from CourseEnrollmentSerializer
        """
        if api_version == API_V05:
            serializer = CourseEnrollmentSerializerv05
        else:
            serializer = CourseEnrollmentSerializer

        return serializer(
            CourseEnrollment.enrollments_for_user(self.user)[0],
            context={'request': self.request, 'api_version': api_version},
        ).data

    def _expiration_in_response(self, response, api_version):
        """
        Assert that audit_access_expires field in present in response
        based on version of api being used
        """
        if api_version != API_V05:
            assert 'audit_access_expires' in response
        else:
            assert 'audit_access_expires' not in response

    @ddt.data(API_V05, API_V1)
    def test_success(self, api_version):
        serialized = self.get_serialized_data(api_version)
        assert serialized['course']['name'] == self.course.display_name
        assert serialized['course']['number'] == self.course.id.course
        assert serialized['course']['org'] == self.course.id.org
        self._expiration_in_response(serialized, api_version)

        # Assert utm parameters
        qstwitter = parse_qs('utm_campaign=social-sharing-db&utm_medium=social&utm_source=twitter')
        qsfacebook = parse_qs('utm_campaign=social-sharing-db&utm_medium=social&utm_source=facebook')

        self.assertDictEqual(qsfacebook, parse_qs(serialized['course']['course_sharing_utm_parameters']['facebook']))  # noqa: PT009  # pylint: disable=line-too-long
        self.assertDictEqual(qstwitter, parse_qs(serialized['course']['course_sharing_utm_parameters']['twitter']))  # noqa: PT009  # pylint: disable=line-too-long

    @ddt.data(API_V05, API_V1)
    def test_with_display_overrides(self, api_version):
        self.course.display_coursenumber = "overridden_number"
        self.course.display_organization = "overridden_org"
        self.course = self.update_course(self.course, self.user.id)

        serialized = self.get_serialized_data(api_version)
        assert serialized['course']['number'] == self.course.display_coursenumber
        assert serialized['course']['org'] == self.course.display_organization
        self._expiration_in_response(serialized, api_version)


@ddt.ddt
class TestDiscussionCourseEnrollmentSerializer(UrlResetMixin, MobileAPITestCase, MilestonesTestCaseMixin):
    """
    Tests discussion data in course enrollment serializer
    """

    def setUp(self):
        """
        Setup data for test
        """
        with patch.dict('django.conf.settings.FEATURES', {'ENABLE_DISCUSSION_SERVICE': True}):
            super().setUp()
        self.login_and_enroll()
        self.request = RequestFactory().get('/')
        self.request.user = self.user

    def get_serialized_data(self, api_version):
        """
        Return data from CourseEnrollmentSerializer
        """
        if api_version == API_V05:
            serializer = CourseEnrollmentSerializerv05
        else:
            serializer = CourseEnrollmentSerializer

        return serializer(
            CourseEnrollment.enrollments_for_user(self.user)[0],
            context={'request': self.request, 'api_version': api_version},
        ).data

    @ddt.data(True, False)
    def test_discussion_tab_url(self, discussion_tab_enabled):
        """
        Tests discussion tab url is None if tab is disabled
        """
        config, _ = DiscussionsConfiguration.objects.get_or_create(context_key=self.course.id)
        config.enabled = discussion_tab_enabled
        config.save()
        with patch.dict('django.conf.settings.FEATURES', {'ENABLE_DISCUSSION_SERVICE': True}):
            serialized = self.get_serialized_data(API_V2)
        discussion_url = serialized["course"]["discussion_url"]
        if discussion_tab_enabled:
            assert discussion_url is not None
            assert isinstance(discussion_url, str)
        else:
            assert discussion_url is None


@ddt.ddt
class TestUserEnrollmentsStatus(MobileAPITestCase, MobileAuthUserTestMixin):
    """
    Tests for /api/mobile/{api_version}/users/<user_name>/enrollments_status/
    """

    REVERSE_INFO = {'name': 'user-enrollments-status', 'params': ['username', 'api_version']}

    def test_no_mobile_available_courses(self) -> None:
        self.login()
        courses = [CourseFactory.create(org="edx", mobile_available=False) for _ in range(3)]
        for course in courses:
            self.enroll(course.id)

        response = self.api_response(api_version=API_V1)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertListEqual(response.data, [])  # noqa: PT009

    def test_no_enrollments(self) -> None:
        self.login()
        for _ in range(3):
            CourseFactory.create(org="edx", mobile_available=True)

        response = self.api_response(api_version=API_V1)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertListEqual(response.data, [])  # noqa: PT009

    def test_user_have_only_active_enrollments_and_no_completions(self) -> None:
        self.login()
        courses = [CourseFactory.create(org="edx", mobile_available=True) for _ in range(3)]
        for course in courses:
            self.enroll(course.id)

        response = self.api_response(api_version=API_V1)

        expected_response = [
            {'course_id': str(courses[0].course_id), 'course_name': courses[0].display_name, 'recently_active': True},
            {'course_id': str(courses[1].course_id), 'course_name': courses[1].display_name, 'recently_active': True},
            {'course_id': str(courses[2].course_id), 'course_name': courses[2].display_name, 'recently_active': True},
        ]

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertListEqual(response.data, expected_response)  # noqa: PT009

    def test_user_have_active_and_inactive_enrollments_and_no_completions(self) -> None:
        self.login()
        courses = [CourseFactory.create(org="edx", mobile_available=True) for _ in range(3)]
        for course in courses:
            self.enroll(course.id)
        old_course = CourseFactory.create(org="edx", mobile_available=True)
        self.enroll(old_course.id)
        old_enrollment = CourseEnrollment.objects.filter(user=self.user, course=old_course.course_id).first()
        old_enrollment.created = datetime.datetime.now(pytz.UTC) - datetime.timedelta(days=31)
        old_enrollment.save()

        response = self.api_response(api_version=API_V1)

        expected_response = [
            {'course_id': str(courses[0].course_id), 'course_name': courses[0].display_name, 'recently_active': True},
            {'course_id': str(courses[1].course_id), 'course_name': courses[1].display_name, 'recently_active': True},
            {'course_id': str(courses[2].course_id), 'course_name': courses[2].display_name, 'recently_active': True},
            {'course_id': str(old_course.course_id), 'course_name': old_course.display_name, 'recently_active': False}
        ]

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertListEqual(response.data, expected_response)  # noqa: PT009

    @ddt.data(
        (27, True),
        (28, True),
        (29, True),
        (31, False),
        (32, False),
    )
    @ddt.unpack
    def test_different_enrollment_dates(self, enrolled_days_ago: int, recently_active_status: bool) -> None:
        self.login()
        course = CourseFactory.create(org="edx", mobile_available=True, run='1001')
        self.enroll(course.id)
        enrollment = CourseEnrollment.objects.filter(user=self.user, course=course.course_id).first()
        enrollment.created = datetime.datetime.now(pytz.UTC) - datetime.timedelta(days=enrolled_days_ago)
        enrollment.save()

        response = self.api_response(api_version=API_V1)

        expected_response = [
            {
                'course_id': str(course.course_id),
                'course_name': course.display_name,
                'recently_active': recently_active_status
            }
        ]

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertListEqual(response.data, expected_response)  # noqa: PT009

    @ddt.data(
        (27, True),
        (28, True),
        (29, True),
        (31, False),
        (32, False),
    )
    @ddt.unpack
    def test_different_completion_dates(self, completed_days_ago: int, recently_active_status: bool) -> None:
        self.login()
        course = CourseFactory.create(org="edx", mobile_available=True, run='1010')
        section = BlockFactory.create(
            parent=course,
            category='chapter',
        )
        self.enroll(course.id)
        enrollment = CourseEnrollment.objects.filter(user=self.user, course=course.course_id).first()
        # make enrollment older 30 days ago
        enrollment.created = datetime.datetime.now(pytz.UTC) - datetime.timedelta(days=50)
        enrollment.save()
        completion = BlockCompletion.objects.create(
            user=self.user,
            context_key=course.context_key,
            block_type='course',
            block_key=section.location,
            completion=0.5,
        )
        completion.created = datetime.datetime.now(pytz.UTC) - datetime.timedelta(days=completed_days_ago)
        completion.save()

        response = self.api_response(api_version=API_V1)

        expected_response = [
            {
                'course_id': str(course.course_id),
                'course_name': course.display_name,
                'recently_active': recently_active_status
            }
        ]

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertListEqual(response.data, expected_response)  # noqa: PT009
