"""
Tests of student.roles
"""

from unittest.mock import patch

import ddt
from django.contrib.auth.models import Permission
from django.test import TestCase
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import LibraryLocator
from openedx_authz.api.data import ContentLibraryData, CourseOverviewData, RoleAssignmentData, RoleData, UserData
from openedx_authz.constants.roles import COURSE_ADMIN, COURSE_STAFF
from openedx_authz.engine.enforcer import AuthzEnforcer

from common.djangoapps.student.admin import CourseAccessRoleHistoryAdmin
from common.djangoapps.student.models import CourseAccessRoleHistory, User
from common.djangoapps.student.role_helpers import get_course_roles, has_staff_roles
from common.djangoapps.student.roles import (
    ROLE_CACHE_UNGROUPED_ROLES__KEY,
    AuthzCompatCourseAccessRole,
    CourseAccessRole,
    CourseBetaTesterRole,
    CourseDataResearcherRole,
    CourseFinanceAdminRole,
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseRole,
    CourseSalesAdminRole,
    CourseStaffRole,
    GlobalStaff,
    LibraryUserRole,
    OrgContentCreatorRole,
    OrgInstructorRole,
    OrgStaffRole,
    RoleCache,
    get_authz_compat_course_access_roles_for_user,
    get_role_cache_key_for_course,
)
from common.djangoapps.student.tests.factories import AnonymousUserFactory, InstructorFactory, StaffFactory, UserFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.toggles import AUTHZ_COURSE_AUTHORING_FLAG


