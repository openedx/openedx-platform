"""
Tests for content XBlock Services
"""
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from opaque_keys.edx.locator import LibraryLocatorV2
from organizations.models import Organization

from common.djangoapps.student.auth import update_org_role
from common.djangoapps.student.roles import OrgStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.fixtures import seed_policies
from openedx.core.djangoapps.content.services import StudioPermissionsService
from openedx.core.djangoapps.content_libraries.api import (
    AccessLevel,
    ContentLibraryMetadata,
    assign_library_role_to_user,
    create_library,
)
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

User = get_user_model()


class StudioPermissionsServiceTestCase(ModuleStoreTestCase, TransactionTestCase):
    """
    Test the studio permissions service.
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

    def test_user_can_read_course(self) -> None:
        course = CourseFactory.create(org=self.org.short_name)
        user = self._create_privileged_org_user()
        service = StudioPermissionsService(user=user)
        assert service.can_read(course.location)

    def test_user_can_write_course(self) -> None:
        course = CourseFactory.create(org=self.org.short_name)
        user = self._create_privileged_org_user()
        service = StudioPermissionsService(user=user)
        assert service.can_write(course.location)

    def test_user_cannot_read_course(self) -> None:
        course = CourseFactory.create(org=self.org.short_name)
        user = UserFactory.create()
        service = StudioPermissionsService(user=user)
        assert not service.can_read(course.location)

    def test_user_cannot_write_course(self) -> None:
        course = CourseFactory.create(org=self.org.short_name)
        user = UserFactory.create()
        service = StudioPermissionsService(user=user)
        assert not service.can_write(course.location)

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

    def test_user_can_read_library(self) -> None:
        library = self._create_library()
        user = self._create_privileged_library_user(library.key)
        service = StudioPermissionsService(user=user)
        assert service.can_read(library.key)

    def test_user_can_write_library(self) -> None:
        library = self._create_library()
        user = self._create_privileged_library_user(library.key)
        service = StudioPermissionsService(user=user)
        assert service.can_write(library.key)

    def test_user_cannot_read_library(self) -> None:
        library = self._create_library()
        user = UserFactory.create()
        service = StudioPermissionsService(user=user)
        assert not service.can_read(library.key)

    def test_user_cannot_write_library(self) -> None:
        library = self._create_library()
        user = UserFactory.create()
        service = StudioPermissionsService(user=user)
        assert not service.can_write(library.key)
