"""
Test for course API
"""

from datetime import datetime, timedelta
from hashlib import md5
from unittest import mock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import Http404
from django.test import TestCase, override_settings
from opaque_keys.edx.keys import CourseKey
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.modulestore.exceptions import ItemNotFoundError  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (  # lint-amnesty, pylint: disable=wrong-import-order
    ModuleStoreTestCase,
    SharedModuleStoreTestCase,
)
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    BlockFactory,
    check_mongo_calls,
)

from ..api import (
    UNKNOWN_BLOCK_DISPLAY_NAME,
    course_detail,
    get_course_members,
    get_course_run_url,
    get_due_dates,
    list_courses,
)
from ..exceptions import OverEnrollmentLimitException
from .mixins import CourseApiFactoryMixin


class CourseApiTestMixin(CourseApiFactoryMixin):
    """
    Establish basic functionality for Course API tests
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.request_factory = APIRequestFactory()
        CourseOverview.get_all_courses()  # seed the CourseOverview table

    def verify_course(self, course, course_id='course-v1:edX+toy+2012_Fall'):
        """
        Ensure that the returned course is the course we just created
        """
        assert course_id == str(course.id)


class CourseDetailTestMixin(CourseApiTestMixin):
    """
    Common functionality for course_detail tests
    """
    ENABLED_SIGNALS = ['course_published']

    def _make_api_call(self, requesting_user, target_user, course_key):
        """
        Call the `course_detail` api endpoint to get information on the course
        identified by `course_key`.
        """
        request = Request(self.request_factory.get('/'))
        request.user = requesting_user
        with check_mongo_calls(0):
            return course_detail(request, target_user.username, course_key)


class TestGetCourseDetail(CourseDetailTestMixin, SharedModuleStoreTestCase):
    """
    Test course_detail api function
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = cls.create_course()
        cls.hidden_course = cls.create_course(course='hidden', visible_to_staff_only=True)
        cls.honor_user = cls.create_user('honor', is_staff=False)
        cls.staff_user = cls.create_user('staff', is_staff=True)

    def test_get_existing_course(self):
        course = self._make_api_call(self.honor_user, self.honor_user, self.course.id)
        self.verify_course(course)

    def test_get_nonexistent_course(self):
        course_key = CourseKey.from_string('edX/toy/nope')
        with pytest.raises(Http404):
            self._make_api_call(self.honor_user, self.honor_user, course_key)

    def test_hidden_course_for_honor(self):
        with pytest.raises(Http404):
            self._make_api_call(self.honor_user, self.honor_user, self.hidden_course.id)

    def test_hidden_course_for_staff(self):
        course = self._make_api_call(self.staff_user, self.staff_user, self.hidden_course.id)
        self.verify_course(course, course_id='course-v1:edX+hidden+2012_Fall')

    def test_hidden_course_for_staff_as_honor(self):
        with pytest.raises(Http404):
            self._make_api_call(self.staff_user, self.honor_user, self.hidden_course.id)


class CourseListTestMixin(CourseApiTestMixin):
    """
    Common behavior for list_courses tests
    """

    def _make_api_call(self,
                       requesting_user,
                       specified_user,
                       org=None,
                       filter_=None,
                       permissions=None,
                       course_keys=None):
        """
        Call the list_courses api endpoint to get information about
        `specified_user` on behalf of `requesting_user`.
        """
        request = Request(self.request_factory.get('/'))
        request.user = requesting_user
        with check_mongo_calls(0):
            return list_courses(
                request,
                specified_user.username,
                org=org,
                filter_=filter_,
                permissions=permissions,
                course_keys=course_keys,
            )

    def verify_courses(self, courses):
        """
        Verify that there is one course, and that it has the expected format.
        """
        assert len(courses) == 1
        self.verify_course(courses[0])


