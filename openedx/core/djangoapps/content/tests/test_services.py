"""
Tests for content XBlock Services
"""
from unittest.mock import patch

import ddt
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.locator import LibraryLocatorV2
from openedx_authz import api as authz_api
from openedx_authz.api import assign_role_to_user_in_scope
from openedx_authz.constants.permissions import (
    COURSES_CREATE_FILES,
    COURSES_EDIT_COURSE_CONTENT,
    COURSES_EDIT_FILES,
    COURSES_MANAGE_COURSE_UPDATES,
    COURSES_VIEW_COURSE,
    COURSES_VIEW_FILES,
    EDIT_LIBRARY_CONTENT,
    REUSE_LIBRARY_CONTENT,
    VIEW_LIBRARY,
)
from openedx_authz.constants.roles import COURSE_STAFF
from openedx_authz.data import PermissionData
from openedx_authz.engine.enforcer import AuthzEnforcer
from organizations.models import Organization

from common.djangoapps.student.auth import update_org_role
from common.djangoapps.student.roles import OrgStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.fixtures import seed_policies
from openedx.core.djangoapps.content.services import COMMON_PERM_KEY, StudioPermissionsService
from openedx.core.djangoapps.content_libraries.api import (
    AccessLevel,
    ContentLibraryMetadata,
    assign_library_role_to_user,
    create_library,
)
from openedx.core.toggles import AUTHZ_COURSE_AUTHORING_FLAG
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

User = get_user_model()


