"""
Tests tagging rest api views
"""

from __future__ import annotations

import abc
import json
from io import BytesIO
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import ddt
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from edx_django_utils.cache import RequestCache
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator, LibraryCollectionLocator, LibraryContainerLocator
from openedx_tagging.models import Tag, Taxonomy
from openedx_tagging.models.system_defined import SystemDefinedTaxonomy
from openedx_tagging.rest_api.v1.serializers import TaxonomySerializer
from organizations.models import Organization
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.auth import add_users, update_org_role
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseStaffRole,
    OrgContentCreatorRole,
    OrgInstructorRole,
    OrgLibraryUserRole,
    OrgStaffRole
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content_libraries.api import AccessLevel, create_library, set_library_user_permissions
from openedx.core.djangoapps.content_tagging import api as tagging_api
from openedx.core.djangoapps.content_tagging.models import TaxonomyOrg
from openedx.core.djangolib.testing.utils import skip_unless_cms

from ....tests.test_objecttag_export_helpers import TaggedCourseMixin

User = get_user_model()

TAXONOMY_ORG_LIST_URL = "/api/content_tagging/v1/taxonomies/"
TAXONOMY_ORG_DETAIL_URL = "/api/content_tagging/v1/taxonomies/{pk}/"
TAXONOMY_ORG_UPDATE_ORG_URL = "/api/content_tagging/v1/taxonomies/{pk}/orgs/"
OBJECT_TAG_UPDATE_URL = "/api/content_tagging/v1/object_tags/{object_id}/"
OBJECT_TAGS_EXPORT_URL = "/api/content_tagging/v1/object_tags/{object_id}/export/"
OBJECT_TAGS_URL = "/api/content_tagging/v1/object_tags/{object_id}/"
TAXONOMY_TEMPLATE_URL = "/api/content_tagging/v1/taxonomies/import/{filename}"
TAXONOMY_CREATE_IMPORT_URL = "/api/content_tagging/v1/taxonomies/import/"
TAXONOMY_TAGS_IMPORT_URL = "/api/content_tagging/v1/taxonomies/{pk}/tags/import/"
TAXONOMY_TAGS_URL = "/api/content_tagging/v1/taxonomies/{pk}/tags/"


def check_taxonomy(
    data,
    pk,
    name,
    description=None,
    enabled=True,
    allow_multiple=True,
    allow_free_text=False,
    system_defined=False,
    visible_to_authors=True,
    export_id=None,
    **_
):
    """
    Check the given data against the expected values.
    """
    assert data["id"] == pk
    assert data["name"] == name
    assert data["description"] == description
    assert data["enabled"] == enabled
    assert data["allow_multiple"] == allow_multiple
    assert data["allow_free_text"] == allow_free_text
    assert data["system_defined"] == system_defined
    assert data["visible_to_authors"] == visible_to_authors
    assert data["export_id"] == export_id


class TestTaxonomyObjectsMixin:
    """
    Sets up data for testing Content Taxonomies.
    """
    def _setUp_orgs(self):
        """
        Create orgs for testing
        """
        self.orgA = Organization.objects.create(name="Organization A", short_name="orgA")
        self.orgB = Organization.objects.create(name="Organization B", short_name="orgB")
        self.orgX = Organization.objects.create(name="Organization X", short_name="orgX")

    def _setUp_courses(self):
        """
        Create courses for testing
        """
        self.courseA = CourseLocator("orgA", "101", "test")
        self.courseB = CourseLocator("orgB", "101", "test")

    def _setUp_library(self):
        """
        Create library for testing
        """
        self.content_libraryA = create_library(
            org=self.orgA,
            slug="lib_a",
            title="Library Org A",
            description="This is a library from Org A",
        )
        self.libraryA = str(self.content_libraryA.key)

    def _setUp_collection(self):
        self.collection_key = str(LibraryCollectionLocator(self.content_libraryA.key, 'test-collection'))

    def _setUp_container(self):
        self.container_key = str(LibraryContainerLocator(self.content_libraryA.key, 'unit', 'unit1'))

    def _setUp_users(self):
        """
        Create users for testing
        """
        self.user = User.objects.create(
            username="user",
            email="user@example.com",
        )
        self.staff = User.objects.create(
            username="staff",
            email="staff@example.com",
            is_staff=True,
        )
        self.superuser = User.objects.create(
            username="superuser",
            email="superuser@example.com",
            is_superuser=True,
        )

        self.staffA = User.objects.create(
            username="staffA",
            email="staffA@example.com",
        )
        update_org_role(self.staff, OrgStaffRole, self.staffA, [self.orgA.short_name])

        self.staffB = User.objects.create(
            username="staffB",
            email="staffB@example.com",
        )
        update_org_role(self.staff, OrgStaffRole, self.staffB, [self.orgB.short_name])

        self.content_creatorA = User.objects.create(
            username="content_creatorA",
            email="content_creatorA@example.com",
        )
        update_org_role(self.staff, OrgContentCreatorRole, self.content_creatorA, [self.orgA.short_name])

        self.instructorA = User.objects.create(
            username="instructorA",
            email="instructorA@example.com",
        )
        update_org_role(self.staff, OrgInstructorRole, self.instructorA, [self.orgA.short_name])

        self.library_staffA = User.objects.create(
            username="library_staffA",
            email="library_staffA@example.com",
        )
        update_org_role(self.staff, OrgLibraryUserRole, self.library_staffA, [self.orgA.short_name])

        self.course_instructorA = User.objects.create(
            username="course_instructorA",
            email="course_instructorA@example.com",
        )
        add_users(self.staff, CourseInstructorRole(self.courseA), self.course_instructorA)

        self.course_staffA = User.objects.create(
            username="course_staffA",
            email="course_staffA@example.com",
        )
        add_users(self.staff, CourseStaffRole(self.courseA), self.course_staffA)

        self.library_userA = User.objects.create(
            username="library_userA",
            email="library_userA@example.com",
        )
        set_library_user_permissions(
            self.content_libraryA.key,
            self.library_userA,
            AccessLevel.READ_LEVEL
        )

    def _setUp_taxonomies(self):
        """
        Create taxonomies for testing
        """
        # Orphaned taxonomy
        self.ot1 = tagging_api.create_taxonomy(name="ot1", enabled=True)
        self.ot2 = tagging_api.create_taxonomy(name="ot2", enabled=False)

        # System defined taxonomy
        self.st1 = tagging_api.create_taxonomy(name="st1", enabled=True)
        self.st1.taxonomy_class = SystemDefinedTaxonomy
        self.st1.save()
        TaxonomyOrg.objects.create(
            taxonomy=self.st1,
            rel_type=TaxonomyOrg.RelType.OWNER,
            org=None,
        )
        self.st2 = tagging_api.create_taxonomy(name="st2", enabled=False)
        self.st2.taxonomy_class = SystemDefinedTaxonomy
        self.st2.save()
        TaxonomyOrg.objects.create(
            taxonomy=self.st2,
            rel_type=TaxonomyOrg.RelType.OWNER,
        )

        # Global taxonomy, which contains tags
        self.t1 = tagging_api.create_taxonomy(name="t1", enabled=True)
        TaxonomyOrg.objects.create(
            taxonomy=self.t1,
            rel_type=TaxonomyOrg.RelType.OWNER,
        )
        self.t2 = tagging_api.create_taxonomy(name="t2", enabled=False)
        TaxonomyOrg.objects.create(
            taxonomy=self.t2,
            rel_type=TaxonomyOrg.RelType.OWNER,
        )
        root1 = Tag.objects.create(taxonomy=self.t1, value="ALPHABET")
        Tag.objects.create(taxonomy=self.t1, value="android", parent=root1)
        Tag.objects.create(taxonomy=self.t1, value="abacus", parent=root1)
        Tag.objects.create(taxonomy=self.t1, value="azure", parent=root1)
        Tag.objects.create(taxonomy=self.t1, value="aardvark", parent=root1)
        Tag.objects.create(taxonomy=self.t1, value="anvil", parent=root1)

        # OrgA taxonomy
        self.tA1 = tagging_api.create_taxonomy(name="tA1", enabled=True)
        TaxonomyOrg.objects.create(
            taxonomy=self.tA1,
            org=self.orgA, rel_type=TaxonomyOrg.RelType.OWNER,)
        self.tA2 = tagging_api.create_taxonomy(name="tA2", enabled=False)
        TaxonomyOrg.objects.create(
            taxonomy=self.tA2,
            org=self.orgA,
            rel_type=TaxonomyOrg.RelType.OWNER,
        )

        # OrgB taxonomy
        self.tB1 = tagging_api.create_taxonomy(name="tB1", enabled=True)
        TaxonomyOrg.objects.create(
            taxonomy=self.tB1,
            org=self.orgB,
            rel_type=TaxonomyOrg.RelType.OWNER,
        )
        self.tB2 = tagging_api.create_taxonomy(name="tB2", enabled=False)
        TaxonomyOrg.objects.create(
            taxonomy=self.tB2,
            org=self.orgB,
            rel_type=TaxonomyOrg.RelType.OWNER,
        )

        # OrgA and OrgB taxonomy
        self.tBA1 = tagging_api.create_taxonomy(name="tBA1", enabled=True)
        TaxonomyOrg.objects.create(
            taxonomy=self.tBA1,
            org=self.orgA,
            rel_type=TaxonomyOrg.RelType.OWNER,
        )
        TaxonomyOrg.objects.create(
            taxonomy=self.tBA1,
            org=self.orgB,
            rel_type=TaxonomyOrg.RelType.OWNER,
        )
        self.tBA2 = tagging_api.create_taxonomy(name="tBA2", enabled=False)
        TaxonomyOrg.objects.create(
            taxonomy=self.tBA2,
            org=self.orgA,
            rel_type=TaxonomyOrg.RelType.OWNER,
        )
        TaxonomyOrg.objects.create(
            taxonomy=self.tBA2,
            org=self.orgB,
            rel_type=TaxonomyOrg.RelType.OWNER,
        )

    def setUp(self):

        super().setUp()

        self._setUp_orgs()
        self._setUp_courses()
        self._setUp_library()
        self._setUp_users()
        self._setUp_taxonomies()
        self._setUp_collection()
        self._setUp_container()

        # Clear all request caches in between test runs to keep query counts consistent.
        RequestCache.clear_all_namespaces()