class TestGetCourseList(CourseListTestMixin, SharedModuleStoreTestCase):
    """
    Test the behavior of the `list_courses` api function.
    """
    ENABLED_SIGNALS = ['course_published']

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = cls.create_course()
        cls.staff_user = cls.create_user("staff", is_staff=True)
        cls.honor_user = cls.create_user("honor", is_staff=False)

    def test_as_staff(self):
        courses = self._make_api_call(self.staff_user, self.staff_user)
        assert len(courses) == 1
        self.verify_courses(courses)

    def test_for_honor_user_as_staff(self):
        courses = self._make_api_call(self.staff_user, self.honor_user)
        self.verify_courses(courses)

    def test_as_honor(self):
        courses = self._make_api_call(self.honor_user, self.honor_user)
        self.verify_courses(courses)

    def test_for_staff_user_as_honor(self):
        with pytest.raises(PermissionDenied):
            self._make_api_call(self.honor_user, self.staff_user)

    def test_as_anonymous(self):
        anonuser = AnonymousUser()
        courses = self._make_api_call(anonuser, anonuser)
        self.verify_courses(courses)

    def test_for_honor_user_as_anonymous(self):
        anonuser = AnonymousUser()
        with pytest.raises(PermissionDenied):
            self._make_api_call(anonuser, self.staff_user)


class TestGetCourseListMultipleCourses(CourseListTestMixin, ModuleStoreTestCase):
    """
    Test the behavior of the `list_courses` api function (with tests that
    modify the courseware).
    """
    ENABLED_SIGNALS = ['course_published']

    def setUp(self):
        super().setUp()
        self.course = self.create_course(mobile_available=False)
        self.staff_user = self.create_user("staff", is_staff=True)
        self.honor_user = self.create_user("honor", is_staff=False)

    def test_multiple_courses(self):
        self.create_course(course='second')
        courses = self._make_api_call(self.honor_user, self.honor_user)
        assert len(courses) == 2

    def test_filter_by_org(self):
        """Verify that courses are filtered by the provided org key."""
        # Create a second course to be filtered out of queries.
        alternate_course = self.create_course(
            org=md5(self.course.org.encode('utf-8')).hexdigest()
        )

        assert alternate_course.org != self.course.org

        # No filtering.
        unfiltered_courses = self._make_api_call(self.staff_user, self.staff_user)
        for org in [self.course.org, alternate_course.org]:
            assert any((course.org == org) for course in unfiltered_courses)

        # With filtering.
        filtered_courses = self._make_api_call(self.staff_user, self.staff_user, org=self.course.org)
        assert all((course.org == self.course.org) for course in filtered_courses)

    def test_filter(self):
        # Create a second course to be filtered out of queries.
        alternate_course = self.create_course(course='mobile')

        test_cases = [
            (None, [alternate_course, self.course]),
            (dict(mobile_available=True), [alternate_course]),
            (dict(mobile_available=False), [self.course]),
        ]
        for filter_, expected_courses in test_cases:
            filtered_courses = self._make_api_call(self.staff_user, self.staff_user, filter_=filter_)
            assert {course.id for course in filtered_courses} == {course.id for course in expected_courses},\
                f'testing course_api.api.list_courses with filter_={filter_}'

    def test_permissions(self):

        # Create a second course to be filtered out of queries.
        self.create_course(course='should-be-hidden-course')

        # Create instructor (non-staff), and enroll him in the course.
        instructor_user = self.create_user('the-instructor', is_staff=False)
        self.create_enrollment(user=instructor_user, course_id=self.course.id)
        self.create_courseaccessrole(
            user=instructor_user,
            course_id=self.course.id,
            role='instructor',
            org='edX',
        )

        filtered_courses = self._make_api_call(
            instructor_user,
            instructor_user,
            permissions={'instructor'})

        self.assertEqual({c.id for c in filtered_courses}, {self.course.id})

    def test_filter_by_keys(self):
        """
        Verify that courses are filtered by the provided course keys.
        """

        # Create alternative courses to be included in the `course_keys` filter.
        alternative_course_1 = self.create_course(course='alternative-course-1')
        alternative_course_2 = self.create_course(course='alternative-course-2')

        # No filtering.
        unfiltered_expected_courses = [
            self.course,
            alternative_course_1,
            alternative_course_2,
        ]
        unfiltered_courses = self._make_api_call(self.honor_user, self.honor_user)
        assert {course.id for course in unfiltered_courses} == {course.id for course in unfiltered_expected_courses}

        # With filtering.
        filtered_expected_courses = [
            alternative_course_1,
            alternative_course_2,
        ]
        filtered_courses = self._make_api_call(
            self.honor_user,
            self.honor_user,
            course_keys={
                alternative_course_1.id,
                alternative_course_2.id
            }
        )
        assert {course.id for course in filtered_courses} == {course.id for course in filtered_expected_courses}