@ddt.ddt
class RolesTestCase(TestCase):
    """
    Tests of student.roles
    """

    def setUp(self):
        super().setUp()
        self._seed_database_with_policies()
        self.course_key = CourseKey.from_string('course-v1:course-v1:edX+toy+2012_Fall')
        self.course_loc = self.course_key.make_usage_key('course', '2012_Fall')
        self.course = CourseOverviewFactory.create(id=self.course_key)
        self.anonymous_user = AnonymousUserFactory()
        self.student = UserFactory()
        self.global_staff = UserFactory(is_staff=True)
        self.course_staff = StaffFactory(course_key=self.course_key)
        self.course_instructor = InstructorFactory(course_key=self.course_key)
        self.orgs = ["Marvel", "DC"]

    @classmethod
    def _seed_database_with_policies(cls):
        """Seed the database with policies from the policy file for openedx_authz tests.

        This simulates the one-time database seeding that would happen
        during application deployment, separate from the runtime policy loading.
        """
        import casbin
        import pkg_resources
        from openedx_authz.engine.utils import migrate_policy_between_enforcers

        global_enforcer = AuthzEnforcer.get_enforcer()
        global_enforcer.load_policy()
        model_path = pkg_resources.resource_filename("openedx_authz.engine", "config/model.conf")
        policy_path = pkg_resources.resource_filename("openedx_authz.engine", "config/authz.policy")

        migrate_policy_between_enforcers(
            source_enforcer=casbin.Enforcer(model_path, policy_path),
            target_enforcer=global_enforcer,
        )
        global_enforcer.clear_policy()  # Clear to simulate fresh start for each test

    @ddt.data(True, False)
    def test_global_staff(self, authz_enabled):
        with override_waffle_flag(AUTHZ_COURSE_AUTHORING_FLAG, active=authz_enabled):
            assert not GlobalStaff().has_user(self.student)
            assert not GlobalStaff().has_user(self.course_staff)
            assert not GlobalStaff().has_user(self.course_instructor)
            assert GlobalStaff().has_user(self.global_staff)

    @ddt.data(True, False)
    def test_has_staff_roles(self, authz_enabled):
        with override_waffle_flag(AUTHZ_COURSE_AUTHORING_FLAG, active=authz_enabled):
            assert has_staff_roles(self.global_staff, self.course_key)
            assert has_staff_roles(self.course_staff, self.course_key)
            assert has_staff_roles(self.course_instructor, self.course_key)
            assert not has_staff_roles(self.student, self.course_key)

    @ddt.data(True, False)
    def test_get_course_roles(self, authz_enabled):
        with override_waffle_flag(AUTHZ_COURSE_AUTHORING_FLAG, active=authz_enabled):
            assert not list(get_course_roles(self.student))
            assert not list(get_course_roles(self.global_staff))
            assert list(get_course_roles(self.course_staff)) == [
                AuthzCompatCourseAccessRole(
                    user_id=self.course_staff.id,
                    username=self.course_staff.username,
                    course_id=self.course_key,
                    org=self.course_key.org,
                    role=CourseStaffRole.ROLE,
                )
            ]
            assert list(get_course_roles(self.course_instructor)) == [
                AuthzCompatCourseAccessRole(
                    user_id=self.course_instructor.id,
                    username=self.course_instructor.username,
                    course_id=self.course_key,
                    org=self.course_key.org,
                    role=CourseInstructorRole.ROLE,
                )
            ]

    def test_group_name_case_sensitive(self):
        uppercase_course_id = "ORG/COURSE/NAME"
        lowercase_course_id = uppercase_course_id.lower()
        uppercase_course_key = CourseKey.from_string(uppercase_course_id)
        lowercase_course_key = CourseKey.from_string(lowercase_course_id)

        role = "role"

        lowercase_user = UserFactory()
        CourseRole(role, lowercase_course_key).add_users(lowercase_user)
        uppercase_user = UserFactory()
        CourseRole(role, uppercase_course_key).add_users(uppercase_user)

        assert CourseRole(role, lowercase_course_key).has_user(lowercase_user)
        assert not CourseRole(role, uppercase_course_key).has_user(lowercase_user)
        assert not CourseRole(role, lowercase_course_key).has_user(uppercase_user)
        assert CourseRole(role, uppercase_course_key).has_user(uppercase_user)

    @ddt.data(True, False)
    def test_course_role(self, authz_enabled):
        """
        Test that giving a user a course role enables access appropriately
        """
        with override_waffle_flag(AUTHZ_COURSE_AUTHORING_FLAG, active=authz_enabled):
            assert not CourseStaffRole(self.course_key).has_user(self.student), \
                f'Student has premature access to {self.course_key}'
            CourseStaffRole(self.course_key).add_users(self.student)
            assert CourseStaffRole(self.course_key).has_user(self.student), \
                f"Student doesn't have access to {str(self.course_key)}"

            # remove access and confirm
            CourseStaffRole(self.course_key).remove_users(self.student)
            assert not CourseStaffRole(self.course_key).has_user(self.student), \
                f'Student still has access to {self.course_key}'

    def test_org_role(self):
        """
        Test that giving a user an org role enables access appropriately
        """
        assert not OrgStaffRole(self.course_key.org).has_user(self.student), \
            f'Student has premature access to {self.course_key.org}'
        OrgStaffRole(self.course_key.org).add_users(self.student)
        assert OrgStaffRole(self.course_key.org).has_user(self.student), \
            f"Student doesn't have access to {str(self.course_key.org)}"

        # remove access and confirm
        OrgStaffRole(self.course_key.org).remove_users(self.student)
        if hasattr(self.student, '_roles'):
            del self.student._roles
        assert not OrgStaffRole(self.course_key.org).has_user(self.student), \
            f'Student still has access to {self.course_key.org}'

    def test_org_and_course_roles(self):
        """
        Test that Org and course roles don't interfere with course roles or vice versa
        """
        OrgInstructorRole(self.course_key.org).add_users(self.student)
        CourseInstructorRole(self.course_key).add_users(self.student)
        assert OrgInstructorRole(self.course_key.org).has_user(self.student), \
            f"Student doesn't have access to {str(self.course_key.org)}"
        assert CourseInstructorRole(self.course_key).has_user(self.student), \
            f"Student doesn't have access to {str(self.course_key)}"

        # remove access and confirm
        OrgInstructorRole(self.course_key.org).remove_users(self.student)
        assert not OrgInstructorRole(self.course_key.org).has_user(self.student), \
            f'Student still has access to {self.course_key.org}'
        assert CourseInstructorRole(self.course_key).has_user(self.student), \
            f"Student doesn't have access to {str(self.course_key)}"

        # ok now keep org role and get rid of course one
        OrgInstructorRole(self.course_key.org).add_users(self.student)
        CourseInstructorRole(self.course_key).remove_users(self.student)
        assert OrgInstructorRole(self.course_key.org).has_user(self.student), \
            f'Student lost has access to {self.course_key.org}'
        assert not CourseInstructorRole(self.course_key).has_user(self.student), \
            f"Student doesn't have access to {str(self.course_key)}"

    @ddt.data(True, False)
    def test_get_user_for_role(self, authz_enabled):
        """
        test users_for_role
        """
        with override_waffle_flag(AUTHZ_COURSE_AUTHORING_FLAG, active=authz_enabled):
            role = CourseStaffRole(self.course_key)
            role.add_users(self.student)
            assert len(role.users_with_role()) > 0

    @ddt.data(True, False)
    def test_add_users_doesnt_add_duplicate_entry(self, authz_enabled):
        """
        Tests that calling add_users multiple times before a single call
        to remove_users does not result in the user remaining in the group.
        """
        with override_waffle_flag(AUTHZ_COURSE_AUTHORING_FLAG, active=authz_enabled):
            role = CourseStaffRole(self.course_key)
            role.add_users(self.student)
            assert role.has_user(self.student)
            # Call add_users a second time, then remove just once.
            role.add_users(self.student)
            role.remove_users(self.student)
            assert not role.has_user(self.student)

    def test_get_orgs_for_user(self):
        """
        Test get_orgs_for_user
        """
        role = OrgContentCreatorRole(org=self.orgs[0])
        assert len(role.get_orgs_for_user(self.student)) == 0
        role.add_users(self.student)
        assert len(role.get_orgs_for_user(self.student)) == 1
        role_second_org = OrgContentCreatorRole(org=self.orgs[1])
        role_second_org.add_users(self.student)
        assert len(role.get_orgs_for_user(self.student)) == 2

    @override_waffle_flag(AUTHZ_COURSE_AUTHORING_FLAG, active=True)
    def test_get_orgs_for_user_authz(self):
        """
        Test get_orgs_for_user using AuthZ compatibility layer
        """
        role = CourseStaffRole(self.course_key)

        other_org = "MIT"
        other_course_key = CourseKey.from_string(f"course-v1:{other_org}+Javascript+2026_T1")
        another_course_key = CourseKey.from_string(f"course-v1:{other_org}+Python+2026_T1")

        staff_authz_role = RoleData(external_key=COURSE_STAFF)
        instructor_authz_role = RoleData(external_key=COURSE_ADMIN)

        assignments = [
            RoleAssignmentData(
                subject=UserData(external_key=self.student.username),
                roles=[staff_authz_role],
                scope=CourseOverviewData(external_key=str(self.course_key)),
            ),
            RoleAssignmentData(
                subject=UserData(external_key=self.student.username),
                roles=[staff_authz_role],
                scope=CourseOverviewData(external_key=str(other_course_key)),
            ),
            RoleAssignmentData(
                subject=UserData(external_key=self.student.username),
                roles=[staff_authz_role],
                scope=CourseOverviewData(external_key=str(another_course_key)),
            ),
            # Non-matching role should be ignored
            RoleAssignmentData(
                subject=UserData(external_key=self.student.username),
                roles=[instructor_authz_role],
                scope=CourseOverviewData(external_key=str(self.course_key)),
            ),
        ]

        with patch("openedx_authz.api.users.get_user_role_assignments_filtered", return_value=assignments):
            result = role.get_orgs_for_user(self.student)
            self.assertCountEqual(result, [self.course_key.org, other_org])  # noqa: PT009

    def test_get_authz_compat_course_access_roles_for_user(self):
        """
        Test that get_authz_compat_course_access_roles_for_user doesn't crash when the user
        has Libraries V2 or other non-course roles in their assignments.
        """
        lib_assignment = RoleAssignmentData(
            subject=UserData(external_key=self.student.username),
            roles=[RoleData(external_key='test-role')],
            scope=ContentLibraryData(external_key='lib:edX:test-lib'),
        )
        with patch(
            'openedx_authz.api.users.get_subject_role_assignments',
            return_value=[lib_assignment],
        ):
            result = get_authz_compat_course_access_roles_for_user(self.student)
        assert result == set()


