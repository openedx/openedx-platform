"""
Tests for the compatibility of the legacy roles with the Instructor Dashboard AuthZ enforcement.
"""

import ddt
from django.test import TestCase
from opaque_keys.edx.locator import CourseLocator
from openedx_authz.authz import (
    COURSE_ADMIN,
    COURSE_AUDITOR,
    COURSE_BETA_TESTER,
    COURSE_DATA_RESEARCHER,
    COURSE_LIMITED_STAFF,
    COURSE_STAFF,
)

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.instructor.authz.compat import holds_legacy_role_compat_permission
from lms.djangoapps.instructor.authz.tests.mixins import InstructorAuthzTestMixin

COURSE_KEY = CourseLocator("edX", "Demo", "2026")
OTHER_COURSE_KEY = CourseLocator("edX", "Demo", "2027")


@ddt.ddt
class HoldsLegacyRoleCompatPermissionTest(InstructorAuthzTestMixin, TestCase):
    """Only the legacy roles that AuthZ maps without dashboard permissions hold a compatibility permission."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()

    @ddt.data(
        (COURSE_LIMITED_STAFF.external_key, True),
        (COURSE_DATA_RESEARCHER.external_key, True),
        (COURSE_BETA_TESTER.external_key, True),
        # These hold real dashboard permissions: AuthZ is authoritative for them.
        (COURSE_ADMIN.external_key, False),
        (COURSE_STAFF.external_key, False),
        (COURSE_AUDITOR.external_key, False),
    )
    @ddt.unpack
    def test_role(self, role, expected):
        """The result depends on the AuthZ role of the user."""
        self.assign_authz_role(self.user, role, COURSE_KEY)

        assert holds_legacy_role_compat_permission(self.user, COURSE_KEY) == expected

    def test_without_role(self):
        """A user without roles holds no compatibility permission."""
        assert not holds_legacy_role_compat_permission(self.user, COURSE_KEY)

    def test_scoped_to_the_course(self):
        """The role only counts in the course where it is assigned."""
        self.assign_authz_role(self.user, COURSE_LIMITED_STAFF.external_key, COURSE_KEY)

        assert not holds_legacy_role_compat_permission(self.user, OTHER_COURSE_KEY)