class TestGetCourseListExtras(CourseListTestMixin, ModuleStoreTestCase):
    """
    Tests of course_list api function that require alternative configurations
    of created courses.
    """
    ENABLED_SIGNALS = ['course_published']

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.staff_user = cls.create_user("staff", is_staff=True)
        cls.honor_user = cls.create_user("honor", is_staff=False)

    def test_no_courses(self):
        courses = self._make_api_call(self.honor_user, self.honor_user)
        assert len(list(courses)) == 0

    def test_hidden_course_for_honor(self):
        self.create_course(visible_to_staff_only=True)
        courses = self._make_api_call(self.honor_user, self.honor_user)
        assert len(list(courses)) == 0

    def test_hidden_course_for_staff(self):
        self.create_course(visible_to_staff_only=True)
        courses = self._make_api_call(self.staff_user, self.staff_user)
        self.verify_courses(courses)


class TestGetCourseDates(CourseDetailTestMixin, SharedModuleStoreTestCase):
    """
    Test get_due_dates function
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = cls.create_course()
        cls.staff_user = cls.create_user("staff", is_staff=True)
        cls.today = datetime.utcnow()
        cls.yesterday = cls.today - timedelta(days=1)
        cls.tomorrow = cls.today + timedelta(days=1)

        cls.section_1 = BlockFactory.create(
            category='chapter',
            start=cls.yesterday,
            due=cls.tomorrow,
            parent=cls.course,
            display_name='section 1'
        )

        cls.subsection_1 = BlockFactory.create(
            category='sequential',
            parent=cls.section_1,
            display_name='subsection 1'
        )

    def test_get_due_dates(self):
        request = mock.Mock()

        mock_path = 'lms.djangoapps.course_api.api.get_dates_for_course'
        with mock.patch(mock_path) as mock_get_dates:
            mock_get_dates.return_value = {
                (self.section_1.location, 'due'): self.section_1.due.strftime('%Y-%m-%dT%H:%M:%SZ'),
                (self.section_1.location, 'start'): self.section_1.start.strftime('%Y-%m-%dT%H:%M:%SZ'),
            }

            expected_due_dates = [
                {
                    'name': self.section_1.display_name,
                    'url': request.build_absolute_uri.return_value,
                    'date': self.tomorrow.strftime('%Y-%m-%dT%H:%M:%SZ'),
                },
            ]
            actual_due_dates = get_due_dates(request, self.course.id, self.staff_user)
            assert expected_due_dates == actual_due_dates

    def test_get_due_dates_error_fetching_block(self):
        request = mock.Mock()

        mock_path = 'lms.djangoapps.course_api.api.'
        with mock.patch(mock_path + 'get_dates_for_course') as mock_get_dates:
            with mock.patch(mock_path + 'modulestore') as mock_modulestore:
                mock_modulestore.return_value.get_item.side_effect = ItemNotFoundError('whatever')
                mock_get_dates.return_value = {
                    (self.section_1.location, 'due'): self.section_1.due.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    (self.section_1.location, 'start'): self.section_1.start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                }

                expected_due_dates = [
                    {
                        'name': UNKNOWN_BLOCK_DISPLAY_NAME,
                        'url': request.build_absolute_uri.return_value,
                        'date': self.tomorrow.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    },
                ]
                actual_due_dates = get_due_dates(request, self.course.id, self.staff_user)
                assert expected_due_dates == actual_due_dates


class TestGetCourseMembers(CourseApiTestMixin, SharedModuleStoreTestCase):
    """
    Test get_course_members function
    """
    @classmethod
    def setUpClass(cls):
        super(TestGetCourseMembers, cls).setUpClass()
        cls.course = cls.create_course()
        cls.honor = cls.create_user('honor', is_staff=False)
        cls.staff = cls.create_user('staff', is_staff=True)
        cls.instructor = cls.create_user('instructor', is_staff=True)

        # Attach honor to course with enrollment
        cls.create_enrollment(user=cls.honor, course_id=cls.course.id)
        # Attach instructor to course with both enrollment and course access role
        cls.create_enrollment(user=cls.instructor, course_id=cls.course.id)
        cls.create_courseaccessrole(user=cls.instructor, course_id=cls.course.id, role='instructor')
        # Attach staff to course using only course access role
        cls.create_courseaccessrole(user=cls.staff, course_id=cls.course.id, role='staff')

    def test_get_course_members(self):
        """
        Test all different possible filtering
        """
        with self.assertNumQueries(3):
            members = get_course_members(self.course.id)

        self.assertEqual(len(members), 3)

        # Check parameters for all users
        expected_properties = ['id', 'username', 'email', 'name', 'enrollment_mode', 'roles']
        for user_id in members:
            self.assertCountEqual(members[user_id], expected_properties)

        # Check that users have correct roles
        # Honor should be only a student and have the enrollment mode set
        self.assertEqual(members[self.honor.id]['roles'], ['student'])
        self.assertEqual(members[self.honor.id]['enrollment_mode'], 'audit')
        # Instructor should have both roles and enrollment_mode set
        self.assertEqual(members[self.instructor.id]['roles'], ['student', 'instructor'])
        self.assertEqual(members[self.instructor.id]['enrollment_mode'], 'audit')
        # Staff should only have the staff role
        self.assertEqual(members[self.staff.id]['roles'], ['staff'])
        self.assertEqual(members[self.staff.id]['enrollment_mode'], None)

    def test_same_result_with_csa_or_enrollment(self):
        """
        Checks that the API returns the same result regardless if a user
        comes from CourseAccessRoles or CourseEnrollments table.
        """
        # Create new user
        user = TestGetCourseMembers.create_user('test_use', is_staff=True)

        # Attach with course enrollment
        enrollment = TestGetCourseMembers.create_enrollment(
            user=user,
            course_id=self.course.id
        )
        members_enrollments = get_course_members(self.course.id)
        enrollment.delete()

        # Attach with course enrollment
        enrollment = TestGetCourseMembers.create_courseaccessrole(
            user=user,
            course_id=self.course.id,
            role='staff',
        )
        members_courseaccessroles = get_course_members(self.course.id)

        # Check properties (except the ones that change depending on role)
        for item in ['id', 'username', 'email', 'name']:
            self.assertEqual(
                members_courseaccessroles[user.id][item],
                members_enrollments[user.id][item]
            )

    @override_settings(COURSE_MEMBER_API_ENROLLMENT_LIMIT=1)
    def test_course_members_fails_overlimit(self):
        """
        Check if trying to retrieve more than settings.COURSE_MEMBER_API_ENROLLMENT_LIMIT
        fails.
        """
        with self.assertRaises(OverEnrollmentLimitException):
            get_course_members(self.course.id)


class TestGetCourseRunUrl(TestCase):
    """
    Tests of get_course_run_url.
    """
    def test_simple_lookup(self):
        request = Request(APIRequestFactory().get('/'))
        url = get_course_run_url(request, 'course-v1:org+course+run')
        assert url == 'http://learning-mfe/course/course-v1:org+course+run/home'