@ddt.ddt
class RoleCacheTestCase(TestCase):  # pylint: disable=missing-class-docstring

    IN_KEY_STRING = 'course-v1:edX+toy+2012_Fall'
    IN_KEY = CourseKey.from_string(IN_KEY_STRING)
    NOT_IN_KEY = CourseKey.from_string('course-v1:edX+toy+2013_Fall')

    ROLES = (
        (CourseStaffRole(IN_KEY), ('staff', IN_KEY, 'edX')),
        (CourseLimitedStaffRole(IN_KEY), ('limited_staff', IN_KEY, 'edX')),
        (CourseInstructorRole(IN_KEY), ('instructor', IN_KEY, 'edX')),
        (OrgStaffRole(IN_KEY.org), ('staff', None, 'edX')),
        (CourseFinanceAdminRole(IN_KEY), ('finance_admin', IN_KEY, 'edX')),
        (CourseSalesAdminRole(IN_KEY), ('sales_admin', IN_KEY, 'edX')),
        (LibraryUserRole(IN_KEY), ('library_user', IN_KEY, 'edX')),
        (CourseDataResearcherRole(IN_KEY), ('data_researcher', IN_KEY, 'edX')),
        (OrgInstructorRole(IN_KEY.org), ('instructor', None, 'edX')),
        (CourseBetaTesterRole(IN_KEY), ('beta_testers', IN_KEY, 'edX')),
    )

    def setUp(self):
        super().setUp()
        self.user = UserFactory()

    @ddt.data(*ROLES)
    @ddt.unpack
    def test_only_in_role(self, role, target):
        role.add_users(self.user)
        cache = RoleCache(self.user)
        assert cache.has_role(*target)

        for other_role, other_target in self.ROLES:
            if other_role == role:
                continue

            role_base_id = getattr(role, "BASE_ROLE", None)
            other_role_id = getattr(other_role, "ROLE", None)

            if other_role_id and role_base_id == other_role_id:
                assert cache.has_role(*other_target)
            else:
                assert not cache.has_role(*other_target)

    @ddt.data(*ROLES)
    @ddt.unpack
    def test_empty_cache(self, role, target):  # pylint: disable=unused-argument
        cache = RoleCache(self.user)
        assert not cache.has_role(*target)

    def test_get_role_cache_key_for_course_for_course_object_gets_string(self):
        """
        Given a valid course key object, get_role_cache_key_for_course
        should return the string representation of the key.
        """
        course_string = 'course-v1:edX+toy+2012_Fall'
        key = CourseKey.from_string(course_string)
        key = get_role_cache_key_for_course(key)
        assert key == course_string

    def test_get_role_cache_key_for_course_for_undefined_object_returns_default(self):
        """
        Given a value None, get_role_cache_key_for_course
        should return the default key for ungrouped courses.
        """
        key = get_role_cache_key_for_course(None)
        assert key == ROLE_CACHE_UNGROUPED_ROLES__KEY

    def test_role_cache_get_roles_set(self):
        """
        Test that the RoleCache.all_roles_set getter method returns a flat set of all roles for a user
        and that the ._roles attribute is the same as the set to avoid legacy behavior being broken.
        """
        lib0 = LibraryLocator.from_string('library-v1:edX+quizzes')
        course0 = CourseKey.from_string('course-v1:edX+toy+2012_Summer')
        course1 = CourseKey.from_string('course-v1:edX+toy2+2013_Fall')
        role_library_v1 = LibraryUserRole(lib0)
        role_course_0 = CourseInstructorRole(course0)
        role_course_1 = CourseInstructorRole(course1)

        role_library_v1.add_users(self.user)
        role_course_0.add_users(self.user)
        role_course_1.add_users(self.user)

        cache = RoleCache(self.user)
        assert cache.has_role('library_user', lib0, 'edX')
        assert cache.has_role('instructor', course0, 'edX')
        assert cache.has_role('instructor', course1, 'edX')

        assert len(cache.all_roles_set) == 3
        roles_set = cache.all_roles_set
        for role in roles_set:
            assert role.course_id.course in ('quizzes', 'toy2', 'toy')

        assert roles_set == cache._roles  # pylint: disable=protected-access

    def test_role_cache_roles_by_course_id(self):
        """
        Test that the RoleCache.roles_by_course_id getter method returns a dictionary of roles for a user
        that are grouped by course_id or if ungrouped by the ROLE_CACHE_UNGROUPED_ROLES__KEY.
        """
        lib0 = LibraryLocator.from_string('library-v1:edX+quizzes')
        course0 = CourseKey.from_string('course-v1:edX+toy+2012_Summer')
        course1 = CourseKey.from_string('course-v1:edX+toy2+2013_Fall')
        role_library_v1 = LibraryUserRole(lib0)
        role_course_0 = CourseInstructorRole(course0)
        role_course_1 = CourseInstructorRole(course1)
        role_org_staff = OrgStaffRole('edX')

        role_library_v1.add_users(self.user)
        role_course_0.add_users(self.user)
        role_course_1.add_users(self.user)
        role_org_staff.add_users(self.user)

        cache = RoleCache(self.user)
        roles_dict = cache.roles_by_course_id
        assert len(roles_dict) == 4
        assert roles_dict.get(ROLE_CACHE_UNGROUPED_ROLES__KEY).pop().role == 'staff'
        assert roles_dict.get('library-v1:edX+quizzes').pop().course_id.course == 'quizzes'
        assert roles_dict.get('course-v1:edX+toy+2012_Summer').pop().course_id.course == 'toy'
        assert roles_dict.get('course-v1:edX+toy2+2013_Fall').pop().course_id.course == 'toy2'


