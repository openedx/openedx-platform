"""
Unit tests for getting the list of courses for a user through iterating all courses and
by reversing group name formats.
"""

import random
from unittest.mock import Mock, patch

import ddt
from ccx_keys.locator import CCXLocator
from django.test import RequestFactory
from opaque_keys.edx.locations import CourseLocator

from cms.djangoapps.contentstore.tests.utils import AjaxEnabledTestClient
from cms.djangoapps.contentstore.utils import delete_course
from cms.djangoapps.contentstore.views.course import (
    AccessListFallback,
    _accessible_courses_iter_for_tests,
    _accessible_courses_list_from_groups,
    _accessible_courses_summary_iter,
    get_courses_accessible_to_user
)
from common.djangoapps.course_action_state.models import CourseRerunState
from common.djangoapps.student.models.user import CourseAccessRole
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
    GlobalStaff,
    OrgInstructorRole,
    OrgStaffRole,
    UserBasedRole
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.waffle_utils.testutils import WAFFLE_TABLES
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

TOTAL_COURSES_COUNT = 10
USER_COURSES_COUNT = 1


@ddt.ddt
class TestCourseListing(ModuleStoreTestCase):
    """
    Unit tests for getting the list of courses for a logged in user
    """
    def setUp(self):
        """
        Add a user and a course
        """
        super().setUp()
        # create and log in a staff user.
        # create and log in a non-staff user
        self.user = UserFactory(password=self.TEST_PASSWORD)
        self.factory = RequestFactory()
        self.request = self.factory.get('/course')
        self.request.user = self.user
        self.client = AjaxEnabledTestClient()
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

    def _create_course_with_access_groups(self, course_location, user=None):
        """
        Create dummy course with 'CourseFactory' and role (instructor/staff) groups
        """
        CourseFactory.create(
            org=course_location.org,
            number=course_location.course,
            run=course_location.run
        )
        course = CourseOverviewFactory.create(id=course_location, org=course_location.org)
        self._add_role_access_to_user(user, course_location)
        return course

    def _add_role_access_to_user(self, user, course_id):
        """ Assign access roles to user in the course. """
        if user is not None:
            for role in [CourseInstructorRole, CourseStaffRole]:
                role(course_id).add_users(user)

    def tearDown(self):
        """
        Reverse the setup
        """
        self.client.logout()
        ModuleStoreTestCase.tearDown(self)  # pylint: disable=non-parent-method-called

    def test_get_course_list(self):
        """
        Test getting courses with new access group format e.g. 'instructor_edx.course.run'
        """
        course_location = self.store.make_course_key('Org1', 'Course1', 'Run1')
        self._create_course_with_access_groups(course_location, self.user)

        # get courses through iterating all courses
        courses_iter, __ = _accessible_courses_iter_for_tests(self.request)
        courses_list = list(courses_iter)
        self.assertEqual(len(courses_list), 1)

        courses_summary_list, __ = _accessible_courses_summary_iter(self.request)
        self.assertEqual(len(list(courses_summary_list)), 1)

        # get courses by reversing group name formats
        courses_list_by_groups, __ = _accessible_courses_list_from_groups(self.request)
        self.assertEqual(len(courses_list_by_groups), 1)

        # check both course lists have same courses
        course_keys_in_course_list = [course.id for course in courses_list]
        course_keys_in_courses_list_by_groups = [course.id for course in courses_list_by_groups]

        self.assertEqual(course_keys_in_course_list, course_keys_in_courses_list_by_groups)

    def test_courses_list_with_ccx_courses(self):
        """
        Tests that CCX courses are filtered in course listing.
        """
        # Create a course and assign access roles to user.
        course_location = CourseLocator('Org1', 'Course1', 'Course1')
        course = self._create_course_with_access_groups(course_location, self.user)

        # Create a ccx course key and add assign access roles to user.
        ccx_course_key = CCXLocator.from_course_locator(course.id, '1')
        self._add_role_access_to_user(self.user, ccx_course_key)

        # Test that CCX courses are filtered out.
        courses_list, __ = _accessible_courses_list_from_groups(self.request)
        self.assertEqual(len(courses_list), 1)
        self.assertNotIn(
            ccx_course_key,
            [course.id for course in courses_list]
        )

        # Get all courses which user has access.
        instructor_courses = UserBasedRole(self.user, CourseInstructorRole.ROLE).courses_with_role()
        staff_courses = UserBasedRole(self.user, CourseStaffRole.ROLE).courses_with_role()
        all_courses = (instructor_courses | staff_courses)

        # Verify that CCX course exists in access but filtered by `_accessible_courses_list_from_groups`.
        self.assertIn(
            ccx_course_key,
            [access.course_id for access in all_courses]
        )

        # Verify that CCX courses are filtered out while iterating over all courses
        mocked_ccx_course = Mock(id=ccx_course_key)
        with patch(
            'openedx.core.djangoapps.content.course_overviews.models.CourseOverview.get_all_courses',
            return_value=[mocked_ccx_course],
        ):
            courses_iter, __ = _accessible_courses_iter_for_tests(self.request)
            self.assertEqual(len(list(courses_iter)), 0)

    def test_staff_course_listing(self):
        """
        Create courses and verify they take certain amount of mongo calls to call get_courses_accessible_to_user.
        Also verify that fetch accessible courses list for staff user returns CourseSummary instances.
        """

        # Assign & verify staff role to the user
        GlobalStaff().add_users(self.user)
        self.assertTrue(GlobalStaff().has_user(self.user))

        # Create few courses
        for num in range(TOTAL_COURSES_COUNT):
            course_location = self.store.make_course_key('Org', 'CreatedCourse' + str(num), 'Run')
            self._create_course_with_access_groups(course_location, self.user)

        # Fetch accessible courses list & verify their count
        courses_list_by_staff, __ = get_courses_accessible_to_user(self.request)

        self.assertEqual(len(list(courses_list_by_staff)), TOTAL_COURSES_COUNT)
        self.assertTrue(all(isinstance(course, CourseOverview) for course in courses_list_by_staff))

        # Now count the db queries for staff
        with self.assertNumQueries(2):
            list(_accessible_courses_summary_iter(self.request))

    def test_course_limited_staff_course_listing(self):
        # Setup a new course
        course_location = self.store.make_course_key('Org', 'CreatedCourse', 'Run')
        CourseFactory.create(
            org=course_location.org,
            number=course_location.course,
            run=course_location.run
        )
        course = CourseOverviewFactory.create(id=course_location, org=course_location.org)

        # Add the user as a course_limited_staff on the course
        CourseLimitedStaffRole(course.id).add_users(self.user)
        self.assertTrue(CourseLimitedStaffRole(course.id).has_user(self.user))

        # Fetch accessible courses list & verify their count
        courses_list_by_staff, __ = get_courses_accessible_to_user(self.request)

        # Limited Course Staff should not be able to list courses in Studio
        assert len(list(courses_list_by_staff)) == 0

    def test_org_limited_staff_course_listing(self):

        # Setup a new course
        course_location = self.store.make_course_key('Org', 'CreatedCourse', 'Run')
        CourseFactory.create(
            org=course_location.org,
            number=course_location.course,
            run=course_location.run
        )
        course = CourseOverviewFactory.create(id=course_location, org=course_location.org)

        # Add a user as course_limited_staff on the org
        # This is not possible using the course roles classes but is possible via Django admin so we
        # insert a row into the model directly to test that scenario.
        CourseAccessRole.objects.create(user=self.user, org=course_location.org, role=CourseLimitedStaffRole.ROLE)

        # Fetch accessible courses list & verify their count
        courses_list_by_staff, __ = get_courses_accessible_to_user(self.request)

        # Limited Course Staff should not be able to list courses in Studio
        assert len(list(courses_list_by_staff)) == 0

    def test_get_course_list_with_invalid_course_location(self):
        """
        Test getting courses with invalid course location (course deleted from modulestore).
        """
        with self.store.default_store(ModuleStoreEnum.Type.split):
            course_key = self.store.make_course_key('Org', 'Course', 'Run')
            course = self._create_course_with_access_groups(course_key, self.user)

        # get courses through iterating all courses
        courses_iter, __ = _accessible_courses_iter_for_tests(self.request)
        courses_list = list(courses_iter)
        self.assertEqual(len(courses_list), 1)

        courses_summary_iter, __ = _accessible_courses_summary_iter(self.request)
        courses_summary_list = list(courses_summary_iter)
        self.assertTrue(all(isinstance(course, CourseOverview) for course in courses_summary_list))
        self.assertEqual(len(courses_summary_list), 1)

        # get courses by reversing group name formats
        courses_list_by_groups, __ = _accessible_courses_list_from_groups(self.request)
        self.assertEqual(len(courses_list_by_groups), 1)

        course_keys_in_course_list = [course.id for course in courses_list]
        course_keys_in_courses_list_by_groups = [course.id for course in courses_list_by_groups]
        # check course lists have same courses
        self.assertEqual(course_keys_in_course_list, course_keys_in_courses_list_by_groups)
        # now delete this course and re-add user to instructor group of this course
        delete_course(course_key, self.user.id)
        course.delete()

        CourseInstructorRole(course_key).add_users(self.user)

        # Get courses through iterating all courses
        courses_iter, __ = _accessible_courses_iter_for_tests(self.request)

        # Get course summaries by iterating all courses
        courses_summary_iter, __ = _accessible_courses_summary_iter(self.request)

        # Get courses by reversing group name formats
        courses_list_by_groups, __ = _accessible_courses_list_from_groups(self.request)

        # Test that course list returns no course
        self.assertEqual(
            [len(list(courses_iter)), len(courses_list_by_groups), len(list(courses_summary_iter))],
            [0, 0, 0]
        )

    def test_course_listing_performance(self):
        """
        Create large number of courses and give access of some of these courses to the user and
        compare the time to fetch accessible courses for the user through traversing all courses and
        reversing django groups
        """
        # create list of random course numbers which will be accessible to the user
        user_course_ids = random.sample(list(range(TOTAL_COURSES_COUNT)), USER_COURSES_COUNT)

        # create courses and assign those to the user which have their number in user_course_ids
        for number in range(TOTAL_COURSES_COUNT):
            org = f'Org{number}'
            course = f'Course{number}'
            run = f'Run{number}'
            course_location = self.store.make_course_key(org, course, run)
            if number in user_course_ids:
                self._create_course_with_access_groups(course_location, self.user)
            else:
                self._create_course_with_access_groups(course_location)

        # get courses by iterating through all courses
        courses_iter, __ = _accessible_courses_iter_for_tests(self.request)
        self.assertEqual(len(list(courses_iter)), USER_COURSES_COUNT)

        # again get courses by iterating through all courses
        courses_iter, __ = _accessible_courses_iter_for_tests(self.request)
        self.assertEqual(len(list(courses_iter)), USER_COURSES_COUNT)

        # get courses by reversing django groups
        courses_list, __ = _accessible_courses_list_from_groups(self.request)
        self.assertEqual(len(courses_list), USER_COURSES_COUNT)

        # again get courses by reversing django groups
        courses_list, __ = _accessible_courses_list_from_groups(self.request)
        self.assertEqual(len(courses_list), USER_COURSES_COUNT)

        with self.assertNumQueries(1, table_ignorelist=WAFFLE_TABLES):
            _accessible_courses_list_from_groups(self.request)

        with self.assertNumQueries(2, table_ignorelist=WAFFLE_TABLES):
            _accessible_courses_iter_for_tests(self.request)

    def test_course_listing_errored_deleted_courses(self):
        """
        Create good courses, courses that won't load, and deleted courses which still have
        roles. Test course listing.
        """
        course_location = CourseLocator('testOrg', 'testCourse', 'RunBabyRun')
        self._create_course_with_access_groups(course_location, self.user)

        course_location = CourseLocator('doomedCourse', 'testCourse', 'RunBabyRun')
        course = self._create_course_with_access_groups(course_location, self.user)
        course.delete()

        courses_list, __ = _accessible_courses_list_from_groups(self.request)
        self.assertEqual(len(courses_list), 1, courses_list)

    @ddt.data(OrgStaffRole('AwesomeOrg'), OrgInstructorRole('AwesomeOrg'))
    def test_course_listing_org_permissions(self, role):
        """
        Create multiple courses within the same org.  Verify that someone with org-wide permissions can access
        all of them.
        """
        org_course_one = self.store.make_course_key('AwesomeOrg', 'Course1', 'RunBabyRun')
        course_1 = CourseFactory.create(
            org=org_course_one.org,
            number=org_course_one.course,
            run=org_course_one.run
        )
        CourseOverviewFactory.create(id=course_1.id, org='AwesomeOrg')

        org_course_two = self.store.make_course_key('AwesomeOrg', 'Course2', 'RunBabyRun')
        course_2 = CourseFactory.create(
            org=org_course_two.org,
            number=org_course_two.course,
            run=org_course_two.run
        )
        CourseOverviewFactory.create(id=course_2.id, org='AwesomeOrg')

        # Two types of org-wide roles have edit permissions: staff and instructor.  We test both
        role.add_users(self.user)

        courses_list, __ = get_courses_accessible_to_user(self.request)

        # Verify fetched accessible courses list is a list of CourseSummery instances and test expacted
        # course count is returned
        self.assertEqual(len(list(courses_list)), 2)
        self.assertTrue(all(isinstance(course, CourseOverview) for course in courses_list))

    @ddt.data(OrgStaffRole(), OrgInstructorRole())
    def test_course_listing_org_permissions_exception(self, role):
        """
        Create roles with no course_id neither org to make sure AccessListFallback is raised for
        platform-wide permissions
        """
        role.add_users(self.user)

        with self.assertRaises(AccessListFallback):
            _accessible_courses_list_from_groups(self.request)

    def test_course_listing_with_actions_in_progress(self):
        sourse_course_key = CourseLocator('source-Org', 'source-Course', 'source-Run')

        num_courses_to_create = 3
        courses = [
            self._create_course_with_access_groups(
                CourseLocator('Org', 'CreatedCourse' + str(num), 'Run'),
                self.user,
            )
            for num in range(num_courses_to_create)
        ]
        courses_in_progress = [
            self._create_course_with_access_groups(
                CourseLocator('Org', 'InProgressCourse' + str(num), 'Run'),
                self.user,
            )
            for num in range(num_courses_to_create)
        ]

        # simulate initiation of course actions
        for course in courses_in_progress:
            CourseRerunState.objects.initiated(
                sourse_course_key, destination_course_key=course.id, user=self.user, display_name="test course"
            )

        # verify return values
        def _set_of_course_keys(course_list, key_attribute_name='id'):
            """Returns a python set of course keys by accessing the key with the given attribute name."""
            return {getattr(c, key_attribute_name) for c in course_list}

        found_courses, unsucceeded_course_actions = _accessible_courses_iter_for_tests(self.request)
        self.assertSetEqual(_set_of_course_keys(courses + courses_in_progress), _set_of_course_keys(found_courses))
        self.assertSetEqual(
            _set_of_course_keys(courses_in_progress), _set_of_course_keys(unsucceeded_course_actions, 'course_key')
        )
