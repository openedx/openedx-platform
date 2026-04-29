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
from openedx_authz.api.data import OrgCourseOverviewGlobData
from openedx_authz.api.users import assign_role_to_user_in_scope
from openedx_authz.constants.roles import COURSE_DATA_RESEARCHER, COURSE_EDITOR, COURSE_STAFF

from cms.djangoapps.contentstore.tests.utils import AjaxEnabledTestClient
from cms.djangoapps.contentstore.utils import delete_course
from cms.djangoapps.contentstore.views.course import (
    AccessListFallback,
    _accessible_courses_iter_for_tests,
    _accessible_courses_list_from_groups,
    _accessible_courses_summary_iter,
    get_courses_accessible_to_user,
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
    UserBasedRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.waffle_utils.testutils import WAFFLE_TABLES
from openedx.core.djangolib.testing.utils import AUTHZ_TABLES
from xmodule.modulestore import ModuleStoreEnum  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order

TOTAL_COURSES_COUNT = 10
USER_COURSES_COUNT = 1

QUERY_COUNT_TABLE_IGNORELIST = WAFFLE_TABLES + AUTHZ_TABLES


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
        self.assertEqual(len(courses_list), 1)  # noqa: PT009

        courses_summary_list, __ = _accessible_courses_summary_iter(self.request)
        self.assertEqual(len(list(courses_summary_list)), 1)  # noqa: PT009

        # get courses by reversing group name formats
        courses_list_by_groups, __ = _accessible_courses_list_from_groups(self.request)
        self.assertEqual(len(courses_list_by_groups), 1)  # noqa: PT009

        # check both course lists have same courses
        course_keys_in_course_list = [course.id for course in courses_list]
        course_keys_in_courses_list_by_groups = [course.id for course in courses_list_by_groups]

        self.assertEqual(course_keys_in_course_list, course_keys_in_courses_list_by_groups)  # noqa: PT009

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
        self.assertEqual(len(courses_list), 1)  # noqa: PT009
        self.assertNotIn(  # noqa: PT009
            ccx_course_key,
            [course.id for course in courses_list]
        )

        # Get all courses which user has access.
        instructor_courses = UserBasedRole(self.user, CourseInstructorRole.ROLE).courses_with_role()
        staff_courses = UserBasedRole(self.user, CourseStaffRole.ROLE).courses_with_role()
        all_courses = (instructor_courses | staff_courses)

        # Verify that CCX course exists in access but filtered by `_accessible_courses_list_from_groups`.
        self.assertIn(  # noqa: PT009
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
            self.assertEqual(len(list(courses_iter)), 0)  # noqa: PT009

    def test_staff_course_listing(self):
        """
        Create courses and verify they take certain amount of mongo calls to call get_courses_accessible_to_user.
        Also verify that fetch accessible courses list for staff user returns CourseSummary instances.
        """

        # Assign & verify staff role to the user
        GlobalStaff().add_users(self.user)
        self.assertTrue(GlobalStaff().has_user(self.user))  # noqa: PT009

        # Create few courses
        for num in range(TOTAL_COURSES_COUNT):
            course_location = self.store.make_course_key('Org', 'CreatedCourse' + str(num), 'Run')
            self._create_course_with_access_groups(course_location, self.user)

        # Fetch accessible courses list & verify their count
        courses_list_by_staff, __ = get_courses_accessible_to_user(self.request)

        self.assertEqual(len(list(courses_list_by_staff)), TOTAL_COURSES_COUNT)  # noqa: PT009
        self.assertTrue(all(isinstance(course, CourseOverview) for course in courses_list_by_staff))  # noqa: PT009

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
        self.assertTrue(CourseLimitedStaffRole(course.id).has_user(self.user))  # noqa: PT009

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
        course = CourseOverviewFactory.create(id=course_location, org=course_location.org)  # noqa: F841

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
        self.assertEqual(len(courses_list), 1)  # noqa: PT009

        courses_summary_iter, __ = _accessible_courses_summary_iter(self.request)
        courses_summary_list = list(courses_summary_iter)
        self.assertTrue(all(isinstance(course, CourseOverview) for course in courses_summary_list))  # noqa: PT009
        self.assertEqual(len(courses_summary_list), 1)  # noqa: PT009

        # get courses by reversing group name formats
        courses_list_by_groups, __ = _accessible_courses_list_from_groups(self.request)
        self.assertEqual(len(courses_list_by_groups), 1)  # noqa: PT009

        course_keys_in_course_list = [course.id for course in courses_list]
        course_keys_in_courses_list_by_groups = [course.id for course in courses_list_by_groups]
        # check course lists have same courses
        self.assertEqual(course_keys_in_course_list, course_keys_in_courses_list_by_groups)  # noqa: PT009
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
        self.assertEqual(  # noqa: PT009
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
        self.assertEqual(len(list(courses_iter)), USER_COURSES_COUNT)  # noqa: PT009

        # again get courses by iterating through all courses
        courses_iter, __ = _accessible_courses_iter_for_tests(self.request)
        self.assertEqual(len(list(courses_iter)), USER_COURSES_COUNT)  # noqa: PT009

        # get courses by reversing django groups
        courses_list, __ = _accessible_courses_list_from_groups(self.request)
        self.assertEqual(len(courses_list), USER_COURSES_COUNT)  # noqa: PT009

        # again get courses by reversing django groups
        courses_list, __ = _accessible_courses_list_from_groups(self.request)
        self.assertEqual(len(courses_list), USER_COURSES_COUNT)  # noqa: PT009

        with self.assertNumQueries(2, table_ignorelist=QUERY_COUNT_TABLE_IGNORELIST):
            _accessible_courses_list_from_groups(self.request)

        with self.assertNumQueries(2, table_ignorelist=QUERY_COUNT_TABLE_IGNORELIST):
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
        self.assertEqual(len(courses_list), 1, courses_list)  # noqa: PT009

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
        self.assertEqual(len(list(courses_list)), 2)  # noqa: PT009
        self.assertTrue(all(isinstance(course, CourseOverview) for course in courses_list))  # noqa: PT009

    @ddt.data(OrgStaffRole(), OrgInstructorRole())
    def test_course_listing_org_permissions_exception(self, role):
        """
        Create roles with no course_id neither org to make sure AccessListFallback is raised for
        platform-wide permissions
        """
        role.add_users(self.user)

        with self.assertRaises(AccessListFallback):  # noqa: PT027
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
        self.assertSetEqual(_set_of_course_keys(courses + courses_in_progress), _set_of_course_keys(found_courses))  # noqa: PT009  # pylint: disable=line-too-long
        self.assertSetEqual(  # noqa: PT009
            _set_of_course_keys(courses_in_progress), _set_of_course_keys(unsucceeded_course_actions, 'course_key')
        )


class TestCourseListingAuthz(CourseAuthoringAuthzTestMixin, ModuleStoreTestCase):
    """
    Tests course listing using the new AuthZ authorization framework.
    """

    def setUp(self):
        super().setUp()

        self.factory = RequestFactory()

    def _create_course(self, course_key):
        """Helper method to create a course and its overview."""
        course = CourseFactory.create(
            org=course_key.org,
            number=course_key.course,
            run=course_key.run,
        )

        return CourseOverviewFactory.create(id=course.id, org=course_key.org)

    def _mock_authz_toggle(self, enabled_keys):
        def _is_enabled(course_key=None, **_):
            return str(course_key) in enabled_keys
        return _is_enabled

    def _make_request(self, user):
        request = self.factory.get("/course")
        request.user = user
        return request

    def _create_courses(self):
        """Helper method to create multiple courses for testing."""
        authz_keys = [
            CourseLocator("Org1", "Course1", "AuthzRun"),
            CourseLocator("Org1", "Course2", "AuthzRun"),
            CourseLocator("Org1", "Course3", "AuthzRun"),
        ]

        legacy_keys = [
            CourseLocator("Org1", "Course1", "LegacyRun"),
            CourseLocator("Org1", "Course2", "LegacyRun"),
            CourseLocator("Org1", "Course3", "LegacyRun"),
        ]

        authz_courses = [self._create_course(k) for k in authz_keys]
        legacy_courses = [self._create_course(k) for k in legacy_keys]

        return authz_keys, legacy_keys, authz_courses, legacy_courses

    def test_course_listing_with_course_staff_authz_permission(self):
        """
        Create courses and assign access to only some of them to the user.
        Verify that only those courses are returned in the course listing.
        Using COURSE_STAFF role here.
        """
        course_key_1 = CourseLocator("Org1", "Course1", "Run1")
        course1 = self._create_course(course_key_1)

        course_key_2 = CourseLocator("Org1", "Course2", "Run1")
        course2 = self._create_course(course_key_2)  # noqa: F841

        assign_role_to_user_in_scope(
            self.authorized_user.username,
            COURSE_STAFF.external_key,
            str(course_key_1),
        )

        request = self.factory.get("/course")
        request.user = self.authorized_user

        courses_list, _ = get_courses_accessible_to_user(request)

        courses = list(courses_list)

        self.assertEqual(len(courses), 1)  # noqa: PT009
        self.assertEqual(courses[0].id, course1.id)  # noqa: PT009
        self.assertNotIn(course_key_2, {c.id for c in courses})  # noqa: PT009

    def test_course_listing_with_course_editor_authz_permission(self):
        """
        Create courses and assign access to only some of them to the user.
        Verify that only those courses are returned in the course listing.
        Using COURSE_EDITOR role here.
        """
        course_key_1 = CourseLocator("Org1", "Course1", "Run1")
        course1 = self._create_course(course_key_1)

        course_key_2 = CourseLocator("Org1", "Course2", "Run1")
        course2 = self._create_course(course_key_2)  # noqa: F841

        assign_role_to_user_in_scope(
            self.authorized_user.username,
            COURSE_EDITOR.external_key,
            str(course_key_1),
        )

        request = self.factory.get("/course")
        request.user = self.authorized_user

        courses_list, _ = get_courses_accessible_to_user(request)

        courses = list(courses_list)

        self.assertEqual(len(courses), 1)  # noqa: PT009
        self.assertEqual(courses[0].id, course1.id)  # noqa: PT009
        self.assertNotIn(course_key_2, {c.id for c in courses})  # noqa: PT009

    def test_course_listing_without_permissions(self):
        """
        Create a course but do not assign access to the user.
        Verify that no courses are returned in the course listing.
        """
        course_key = CourseLocator("Org1", "Course1", "Run1")

        self._create_course(course_key)

        request = self.factory.get("/course")
        request.user = self.unauthorized_user

        courses_list, _ = get_courses_accessible_to_user(request)

        self.assertEqual(len(list(courses_list)), 0)  # noqa: PT009

    def test_non_staff_user_cannot_access(self):
        """
        Create a course and assign a non-staff role to the user.
        Verify that the course is not returned in the course listing.
        """
        non_staff_user = UserFactory()
        course_key = CourseLocator("Org1", "Course1", "Run1")
        self._create_course(course_key)
        self.add_user_to_role_in_course(non_staff_user, COURSE_DATA_RESEARCHER.external_key, course_key)

        request = self.factory.get("/course")
        request.user = non_staff_user

        courses_list, _ = get_courses_accessible_to_user(request)

        self.assertEqual(len(list(courses_list)), 0)  # noqa: PT009

    def test_authz_and_legacy_basic(self):
        """
        AuthZ roles should only apply when toggle is enabled.
        Legacy roles should still grant access.
        """
        authz_keys, legacy_keys, authz_courses, legacy_courses = self._create_courses()

        enabled_keys = {str(authz_keys[0]), str(authz_keys[2])}

        with patch.object(
            core_toggles.AUTHZ_COURSE_AUTHORING_FLAG,
            "is_enabled",
            side_effect=self._mock_authz_toggle(enabled_keys),
        ):
            user = UserFactory()

            # AuthZ roles
            assign_role_to_user_in_scope(
                user.username,
                COURSE_STAFF.external_key,
                str(authz_keys[0]),  # toggle ON → valid
            )
            assign_role_to_user_in_scope(
                user.username,
                COURSE_EDITOR.external_key,
                str(authz_keys[1]),  # toggle OFF → ignored
            )

            # Legacy role
            CourseInstructorRole(legacy_keys[0]).add_users(user)

            courses, _ = get_courses_accessible_to_user(self._make_request(user))

            result_ids = {c.id for c in courses}

            expected_ids = {
                authz_courses[0].id,
                legacy_courses[0].id,
            }

            self.assertEqual(result_ids, expected_ids)  # noqa: PT009

    def test_authz_role_ignored_when_toggle_off(self):
        """
        AuthZ role should not grant access if toggle is disabled for that course.
        """
        authz_keys, _, authz_courses, _ = self._create_courses()

        enabled_keys = {str(authz_keys[2])}  # only Course3 enabled

        with patch.object(
            core_toggles.AUTHZ_COURSE_AUTHORING_FLAG,
            "is_enabled",
            side_effect=self._mock_authz_toggle(enabled_keys),
        ):
            user = UserFactory()

            assign_role_to_user_in_scope(
                user.username,
                COURSE_EDITOR.external_key,
                str(authz_keys[1]),  # toggle OFF → ignored
            )

            courses, _ = get_courses_accessible_to_user(self._make_request(user))

            result_ids = {c.id for c in courses}
            expected_ids = set()  # no access since toggle is off

            self.assertEqual(result_ids, expected_ids)  # noqa: PT009

    def test_multiple_roles_mixed_authz_and_legacy(self):
        """
        User should receive:
        - AuthZ courses when toggle is enabled
        - Legacy courses independently
        """
        authz_keys, legacy_keys, authz_courses, legacy_courses = self._create_courses()

        enabled_keys = {str(k) for k in authz_keys}  # all enabled

        with patch.object(
            core_toggles.AUTHZ_COURSE_AUTHORING_FLAG,
            "is_enabled",
            side_effect=self._mock_authz_toggle(enabled_keys),
        ):
            user = UserFactory()

            # AuthZ roles
            assign_role_to_user_in_scope(
                user.username,
                COURSE_STAFF.external_key,
                str(authz_keys[0]),
            )
            assign_role_to_user_in_scope(
                user.username,
                COURSE_EDITOR.external_key,
                str(authz_keys[1]),
            )

            # Legacy role
            CourseInstructorRole(legacy_keys[2]).add_users(user)

            courses, _ = get_courses_accessible_to_user(self._make_request(user))

            result_ids = {c.id for c in courses}

            expected_ids = {
                authz_courses[0].id,
                authz_courses[1].id,
                legacy_courses[2].id,
            }

            self.assertEqual(result_ids, expected_ids)  # noqa: PT009

    def test_staff_gets_all_courses(self):
        """
        Global staff should bypass AuthZ/legacy restrictions and get all courses.
        """
        authz_keys, legacy_keys, authz_courses, legacy_courses = self._create_courses()

        with patch.object(
            core_toggles.AUTHZ_COURSE_AUTHORING_FLAG,
            "is_enabled",
            return_value=False,  # irrelevant for staff
        ):
            user = UserFactory()
            GlobalStaff().add_users(user)

            courses, _ = get_courses_accessible_to_user(self._make_request(user))

            result_ids = {c.id for c in courses}

            expected_ids = {
                *(c.id for c in authz_courses),
                *(c.id for c in legacy_courses),
            }

            self.assertEqual(result_ids, expected_ids)  # noqa: PT009

    def test_superuser_gets_all_courses(self):
        """
        Superuser should bypass all permission checks and get all courses.
        """
        _, _, authz_courses, legacy_courses = self._create_courses()

        with patch.object(
            core_toggles.AUTHZ_COURSE_AUTHORING_FLAG,
            "is_enabled",
            return_value=False,  # irrelevant for superuser
        ):
            user = UserFactory(is_superuser=True)

            courses, _ = get_courses_accessible_to_user(self._make_request(user))

            result_ids = {c.id for c in courses}

            expected_ids = {
                *(c.id for c in authz_courses),
                *(c.id for c in legacy_courses),
            }

            self.assertEqual(result_ids, expected_ids)  # noqa: PT009

    def test_course_listing_with_org_scope(self):
        """
        Verify that assigning a course role like course_staff with an org-wide scope
        (`course-v1:Org1+*`) grants access to all courses in that org when
        the AuthZ course authoring toggle is enabled.
        """
        _, _, authz_courses, legacy_courses = self._create_courses()
        org_scope = OrgCourseOverviewGlobData(external_key='course-v1:Org1+*')
        assign_role_to_user_in_scope(
            self.authorized_user.username,
            COURSE_STAFF.external_key,
            org_scope.external_key,
        )

        request = self._make_request(self.authorized_user)

        with patch.object(
            core_toggles.AUTHZ_COURSE_AUTHORING_FLAG,
            "is_enabled",
            return_value=True,
        ):
            courses, _ = get_courses_accessible_to_user(request)

            result_ids = {c.id for c in courses}

            expected_ids = {
                *(c.id for c in authz_courses),
                *(c.id for c in legacy_courses),
            }

            self.assertEqual(result_ids, expected_ids)  # noqa: PT009

    def test_course_listing_with_org_scope_with_toggle(self):
        """
        If the authz toggle is enabled only for a subset of org courses, only
        those course keys should appear in the resulting course list.
        """
        authz_keys, _, _, _ = self._create_courses()
        # enable only the first and third course keys
        enabled_keys = {str(authz_keys[0]), str(authz_keys[2])}
        org_scope = OrgCourseOverviewGlobData(external_key='course-v1:Org1+*')
        assign_role_to_user_in_scope(
            self.authorized_user.username,
            COURSE_STAFF.external_key,
            org_scope.external_key,
        )

        request = self._make_request(self.authorized_user)

        with patch.object(
            core_toggles.AUTHZ_COURSE_AUTHORING_FLAG,
            "is_enabled",
            side_effect=self._mock_authz_toggle(enabled_keys),
        ):
            courses, _ = get_courses_accessible_to_user(request)

            result_ids = {c.id for c in courses}

            expected = {authz_keys[0], authz_keys[2]}
            self.assertEqual(result_ids, expected)  # noqa: PT009

    def test_course_listing_with_org_scope_without_courses(self):
        """
        When the scope is an OrgCourseOverviewGlobData for an org that has no
        courses, `get_courses_accessible_to_user` should return an empty
        list.
        """
        org_scope = OrgCourseOverviewGlobData(external_key='course-v1:Org2+*')
        assign_role_to_user_in_scope(
            self.authorized_user.username,
            COURSE_STAFF.external_key,
            org_scope.external_key,
        )

        request = self._make_request(self.authorized_user)

        with patch.object(
            core_toggles.AUTHZ_COURSE_AUTHORING_FLAG,
            "is_enabled",
            return_value=True,
        ):
            courses, _ = get_courses_accessible_to_user(request)

            self.assertEqual(courses, [])  # noqa: PT009

    def test_course_listing_with_org_scope_fetched_once(self):
        """
        Verify that course overviews are fetched once with all authorized orgs.
        """
        org_scope1 = OrgCourseOverviewGlobData(external_key='course-v1:Org1+*')
        org_scope2 = OrgCourseOverviewGlobData(external_key='course-v1:Org2+*')
        assign_role_to_user_in_scope(
            self.authorized_user.username,
            COURSE_STAFF.external_key,
            org_scope1.external_key,
        )
        assign_role_to_user_in_scope(
            self.authorized_user.username,
            COURSE_STAFF.external_key,
            org_scope2.external_key,
        )

        request = self._make_request(self.authorized_user)

        with patch.object(
            core_toggles.AUTHZ_COURSE_AUTHORING_FLAG,
            "is_enabled",
            return_value=True,
        ), patch.object(
            CourseOverview,
            "get_all_courses",
        ) as mock_get_all_courses:
            courses, _ = get_courses_accessible_to_user(request)

        mock_get_all_courses.assert_called_once_with(orgs={"Org1", "Org2"})