class CourseAccessRoleHistoryTest(TestCase):
    """
    Tests for the CourseAccessRoleHistory model and associated signals/admin actions.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory(username="test_user", email="test@example.com")
        self.admin_user = UserFactory(
            username="admin_user",
            email="admin@example.com",
            is_staff=True,
            is_superuser=True,
        )
        self.course_key = CourseKey.from_string("course-v1:OrgX+CourseY+2023_Fall")
        self.org = "OrgX"

        revert_permission = Permission.objects.get(
            codename="can_revert_course_access_role", content_type__app_label="student"
        )
        delete_history_permission = Permission.objects.get(
            codename="can_delete_course_access_role_history",
            content_type__app_label="student",
        )
        self.admin_user.user_permissions.add(
            revert_permission, delete_history_permission
        )
        self.admin_user = User.objects.get(pk=self.admin_user.pk)

    def test_create_logs_history(self):
        """
        Test that creating a CourseAccessRole logs a history entry.
        """
        CourseAccessRole.objects.create(
            user=self.user, org=self.org, course_id=self.course_key, role="student"
        )

        history = CourseAccessRoleHistory.objects.first()
        self.assertIsNotNone(history)  # noqa: PT009
        self.assertEqual(history.user, self.user)  # noqa: PT009
        self.assertEqual(history.org, self.org)  # noqa: PT009
        self.assertEqual(history.course_id, self.course_key)  # noqa: PT009
        self.assertEqual(history.role, "student")  # noqa: PT009
        self.assertEqual(history.action_type, "created")  # noqa: PT009
        self.assertIsNone(history.old_values)  # noqa: PT009

    def test_update_logs_history(self):
        """
        Test that updating a CourseAccessRole logs a history entry with old_values.
        """
        role_instance = CourseAccessRole.objects.create(
            user=self.user, org=self.org, course_id=self.course_key, role="student"
        )
        role_instance.role = "staff"
        role_instance.save()

        history_entries = CourseAccessRoleHistory.objects.filter(
            user=self.user, course_id=self.course_key
        ).order_by("created")
        self.assertEqual(history_entries.count(), 2)  # noqa: PT009

        update_history = history_entries.last()
        self.assertEqual(update_history.action_type, "updated")  # noqa: PT009
        self.assertIsNotNone(update_history.old_values)  # noqa: PT009
        self.assertEqual(update_history.old_values["role"], "student")  # noqa: PT009
        self.assertEqual(update_history.role, "staff")  # noqa: PT009

    def test_delete_logs_history(self):
        """
        Test that deleting a CourseAccessRole logs a history entry.
        """
        role_instance = CourseAccessRole.objects.create(
            user=self.user, org=self.org, course_id=self.course_key, role="student"
        )

        role_instance.delete()

        history_entries = CourseAccessRoleHistory.objects.filter(
            user=self.user, course_id=self.course_key
        ).order_by("created")
        self.assertEqual(history_entries.count(), 2)  # noqa: PT009

        delete_history = history_entries.last()
        self.assertEqual(delete_history.action_type, "deleted")  # noqa: PT009
        self.assertIsNone(delete_history.old_values)  # noqa: PT009
        self.assertEqual(delete_history.role, "student")  # noqa: PT009


class CourseAccessRoleAdminActionsTest(TestCase):
    """
    Tests for the admin actions (revert, delete) on CourseAccessRoleHistory.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory(
            username="test_user_admin", email="test_admin@example.com"
        )
        self.admin_user = UserFactory(
            username="admin_action_user",
            email="admin_action@example.com",
            is_staff=True,
            is_superuser=True,
        )
        self.course_key = CourseKey.from_string(
            "course-v1:AdminOrg+AdminCourse+2024_Spring"
        )
        self.org = "AdminOrg"
        revert_permission = Permission.objects.get(
            codename="can_revert_course_access_role", content_type__app_label="student"
        )
        delete_history_permission = Permission.objects.get(
            codename="can_delete_course_access_role_history",
            content_type__app_label="student",
        )
        self.admin_user.user_permissions.add(
            revert_permission, delete_history_permission
        )
        self.admin_user = User.objects.get(pk=self.admin_user.pk)
        self.messages = []

    def _get_admin_action_response(self, action, queryset):
        """Helper to call admin actions and capture messages."""
        from django.contrib.admin import AdminSite

        model_admin = CourseAccessRoleHistoryAdmin(CourseAccessRoleHistory, AdminSite())
        request = self.client.get("/")
        request.user = self.admin_user

        def mock_message_user(request, message, level=None):
            self.messages.append(message)

        model_admin.message_user = mock_message_user

        response = action(model_admin, request, queryset)
        return response

    def test_revert_created_action(self):
        """
        Test reverting a 'created' history entry should delete the CourseAccessRole.
        """
        CourseAccessRole.objects.create(
            user=self.user, org=self.org, course_id=self.course_key, role="beta_tester"
        )
        self.assertEqual(CourseAccessRole.objects.count(), 1)  # noqa: PT009
        created_history = CourseAccessRoleHistory.objects.filter(
            action_type="created"
        ).first()
        self.assertIsNotNone(created_history)  # noqa: PT009

        self._get_admin_action_response(
            CourseAccessRoleHistoryAdmin.revert_selected_history,
            CourseAccessRoleHistory.objects.filter(pk=created_history.pk),
        )

        self.assertEqual(CourseAccessRole.objects.count(), 0)  # noqa: PT009
        self.assertIn(  # noqa: PT009
            f"Successfully reverted creation of role for {self.user.username} in {self.course_key}",
            self.messages[0],
        )

    def test_revert_updated_action(self):
        """
        Test reverting an 'updated' history entry should restore the CourseAccessRole to its old_values.
        """
        role_instance = CourseAccessRole.objects.create(
            user=self.user, org=self.org, course_id=self.course_key, role="old_role"
        )

        role_instance.role = "new_role"
        role_instance.save()

        self.assertEqual(CourseAccessRole.objects.get().role, "new_role")  # noqa: PT009
        updated_history = CourseAccessRoleHistory.objects.filter(
            action_type="updated"
        ).first()
        self.assertIsNotNone(updated_history)  # noqa: PT009
        self.assertEqual(updated_history.old_values["role"], "old_role")  # noqa: PT009

        self._get_admin_action_response(
            CourseAccessRoleHistoryAdmin.revert_selected_history,
            CourseAccessRoleHistory.objects.filter(pk=updated_history.pk),
        )

        self.assertEqual(CourseAccessRole.objects.get().role, "old_role")  # noqa: PT009
        self.assertIn(  # noqa: PT009
            f"Successfully reverted update of role for {self.user.username} to old_role in {self.course_key}",
            self.messages[0],
        )

    def test_revert_deleted_action(self):
        """
        Test reverting a 'deleted' history entry should recreate the CourseAccessRole.
        """
        role_instance = CourseAccessRole.objects.create(
            user=self.user,
            org=self.org,
            course_id=self.course_key,
            role="to_be_deleted",
        )
        self.assertEqual(CourseAccessRole.objects.count(), 1)  # noqa: PT009
        initial_history_count = CourseAccessRoleHistory.objects.count()  # noqa: F841

        role_instance.delete()
        self.assertEqual(CourseAccessRole.objects.count(), 0)  # noqa: PT009
        deleted_history = CourseAccessRoleHistory.objects.filter(
            action_type="deleted"
        ).first()
        self.assertIsNotNone(deleted_history)  # noqa: PT009

        self._get_admin_action_response(
            CourseAccessRoleHistoryAdmin.revert_selected_history,
            CourseAccessRoleHistory.objects.filter(pk=deleted_history.pk),
        )

        self.assertEqual(CourseAccessRole.objects.count(), 1)  # noqa: PT009
        reverted_role = CourseAccessRole.objects.first()
        self.assertEqual(reverted_role.user, self.user)  # noqa: PT009
        self.assertEqual(reverted_role.org, self.org)  # noqa: PT009
        self.assertEqual(reverted_role.course_id, self.course_key)  # noqa: PT009
        self.assertEqual(reverted_role.role, "to_be_deleted")  # noqa: PT009
        self.assertIn(  # noqa: PT009
            f"Successfully reverted deletion of role for {self.user.username} in {self.course_key}",
            self.messages[0],
        )

    def test_delete_history_action(self):
        """
        Test the admin action to delete selected history entries.
        """
        CourseAccessRole.objects.create(
            user=self.user, org=self.org, course_id=self.course_key, role="some_role"
        )
        self.assertEqual(CourseAccessRoleHistory.objects.count(), 1)  # noqa: PT009
        history_entry = CourseAccessRoleHistory.objects.first()

        self._get_admin_action_response(
            CourseAccessRoleHistoryAdmin.delete_selected_history_entries,
            CourseAccessRoleHistory.objects.filter(pk=history_entry.pk),
        )

        self.assertEqual(CourseAccessRoleHistory.objects.count(), 0)  # noqa: PT009
        self.assertIn("Successfully deleted 1 selected history entry.", self.messages[0])  # noqa: PT009