@skip_unless_cms
@ddt.ddt
class TestTaxonomyListCreateViewSet(TestTaxonomyObjectsMixin, APITestCase):
    """
    Test cases for TaxonomyViewSet for list and create actions
    """

    def _test_list_taxonomy(
            self,
            user_attr: str,
            expected_taxonomies: list[str],
            enabled_parameter: bool | None = None,
            org_parameter: str | None = None,
            unassigned_parameter: bool | None = None,
            page_size: int | None = None,
    ) -> None:
        """
        Helper function to call the list endpoint and check the response
        """
        url = TAXONOMY_ORG_LIST_URL

        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        # Set parameters cleaning empty values
        query_params = {k: v for k, v in {
            "enabled": enabled_parameter,
            "org": org_parameter,
            "unassigned": unassigned_parameter,
            "page_size": page_size,
        }.items() if v is not None}

        response = self.client.get(url, query_params, format="json")

        assert response.status_code == status.HTTP_200_OK
        self.assertEqual(set(t["name"] for t in response.data["results"]), set(expected_taxonomies))

    def test_list_taxonomy_staff(self) -> None:
        """
        Tests that staff users see all taxonomies
        """
        # page_size=10, and so "tBA1" and "tBA2" appear on the second page
        expected_taxonomies = ["ot1", "ot2", "st1", "st2", "t1", "t2", "tA1", "tA2", "tB1", "tB2"]
        self._test_list_taxonomy(
            user_attr="staff",
            expected_taxonomies=expected_taxonomies,
            page_size=10,
        )

    @ddt.data(
        "content_creatorA",
        "instructorA",
        "library_staffA",
        "course_instructorA",
        "course_staffA",
        "library_userA",
    )
    def test_list_taxonomy_orgA(self, user_attr: str) -> None:
        """
        Tests that non staff users from orgA can see only enabled taxonomies from orgA and global taxonomies
        """
        expected_taxonomies = ["st1", "t1", "tA1", "tBA1"]
        self._test_list_taxonomy(
            user_attr=user_attr,
            enabled_parameter=True,
            expected_taxonomies=expected_taxonomies,
        )

    @ddt.data(
        (True, ["ot1", "st1", "t1", "tA1", "tB1", "tBA1"]),
        (False, ["ot2", "st2", "t2", "tA2", "tB2", "tBA2"]),
    )
    @ddt.unpack
    def test_list_taxonomy_enabled_filter(self, enabled_parameter: bool, expected_taxonomies: list[str]) -> None:
        """
        Tests that the enabled filter works as expected
        """
        self._test_list_taxonomy(
            user_attr="staff",
            enabled_parameter=enabled_parameter,
            expected_taxonomies=expected_taxonomies
        )

    @ddt.data(
        ("orgA", ["st1", "st2", "t1", "t2", "tA1", "tA2", "tBA1", "tBA2"]),
        ("orgB", ["st1", "st2", "t1", "t2", "tB1", "tB2", "tBA1", "tBA2"]),
        ("orgX", ["st1", "st2", "t1", "t2"]),
        # Non-existent orgs are ignored
        ("invalidOrg", ["st1", "st2", "t1", "t2"]),
    )
    @ddt.unpack
    def test_list_taxonomy_org_filter(self, org_parameter: str, expected_taxonomies: list[str]) -> None:
        """
        Tests that the org filter works as expected
        """
        self._test_list_taxonomy(
            user_attr="staff",
            org_parameter=org_parameter,
            expected_taxonomies=expected_taxonomies,
        )

    def test_list_unassigned_taxonomies(self):
        """
        Test that passing in "unassigned" query param returns Taxonomies that
        are unassigned. i.e. does not belong to any org
        """
        self._test_list_taxonomy(
            user_attr="staff",
            expected_taxonomies=["ot1", "ot2"],
            unassigned_parameter=True,
        )

    def test_list_unassigned_and_org_filter_invalid(self) -> None:
        """
        Test that passing "org" and "unassigned" query params should throw an error
        """
        url = TAXONOMY_ORG_LIST_URL

        self.client.force_authenticate(user=self.user)

        query_params = {"org": "orgA", "unassigned": "true"}

        response = self.client.get(url, query_params, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @ddt.data(
        ("user", (), None),
        ("staffA", ["tA2", "tBA1", "tBA2"], None),
        ("staff", ["st2", "t1", "t2"], "3"),
    )
    @ddt.unpack
    def test_list_taxonomy_pagination(
        self, user_attr: str, expected_taxonomies: list[str], expected_next_page: str | None
    ) -> None:
        """
        Tests that the pagination works as expected
        """
        url = TAXONOMY_ORG_LIST_URL

        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        query_params = {"page_size": 3, "page": 2}

        response = self.client.get(url, query_params, format="json")

        assert response.status_code == status.HTTP_200_OK if len(expected_taxonomies) > 0 else status.HTTP_404_NOT_FOUND
        if status.is_success(response.status_code):
            self.assertEqual(set(t["name"] for t in response.data["results"]), set(expected_taxonomies))
            parsed_url = urlparse(response.data["next"])

            next_page = parse_qs(parsed_url.query).get("page", [None])[0]
            assert next_page == expected_next_page

    def test_list_invalid_page(self) -> None:
        """
        Tests that using an invalid page will raise NOT_FOUND
        """
        url = TAXONOMY_ORG_LIST_URL

        self.client.force_authenticate(user=self.user)

        query_params = {"page": 123123}

        response = self.client.get(url, query_params, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @ddt.data(
        (None, status.HTTP_401_UNAUTHORIZED),
        ("user", status.HTTP_403_FORBIDDEN),
        ("content_creatorA", status.HTTP_403_FORBIDDEN),
        ("instructorA", status.HTTP_403_FORBIDDEN),
        ("library_staffA", status.HTTP_403_FORBIDDEN),
        ("course_instructorA", status.HTTP_403_FORBIDDEN),
        ("course_staffA", status.HTTP_403_FORBIDDEN),
        ("library_userA", status.HTTP_403_FORBIDDEN),
        ("staffA", status.HTTP_201_CREATED),
        ("staff", status.HTTP_201_CREATED),
    )
    @ddt.unpack
    def test_create_taxonomy(self, user_attr: str, expected_status: int) -> None:
        """
        Tests that only Taxonomy admins and org level admins can create taxonomies
        """
        url = TAXONOMY_ORG_LIST_URL

        create_data = {
            "name": "taxonomy_data",
            "description": "This is a description",
            "enabled": True,
            "allow_multiple": True,
            "export_id": "taxonomy_data",
        }

        if user_attr:
            user = getattr(self, user_attr)
            self.client.force_authenticate(user=user)

        response = self.client.post(url, create_data, format="json")
        assert response.status_code == expected_status

        # If we were able to create the taxonomy, check if it was created
        if status.is_success(expected_status):
            check_taxonomy(response.data, response.data["id"], **create_data)
            url = TAXONOMY_ORG_DETAIL_URL.format(pk=response.data["id"])

            response = self.client.get(url)
            check_taxonomy(response.data, response.data["id"], **create_data)

            # Also checks if the taxonomy was associated with the org
            if user_attr == "staffA":
                assert response.data["orgs"] == [self.orgA.short_name]

    @ddt.data(
        ('staff', 11),
        ("content_creatorA", 22),
        ("library_staffA", 22),
        ("library_userA", 22),
        ("instructorA", 22),
        ("course_instructorA", 22),
        ("course_staffA", 22),
    )
    @ddt.unpack
    def test_list_taxonomy_query_count(self, user_attr: str, expected_queries: int):
        """
        Test how many queries are used when retrieving taxonomies and permissions
        """
        url = TAXONOMY_ORG_LIST_URL + f'?org={self.orgA.short_name}&enabled=true'
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)
        with self.assertNumQueries(expected_queries):
            response = self.client.get(url)

        assert response.status_code == 200
        assert response.data["can_add_taxonomy"] == user.is_staff
        assert len(response.data["results"]) == 4
        for taxonomy in response.data["results"]:
            if taxonomy["system_defined"]:
                assert not taxonomy["can_change_taxonomy"]
                assert not taxonomy["can_delete_taxonomy"]
                assert taxonomy["can_tag_object"]
            else:
                assert taxonomy["can_change_taxonomy"] == user.is_staff
                assert taxonomy["can_delete_taxonomy"] == user.is_staff
                assert taxonomy["can_tag_object"]


@ddt.ddt
class TestTaxonomyDetailExportMixin(TestTaxonomyObjectsMixin):
    """
    Test cases to be used with detail and export actions
    """

    @abc.abstractmethod
    def _test_api_call(self, **_kwargs) -> None:
        """
        Helper function to call the detail/export endpoint and check the response
        """

    @ddt.data(
        "user",
        "content_creatorA",
        "instructorA",
        "library_staffA",
        "course_instructorA",
        "course_staffA",
        "library_userA",
    )
    def test_detail_taxonomy_all_org_enabled(self, user_attr: str) -> None:
        """
        Tests that everyone can see enabled global taxonomies
        """
        self._test_api_call(
            user_attr=user_attr,
            taxonomy_attr="t1",
            expected_status=status.HTTP_200_OK,
            reason="Everyone should see enabled global taxonomies",
        )

    @ddt.data(
        ("content_creatorA", "tA1", "User with OrgContentCreatorRole(orgA) should see an enabled taxonomy from orgA"),
        ("content_creatorA", "tBA1", "User with OrgContentCreatorRole(orgA) should see an enabled taxonomy from orgA"),
        ("content_creatorA", "t1", "User with OrgContentCreatorRole(orgA) should see an enabled global taxonomy"),
        ("instructorA", "tA1", "User with OrgInstructorRole(orgA) should see an enabled taxonomy from orgA"),
        ("instructorA", "tBA1", "User with OrgInstructorRole(orgA) should see an enabled taxonomy from orgA"),
        ("instructorA", "t1", "User with OrgInstructorRole(orgA) should see an enabled global taxonomy"),
        ("library_staffA", "tA1", "User with OrgLibraryUserRole(orgA) should see an enabled taxonomy from orgA"),
        ("library_staffA", "tBA1", "User with OrgLibraryUserRole(orgA) should see an enabled taxonomy from orgA"),
        ("library_staffA", "t1", "User with OrgInstructorRole(orgA) should see an enabled global taxonomy"),
        (
            "course_instructorA",
            "tA1",
            "User with CourseInstructorRole in a course from orgA should see an enabled taxonomy from orgA"
        ),
        (
            "course_instructorA",
            "tBA1",
            "User with CourseInstructorRole in a course from orgA should see an enabled taxonomy from orgA"
        ),
        (
            "course_instructorA",
            "t1",
            "User with CourseInstructorRole in a course from orgA should see an enabled global taxonomy"
        ),
        (
            "course_staffA",
            "tA1",
            "User with CourseStaffRole in a course from orgA should see an enabled taxonomy from orgA"
        ),
        (
            "course_staffA",
            "tBA1",
            "User with CourseStaffRole in a course from orgA should see an enabled taxonomy from orgA"
        ),
        (
            "course_staffA",
            "t1",
            "User with CourseStaffRole in a course from orgA should see an enabled global taxonomy"
        ),
        (
            "library_userA",
            "tA1",
            "User with permission on a library from orgA should see an enabled taxonomy from orgA"
        ),
        (
            "library_userA",
            "tBA1",
            "User with permission on a library from orgA should see an enabled taxonomy from orgA"
        ),
        (
            "library_userA",
            "t1",
            "User with permission on a library from orgA should see an enabled global taxonomy"
        ),
    )
    @ddt.unpack
    def test_detail_taxonomy_org_user_see_enabled(self, user_attr: str, taxonomy_attr: str, reason: str) -> None:
        """
        Tests that org users (content creators and instructors) can see enabled global taxonomies and taxonomies
        from their orgs
        """
        self._test_api_call(
            user_attr=user_attr,
            taxonomy_attr=taxonomy_attr,
            expected_status=status.HTTP_200_OK,
            reason=reason,
        )

    @ddt.data(
        "tA2",
        "tBA2",
    )
    def test_detail_taxonomy_org_admin_see_disabled(self, taxonomy_attr: str) -> None:
        """
        Tests that org admins can see disabled taxonomies from their orgs
        """
        self._test_api_call(
            user_attr="staffA",
            taxonomy_attr=taxonomy_attr,
            expected_status=status.HTTP_200_OK,
            reason="User with OrgContentCreatorRole(orgA) should see a disabled taxonomy from orgA",
        )

    @ddt.data(
        "st2",
        "t2",
    )
    def test_detail_taxonomy_org_admin_dont_see_disabled_global(self, taxonomy_attr: str) -> None:
        """
        Tests that org admins can't see disabled global taxonomies
        """
        self._test_api_call(
            user_attr="staffA",
            taxonomy_attr=taxonomy_attr,
            expected_status=status.HTTP_404_NOT_FOUND,
            reason="User with OrgContentCreatorRole(orgA) shouldn't see a disabled global taxonomy",
        )

    @ddt.data(
        ("content_creatorA", "t2", "User with OrgContentCreatorRole(orgA) shouldn't see a disabled global taxonomy"),
        ("instructorA", "tA2", "User with OrgInstructorRole(orgA) shouldn't see a disabled taxonomy from orgA"),
        ("instructorA", "tBA2", "User with OrgInstructorRole(orgA) shouldn't see a disabled taxonomy from orgA"),
        ("instructorA", "t2", "User with OrgInstructorRole(orgA) shouldn't see a disabled global taxonomy"),
        ("library_staffA", "tA2", "User with OrgLibraryUserRole(orgA) shouldn't see a disabled taxonomy from orgA"),
        ("library_staffA", "tBA2", "User with OrgLibraryUserRole(orgA) shouldn't see a disabled taxonomy from orgA"),
        ("library_staffA", "t2", "User with OrgInstructorRole(orgA) shouldn't see a disabled global taxonomy"),
        (
            "course_instructorA",
            "tA2",
            "User with CourseInstructorRole in a course from orgA shouldn't see a disabled taxonomy from orgA"
        ),
        (
            "course_instructorA",
            "tBA2",
            "User with CourseInstructorRole in a course from orgA shouldn't see a disabled taxonomy from orgA"
        ),
        (
            "course_instructorA",
            "t2",
            "User with CourseInstructorRole in a course from orgA shouldn't see a disabled global taxonomy"
        ),
        (
            "course_staffA",
            "tA2",
            "User with CourseStaffRole in a course from orgA shouldn't see a disabled taxonomy from orgA"
        ),
        (
            "course_staffA",
            "tBA2",
            "User with CourseStaffRole in a course from orgA shouldn't see a disabled taxonomy from orgA"
        ),
        (
            "course_staffA",
            "t2",
            "User with CourseStaffRole in a course from orgA should't see a disabled global taxonomy"
        ),
        (
            "library_userA",
            "tA2",
            "User with permission on a library from orgA shouldn't see an disabled taxonomy from orgA"
        ),
        (
            "library_userA",
            "tBA2",
            "User with permission on a library from orgA shouldn't see an disabled taxonomy from orgA"
        ),
        (
            "library_userA",
            "t2",
            "User with permission on a library from orgA shouldn't see an disabled global taxonomy"
        ),
    )
    @ddt.unpack
    def test_detail_taxonomy_org_user_dont_see_disabled(self, user_attr: str, taxonomy_attr: str, reason: str) -> None:
        """
        Tests that org users (content creators and instructors) can't see disabled global taxonomies and taxonomies
        from their orgs
        """
        self._test_api_call(
            user_attr=user_attr,
            taxonomy_attr=taxonomy_attr,
            expected_status=status.HTTP_404_NOT_FOUND,
            reason=reason,
        )

    @ddt.data(
        ("staff", "ot1", "Staff should see an enabled no org taxonomy"),
        ("staff", "ot2", "Staff should see a disabled no org taxonomy"),
    )
    @ddt.unpack
    def test_detail_taxonomy_staff_see_no_org(self, user_attr: str, taxonomy_attr: str, reason: str) -> None:
        """
        Tests that staff can see taxonomies with no org
        """
        self._test_api_call(
            user_attr=user_attr,
            taxonomy_attr=taxonomy_attr,
            expected_status=status.HTTP_200_OK,
            reason=reason,
        )

    @ddt.data(
        "staffA",
        "content_creatorA",
        "instructorA",
        "library_staffA",
        "course_instructorA",
        "course_staffA",
        "library_userA"
    )
    def test_detail_taxonomy_other_dont_see_no_org(self, user_attr: str) -> None:
        """
        Tests that org users can't see taxonomies with no org
        """
        self._test_api_call(
            user_attr=user_attr,
            taxonomy_attr="ot1",
            expected_status=status.HTTP_404_NOT_FOUND,
            reason="Only taxonomy admins should see taxonomies with no org",
        )

    @ddt.data(
        "staffA",
        "content_creatorA",
        "instructorA",
        "library_staffA",
        "course_instructorA",
        "course_staffA",
        "library_userA"
    )
    def test_detail_taxonomy_dont_see_other_org(self, user_attr: str) -> None:
        """
        Tests that org users can't see taxonomies from other orgs
        """
        self._test_api_call(
            user_attr=user_attr,
            taxonomy_attr="tB1",
            expected_status=status.HTTP_404_NOT_FOUND,
            reason="Users shouldn't see taxonomies from other orgs",
        )

    @ddt.data(
        "ot1",
        "ot2",
        "st1",
        "st2",
        "t1",
        "t2",
        "tA1",
        "tA2",
        "tB1",
        "tB2",
        "tBA1",
        "tBA2",
    )
    def test_detail_taxonomy_staff_see_all(self, taxonomy_attr: str) -> None:
        """
        Tests that staff can see all taxonomies
        """
        self._test_api_call(
            user_attr="staff",
            taxonomy_attr=taxonomy_attr,
            expected_status=status.HTTP_200_OK,
            reason="Staff should see all taxonomies",
        )


@skip_unless_cms
class TestTaxonomyDetailViewSet(TestTaxonomyDetailExportMixin, APITestCase):
    """
    Test cases for TaxonomyViewSet with detail action
    """

    def _test_api_call(self, **kwargs) -> None:
        """
        Helper function to call the retrieve endpoint and check the response
        """
        user_attr = kwargs.get("user_attr")
        taxonomy_attr = kwargs.get("taxonomy_attr")
        expected_status = kwargs.get("expected_status")
        reason = kwargs.get("reason", "Unexpected response status")

        assert taxonomy_attr is not None, "taxonomy_attr is required"
        assert user_attr is not None, "user_attr is required"
        assert expected_status is not None, "expected_status is required"

        taxonomy = getattr(self, taxonomy_attr)

        url = TAXONOMY_ORG_DETAIL_URL.format(pk=taxonomy.pk)

        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        response = self.client.get(url)
        assert response.status_code == expected_status, reason

        if status.is_success(expected_status):
            request = MagicMock()
            request.user = user
            context = {"request": request}
            check_taxonomy(
                response.data,
                taxonomy.pk,
                **(TaxonomySerializer(taxonomy.cast(), context=context)).data,
            )


@skip_unless_cms
class TestTaxonomyExportViewSet(TestTaxonomyDetailExportMixin, APITestCase):
    """
    Test cases for TaxonomyViewSet with export action
    """

    def _test_api_call(self, **kwargs) -> None:
        """
        Helper function to call the export endpoint and check the response
        """
        user_attr = kwargs.get("user_attr")
        taxonomy_attr = kwargs.get("taxonomy_attr")
        expected_status = kwargs.get("expected_status")
        reason = kwargs.get("reason", "Unexpected response status")

        assert taxonomy_attr is not None, "taxonomy_attr is required"
        assert user_attr is not None, "user_attr is required"
        assert expected_status is not None, "expected_status is required"

        taxonomy = getattr(self, taxonomy_attr)

        url = TAXONOMY_ORG_DETAIL_URL.format(pk=taxonomy.pk)

        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        response = self.client.get(url)
        assert response.status_code == expected_status, reason
        assert len(response.data) > 0


@ddt.ddt
class TestTaxonomyChangeMixin(TestTaxonomyObjectsMixin):
    """
    Test cases to be used with update, patch and delete actions
    """

    @abc.abstractmethod
    def _test_api_call(self, **_kwargs) -> None:
        """
        Helper function to call the update/patch/delete endpoint and check the response
        """

    @ddt.data(
        "ot1",
        "ot2",
        "st1",
        "st2",
        "t1",
        "t2",
        "tA1",
        "tA2",
        "tB1",
        "tB2",
        "tBA1",
        "tBA2",
    )
    def test_regular_user_cant_edit_taxonomies(self, taxonomy_attr: str) -> None:
        """
        Tests that regular users can't edit taxonomies
        """
        self._test_api_call(
            user_attr="user",
            taxonomy_attr=taxonomy_attr,
            expected_status=[status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND],
            reason="Regular users shouldn't be able to edit taxonomies",
        )

    @ddt.data(
        "content_creatorA",
        "instructorA",
        "library_staffA",
        "course_instructorA",
        "course_staffA",
        "library_userA",
    )
    def test_org_user_cant_edit_org_taxonomies(self, user_attr: str) -> None:
        """
        Tests that content creators and instructors from orgA can't edit taxonomies from orgA
        """
        self._test_api_call(
            user_attr=user_attr,
            taxonomy_attr="tA1",
            expected_status=[status.HTTP_403_FORBIDDEN],
            reason="Content creators and instructors shouldn't be able to edit taxonomies",
        )

    @ddt.data(
        "tA1",
        "tA2",
        "tBA1",
        "tBA2",
    )
    def test_org_staff_can_edit_org_taxonomies(self, taxonomy_attr: str) -> None:
        """
        Tests that org staff can edit taxonomies from their orgs
        """
        self._test_api_call(
            user_attr="staffA",
            taxonomy_attr=taxonomy_attr,
            # Check both status: 200 for update and 204 for delete
            expected_status=[status.HTTP_200_OK, status.HTTP_204_NO_CONTENT],
            reason="Org staff should be able to edit taxonomies from their orgs",
        )

    @ddt.data(
        "tB1",
        "tB2",
    )
    def test_org_staff_cant_edit_other_org_taxonomies(self, taxonomy_attr: str) -> None:
        """
        Tests that org staff can't edit taxonomies from other orgs
        """
        self._test_api_call(
            user_attr="staffA",
            taxonomy_attr=taxonomy_attr,
            expected_status=[status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND],
            reason="Org staff shouldn't be able to edit taxonomies from other orgs",
        )

    @ddt.data(
        "ot1",
        "ot2",
        "t1",
        "t2",
        "tA1",
        "tA2",
        "tB1",
        "tB2",
        "tBA1",
        "tBA2",

    )
    def test_staff_can_edit_almost_all_taxonomies(self, taxonomy_attr: str) -> None:
        """
        Tests that staff can edit all but system defined taxonomies
        """
        self._test_api_call(
            user_attr="staff",
            taxonomy_attr=taxonomy_attr,
            # Check both status: 200 for update and 204 for delete
            expected_status=[status.HTTP_200_OK, status.HTTP_204_NO_CONTENT],
            reason="Staff should be able to edit all but system defined taxonomies",
        )

    @ddt.data(
        "st1",
        "st2",
    )
    def test_staff_cant_edit_system_defined_taxonomies(self, taxonomy_attr: str) -> None:
        """
        Tests that staff can't edit system defined taxonomies
        """
        self._test_api_call(
            user_attr="staff",
            taxonomy_attr=taxonomy_attr,
            # Check both status: 200 for update and 204 for delete
            expected_status=[status.HTTP_403_FORBIDDEN],
            reason="Staff shouldn't be able to edit system defined ",
        )


@skip_unless_cms
class TestTaxonomyUpdateViewSet(TestTaxonomyChangeMixin, APITestCase):
    """
    Test cases for TaxonomyViewSet with PUT method
    """

    def _test_api_call(self, **kwargs) -> None:
        user_attr = kwargs.get("user_attr")
        taxonomy_attr = kwargs.get("taxonomy_attr")
        expected_status = kwargs.get("expected_status")
        reason = kwargs.get("reason", "Unexpected response status")

        assert taxonomy_attr is not None, "taxonomy_attr is required"
        assert user_attr is not None, "user_attr is required"
        assert expected_status is not None, "expected_status is required"

        taxonomy = getattr(self, taxonomy_attr)

        url = TAXONOMY_ORG_DETAIL_URL.format(pk=taxonomy.pk)

        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        response = self.client.put(url, {"name": "new name"}, format="json")
        assert response.status_code in expected_status, reason

        # If we were able to update the taxonomy, check if the name changed
        if status.is_success(response.status_code):
            response = self.client.get(url)
            check_taxonomy(
                response.data,
                response.data["id"],
                **{
                    "name": "new name",
                    "description": taxonomy.description,
                    "enabled": taxonomy.enabled,
                    "export_id": taxonomy.export_id,
                },
            )


@skip_unless_cms
class TestTaxonomyPatchViewSet(TestTaxonomyChangeMixin, APITestCase):
    """
    Test cases for TaxonomyViewSet with PATCH method
    """

    def _test_api_call(self, **kwargs) -> None:
        user_attr = kwargs.get("user_attr")
        taxonomy_attr = kwargs.get("taxonomy_attr")
        expected_status = kwargs.get("expected_status")
        reason = kwargs.get("reason", "Unexpected response status")

        assert taxonomy_attr is not None, "taxonomy_attr is required"
        assert user_attr is not None, "user_attr is required"
        assert expected_status is not None, "expected_status is required"

        taxonomy = getattr(self, taxonomy_attr)

        url = TAXONOMY_ORG_DETAIL_URL.format(pk=taxonomy.pk)

        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        response = self.client.patch(url, {"name": "new name"}, format="json")
        assert response.status_code in expected_status, reason

        # If we were able to patch the taxonomy, check if the name changed
        if status.is_success(response.status_code):
            response = self.client.get(url)
            check_taxonomy(
                response.data,
                response.data["id"],
                **{
                    "name": "new name",
                    "description": taxonomy.description,
                    "enabled": taxonomy.enabled,
                    "export_id": taxonomy.export_id,
                },
            )


@skip_unless_cms
class TestTaxonomyDeleteViewSet(TestTaxonomyChangeMixin, APITestCase):
    """
    Test cases for TaxonomyViewSet with DELETE method
    """

    def _test_api_call(self, **kwargs) -> None:
        user_attr = kwargs.get("user_attr")
        taxonomy_attr = kwargs.get("taxonomy_attr")
        expected_status = kwargs.get("expected_status")
        reason = kwargs.get("reason", "Unexpected response status")

        assert taxonomy_attr is not None, "taxonomy_attr is required"
        assert user_attr is not None, "user_attr is required"
        assert expected_status is not None, "expected_status is required"

        taxonomy = getattr(self, taxonomy_attr)

        url = TAXONOMY_ORG_DETAIL_URL.format(pk=taxonomy.pk)

        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        response = self.client.delete(url)
        assert response.status_code in expected_status, reason

        # If we were able to delete the taxonomy, check that it's really gone
        if status.is_success(response.status_code):
            response = self.client.get(url)
            assert response.status_code == status.HTTP_404_NOT_FOUND


@skip_unless_cms
@ddt.ddt
class TestTaxonomyUpdateOrg(TestTaxonomyObjectsMixin, APITestCase):
    """
    Test cases for updating orgs from taxonomies
    """

    def test_update_org(self) -> None:
        """
        Tests that taxonomy admin can add/remove orgs from a taxonomy
        """
        url = TAXONOMY_ORG_UPDATE_ORG_URL.format(pk=self.tA1.pk)
        self.client.force_authenticate(user=self.staff)

        response = self.client.put(url, {"orgs": [self.orgB.short_name, self.orgX.short_name]}, format="json")
        assert response.status_code == status.HTTP_200_OK

        # Check that the orgs were updated
        url = TAXONOMY_ORG_DETAIL_URL.format(pk=self.tA1.pk)
        response = self.client.get(url)
        assert response.data["orgs"] == [self.orgB.short_name, self.orgX.short_name]
        assert not response.data["all_orgs"]

    def test_update_all_org(self) -> None:
        """
        Tests that taxonomy admin can associate a taxonomy to all orgs
        """
        url = TAXONOMY_ORG_UPDATE_ORG_URL.format(pk=self.tA1.pk)
        self.client.force_authenticate(user=self.staff)

        response = self.client.put(url, {"all_orgs": True}, format="json")
        assert response.status_code == status.HTTP_200_OK

        # Check that the orgs were updated
        url = TAXONOMY_ORG_DETAIL_URL.format(pk=self.tA1.pk)
        response = self.client.get(url)
        assert response.data["orgs"] == []
        assert response.data["all_orgs"]

    def test_update_no_org(self) -> None:
        """
        Tests that taxonomy admin can associate a taxonomy no orgs
        """
        url = TAXONOMY_ORG_UPDATE_ORG_URL.format(pk=self.tA1.pk)
        self.client.force_authenticate(user=self.staff)

        response = self.client.put(url, {"orgs": []}, format="json")

        assert response.status_code == status.HTTP_200_OK

        # Check that the orgs were updated
        url = TAXONOMY_ORG_DETAIL_URL.format(pk=self.tA1.pk)
        response = self.client.get(url)
        assert response.data["orgs"] == []
        assert not response.data["all_orgs"]

    @ddt.data(
        (True, ["orgX"], "Using both all_orgs and orgs parameters should throw error"),
        (False, None, "Using neither all_orgs or orgs parameter should throw error"),
        (None, None, "Using neither all_orgs or orgs parameter should throw error"),
        (False, 'InvalidOrg', "Passing an invalid org should throw error"),
    )
    @ddt.unpack
    def test_update_org_invalid_inputs(self, all_orgs: bool, orgs: list[str], reason: str) -> None:
        """
        Tests if passing both or none of all_orgs and orgs parameters throws error
        """
        url = TAXONOMY_ORG_UPDATE_ORG_URL.format(pk=self.tA1.pk)
        self.client.force_authenticate(user=self.staff)

        # Set body cleaning empty values
        body = {k: v for k, v in {"all_orgs": all_orgs, "orgs": orgs}.items() if v is not None}
        response = self.client.put(url, body, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST, reason

        # Check that the orgs didn't change
        url = TAXONOMY_ORG_DETAIL_URL.format(pk=self.tA1.pk)
        response = self.client.get(url)
        assert response.data["orgs"] == [self.orgA.short_name]

    def test_update_org_system_defined(self) -> None:
        """
        Tests that is not possible to change the orgs associated with a system defined taxonomy
        """
        url = TAXONOMY_ORG_UPDATE_ORG_URL.format(pk=self.st1.pk)
        self.client.force_authenticate(user=self.staff)

        response = self.client.put(url, {"orgs": [self.orgA.short_name]}, format="json")
        assert response.status_code in [status.HTTP_403_FORBIDDEN, status.HTTP_400_BAD_REQUEST]

        # Check that the orgs didn't change
        url = TAXONOMY_ORG_DETAIL_URL.format(pk=self.st1.pk)
        response = self.client.get(url)
        assert response.data["orgs"] == []
        assert response.data["all_orgs"]

    @ddt.data(
        "staffA",
        "content_creatorA",
        "instructorA",
        "library_staffA",
        "course_instructorA",
        "course_staffA",
        "library_userA",
    )
    def test_update_org_no_perm(self, user_attr: str) -> None:
        """
        Tests that only taxonomy admins can associate orgs to taxonomies
        """
        url = TAXONOMY_ORG_UPDATE_ORG_URL.format(pk=self.tA1.pk)
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        response = self.client.put(url, {"orgs": []}, format="json")
        assert response.status_code in [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND]

        # Check that the orgs didn't change
        url = TAXONOMY_ORG_DETAIL_URL.format(pk=self.tA1.pk)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["orgs"] == [self.orgA.short_name]

    def test_update_org_check_permissions_orgA(self) -> None:
        """
        Tests that adding an org to a taxonomy allow org level admins to edit it
        """
        url = TAXONOMY_ORG_DETAIL_URL.format(pk=self.tB1.pk)
        self.client.force_authenticate(user=self.staffA)

        response = self.client.put(url, {"name": "new name"}, format="json")

        # User staffA can't update metadata from a taxonomy from orgB
        assert response.status_code == status.HTTP_404_NOT_FOUND

        url = TAXONOMY_ORG_UPDATE_ORG_URL.format(pk=self.tB1.pk)
        self.client.force_authenticate(user=self.staff)

        # Add the taxonomy tB1 to orgA
        response = self.client.put(url, {"orgs": [self.orgA.short_name]}, format="json")

        url = TAXONOMY_ORG_DETAIL_URL.format(pk=self.tB1.pk)
        self.client.force_authenticate(user=self.staffA)

        response = self.client.put(url, {"name": "new name"}, format="json")

        # Now staffA can change the metadata from a tB1 because it's associated with orgA
        assert response.status_code == status.HTTP_200_OK

    def test_update_org_check_permissions_all_orgs(self) -> None:
        """
        Tests that adding an org to all orgs only let taxonomy global admins to edit it
        """
        url = TAXONOMY_ORG_DETAIL_URL.format(pk=self.tA1.pk)
        self.client.force_authenticate(user=self.staffA)

        response = self.client.put(url, {"name": "new name"}, format="json")

        # User staffA can update metadata from a taxonomy from orgA
        assert response.status_code == status.HTTP_200_OK

        url = TAXONOMY_ORG_UPDATE_ORG_URL.format(pk=self.tB1.pk)
        self.client.force_authenticate(user=self.staff)

        # Add the taxonomy tA1 to all orgs
        response = self.client.put(url, {"all_orgs": True}, format="json")

        url = TAXONOMY_ORG_DETAIL_URL.format(pk=self.tB1.pk)
        self.client.force_authenticate(user=self.staffA)

        response = self.client.put(url, {"name": "new name"}, format="json")

        # Now staffA can't change the metadata from a tA1 because only global taxonomy admins can edit all orgs
        # taxonomies
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_update_org_check_permissions_no_orgs(self) -> None:
        """
        Tests that remove all orgs from a taxonomy only let taxonomy global admins to edit it
        """
        url = TAXONOMY_ORG_DETAIL_URL.format(pk=self.tA1.pk)
        self.client.force_authenticate(user=self.staffA)

        response = self.client.put(url, {"name": "new name"}, format="json")

        # User staffA can update metadata from a taxonomy from orgA
        assert response.status_code == status.HTTP_200_OK

        url = TAXONOMY_ORG_UPDATE_ORG_URL.format(pk=self.tB1.pk)
        self.client.force_authenticate(user=self.staff)

        # Remove all orgs from tA1
        response = self.client.put(url, {"orgs": []}, format="json")

        url = TAXONOMY_ORG_DETAIL_URL.format(pk=self.tB1.pk)
        self.client.force_authenticate(user=self.staffA)

        response = self.client.put(url, {"name": "new name"}, format="json")

        # Now staffA can't change the metadata from a tA1 because only global taxonomy admins can edit no orgs
        # taxonomies
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestObjectTagMixin(TestTaxonomyObjectsMixin):
    """
    Sets up data for testing ObjectTags.
    """
    def setUp(self):
        """
        Setup the test cases
        """
        super().setUp()
        self.xblockA = BlockUsageLocator(
            course_key=self.courseA,
            block_type='problem',
            block_id='block_id'
        )
        self.xblockB = BlockUsageLocator(
            course_key=self.courseB,
            block_type='problem',
            block_id='block_id'
        )

        self.multiple_taxonomy = tagging_api.create_taxonomy(
            name="Multiple Taxonomy",
            allow_multiple=True,
        )
        self.single_value_taxonomy = tagging_api.create_taxonomy(
            name="Required Taxonomy",
            allow_multiple=False,
        )
        for i in range(20):
            # Valid ObjectTags
            Tag.objects.create(taxonomy=self.tA1, value=f"Tag {i}")
            Tag.objects.create(taxonomy=self.tA2, value=f"Tag {i}")
            Tag.objects.create(taxonomy=self.multiple_taxonomy, value=f"Tag {i}")
            Tag.objects.create(taxonomy=self.single_value_taxonomy, value=f"Tag {i}")

        self.open_taxonomy = tagging_api.create_taxonomy(
            name="Enabled Free-Text Taxonomy",
            allow_free_text=True,
        )

        # Add org permissions to taxonomy
        TaxonomyOrg.objects.create(
            taxonomy=self.multiple_taxonomy,
            org=self.orgA,
            rel_type=TaxonomyOrg.RelType.OWNER,
        )
        TaxonomyOrg.objects.create(
            taxonomy=self.single_value_taxonomy,
            org=self.orgA,
            rel_type=TaxonomyOrg.RelType.OWNER,
        )
        TaxonomyOrg.objects.create(
            taxonomy=self.open_taxonomy,
            org=self.orgA,
            rel_type=TaxonomyOrg.RelType.OWNER,
        )

        add_users(self.staff, CourseStaffRole(self.courseA), self.staffA)


@skip_unless_cms
@ddt.ddt
class TestObjectTagViewSet(TestObjectTagMixin, APITestCase):
    """
    Testing various cases for the ObjectTagView.
    """

    def _call_put_request(self, object_id, taxonomy_id, tags):
        url = OBJECT_TAG_UPDATE_URL.format(object_id=object_id)
        return self.client.put(url, {"tagsData": [{
            "taxonomy": taxonomy_id,
            "tags": tags,
        }]}, format="json")

    @ddt.data(
        # staffA and staff are staff in courseA and can tag using enabled taxonomies
        ("user", "tA1", ["Tag 1"], status.HTTP_403_FORBIDDEN),
        ("staffA", "tA1", ["Tag 1"], status.HTTP_200_OK),
        ("staff", "tA1", ["Tag 1"], status.HTTP_200_OK),
        ("user", "tA1", [], status.HTTP_403_FORBIDDEN),
        ("staffA", "tA1", [], status.HTTP_200_OK),
        ("staff", "tA1", [], status.HTTP_200_OK),
        ("user", "multiple_taxonomy", ["Tag 1", "Tag 2"], status.HTTP_403_FORBIDDEN),
        ("staffA", "multiple_taxonomy", ["Tag 1", "Tag 2"], status.HTTP_200_OK),
        ("staff", "multiple_taxonomy", ["Tag 1", "Tag 2"], status.HTTP_200_OK),
        ("user", "open_taxonomy", ["tag1"], status.HTTP_403_FORBIDDEN),
        ("staffA", "open_taxonomy", ["tag1"], status.HTTP_200_OK),
        ("staff", "open_taxonomy", ["tag1"], status.HTTP_200_OK),
    )
    @ddt.unpack
    def test_tag_course(self, user_attr, taxonomy_attr, tag_values, expected_status):
        """
        Tests that only staff and org level users can tag courses
        """
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        taxonomy = getattr(self, taxonomy_attr)

        response = self._call_put_request(self.courseA, taxonomy.pk, tag_values)

        assert response.status_code == expected_status
        if status.is_success(expected_status):
            tags_by_taxonomy = response.data[str(self.courseA)]["taxonomies"]
            if tag_values:
                response_taxonomy = tags_by_taxonomy[0]
                assert response_taxonomy["name"] == taxonomy.name
                response_tags = response_taxonomy["tags"]
                assert [t["value"] for t in response_tags] == tag_values
            else:
                assert tags_by_taxonomy == []  # No tags are set from any taxonomy

            # Check that re-fetching the tags returns what we set
            url = OBJECT_TAG_UPDATE_URL.format(object_id=self.courseA)
            new_response = self.client.get(url, format="json")
            assert status.is_success(new_response.status_code)
            assert new_response.data == response.data

    @ddt.data(
        "staffA",
        "staff",
    )
    def test_tag_course_disabled_taxonomy(self, user_attr):
        """
        Nobody can use disable taxonomies to tag objects
        """
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        disabled_taxonomy = self.tA2
        assert disabled_taxonomy.enabled is False

        response = self._call_put_request(self.courseA, disabled_taxonomy.pk, ["Tag 1"])

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @ddt.data(
        ("staffA", "tA1"),
        ("staff", "tA1"),
        ("staffA", "multiple_taxonomy"),
        ("staff", "multiple_taxonomy"),
    )
    @ddt.unpack
    def test_tag_course_invalid(self, user_attr, taxonomy_attr):
        """
        Tests that nobody can add invalid tags to a course using a closed taxonomy
        """
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        taxonomy = getattr(self, taxonomy_attr)

        response = self._call_put_request(self.courseA, taxonomy.pk, ["invalid"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @ddt.data(
        # staffA and staff are staff in courseA (owner of xblockA) and can tag using any taxonomies
        ("user", "tA1", ["Tag 1"], status.HTTP_403_FORBIDDEN),
        ("staffA", "tA1", ["Tag 1"], status.HTTP_200_OK),
        ("staff", "tA1", ["Tag 1"], status.HTTP_200_OK),
        ("user", "multiple_taxonomy", ["Tag 1", "Tag 2"], status.HTTP_403_FORBIDDEN),
        ("staffA", "tA1", [], status.HTTP_200_OK),
        ("staff", "tA1", [], status.HTTP_200_OK),
        ("staffA", "multiple_taxonomy", ["Tag 1", "Tag 2"], status.HTTP_200_OK),
        ("staff", "multiple_taxonomy", ["Tag 1", "Tag 2"], status.HTTP_200_OK),
        ("user", "open_taxonomy", ["tag1"], status.HTTP_403_FORBIDDEN),
        ("staffA", "open_taxonomy", ["tag1"], status.HTTP_200_OK),
        ("staff", "open_taxonomy", ["tag1"], status.HTTP_200_OK),
    )
    @ddt.unpack
    def test_tag_xblock(self, user_attr, taxonomy_attr, tag_values, expected_status):
        """
        Tests that only staff and org level users can tag xblocks
        """
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        taxonomy = getattr(self, taxonomy_attr)

        response = self._call_put_request(self.xblockA, taxonomy.pk, tag_values)

        assert response.status_code == expected_status
        if status.is_success(expected_status):
            tags_by_taxonomy = response.data[str(self.xblockA)]["taxonomies"]
            if tag_values:
                response_taxonomy = tags_by_taxonomy[0]
                assert response_taxonomy["name"] == taxonomy.name
                response_tags = response_taxonomy["tags"]
                assert [t["value"] for t in response_tags] == tag_values
            else:
                assert tags_by_taxonomy == []  # No tags are set from any taxonomy

            # Check that re-fetching the tags returns what we set
            url = OBJECT_TAG_UPDATE_URL.format(object_id=self.xblockA)
            new_response = self.client.get(url, format="json")
            assert status.is_success(new_response.status_code)
            assert new_response.data == response.data

    @ddt.data(
        "staffA",
        "staff",
    )
    def test_tag_xblock_disabled_taxonomy(self, user_attr):
        """
        Tests that nobody can use disabled taxonomies to tag xblocks
        """
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        disabled_taxonomy = self.tA2
        assert disabled_taxonomy.enabled is False

        response = self._call_put_request(self.xblockA, disabled_taxonomy.pk, ["Tag 1"])

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @ddt.data(
        ("staffA", "tA1"),
        ("staff", "tA1"),
        ("staffA", "multiple_taxonomy"),
        ("staff", "multiple_taxonomy"),
    )
    @ddt.unpack
    def test_tag_xblock_invalid(self, user_attr, taxonomy_attr):
        """
        Tests that staff can't add invalid tags to a xblock using a closed taxonomy
        """
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        taxonomy = getattr(self, taxonomy_attr)

        response = self._call_put_request(self.xblockA, taxonomy.pk, ["invalid"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @ddt.data(
        # staffA and staff are staff in libraryA and can tag using enabled taxonomies
        ("user", "tA1", ["Tag 1"], status.HTTP_403_FORBIDDEN),
        ("staffA", "tA1", ["Tag 1"], status.HTTP_200_OK),
        ("staff", "tA1", ["Tag 1"], status.HTTP_200_OK),
        ("user", "tA1", [], status.HTTP_403_FORBIDDEN),
        ("staffA", "tA1", [], status.HTTP_200_OK),
        ("staff", "tA1", [], status.HTTP_200_OK),
        ("user", "multiple_taxonomy", ["Tag 1", "Tag 2"], status.HTTP_403_FORBIDDEN),
        ("staffA", "multiple_taxonomy", ["Tag 1", "Tag 2"], status.HTTP_200_OK),
        ("staff", "multiple_taxonomy", ["Tag 1", "Tag 2"], status.HTTP_200_OK),
        ("user", "open_taxonomy", ["tag1"], status.HTTP_403_FORBIDDEN),
        ("staffA", "open_taxonomy", ["tag1"], status.HTTP_200_OK),
        ("staff", "open_taxonomy", ["tag1"], status.HTTP_200_OK),
    )
    @ddt.unpack
    def test_tag_library(self, user_attr, taxonomy_attr, tag_values, expected_status):
        """
        Tests that only staff and org level users can tag libraries
        """
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        taxonomy = getattr(self, taxonomy_attr)

        response = self._call_put_request(self.libraryA, taxonomy.pk, tag_values)

        assert response.status_code == expected_status
        if status.is_success(expected_status):
            tags_by_taxonomy = response.data[str(self.libraryA)]["taxonomies"]
            if tag_values:
                response_taxonomy = tags_by_taxonomy[0]
                assert response_taxonomy["name"] == taxonomy.name
                response_tags = response_taxonomy["tags"]
                assert [t["value"] for t in response_tags] == tag_values
            else:
                assert tags_by_taxonomy == []  # No tags are set from any taxonomy

            # Check that re-fetching the tags returns what we set
            url = OBJECT_TAG_UPDATE_URL.format(object_id=self.libraryA)
            new_response = self.client.get(url, format="json")
            assert status.is_success(new_response.status_code)
            assert new_response.data == response.data

    @ddt.data(
        "staffA",
        "staff",
    )
    def test_tag_library_disabled_taxonomy(self, user_attr):
        """
        Nobody can use disabled taxonomies to tag objects
        """
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        disabled_taxonomy = self.tA2
        assert disabled_taxonomy.enabled is False

        response = self._call_put_request(self.libraryA, disabled_taxonomy.pk, ["Tag 1"])

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @ddt.data(
        ("staffA", "tA1"),
        ("staff", "tA1"),
        ("staffA", "multiple_taxonomy"),
        ("staff", "multiple_taxonomy"),
    )
    @ddt.unpack
    def test_tag_library_invalid(self, user_attr, taxonomy_attr):
        """
        Tests that nobody can add invalid tags to a library using a closed taxonomy
        """
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        taxonomy = getattr(self, taxonomy_attr)

        response = self._call_put_request(self.libraryA, taxonomy.pk, ["invalid"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @ddt.data(
        # staffA and staff are staff in collection and can tag using enabled taxonomies
        ("user", "tA1", ["Tag 1"], status.HTTP_403_FORBIDDEN),
        ("staffA", "tA1", ["Tag 1"], status.HTTP_200_OK),
        ("staff", "tA1", ["Tag 1"], status.HTTP_200_OK),
        ("user", "tA1", [], status.HTTP_403_FORBIDDEN),
        ("staffA", "tA1", [], status.HTTP_200_OK),
        ("staff", "tA1", [], status.HTTP_200_OK),
        ("user", "multiple_taxonomy", ["Tag 1", "Tag 2"], status.HTTP_403_FORBIDDEN),
        ("staffA", "multiple_taxonomy", ["Tag 1", "Tag 2"], status.HTTP_200_OK),
        ("staff", "multiple_taxonomy", ["Tag 1", "Tag 2"], status.HTTP_200_OK),
        ("user", "open_taxonomy", ["tag1"], status.HTTP_403_FORBIDDEN),
        ("staffA", "open_taxonomy", ["tag1"], status.HTTP_200_OK),
        ("staff", "open_taxonomy", ["tag1"], status.HTTP_200_OK),
    )
    @ddt.unpack
    def test_tag_collection(self, user_attr, taxonomy_attr, tag_values, expected_status):
        """
        Tests that only staff and org level users can tag collections
        """
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        taxonomy = getattr(self, taxonomy_attr)

        response = self._call_put_request(self.collection_key, taxonomy.pk, tag_values)

        assert response.status_code == expected_status
        if status.is_success(expected_status):
            tags_by_taxonomy = response.data[str(self.collection_key)]["taxonomies"]
            if tag_values:
                response_taxonomy = tags_by_taxonomy[0]
                assert response_taxonomy["name"] == taxonomy.name
                response_tags = response_taxonomy["tags"]
                assert [t["value"] for t in response_tags] == tag_values
            else:
                assert tags_by_taxonomy == []  # No tags are set from any taxonomy

            # Check that re-fetching the tags returns what we set
            url = OBJECT_TAG_UPDATE_URL.format(object_id=self.collection_key)
            new_response = self.client.get(url, format="json")
            assert status.is_success(new_response.status_code)
            assert new_response.data == response.data

    @ddt.data(
        # staffA and staff are staff in collection and can tag using enabled taxonomies
        ("user", "tA1", ["Tag 1"], status.HTTP_403_FORBIDDEN),
        ("staffA", "tA1", ["Tag 1"], status.HTTP_200_OK),
        ("staff", "tA1", ["Tag 1"], status.HTTP_200_OK),
        ("user", "tA1", [], status.HTTP_403_FORBIDDEN),
        ("staffA", "tA1", [], status.HTTP_200_OK),
        ("staff", "tA1", [], status.HTTP_200_OK),
        ("user", "multiple_taxonomy", ["Tag 1", "Tag 2"], status.HTTP_403_FORBIDDEN),
        ("staffA", "multiple_taxonomy", ["Tag 1", "Tag 2"], status.HTTP_200_OK),
        ("staff", "multiple_taxonomy", ["Tag 1", "Tag 2"], status.HTTP_200_OK),
        ("user", "open_taxonomy", ["tag1"], status.HTTP_403_FORBIDDEN),
        ("staffA", "open_taxonomy", ["tag1"], status.HTTP_200_OK),
        ("staff", "open_taxonomy", ["tag1"], status.HTTP_200_OK),
    )
    @ddt.unpack
    def test_tag_container(self, user_attr, taxonomy_attr, tag_values, expected_status):
        """
        Tests that only staff and org level users can tag containers
        """
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        taxonomy = getattr(self, taxonomy_attr)

        response = self._call_put_request(self.container_key, taxonomy.pk, tag_values)

        assert response.status_code == expected_status
        if status.is_success(expected_status):
            tags_by_taxonomy = response.data[str(self.container_key)]["taxonomies"]
            if tag_values:
                response_taxonomy = tags_by_taxonomy[0]
                assert response_taxonomy["name"] == taxonomy.name
                response_tags = response_taxonomy["tags"]
                assert [t["value"] for t in response_tags] == tag_values
            else:
                assert tags_by_taxonomy == []  # No tags are set from any taxonomy

            # Check that re-fetching the tags returns what we set
            url = OBJECT_TAG_UPDATE_URL.format(object_id=self.container_key)
            new_response = self.client.get(url, format="json")
            assert status.is_success(new_response.status_code)
            assert new_response.data == response.data

    @ddt.data(
        "staffA",
        "staff",
    )
    def test_tag_collection_disabled_taxonomy(self, user_attr):
        """
        Nobody can use disabled taxonomies to tag objects
        """
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        disabled_taxonomy = self.tA2
        assert disabled_taxonomy.enabled is False

        response = self._call_put_request(self.collection_key, disabled_taxonomy.pk, ["Tag 1"])

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @ddt.data(
        ("staffA", "tA1"),
        ("staff", "tA1"),
        ("staffA", "multiple_taxonomy"),
        ("staff", "multiple_taxonomy"),
    )
    @ddt.unpack
    def test_tag_collection_invalid(self, user_attr, taxonomy_attr):
        """
        Tests that nobody can add invalid tags to a collection using a closed taxonomy
        """
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        taxonomy = getattr(self, taxonomy_attr)

        response = self._call_put_request(self.collection_key, taxonomy.pk, ["invalid"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @ddt.data(
        ("superuser", status.HTTP_200_OK),
        ("staff", status.HTTP_403_FORBIDDEN),
        ("staffA", status.HTTP_403_FORBIDDEN),
        ("staffB", status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_tag_cross_org(self, user_attr, expected_status):
        """
        Tests that only superusers may add a taxonomy from orgA to an object from orgB
        """
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        response = self._call_put_request(self.courseB, self.tA1.pk, ["Tag 1"])

        assert response.status_code == expected_status

    @ddt.data(
        ("superuser", status.HTTP_200_OK),
        ("staff", status.HTTP_403_FORBIDDEN),
        ("staffA", status.HTTP_403_FORBIDDEN),
        ("staffB", status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_tag_no_org(self, user_attr, expected_status):
        """
        Tests that only superusers may add a no-org taxonomy to an object
        """
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)

        response = self._call_put_request(self.courseA, self.ot1.pk, [])

        assert response.status_code == expected_status

    @ddt.data(
        "courseB",
        "xblockB",
    )
    def test_tag_no_permission(self, objectid_attr):
        """
        Test that a user without access to courseB can't apply tags to it
        """
        self.client.force_authenticate(user=self.staffA)
        object_id = getattr(self, objectid_attr)

        response = self._call_put_request(object_id, self.tA1.pk, ["Tag 1"])

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @ddt.data(
        "courseB",
        "xblockB",
    )
    def test_tag_unauthorized(self, objectid_attr):
        """
        Test that a user without access to courseB can't apply tags to it
        """
        object_id = getattr(self, objectid_attr)

        response = self._call_put_request(object_id, self.tA1.pk, ["Tag 1"])

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_tag_invalid_object(self):
        """
        Test that we cannot tag an object that is not a CouseKey, LibraryLocatorV2 or UsageKey
        """
        self.client.force_authenticate(user=self.staff)

        response = self._call_put_request('invalid_key', self.tA1.pk, ["Tag 1"])

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_tags(self):
        """
        Test that we can get tags for an object
        """
        self.client.force_authenticate(user=self.staffA)
        taxonomy = self.multiple_taxonomy
        tag_values = ["Tag 1", "Tag 2"]

        # Tag an object
        response1 = self._call_put_request(self.courseA, taxonomy.pk, tag_values)
        assert status.is_success(response1.status_code)

        # Fetch this object's tags for a single taxonomy
        expected_tags = [{
            'name': 'Multiple Taxonomy',
            'export_id': '13-multiple-taxonomy',
            'taxonomy_id': taxonomy.pk,
            'can_tag_object': True,
            'tags': [
                {
                    'value': 'Tag 1',
                    'lineage': ['Tag 1'],
                    'can_delete_objecttag': True,
                    'is_copied': False,
                },
                {
                    'value': 'Tag 2',
                    'lineage': ['Tag 2'],
                    'can_delete_objecttag': True,
                    'is_copied': False,
                },
            ],
        }]

        # Fetch tags for a single taxonomy
        get_url = OBJECT_TAGS_URL.format(object_id=self.courseA)
        get_url += f"?taxonomy={taxonomy.pk}"
        response2 = self.client.get(get_url, format="json")
        assert status.is_success(response2.status_code)
        assert response2.data[str(self.courseA)]["taxonomies"] == expected_tags

        # Fetch all of this object's tags, for all taxonomies
        get_all_url = OBJECT_TAGS_URL.format(object_id=self.courseA)
        response3 = self.client.get(get_all_url, format="json")
        assert status.is_success(response3.status_code)
        assert response3.data[str(self.courseA)]["taxonomies"] == expected_tags

    def test_get_copied_tags(self):
        self.client.force_authenticate(user=self.staffB)

        object_id_1 = str(self.courseA)
        object_id_2 = str(self.courseB)
        tagging_api.tag_object(object_id=object_id_1, taxonomy=self.t1, tags=["android"])
        tagging_api.tag_object(object_id=object_id_2, taxonomy=self.t1, tags=["anvil"])
        tagging_api.copy_tags_as_read_only(object_id_1, object_id_2)

        expected_tags = [{
            'name': self.t1.name,
            'taxonomy_id': self.t1.pk,
            'can_tag_object': True,
            'export_id': self.t1.export_id,
            'tags': [
                {
                    'value': 'android',
                    'lineage': ['ALPHABET', 'android'],
                    'can_delete_objecttag': False,
                    'is_copied': True,
                },
                {
                    'value': 'anvil',
                    'lineage': ['ALPHABET', 'anvil'],
                    'can_delete_objecttag': True,
                    'is_copied': False,
                },
            ]
        }]

        get_url = OBJECT_TAGS_URL.format(object_id=self.courseB)
        response = self.client.get(get_url, format="json")
        assert status.is_success(response.status_code)
        assert response.data[str(object_id_2)]["taxonomies"] == expected_tags

    @ddt.data(
        ('staff', 'courseA', 8),
        ('staff', 'libraryA', 8),
        ('staff', 'collection_key', 8),
        ("content_creatorA", 'courseA', 17, False),
        ("content_creatorA", 'libraryA', 17, False),
        ("content_creatorA", 'collection_key', 17, False),
        ("library_staffA", 'libraryA', 17, False),  # Library users can only view objecttags, not change them?
        ("library_staffA", 'collection_key', 17, False),
        ("library_userA", 'libraryA', 17, False),
        ("library_userA", 'collection_key', 17, False),
        ("instructorA", 'courseA', 17),
        ("course_instructorA", 'courseA', 17),
        ("course_staffA", 'courseA', 17),
    )
    @ddt.unpack
    def test_object_tags_query_count(
            self,
            user_attr: str,
            object_attr: str,
            expected_queries: int,
            expected_perm: bool = True):
        """
        Test how many queries are used when retrieving object tags and permissions
        """
        object_key = getattr(self, object_attr)
        object_id = str(object_key)
        tagging_api.tag_object(object_id=object_id, taxonomy=self.t1, tags=["anvil", "android"])
        expected_tags = [
            {
                "value": "android",
                "lineage": ["ALPHABET", "android"],
                "can_delete_objecttag": expected_perm,
                "is_copied": False,
            },
            {
                "value": "anvil",
                "lineage": ["ALPHABET", "anvil"],
                "can_delete_objecttag": expected_perm,
                "is_copied": False,
            },
        ]
        url = OBJECT_TAGS_URL.format(object_id=object_id)
        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)
        with self.assertNumQueries(expected_queries):
            response = self.client.get(url)

        assert response.status_code == 200
        assert len(response.data[object_id]["taxonomies"]) == 1
        assert response.data[object_id]["taxonomies"][0]["can_tag_object"] == expected_perm
        assert response.data[object_id]["taxonomies"][0]["tags"] == expected_tags


@skip_unless_cms
@ddt.ddt
class TestContentObjectChildrenExportView(TaggedCourseMixin, APITestCase):  # type: ignore[misc]
    """
    Tests exporting course children with tags
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.staff = UserFactory.create(
            username="staff",
            email="staff@example.com",
            is_staff=True,
        )

        self.staffA = UserFactory.create(
            username="staffA",
            email="userA@example.com",
        )
        update_org_role(self.staff, OrgStaffRole, self.staffA, [self.orgA.short_name])

    @ddt.data(
        "staff",
        "staffA",
    )
    def test_export_course(self, user_attr) -> None:
        url = OBJECT_TAGS_EXPORT_URL.format(object_id=str(self.course.id))

        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.headers['Content-Type'] == 'text/csv'

        zip_content = BytesIO(b"".join(response.streaming_content)).getvalue()  # type: ignore[attr-defined]
        assert zip_content == self.expected_csv.encode()

    def test_export_course_anoymous_forbidden(self) -> None:
        url = OBJECT_TAGS_EXPORT_URL.format(object_id=str(self.course.id))
        response = self.client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_export_course_user_forbidden(self) -> None:
        url = OBJECT_TAGS_EXPORT_URL.format(object_id=str(self.course.id))
        self.client.force_authenticate(user=self.user)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_export_course_invalid_id(self) -> None:
        url = OBJECT_TAGS_EXPORT_URL.format(object_id="invalid")
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN


@skip_unless_cms
@ddt.ddt
class TestDownloadTemplateView(APITestCase):
    """
    Tests the taxonomy template downloads.
    """
    @ddt.data(
        ("template.csv", "text/csv"),
        ("template.json", "application/json"),
    )
    @ddt.unpack
    def test_download(self, filename, content_type) -> None:
        url = TAXONOMY_TEMPLATE_URL.format(filename=filename)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.headers['Content-Type'] == content_type
        assert response.headers['Content-Disposition'] == f'attachment; filename="{filename}"'
        assert int(response.headers['Content-Length']) > 0

    def test_download_not_found(self) -> None:
        url = TAXONOMY_TEMPLATE_URL.format(filename="template.txt")
        response = self.client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_download_method_not_allowed(self) -> None:
        url = TAXONOMY_TEMPLATE_URL.format(filename="template.txt")
        response = self.client.post(url)
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class ImportTaxonomyMixin(TestTaxonomyObjectsMixin):
    """
    Mixin to test importing taxonomies.
    """
    def _get_file(self, tags: list, file_format: str) -> SimpleUploadedFile:
        """
        Returns a file for the given format.
        """
        if file_format == "csv":
            csv_data = "id,value"
            for tag in tags:
                csv_data += f"\n{tag['id']},{tag['value']}"
            return SimpleUploadedFile("taxonomy.csv", csv_data.encode(), content_type="text/csv")
        else:  # json
            json_data = {"tags": tags}
            return SimpleUploadedFile("taxonomy.json", json.dumps(json_data).encode(), content_type="application/json")


@skip_unless_cms
@ddt.ddt
class TestCreateImportView(ImportTaxonomyMixin, APITestCase):
    """
    Tests the create/import taxonomy action.
    """
    @ddt.data(
        "csv",
        "json",
    )
    def test_import_global_admin(self, file_format: str) -> None:
        """
        Tests importing a valid taxonomy file with a global admin.
        """
        url = TAXONOMY_CREATE_IMPORT_URL
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4"},
        ]
        file = self._get_file(new_tags, file_format)

        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "taxonomy_export_id": "imported_taxonomy",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_201_CREATED

        # Check if the taxonomy was created
        taxonomy = response.data
        assert taxonomy["name"] == "Imported Taxonomy name"
        assert taxonomy["description"] == "Imported Taxonomy description"
        assert taxonomy["export_id"] == "imported_taxonomy"

        # Check if the tags were created
        url = TAXONOMY_TAGS_URL.format(pk=taxonomy["id"])
        response = self.client.get(url)
        tags = response.data["results"]
        assert len(tags) == len(new_tags)
        for i, tag in enumerate(tags):
            assert tag["value"] == new_tags[i]["value"]

        # Check if the taxonomy was no association with orgs
        assert len(taxonomy["orgs"]) == 0

    @ddt.data(
        "csv",
        "json",
    )
    def test_import_orgA_admin(self, file_format: str) -> None:
        """
        Tests importing a valid taxonomy file with a orgA admin.
        """
        url = TAXONOMY_CREATE_IMPORT_URL
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4"},
        ]
        file = self._get_file(new_tags, file_format)

        self.client.force_authenticate(user=self.staffA)
        response = self.client.post(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "taxonomy_export_id": "imported_taxonomy",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_201_CREATED

        # Check if the taxonomy was created
        taxonomy = response.data
        assert taxonomy["name"] == "Imported Taxonomy name"
        assert taxonomy["description"] == "Imported Taxonomy description"
        assert taxonomy["export_id"] == "imported_taxonomy"

        # Check if the tags were created
        url = TAXONOMY_TAGS_URL.format(pk=taxonomy["id"])
        response = self.client.get(url)
        tags = response.data["results"]
        assert len(tags) == len(new_tags)
        for i, tag in enumerate(tags):
            assert tag["value"] == new_tags[i]["value"]

        # Check if the taxonomy was associated with the orgA
        assert len(taxonomy["orgs"]) == 1
        assert taxonomy["orgs"][0] == self.orgA.short_name

    def test_import_no_file(self) -> None:
        """
        Tests importing a taxonomy without a file.
        """
        url = TAXONOMY_CREATE_IMPORT_URL
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "taxonomy_export_id": "imported_taxonomy",
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["file"][0] == "No file was submitted."

        # Check if the taxonomy was not created
        assert not Taxonomy.objects.filter(name="Imported Taxonomy name").exists()

    @ddt.data(
        "csv",
        "json",
    )
    def test_import_no_name(self, file_format) -> None:
        """
        Tests importing a taxonomy without specifing a name.
        """
        url = TAXONOMY_CREATE_IMPORT_URL
        file = SimpleUploadedFile(f"taxonomy.{file_format}", b"invalid file content")
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            url,
            {
                "taxonomy_description": "Imported Taxonomy description",
                "taxonomy_export_id": "imported_taxonomy",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["taxonomy_name"][0] == "This field is required."

        # Check if the taxonomy was not created
        assert not Taxonomy.objects.filter(name="Imported Taxonomy name").exists()

    @ddt.data(
        "csv",
        "json",
    )
    def test_import_no_export_id(self, file_format) -> None:
        url = TAXONOMY_CREATE_IMPORT_URL
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
        ]
        file = self._get_file(new_tags, file_format)
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            url,
            {
                "taxonomy_name": "Imported Taxonomy",
                "taxonomy_description": "Imported Taxonomy description",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_201_CREATED

        taxonomy = response.data
        taxonomy_id = taxonomy["id"]
        assert taxonomy["name"] == "Imported Taxonomy"
        assert taxonomy["description"] == "Imported Taxonomy description"
        assert taxonomy["export_id"] == f"{taxonomy_id}-imported-taxonomy"

        # Check if the taxonomy was not created
        assert not Taxonomy.objects.filter(name="Imported Taxonomy name").exists()

    def test_import_invalid_format(self) -> None:
        """
        Tests importing a taxonomy with an invalid file format.
        """
        url = TAXONOMY_CREATE_IMPORT_URL
        file = SimpleUploadedFile("taxonomy.invalid", b"invalid file content")
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "taxonomy_export_id": "imported_taxonomy_id",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["file"][0] == "File type not supported: invalid"

        # Check if the taxonomy was not created
        assert not Taxonomy.objects.filter(name="Imported Taxonomy name").exists()

    @ddt.data(
        "csv",
        "json",
    )
    def test_import_invalid_content(self, file_format) -> None:
        """
        Tests importing a taxonomy with an invalid file content.
        """
        url = TAXONOMY_CREATE_IMPORT_URL
        file = SimpleUploadedFile(f"taxonomy.{file_format}", b"invalid file content")
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "taxonomy_export_id": "imported_taxonomy",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert f"Invalid '.{file_format}' format:" in response.data

        # Check if the taxonomy was not created
        assert not Taxonomy.objects.filter(name="Imported Taxonomy name").exists()

    def test_import_no_perm(self) -> None:
        """
        Tests importing a taxonomy using a user without permission.
        """
        url = TAXONOMY_CREATE_IMPORT_URL
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4"},
        ]
        file = self._get_file(new_tags, "json")

        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "taxonomy_export_id": "imported_taxonomy",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Check if the taxonomy was not created
        assert not Taxonomy.objects.filter(name="Imported Taxonomy name").exists()


@skip_unless_cms
@ddt.ddt
class TestImportTagsView(ImportTaxonomyMixin, APITestCase):
    """
    Tests the taxonomy import tags action.
    """
    def setUp(self):
        ImportTaxonomyMixin.setUp(self)

        self.taxonomy = tagging_api.create_taxonomy(
            name="Test import taxonomy",
        )
        tag_1 = Tag.objects.create(
            taxonomy=self.taxonomy,
            external_id="old_tag_1",
            value="Old tag 1",
        )
        tag_2 = Tag.objects.create(
            taxonomy=self.taxonomy,
            external_id="old_tag_2",
            value="Old tag 2",
        )
        self.old_tags = [tag_1, tag_2]

    @ddt.data(
        "csv",
        "json",
    )
    def test_import(self, file_format: str) -> None:
        """
        Tests importing a valid taxonomy file.
        """
        url = TAXONOMY_TAGS_IMPORT_URL.format(pk=self.taxonomy.id)
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4"},
        ]
        file = self._get_file(new_tags, file_format)

        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            url,
            {"file": file},
            format="multipart"
        )
        assert response.status_code == status.HTTP_200_OK

        # Check if the tags were created
        url = TAXONOMY_TAGS_URL.format(pk=self.taxonomy.id)
        response = self.client.get(url)
        tags = response.data["results"]
        assert len(tags) == len(new_tags)
        for i, tag in enumerate(tags):
            assert tag["value"] == new_tags[i]["value"]

    def test_import_no_file(self) -> None:
        """
        Tests importing a taxonomy without a file.
        """
        url = TAXONOMY_TAGS_IMPORT_URL.format(pk=self.taxonomy.id)
        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            url,
            {},
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["file"][0] == "No file was submitted."

        # Check if the taxonomy was not changed
        url = TAXONOMY_TAGS_URL.format(pk=self.taxonomy.id)
        response = self.client.get(url)
        tags = response.data["results"]
        assert len(tags) == len(self.old_tags)
        for i, tag in enumerate(tags):
            assert tag["value"] == self.old_tags[i].value

    def test_import_invalid_format(self) -> None:
        """
        Tests importing a taxonomy with an invalid file format.
        """
        url = TAXONOMY_TAGS_IMPORT_URL.format(pk=self.taxonomy.id)
        file = SimpleUploadedFile("taxonomy.invalid", b"invalid file content")
        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            url,
            {"file": file},
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["file"][0] == "File type not supported: invalid"

        # Check if the taxonomy was not changed
        url = TAXONOMY_TAGS_URL.format(pk=self.taxonomy.id)
        response = self.client.get(url)
        tags = response.data["results"]
        assert len(tags) == len(self.old_tags)
        for i, tag in enumerate(tags):
            assert tag["value"] == self.old_tags[i].value

    @ddt.data(
        "csv",
        "json",
    )
    def test_import_invalid_content(self, file_format) -> None:
        """
        Tests importing a taxonomy with an invalid file content.
        """
        url = TAXONOMY_TAGS_IMPORT_URL.format(pk=self.taxonomy.id)
        file = SimpleUploadedFile(f"taxonomy.{file_format}", b"invalid file content")
        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "taxonomy_export_id": "imported_taxonomy",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert f"Invalid '.{file_format}' format:" in response.data

        # Check if the taxonomy was not changed
        url = TAXONOMY_TAGS_URL.format(pk=self.taxonomy.id)
        response = self.client.get(url)
        tags = response.data["results"]
        assert len(tags) == len(self.old_tags)
        for i, tag in enumerate(tags):
            assert tag["value"] == self.old_tags[i].value

    @ddt.data(
        "csv",
        "json",
    )
    def test_import_free_text(self, file_format) -> None:
        """
        Tests importing a taxonomy with an invalid file content.
        """
        self.taxonomy.allow_free_text = True
        self.taxonomy.save()
        url = TAXONOMY_TAGS_IMPORT_URL.format(pk=self.taxonomy.id)
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4"},
        ]
        file = self._get_file(new_tags, file_format)

        self.client.force_authenticate(user=self.staff)
        response = self.client.put(
            url,
            {"file": file},
            format="multipart"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == f"Invalid taxonomy ({self.taxonomy.id}): You cannot import a free-form taxonomy."

        # Check if the taxonomy has no tags, since it is free text
        url = TAXONOMY_TAGS_URL.format(pk=self.taxonomy.id)
        response = self.client.get(url)
        tags = response.data["results"]
        assert len(tags) == 0

    def test_import_no_perm(self) -> None:
        """
        Tests importing a taxonomy using a user without permission.
        """
        url = TAXONOMY_TAGS_IMPORT_URL.format(pk=self.taxonomy.id)
        new_tags = [
            {"id": "tag_1", "value": "Tag 1"},
            {"id": "tag_2", "value": "Tag 2"},
            {"id": "tag_3", "value": "Tag 3"},
            {"id": "tag_4", "value": "Tag 4"},
        ]
        file = self._get_file(new_tags, "json")

        self.client.force_authenticate(user=self.user)
        response = self.client.put(
            url,
            {
                "taxonomy_name": "Imported Taxonomy name",
                "taxonomy_description": "Imported Taxonomy description",
                "taxonomt_export_id": "imported_taxonomy",
                "file": file,
            },
            format="multipart"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        # Check if the taxonomy was not changed
        url = TAXONOMY_TAGS_URL.format(pk=self.taxonomy.id)
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(url)
        tags = response.data["results"]
        assert len(tags) == len(self.old_tags)
        for i, tag in enumerate(tags):
            assert tag["value"] == self.old_tags[i].value


@skip_unless_cms
@ddt.ddt
class TestTaxonomyTagsViewSet(TestTaxonomyObjectsMixin, APITestCase):
    """
    Test cases for TaxonomyTagsViewSet retrive action.
    """
    @ddt.data(
        ('staff', 11),
        ("content_creatorA", 13),
        ("library_staffA", 13),
        ("library_userA", 13),
        ("instructorA", 13),
        ("course_instructorA", 13),
        ("course_staffA", 13),
    )
    @ddt.unpack
    def test_taxonomy_tags_query_count(self, user_attr: str, expected_queries: int):
        """
        Test how many queries are used when retrieving small taxonomies+tags and permissions
        """
        url = f"{TAXONOMY_TAGS_URL}?search_term=an&parent_tag=ALPHABET".format(pk=self.t1.id)

        user = getattr(self, user_attr)
        self.client.force_authenticate(user=user)
        with self.assertNumQueries(expected_queries):
            response = self.client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["can_add_tag"] == user.is_staff
        assert len(response.data["results"]) == 2
        for taxonomy in response.data["results"]:
            assert taxonomy["can_change_tag"] == user.is_staff
            assert taxonomy["can_delete_tag"] == user.is_staff
