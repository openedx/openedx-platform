"""
Tests for the Instructor Dashboard AuthZ API.
"""

from unittest.mock import Mock, patch

import ddt
from django.test import TestCase
from opaque_keys.edx.locator import CourseLocator
from openedx_authz.constants.roles import COURSE_ADMIN, COURSE_AUDITOR, COURSE_STAFF

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.instructor.authz import constants
from lms.djangoapps.instructor.authz.api import (
    get_instructor_authz_permissions,
    user_has_any_instructor_permission,
    user_has_instructor_permission,
)
from lms.djangoapps.instructor.authz.tests.mixins import InstructorAuthzTestMixin

COURSE_KEY = CourseLocator("edX", "Demo", "2026")
API = "lms.djangoapps.instructor.authz.api"


@ddt.ddt
class UserHasInstructorPermissionTest(InstructorAuthzTestMixin, TestCase):
    """
    Permissions use AuthZ with the flag on, and legacy checks otherwise.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory()

    @ddt.data(
        (COURSE_ADMIN.external_key, constants.COURSES_VIEW_COURSE_INFO, True),
        (COURSE_STAFF.external_key, constants.COURSES_ENROLL_LEARNERS, True),
        (COURSE_STAFF.external_key, constants.COURSES_MANAGE_COHORTS, True),
        (COURSE_ADMIN.external_key, constants.COURSES_MANAGE_COURSE_TEAM, True),
        # Course Staff must not get ``manage_course_team``.
        (COURSE_STAFF.external_key, constants.COURSES_MANAGE_COURSE_TEAM, False),
        (COURSE_AUDITOR.external_key, constants.COURSES_VIEW_ENROLLMENTS, False),
    )
    @ddt.unpack
    def test_flag_enabled_uses_authz(self, role, permission, expected):
        """With the flag on, the result is the one of the AuthZ role of the user."""
        self.assign_authz_role(self.user, role, COURSE_KEY)

        assert user_has_instructor_permission(self.user, permission, COURSE_KEY) == expected

    def test_flag_enabled_ignores_legacy_check(self):
        """With the flag on, the legacy check is never called."""
        legacy_check = Mock(return_value=True)

        allowed = user_has_instructor_permission(
            self.user, constants.COURSES_VIEW_ENROLLMENTS, COURSE_KEY, legacy_check
        )

        assert not allowed
        legacy_check.assert_not_called()

    def test_flag_disabled_ignores_authz(self):
        """With the flag off, AuthZ roles are ignored and only the legacy check is called."""
        self.set_authz_enabled(False)
        self.assign_authz_role(self.user, COURSE_ADMIN.external_key, COURSE_KEY)
        legacy_check = Mock(return_value=False)

        allowed = user_has_instructor_permission(
            self.user, constants.COURSES_VIEW_ENROLLMENTS, COURSE_KEY, legacy_check
        )

        assert not allowed
        legacy_check.assert_called_once_with(self.user, COURSE_KEY)

    @ddt.data(True, False)
    def test_flag_disabled_returns_legacy_result(self, legacy_result):
        """With the flag off, the result is the one of the legacy check."""
        self.set_authz_enabled(False)

        allowed = user_has_instructor_permission(
            self.user, constants.COURSES_VIEW_ENROLLMENTS, COURSE_KEY, Mock(return_value=legacy_result)
        )

        assert allowed == legacy_result

    def test_flag_disabled_uses_default_legacy_check(self):
        """With the flag off and no legacy check given, the one registered for the permission is used."""
        self.set_authz_enabled(False)
        default_check = Mock(return_value=True)

        with patch.dict(f"{API}.LEGACY_PERMISSION_CHECKS", {constants.COURSES_VIEW_GRADEBOOK: default_check}):
            allowed = user_has_instructor_permission(self.user, constants.COURSES_VIEW_GRADEBOOK, COURSE_KEY)

        assert allowed
        default_check.assert_called_once_with(self.user, COURSE_KEY)

    def test_flag_disabled_without_legacy_check_denies(self):
        """With the flag off, a permission without any legacy check is denied."""
        self.set_authz_enabled(False)

        assert not user_has_instructor_permission(self.user, "courses.unknown_permission", COURSE_KEY)

    def test_enabled_per_course(self):
        """The flag is evaluated for the course being checked."""
        self.authz_flag_mock.side_effect = lambda course_key=None: course_key == COURSE_KEY
        other_course = CourseLocator("edX", "Other", "2026")
        self.assign_authz_role(self.user, COURSE_ADMIN.external_key, COURSE_KEY)
        legacy_check = Mock(return_value=False)

        assert user_has_instructor_permission(self.user, constants.COURSES_VIEW_ENROLLMENTS, COURSE_KEY, legacy_check)
        assert not user_has_instructor_permission(
            self.user, constants.COURSES_VIEW_ENROLLMENTS, other_course, legacy_check
        )

    def test_logs_the_access_path(self):
        """Each decision is logged with the path (``authz`` or ``legacy``) that resolved it."""
        self.assign_authz_role(self.user, COURSE_STAFF.external_key, COURSE_KEY)

        with self.assertLogs(API, level="DEBUG") as logs:
            user_has_instructor_permission(self.user, constants.COURSES_VIEW_ENROLLMENTS, COURSE_KEY)
            self.set_authz_enabled(False)
            user_has_instructor_permission(self.user, constants.COURSES_VIEW_ENROLLMENTS, COURSE_KEY, lambda *_: True)

        assert [record.access_path for record in logs.records] == ["authz", "legacy"]
        assert [record.getMessage() for record in logs.records] == [
            "Instructor dashboard permission granted via authz",
            "Instructor dashboard permission granted via legacy",
        ]

    def test_any_permission_flag_enabled(self):
        """With the flag on, one granted permission of the set is enough."""
        self.set_authz_enabled(True)
        with patch(f"{API}.authz_api.is_user_allowed", side_effect=lambda _u, perm, _s: perm == "b"):
            assert user_has_any_instructor_permission(self.user, ["a", "b"], COURSE_KEY)
            assert not user_has_any_instructor_permission(self.user, ["a", "c"], COURSE_KEY)

    def test_any_permission_flag_disabled_uses_default_checks(self):
        """With the flag off, the default legacy check of any permission grants access."""
        self.set_authz_enabled(False)
        checks = {"a": lambda *_: False, "b": lambda *_: True}
        with patch.dict(f"{API}.LEGACY_PERMISSION_CHECKS", checks, clear=True):
            assert user_has_any_instructor_permission(self.user, ["a", "b"], COURSE_KEY)
            assert not user_has_any_instructor_permission(self.user, ["a"], COURSE_KEY)

    def test_any_permission_flag_disabled_explicit_legacy_check(self):
        """With the flag off, an explicit legacy check replaces the defaults."""
        self.set_authz_enabled(False)
        assert user_has_any_instructor_permission(self.user, ["a", "b"], COURSE_KEY, lambda *_: True)
        assert not user_has_any_instructor_permission(self.user, ["a", "b"], COURSE_KEY, lambda *_: False)

    def test_any_permission_flag_disabled_explicit_legacy_check_runs_once(self):
        """With the flag off, an explicit legacy check covers the whole set: it runs and is logged once."""
        self.set_authz_enabled(False)
        legacy_check = Mock(return_value=False)

        with self.assertLogs(API, level="DEBUG") as logs:
            assert not user_has_any_instructor_permission(self.user, ["a", "b", "c"], COURSE_KEY, legacy_check)

        legacy_check.assert_called_once_with(self.user, COURSE_KEY)
        assert [record.access_path for record in logs.records] == ["legacy"]
        assert logs.records[0].authz_permissions == ["a", "b", "c"]

    def test_any_permission_empty_collection_denies(self):
        """An empty collection denies instead of raising, whatever the flag and the legacy check."""
        for enabled in (True, False):
            self.set_authz_enabled(enabled)
            assert not user_has_any_instructor_permission(self.user, [], COURSE_KEY)
            assert not user_has_any_instructor_permission(self.user, [], COURSE_KEY, lambda *_: True)

    def test_get_instructor_authz_permissions(self):
        """All the dashboard permissions are resolved with their effective value for the user."""
        self.assign_authz_role(self.user, COURSE_STAFF.external_key, COURSE_KEY)

        result = get_instructor_authz_permissions(self.user, COURSE_KEY)

        assert set(result) == set(constants.INSTRUCTOR_DASHBOARD_PERMISSIONS)
        assert result[constants.COURSES_VIEW_COHORTS]
        assert result[constants.COURSES_VIEW_COURSE_TEAM]
        assert not result[constants.COURSES_MANAGE_COURSE_TEAM]