@ddt.ddt
class StudioPermissionsServiceTestCase(ModuleStoreTestCase, TransactionTestCase):
    """
    Test the studio permissions service.

    Existing tests for legacy functionality available at:
    cms.djangoapps.contentstore.tests.test_libraries.TestLibraryAccess.test_studio_user_permissions
    """

    def setUp(self) -> None:
        super().setUp()
        seed_policies()
        self.org = Organization.objects.create(name="Organization A", short_name="orgA")
        self.staff = UserFactory.create(
            is_staff=True,
        )

    def _create_privileged_org_user(self) -> User:
        user = UserFactory.create()
        update_org_role(self.staff, OrgStaffRole, user, [self.org.short_name])
        return user

    def _create_authz_privileged_course_user(self, course_key) -> User:
        """Helper method to add a user to a role for the course."""
        user = UserFactory.create()
        assign_role_to_user_in_scope(
            user.username,
            COURSE_STAFF.external_key,
            str(course_key)
        )
        AuthzEnforcer.get_enforcer().load_policy()
        return user

    def _create_library(self) -> ContentLibraryMetadata:
        return create_library(
            org=self.org,
            slug="lib",
            title="Library Org",
            description="This is a library from Org",
        )

    def _create_privileged_library_user(self, library_key: LibraryLocatorV2) -> User:
        user = UserFactory.create()
        assign_library_role_to_user(library_key, user, AccessLevel.ADMIN_LEVEL)
        return user

    @ddt.data(
        ("read_content", VIEW_LIBRARY),
        ("edit_content", EDIT_LIBRARY_CONTENT),
        ("read_files", VIEW_LIBRARY),
        ("create_files", EDIT_LIBRARY_CONTENT),
        ("edit_files", EDIT_LIBRARY_CONTENT),
        ("reuse_content", REUSE_LIBRARY_CONTENT),
    )
    @ddt.unpack
    def test_library_permission_allowed(self, label: COMMON_PERM_KEY, perm: PermissionData) -> None:
        library = self._create_library()
        user = self._create_privileged_library_user(library.key)
        service = StudioPermissionsService(user=user)
        with patch(
            "openedx.core.djangoapps.content.services.is_user_allowed", side_effect=authz_api.is_user_allowed
        ) as mock_allowed:
            assert getattr(service, "can_" + label)(library.key)
            mock_allowed.assert_called_with(user, perm.identifier, str(library.key))

    @ddt.data(
        ("read_content", VIEW_LIBRARY),
        ("edit_content", EDIT_LIBRARY_CONTENT),
        ("read_files", VIEW_LIBRARY),
        ("create_files", EDIT_LIBRARY_CONTENT),
        ("edit_files", EDIT_LIBRARY_CONTENT),
        ("reuse_content", REUSE_LIBRARY_CONTENT),
    )
    @ddt.unpack
    def test_library_permission_not_allowed(self, label: COMMON_PERM_KEY, perm: PermissionData) -> None:
        library = self._create_library()
        user = UserFactory.create()
        service = StudioPermissionsService(user=user)
        with patch(
            "openedx.core.djangoapps.content.services.is_user_allowed", side_effect=authz_api.is_user_allowed
        ) as mock_allowed:
            assert not getattr(service, "can_" + label)(library.key)
            mock_allowed.assert_called_with(user, perm.identifier, str(library.key))

    @override_waffle_flag(AUTHZ_COURSE_AUTHORING_FLAG, active=True)
    @ddt.data(
        ("read_content", COURSES_VIEW_COURSE),
        ("edit_content", COURSES_EDIT_COURSE_CONTENT),
        ("read_files", COURSES_VIEW_FILES),
        ("create_files", COURSES_CREATE_FILES),
        ("edit_files", COURSES_EDIT_FILES),
        ("manage_updates", COURSES_MANAGE_COURSE_UPDATES),
    )
    @ddt.unpack
    def test_course_permission_allowed_with_authz(self, label: COMMON_PERM_KEY, perm: PermissionData) -> None:
        course = CourseFactory.create(org=self.org.short_name)
        user = self._create_authz_privileged_course_user(course.context_key)
        service = StudioPermissionsService(user=user)
        with patch(
            "openedx.core.djangoapps.content.services.is_user_allowed", side_effect=authz_api.is_user_allowed
        ) as mock_allowed:
            assert getattr(service, "can_" + label)(course.context_key)
            mock_allowed.assert_called_with(user, perm.identifier, str(course.context_key))

    @override_waffle_flag(AUTHZ_COURSE_AUTHORING_FLAG, active=True)
    @ddt.data(
        ("read_content", COURSES_VIEW_COURSE),
        ("edit_content", COURSES_EDIT_COURSE_CONTENT),
        ("read_files", COURSES_VIEW_FILES),
        ("create_files", COURSES_CREATE_FILES),
        ("edit_files", COURSES_EDIT_FILES),
        ("manage_updates", COURSES_MANAGE_COURSE_UPDATES),
    )
    @ddt.unpack
    def test_course_permission_not_allowed_with_authz(self, label: COMMON_PERM_KEY, perm: PermissionData) -> None:
        course = CourseFactory.create(org=self.org.short_name)
        user = UserFactory.create()
        service = StudioPermissionsService(user=user)
        with patch(
            "openedx.core.djangoapps.content.services.is_user_allowed", side_effect=authz_api.is_user_allowed
        ) as mock_allowed:
            assert not getattr(service, "can_" + label)(course.context_key)
            mock_allowed.assert_called_with(user, perm.identifier, str(course.context_key))

    @ddt.data(
        "read_content",
        "edit_content",
        "read_files",
        "create_files",
        "edit_files",
        "manage_updates",
    )
    def test_course_permission_allowed_without_authz(
        self, label: COMMON_PERM_KEY,
    ) -> None:
        course = CourseFactory.create(org=self.org.short_name)
        user = self._create_privileged_org_user()
        service = StudioPermissionsService(user=user)
        assert getattr(service, "can_" + label)(course.context_key)

    @ddt.data(
        "read_content",
        "edit_content",
        "read_files",
        "create_files",
        "edit_files",
        "manage_updates",
    )
    def test_course_permission_not_allowed_without_authz(
        self, label: COMMON_PERM_KEY,
    ) -> None:
        course = CourseFactory.create(org=self.org.short_name)
        user = UserFactory.create()
        service = StudioPermissionsService(user=user)
        assert not getattr(service, "can_" + label)(course.context_key)
