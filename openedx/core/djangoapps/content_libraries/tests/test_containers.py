"""
Tests for openedx_content-based Content Libraries
"""
from datetime import datetime, timezone
import textwrap

import ddt
from freezegun import freeze_time

from opaque_keys.edx.locator import LibraryLocatorV2

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content_libraries import api
from openedx.core.djangoapps.content_libraries.tests.base import ContentLibrariesRestApiTest
from openedx.core.djangoapps.content_tagging import api as tagging_api
from openedx.core.djangolib.testing.utils import skip_unless_cms


@skip_unless_cms
@ddt.ddt
class ContainersTestCase(ContentLibrariesRestApiTest):
    """
    Tests for containers (Sections, Subsections, Units) in Content Libraries.

    These tests use the REST API, which in turn relies on the Python API.
    Some tests may use the python API directly if necessary to provide
    coverage of any code paths not accessible via the REST API.

    In general, these tests should
    (1) Use public APIs only - don't directly create data using other methods,
        which results in a less realistic test and ties the test suite too
        closely to specific implementation details.
        (Exception: users can be provisioned using a user factory)
    (2) Assert that fields are present in responses, but don't assert that the
        entire response has some specific shape. That way, things like adding
        new fields to an API response, which are backwards compatible, won't
        break any tests, but backwards-incompatible API changes will.
    """

    def setUp(self) -> None:
        super().setUp()
        self.create_date = datetime(2024, 9, 8, 7, 6, 5, tzinfo=timezone.utc)
        self.modified_date = datetime(2024, 10, 9, 8, 7, 6, tzinfo=timezone.utc)
        self.lib = self._create_library(
            slug="containers",
            title="Container Test Library",
            description="Units and more",
        )
        self.lib_key = LibraryLocatorV2.from_string(self.lib["id"])

        self.taxonomy = tagging_api.create_taxonomy('New Taxonomy')
        tagging_api.set_taxonomy_orgs(self.taxonomy, all_orgs=True)
        tagging_api.add_tag_to_taxonomy(self.taxonomy, "one")
        tagging_api.add_tag_to_taxonomy(self.taxonomy, "two")
        tagging_api.add_tag_to_taxonomy(self.taxonomy, "three")
        tagging_api.add_tag_to_taxonomy(self.taxonomy, "four")

        # Create containers
        with freeze_time(self.create_date):
            # Unit
            self.unit = self._create_container(self.lib["id"], "unit", display_name="Alpha Bravo", slug=None)
            self.unit_with_components = self._create_container(
                self.lib["id"],
                "unit",
                display_name="Alpha Charly",
                slug=None,
            )
            self.unit_2 = self._create_container(self.lib["id"], "unit", display_name="Test Unit 2", slug=None)
            self.unit_3 = self._create_container(self.lib["id"], "unit", display_name="Test Unit 3", slug=None)

            # Subsection
            self.subsection = self._create_container(
                self.lib["id"],
                "subsection",
                display_name="Subsection Alpha",
                slug=None,
            )
            self.subsection_with_units = self._create_container(
                self.lib["id"],
                "subsection",
                display_name="Subsection with units",
                slug=None,
            )
            self.subsection_2 = self._create_container(
                self.lib["id"],
                "subsection",
                display_name="Test Subsection 2",
                slug=None,
            )
            self.subsection_3 = self._create_container(
                self.lib["id"],
                "subsection",
                display_name="Test Subsection 3",
                slug=None,
            )

            # Section
            self.section = self._create_container(self.lib["id"], "section", display_name="Section Alpha", slug=None)
            self.section_with_subsections = self._create_container(
                self.lib["id"],
                "section",
                display_name="Section with subsections",
                slug=None,
            )

        # Create blocks
        self.problem_block = self._add_block_to_library(self.lib["id"], "problem", "Problem1", can_stand_alone=False)
        self.html_block = self._add_block_to_library(self.lib["id"], "html", "Html1", can_stand_alone=False)
        self.problem_block_2 = self._add_block_to_library(self.lib["id"], "problem", "Problem2", can_stand_alone=False)
        self.html_block_2 = self._add_block_to_library(self.lib["id"], "html", "Html2")

        with freeze_time(self.modified_date):
            # Add components to `unit_with_components`
            self._add_container_children(
                self.unit_with_components["id"],
                children_ids=[
                    self.problem_block["id"],
                    self.html_block["id"],
                    self.problem_block_2["id"],
                    self.html_block_2["id"],
                ],
            )
            # Refetch to update modified dates
            self.unit_with_components = self._get_container(self.unit_with_components["id"])

            # Add units to `subsection_with_units`
            self._add_container_children(
                self.subsection_with_units["id"],
                children_ids=[
                    self.unit["id"],
                    self.unit_with_components["id"],
                    self.unit_2["id"],
                    self.unit_3["id"],
                ],
            )
            # Refetch to update modified dates
            self.subsection_with_units = self._get_container(self.subsection_with_units["id"])

            # Add subsections to `section_with_subsections`
            self._add_container_children(
                self.section_with_subsections["id"],
                children_ids=[
                    self.subsection["id"],
                    self.subsection_with_units["id"],
                    self.subsection_2["id"],
                    self.subsection_3["id"],
                ],
            )
            # Refetch to update modified dates
            self.section_with_subsections = self._get_container(self.section_with_subsections["id"])

    @ddt.data(
        ("unit", "u1", "Test Unit"),
        ("subsection", "subs1", "Test Subsection"),
        ("section", "s1", "Test Section"),
    )
    @ddt.unpack
    def test_container_crud(self, container_type, slug, display_name) -> None:
        """
        Test Create, Read, Update, and Delete of a Containers
        """
        # Create container:
        create_date = datetime(2024, 9, 8, 7, 6, 5, tzinfo=timezone.utc)
        with freeze_time(create_date):
            container_data = self._create_container(
                self.lib["id"],
                container_type,
                slug=slug,
                display_name=display_name
            )
        container_id = f"lct:CL-TEST:containers:{container_type}:{slug}"
        expected_data = {
            "id": container_id,
            "container_type": container_type,
            "display_name": display_name,
            "last_published": None,
            "published_by": None,
            "last_draft_created": "2024-09-08T07:06:05Z",
            "last_draft_created_by": 'Bob',
            'has_unpublished_changes': True,
            'created': '2024-09-08T07:06:05Z',
            'modified': '2024-09-08T07:06:05Z',
            'collections': [],
        }

        self.assertDictContainsEntries(container_data, expected_data)

        # Fetch the container:
        container_as_read = self._get_container(container_data["id"])
        # make sure it contains the same data when we read it back:
        self.assertDictContainsEntries(container_as_read, expected_data)

        # Update the container:
        modified_date = datetime(2024, 10, 9, 8, 7, 6, tzinfo=timezone.utc)
        with freeze_time(modified_date):
            container_data = self._update_container(container_id, display_name=f"New Display Name for {container_type}")
        expected_data["last_draft_created"] = expected_data["modified"] = "2024-10-09T08:07:06Z"
        expected_data["display_name"] = f"New Display Name for {container_type}"
        self.assertDictContainsEntries(container_data, expected_data)

        # Re-fetch the container
        container_as_re_read = self._get_container(container_data["id"])
        # make sure it contains the same data when we read it back:
        self.assertDictContainsEntries(container_as_re_read, expected_data)

        # Delete the container
        self._delete_container(container_data["id"])
        self._get_container(container_data["id"], expect_response=404)

    @ddt.data(
        ("unit", "u2", "Test Unit"),
        ("subsection", "subs2", "Test Subsection"),
        ("section", "s2", "Test Section"),
    )
    @ddt.unpack
    def test_container_permissions(self, container_type, slug, display_name) -> None:
        """
        Test that a regular user with read-only permissions on the library cannot create, update, or delete containers.
        """
        container_data = self._create_container(self.lib["id"], container_type, slug=slug, display_name=display_name)

        random_user = UserFactory.create(username="Random", email="random@example.com")
        with self.as_user(random_user):
            self._create_container(
                self.lib["id"],
                container_type,
                slug="new_slug",
                display_name=display_name,
                expect_response=403,
            )
            self._get_container(container_data["id"], expect_response=403)
            self._update_container(container_data["id"], display_name="New Display Name", expect_response=403)
            self._delete_container(container_data["id"], expect_response=403)

        # Granting read-only permissions on the library should only allow retrieval, nothing else.
        self._add_user_by_email(self.lib["id"], random_user.email, access_level="read")
        with self.as_user(random_user):
            self._create_container(
                self.lib["id"],
                container_type,
                slug=slug,
                display_name=display_name,
                expect_response=403,
            )
            self._get_container(container_data["id"], expect_response=200)
            self._update_container(container_data["id"], display_name="New Display Name", expect_response=403)
            self._delete_container(container_data["id"], expect_response=403)

    @ddt.data(
        ("unit", "Alpha Bravo", "lct:CL-TEST:containers:unit:alpha-bravo-"),
        ("subsection", "Subsection Alpha", "lct:CL-TEST:containers:subsection:subsection-alpha-"),
        ("section", "Section Alpha", "lct:CL-TEST:containers:section:section-alpha-"),
    )
    @ddt.unpack
    def test_containers_gets_auto_slugs(self, container_type, display_name, expected_id) -> None:
        """
        Test that we can create containers by specifying only a title, and they get
        unique slugs assigned automatically.
        """
        container_1 = getattr(self, container_type)
        container_2 = self._create_container(self.lib["id"], container_type, display_name=display_name, slug=None)

        assert container_1["id"].startswith(expected_id)
        assert container_2["id"].startswith(expected_id)
        assert container_1["id"] != container_2["id"]

    def test_unit_add_children(self) -> None:
        """
        Test that we can add and get unit children components
        """
        # Add some components
        self._add_container_children(
            self.unit["id"],
            children_ids=[self.problem_block["id"], self.html_block["id"]]
        )
        data = self._get_container_children(self.unit["id"])
        assert len(data) == 2
        assert data[0]['id'] == self.problem_block['id']
        assert not data[0]['can_stand_alone']
        assert data[1]['id'] == self.html_block['id']
        assert not data[1]['can_stand_alone']
        problem_block_2 = self._add_block_to_library(self.lib["id"], "problem", "Problem_2", can_stand_alone=False)
        html_block_2 = self._add_block_to_library(self.lib["id"], "html", "Html_2")
        # Add two more components
        self._add_container_children(
            self.unit["id"],
            children_ids=[problem_block_2["id"], html_block_2["id"]]
        )
        data = self._get_container_children(self.unit["id"])
        # Verify total number of components to be 2 + 2 = 4
        assert len(data) == 4
        assert data[2]['id'] == problem_block_2['id']
        assert not data[2]['can_stand_alone']
        assert data[3]['id'] == html_block_2['id']
        assert data[3]['can_stand_alone']

    def test_subsection_add_children(self) -> None:
        # Create units
        child_unit_1 = self._create_container(self.lib["id"], "unit", display_name="Child unit 1", slug=None)
        child_unit_2 = self._create_container(self.lib["id"], "unit", display_name="Child unit 2", slug=None)

        # Add the units to subsection
        self._add_container_children(
            self.subsection["id"],
            children_ids=[child_unit_1["id"], child_unit_2["id"]]
        )
        data = self._get_container_children(self.subsection["id"])
        assert len(data) == 2
        assert data[0]['id'] == child_unit_1['id']
        assert data[1]['id'] == child_unit_2['id']

        child_unit_3 = self._create_container(self.lib["id"], "unit", display_name="Child unit 3", slug=None)
        child_unit_4 = self._create_container(self.lib["id"], "unit", display_name="Child unit 4", slug=None)

        # Add two more units to subsection
        self._add_container_children(
            self.subsection["id"],
            children_ids=[child_unit_3["id"], child_unit_4["id"]]
        )
        data = self._get_container_children(self.subsection["id"])
        # Verify total number of units to be 2 + 2 = 4
        assert len(data) == 4
        assert data[2]['id'] == child_unit_3['id']
        assert data[3]['id'] == child_unit_4['id']

    def test_section_add_children(self) -> None:
        # Create Subsections
        child_subsection_1 = self._create_container(
            self.lib["id"],
            "subsection",
            display_name="Child Subsection 1",
            slug=None,
        )
        child_subsection_2 = self._create_container(
            self.lib["id"],
            "subsection",
            display_name="Child Subsection 2",
            slug=None,
        )

        # Add the subsections to section
        self._add_container_children(
            self.section["id"],
            children_ids=[child_subsection_1["id"], child_subsection_2["id"]]
        )
        data = self._get_container_children(self.section["id"])
        assert len(data) == 2
        assert data[0]['id'] == child_subsection_1['id']
        assert data[1]['id'] == child_subsection_2['id']

        child_subsection_3 = self._create_container(
            self.lib["id"],
            "subsection",
            display_name="Child Subsection 3",
            slug=None,
        )
        child_subsection_4 = self._create_container(
            self.lib["id"],
            "subsection",
            display_name="Child Subsection 4",
            slug=None,
        )

        # Add two more subsections to section
        self._add_container_children(
            self.section["id"],
            children_ids=[child_subsection_3["id"], child_subsection_4["id"]]
        )
        data = self._get_container_children(self.section["id"])
        # Verify total number of subsections to be 2 + 2 = 4
        assert len(data) == 4
        assert data[2]['id'] == child_subsection_3['id']
        assert data[3]['id'] == child_subsection_4['id']

    @ddt.data(
        ("unit_with_components", ["problem_block_2", "problem_block"], ["html_block", "html_block_2"]),
        ("subsection_with_units", ["unit", "unit_with_components"], ["unit_2", "unit_3"]),
        ("section_with_subsections", ["subsection", "subsection_with_units"], ["subsection_2", "subsection_3"]),
    )
    @ddt.unpack
    def test_container_remove_children(self, container_name, items_to_remove, expected_items) -> None:
        """
        Test that we can remove container children
        """
        container = getattr(self, container_name)
        item_to_remove_1 = getattr(self, items_to_remove[0])
        item_to_remove_2 = getattr(self, items_to_remove[1])
        expected_item_1 = getattr(self, expected_items[0])
        expected_item_2 = getattr(self, expected_items[1])
        data = self._get_container_children(container["id"])
        assert len(data) == 4
        # Remove items.
        self._remove_container_children(
            container["id"],
            children_ids=[item_to_remove_1["id"], item_to_remove_2["id"]]
        )
        data = self._get_container_children(container["id"])
        assert len(data) == 2
        assert data[0]['id'] == expected_item_1['id']
        assert data[1]['id'] == expected_item_2['id']

    def test_unit_replace_children(self) -> None:
        """
        Test that we can completely replace/reorder unit children components.
        """
        data = self._get_container_children(self.unit_with_components["id"])
        assert len(data) == 4
        assert data[0]['id'] == self.problem_block['id']
        assert data[1]['id'] == self.html_block['id']
        assert data[2]['id'] == self.problem_block_2['id']
        assert data[3]['id'] == self.html_block_2['id']

        # Reorder the components
        self._patch_container_children(
            self.unit_with_components["id"],
            children_ids=[
                self.problem_block["id"],
                self.problem_block_2["id"],
                self.html_block["id"],
                self.html_block_2["id"],
            ]
        )
        data = self._get_container_children(self.unit_with_components["id"])
        assert len(data) == 4
        assert data[0]['id'] == self.problem_block['id']
        assert data[1]['id'] == self.problem_block_2['id']
        assert data[2]['id'] == self.html_block['id']
        assert data[3]['id'] == self.html_block_2['id']

        # Replace with new components
        new_problem_block = self._add_block_to_library(self.lib["id"], "problem", "New_Problem", can_stand_alone=False)
        new_html_block = self._add_block_to_library(self.lib["id"], "html", "New_Html", can_stand_alone=False)
        self._patch_container_children(
            self.unit_with_components["id"],
            children_ids=[new_problem_block["id"], new_html_block["id"]],
        )
        data = self._get_container_children(self.unit_with_components["id"])
        assert len(data) == 2
        assert data[0]['id'] == new_problem_block['id']
        assert data[1]['id'] == new_html_block['id']

    def test_subsection_replace_children(self) -> None:
        """
        Test that we can completely replace/reorder subsection children.
        """
        data = self._get_container_children(self.subsection_with_units["id"])
        assert len(data) == 4
        assert data[0]['id'] == self.unit['id']
        assert data[1]['id'] == self.unit_with_components['id']
        assert data[2]['id'] == self.unit_2['id']
        assert data[3]['id'] == self.unit_3['id']

        # Reorder the units
        self._patch_container_children(
            self.subsection_with_units["id"],
            children_ids=[
                self.unit_2["id"],
                self.unit["id"],
                self.unit_3["id"],
                self.unit_with_components["id"],
            ]
        )
        data = self._get_container_children(self.subsection_with_units["id"])
        assert len(data) == 4
        assert data[0]['id'] == self.unit_2['id']
        assert data[1]['id'] == self.unit['id']
        assert data[2]['id'] == self.unit_3['id']
        assert data[3]['id'] == self.unit_with_components['id']

        # Replace with new units
        new_unit_1 = self._create_container(self.lib["id"], "unit", display_name="New Unit 1", slug=None)
        new_unit_2 = self._create_container(self.lib["id"], "unit", display_name="New Unit 2", slug=None)
        self._patch_container_children(
            self.subsection_with_units["id"],
            children_ids=[new_unit_1["id"], new_unit_2["id"]],
        )
        data = self._get_container_children(self.subsection_with_units["id"])
        assert len(data) == 2
        assert data[0]['id'] == new_unit_1['id']
        assert data[1]['id'] == new_unit_2['id']

    def test_section_replace_children(self) -> None:
        """
        Test that we can completely replace/reorder section children.
        """
        data = self._get_container_children(self.section_with_subsections["id"])
        assert len(data) == 4
        assert data[0]['id'] == self.subsection['id']
        assert data[1]['id'] == self.subsection_with_units['id']
        assert data[2]['id'] == self.subsection_2['id']
        assert data[3]['id'] == self.subsection_3['id']

        # Reorder the subsections
        self._patch_container_children(
            self.section_with_subsections["id"],
            children_ids=[
                self.subsection_2["id"],
                self.subsection["id"],
                self.subsection_3["id"],
                self.subsection_with_units["id"],
            ]
        )
        data = self._get_container_children(self.section_with_subsections["id"])
        assert len(data) == 4
        assert data[0]['id'] == self.subsection_2['id']
        assert data[1]['id'] == self.subsection['id']
        assert data[2]['id'] == self.subsection_3['id']
        assert data[3]['id'] == self.subsection_with_units['id']

        # Replace with new subsections
        new_subsection_1 = self._create_container(
            self.lib["id"],
            "subsection",
            display_name="New Subsection 1",
            slug=None,
        )
        new_subsection_2 = self._create_container(
            self.lib["id"],
            "subsection",
            display_name="New Subsection 2",
            slug=None,
        )
        self._patch_container_children(
            self.section_with_subsections["id"],
            children_ids=[new_subsection_1["id"], new_subsection_2["id"]],
        )
        data = self._get_container_children(self.section_with_subsections["id"])
        assert len(data) == 2
        assert data[0]['id'] == new_subsection_1['id']
        assert data[1]['id'] == new_subsection_2['id']

    @ddt.data(
        "unit",
        "subsection",
        "section",
    )
    def test_restore_containers(self, container_type) -> None:
        """
        Test restore a deleted container.
        """
        container = getattr(self, container_type)

        # Delete container
        self._delete_container(container["id"])

        # Restore container
        self._restore_container(container["id"])
        new_container_data = self._get_container(container["id"])
        expected_data = {
            "id": container["id"],
            "container_type": container_type,
            "display_name": container["display_name"],
            "last_published": None,
            "published_by": None,
            "last_draft_created": "2024-09-08T07:06:05Z",
            "last_draft_created_by": 'Bob',
            'has_unpublished_changes': True,
            'created': '2024-09-08T07:06:05Z',
            'modified': '2024-09-08T07:06:05Z',
            'collections': [],
        }

        self.assertDictContainsEntries(new_container_data, expected_data)

    @ddt.data(
        "unit",
        "subsection",
        "section",
    )
    def test_tag_containers(self, container_type) -> None:
        container = getattr(self, container_type)

        assert container["tags_count"] == 0
        tagging_api.tag_object(
            container["id"],
            self.taxonomy,
            ['one', 'three', 'four'],
        )

        new_container_data = self._get_container(container["id"])
        assert new_container_data["tags_count"] == 3

    def test_unit_collections(self) -> None:
        # Create a collection
        col1 = api.create_library_collection(
            self.lib_key,
            "COL1",
            title="Collection 1",
            created_by=self.user.id,
            description="Description for Collection 1",
        )

        result = self._patch_container_collections(
            self.unit["id"],
            collection_keys=[col1.key],
        )

        assert result['count'] == 1

        # Fetch the unit
        unit_as_read = self._get_container(self.unit["id"])

        # Verify the collections
        assert unit_as_read['collections'] == [{"title": col1.title, "key": col1.key}]

    def test_section_hierarchy(self):
        with self.assertNumQueries(133):
            hierarchy = self._get_container_hierarchy(self.section_with_subsections["id"])
        assert hierarchy["object_key"] == self.section_with_subsections["id"]
        assert hierarchy["components"] == [
            self._hierarchy_member(self.problem_block),
            self._hierarchy_member(self.html_block),
            self._hierarchy_member(self.problem_block_2),
            self._hierarchy_member(self.html_block_2),
        ]
        assert hierarchy["units"] == [
            self._hierarchy_member(self.unit),
            self._hierarchy_member(self.unit_with_components),
            self._hierarchy_member(self.unit_2),
            self._hierarchy_member(self.unit_3),
        ]
        assert hierarchy["subsections"] == [
            self._hierarchy_member(self.subsection),
            self._hierarchy_member(self.subsection_with_units),
            self._hierarchy_member(self.subsection_2),
            self._hierarchy_member(self.subsection_3),
        ]
        assert hierarchy["sections"] == [
            self._hierarchy_member(self.section_with_subsections),
        ]

    def test_subsection_hierarchy(self):
        with self.assertNumQueries(95):
            hierarchy = self._get_container_hierarchy(self.subsection_with_units["id"])
        assert hierarchy["object_key"] == self.subsection_with_units["id"]
        assert hierarchy["components"] == [
            self._hierarchy_member(self.problem_block),
            self._hierarchy_member(self.html_block),
            self._hierarchy_member(self.problem_block_2),
            self._hierarchy_member(self.html_block_2),
        ]
        assert hierarchy["units"] == [
            self._hierarchy_member(self.unit),
            self._hierarchy_member(self.unit_with_components),
            self._hierarchy_member(self.unit_2),
            self._hierarchy_member(self.unit_3),
        ]
        assert hierarchy["subsections"] == [
            self._hierarchy_member(self.subsection_with_units),
        ]
        assert hierarchy["sections"] == [
            self._hierarchy_member(self.section_with_subsections),
        ]

    def test_units_hierarchy(self):
        with self.assertNumQueries(60):
            hierarchy = self._get_container_hierarchy(self.unit_with_components["id"])
        assert hierarchy["object_key"] == self.unit_with_components["id"]
        assert hierarchy["components"] == [
            self._hierarchy_member(self.problem_block),
            self._hierarchy_member(self.html_block),
            self._hierarchy_member(self.problem_block_2),
            self._hierarchy_member(self.html_block_2),
        ]
        assert hierarchy["units"] == [
            self._hierarchy_member(self.unit_with_components),
        ]
        assert hierarchy["subsections"] == [
            self._hierarchy_member(self.subsection_with_units),
        ]
        assert hierarchy["sections"] == [
            self._hierarchy_member(self.section_with_subsections),
        ]

    def test_container_hierarchy_not_found(self):
        self._get_container_hierarchy(
            "lct:CL-TEST:containers:section:does-not-exist",
            expect_response=404,
        )

    def test_block_hierarchy(self):
        with self.assertNumQueries(27):
            hierarchy = self._get_block_hierarchy(self.problem_block["id"])
        assert hierarchy["object_key"] == self.problem_block["id"]
        assert hierarchy["components"] == [
            self._hierarchy_member(self.problem_block),
        ]
        assert hierarchy["units"] == [
            self._hierarchy_member(self.unit_with_components),
        ]
        assert hierarchy["subsections"] == [
            self._hierarchy_member(self.subsection_with_units),
        ]
        assert hierarchy["sections"] == [
            self._hierarchy_member(self.section_with_subsections),
        ]

    def test_block_hierarchy_not_found(self):
        self._get_block_hierarchy(
            "lb:CL-TEST:problem:does-not-exist",
            expect_response=404,
        )

    def _verify_publish_state(
        self,
        container_id,
        expected_childen_len,
        expected_has_unpublished_changes,
        expected_published_by,
        expected_children_ids,
    ) -> None:
        """
        Verify the publish state of a container
        """
        container = self._get_container(container_id)
        assert container["has_unpublished_changes"] == expected_has_unpublished_changes
        container_children = self._get_container_children(container_id)
        assert len(container_children) == expected_childen_len

        for index in range(expected_childen_len):
            assert container_children[index]["id"] == expected_children_ids[index]
            assert container_children[index]["has_unpublished_changes"] == expected_has_unpublished_changes
            assert container_children[index]["published_by"] == expected_published_by

    def test_publish_unit(self) -> None:
        """
        Test that we can publish the changes to a specific unit
        """
        html_block_3 = self._add_block_to_library(self.lib["id"], "html", "Html3")
        self._add_container_children(
            self.unit["id"],
            children_ids=[
                self.html_block["id"],
                html_block_3["id"],
            ]
        )

        # At first everything is unpublished:
        self._verify_publish_state(
            container_id=self.unit_with_components["id"],
            expected_childen_len=4,
            expected_has_unpublished_changes=True,
            expected_published_by=None,
            expected_children_ids=[
                self.problem_block["id"],
                self.html_block["id"],
                self.problem_block_2["id"],
                self.html_block_2["id"],
            ],
        )
        self._verify_publish_state(
            container_id=self.unit["id"],
            expected_childen_len=2,
            expected_has_unpublished_changes=True,
            expected_published_by=None,
            expected_children_ids=[
                self.html_block["id"],
                html_block_3["id"],
            ],
        )

        # Now publish only unit 1
        self._publish_container(self.unit_with_components["id"])

        # Now it is published:
        self._verify_publish_state(
            container_id=self.unit_with_components["id"],
            expected_childen_len=4,
            expected_has_unpublished_changes=False,
            expected_published_by=self.user.username,
            expected_children_ids=[
                self.problem_block["id"],
                self.html_block["id"],
                self.problem_block_2["id"],
                self.html_block_2["id"],
            ],
        )

        # and unit 2 is still unpublished, except for the shared HTML block that is also in unit 1:
        c2_after = self._get_container(self.unit["id"])
        assert c2_after["has_unpublished_changes"]
        c2_components_after = self._get_container_children(self.unit["id"])
        assert len(c2_components_after) == 2
        assert c2_components_after[0]["id"] == self.html_block["id"]
        assert c2_components_after[0]["has_unpublished_changes"] is False  # published since it's also in unit 1
        assert c2_components_after[0]["published_by"] == self.user.username
        assert c2_components_after[1]["id"] == html_block_3["id"]
        assert c2_components_after[1]["has_unpublished_changes"]  # unaffected
        assert c2_components_after[1]["published_by"] is None

    def test_copy_container(self) -> None:
        """
        Test that we can copy a container and its children.
        """
        tagging_api.tag_object(
            self.section_with_subsections["id"],
            self.taxonomy,
            ['one', 'three', 'four'],
        )
        tagging_api.tag_object(
            self.subsection_with_units["id"],
            self.taxonomy,
            ['one', 'two'],
        )
        tagging_api.tag_object(
            self.unit_with_components["id"],
            self.taxonomy,
            ['one'],
        )
        self._copy_container(self.section_with_subsections["id"])

        from openedx.core.djangoapps.content_staging import api as staging_api

        clipboard_data = staging_api.get_user_clipboard(self.user.id)

        assert clipboard_data is not None
        assert clipboard_data.content.display_name == "Section with subsections"
        assert clipboard_data.content.status == "ready"
        assert clipboard_data.content.purpose == "clipboard"
        assert clipboard_data.content.block_type == "chapter"
        assert str(clipboard_data.source_usage_key) == self.section_with_subsections["id"]

        # Check the tags on the clipboard content:
        assert clipboard_data.content.tags == {
            'lb:CL-TEST:containers:html:Html1': {},
            'lb:CL-TEST:containers:html:Html2': {},
            'lb:CL-TEST:containers:problem:Problem1': {},
            'lb:CL-TEST:containers:problem:Problem2': {},
            self.section_with_subsections["id"]: {
                str(self.taxonomy.id): ['one', 'three', 'four'],
            },
            self.subsection_with_units["id"]: {
                str(self.taxonomy.id): ['one', 'two'],
            },
            self.unit_with_components["id"]: {
                str(self.taxonomy.id): ['one'],
            },
        }

        # Test the actual OLX in the clipboard:
        olx_data = staging_api.get_staged_content_olx(clipboard_data.content.id)
        assert olx_data is not None
        assert olx_data == textwrap.dedent(f"""\
          <chapter copied_from_block="{self.section_with_subsections["id"]}" copied_from_version="2" display_name="Section with subsections">
            <sequential copied_from_block="{self.subsection["id"]}" copied_from_version="1" display_name="Subsection Alpha"/>
            <sequential copied_from_block="{self.subsection_with_units["id"]}" copied_from_version="2" display_name="Subsection with units">
              <vertical copied_from_block="{self.unit["id"]}" copied_from_version="1" display_name="Alpha Bravo"/>
              <vertical copied_from_block="{self.unit_with_components["id"]}" copied_from_version="2" display_name="Alpha Charly">
                <problem url_name="Problem1" copied_from_block="{self.problem_block["id"]}" copied_from_version="1"/>
                <html url_name="Html1" display_name="Text" copied_from_block="{self.html_block["id"]}" copied_from_version="1"><![CDATA[]]></html>
                <problem url_name="Problem2" copied_from_block="{self.problem_block_2["id"]}" copied_from_version="1"/>
                <html url_name="Html2" display_name="Text" copied_from_block="{self.html_block_2["id"]}" copied_from_version="1"><![CDATA[]]></html>
              </vertical>
              <vertical copied_from_block="{self.unit_2["id"]}" copied_from_version="1" display_name="Test Unit 2"/>
              <vertical copied_from_block="{self.unit_3["id"]}" copied_from_version="1" display_name="Test Unit 3"/>
            </sequential>
            <sequential copied_from_block="{self.subsection_2["id"]}" copied_from_version="1" display_name="Test Subsection 2"/>
            <sequential copied_from_block="{self.subsection_3["id"]}" copied_from_version="1" display_name="Test Subsection 3"/>
          </chapter>
        """)

    def test_publish_subsection(self) -> None:
        """
        Test that we can publish the changes to a specific subsection
        """
        unit_4 = self._create_container(self.lib["id"], "unit", display_name="Test Unit 4", slug=None)

        # Add units on subsection
        self._add_container_children(
            self.subsection["id"],
            children_ids=[
                self.unit["id"],
                unit_4["id"],
            ]
        )

        # TODO -- remove this when containers publish their children:
        # https://github.com/openedx/openedx-core/pull/307
        # Removing the unit with components because the components (children of children) are not published.
        # If the unit is kept, the subsection continues to have changes even after it is published.
        self._remove_container_children(
            self.subsection_with_units["id"],
            children_ids=[
                self.unit_with_components["id"],
            ]
        )
        # /TODO

        # At first everything is unpublished:
        self._verify_publish_state(
            container_id=self.subsection_with_units["id"],
            expected_childen_len=3,
            expected_has_unpublished_changes=True,
            expected_published_by=None,
            expected_children_ids=[
                self.unit["id"],
                self.unit_2["id"],
                self.unit_3["id"],
            ],
        )
        self._verify_publish_state(
            container_id=self.subsection["id"],
            expected_childen_len=2,
            expected_has_unpublished_changes=True,
            expected_published_by=None,
            expected_children_ids=[
                self.unit["id"],
                unit_4["id"],
            ],
        )

        # Now publish only subsection 1
        self._publish_container(self.subsection_with_units["id"])

        # Now it is published:
        self._verify_publish_state(
            container_id=self.subsection_with_units["id"],
            expected_childen_len=3,
            expected_has_unpublished_changes=False,
            expected_published_by=self.user.username,
            expected_children_ids=[
                self.unit["id"],
                self.unit_2["id"],
                self.unit_3["id"],
            ],
        )

        # and subsection 2 is still unpublished, except for the shared unit that is also in subsection 1:
        c2_after = self._get_container(self.subsection["id"])
        assert c2_after["has_unpublished_changes"]
        c2_units_after = self._get_container_children(self.subsection["id"])
        assert len(c2_units_after) == 2
        assert c2_units_after[0]["id"] == self.unit["id"]
        assert c2_units_after[0]["has_unpublished_changes"] is False  # published since it's also in subsection 1
        assert c2_units_after[0]["published_by"] == self.user.username
        assert c2_units_after[1]["id"] == unit_4["id"]
        assert c2_units_after[1]["has_unpublished_changes"]  # unaffected
        assert c2_units_after[1]["published_by"] is None

    def test_publish_section(self) -> None:
        """
        Test that we can publish the changes to a specific section
        """
        subsection_4 = self._create_container(self.lib["id"], "subsection", display_name="Test Subsection 4", slug=None)
        self._add_container_children(
            self.section["id"],
            children_ids=[
                self.subsection["id"],
                subsection_4["id"],
            ]
        )

        # TODO -- remove this when containers publish their children:
        # https://github.com/openedx/openedx-core/pull/307
        # Removing the subsection with units because the units (children of children) are not published.
        # If the subsection is kept, the section continues to have changes even after it is published.
        self._remove_container_children(
            self.section_with_subsections["id"],
            children_ids=[
                self.subsection_with_units["id"],
            ]
        )
        # /TODO

        # At first everything is unpublished:
        self._verify_publish_state(
            container_id=self.section_with_subsections["id"],
            expected_childen_len=3,
            expected_has_unpublished_changes=True,
            expected_published_by=None,
            expected_children_ids=[
                self.subsection["id"],
                self.subsection_2["id"],
                self.subsection_3["id"],
            ],
        )
        self._verify_publish_state(
            container_id=self.section["id"],
            expected_childen_len=2,
            expected_has_unpublished_changes=True,
            expected_published_by=None,
            expected_children_ids=[
                self.subsection["id"],
                subsection_4["id"],
            ],
        )

        # Now publish only section 1
        self._publish_container(self.section_with_subsections["id"])

        # Now it is published:
        self._verify_publish_state(
            container_id=self.section_with_subsections["id"],
            expected_childen_len=3,
            expected_has_unpublished_changes=False,
            expected_published_by=self.user.username,
            expected_children_ids=[
                self.subsection["id"],
                self.subsection_2["id"],
                self.subsection_3["id"],
            ],
        )

        # and section 2 is still unpublished, except for the shared subsection that is also in section 1:
        c2_after = self._get_container(self.section["id"])
        assert c2_after["has_unpublished_changes"]
        c2_units_after = self._get_container_children(self.section["id"])
        assert len(c2_units_after) == 2
        assert c2_units_after[0]["id"] == self.subsection["id"]
        assert c2_units_after[0]["has_unpublished_changes"] is False  # published since it's also in section 1
        assert c2_units_after[0]["published_by"] == self.user.username
        assert c2_units_after[1]["id"] == subsection_4["id"]
        assert c2_units_after[1]["has_unpublished_changes"]  # unaffected
        assert c2_units_after[1]["published_by"] is None
