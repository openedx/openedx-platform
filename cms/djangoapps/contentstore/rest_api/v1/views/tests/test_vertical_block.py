"""
Unit tests for the vertical block.
"""

from urllib.parse import quote

from django.urls import reverse
from rest_framework import status
from edx_toggles.toggles.testutils import override_waffle_flag
from xblock.validation import ValidationMessage

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from openedx.core.djangoapps.content_tagging.toggles import DISABLE_TAGGING_FEATURE
from openedx.core.djangoapps.content_libraries.tests import ContentLibrariesRestApiTest
from xmodule.partitions.partitions import (
    ENROLLMENT_TRACK_PARTITION_ID,
    Group,
    UserPartition,
)
from xmodule.modulestore.django import (
    modulestore,
)  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import (
    BlockFactory,
)  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore import (
    ModuleStoreEnum,
)  # lint-amnesty, pylint: disable=wrong-import-order


class BaseXBlockContainer(CourseTestCase, ContentLibrariesRestApiTest):
    """
    Base xBlock container handler.

    Contains common function for processing course xblocks.
    """

    view_name = None

    def setUp(self):
        super().setUp()
        self.store = modulestore()
        self.setup_xblock()

    def setup_xblock(self):
        """
        Set up XBlock objects for testing purposes.

        This method creates XBlock objects representing a course structure with chapters,
        sequentials, verticals and others.
        """
        self.lib = self._create_library(
            slug="containers",
            title="Container Test Library",
            description="Units and more",
        )
        self.unit = self._create_container(self.lib["id"], "unit", display_name="Unit", slug=None)
        self.html_block = self._add_block_to_library(self.lib["id"], "html", "Html1", can_stand_alone=False)
        self._set_library_block_olx(
            self.html_block["id"],
            '<html display_name="Html1">updated content upstream 1</html>'
        )
        # Set version of html to 2
        self._publish_library_block(self.html_block["id"])

        self.chapter = self.create_block(
            parent=self.course.location,
            category="chapter",
            display_name="Week 1",
        )

        self.sequential = self.create_block(
            parent=self.chapter.location,
            category="sequential",
            display_name="Lesson 1",
        )

        self.vertical = self.create_block(
            self.sequential.location,
            "vertical",
            "Unit",
            upstream=self.unit["id"],
            upstream_version=1,
        )

        self.html_unit_first = self.create_block(
            parent=self.vertical.location,
            category="html",
            display_name="Html Content 1",
        )

        self.html_unit_second = self.create_block(
            parent=self.vertical.location,
            category="html",
            display_name="Html Content 2",
            upstream=self.html_block["id"],
            upstream_version=1,
        )

    def create_block(self, parent, category, display_name, **kwargs):
        """
        Creates a block without publishing it.
        """
        return BlockFactory.create(
            parent_location=parent,
            category=category,
            display_name=display_name,
            modulestore=self.store,
            publish_item=False,
            user_id=self.user.id,
            **kwargs,
        )

    def get_reverse_url(self, location):
        """
        Creates url to current view api name
        """
        return reverse(
            f"cms.djangoapps.contentstore:v1:{self.view_name}",
            kwargs={"usage_key_string": location},
        )

    def publish_item(self, store, item_location):
        """
        Publish the item at the given location
        """
        with store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            store.publish(item_location, ModuleStoreEnum.UserID.test)

    def set_group_access(self, xblock, value):
        """
        Sets group_access to specified value and calls update_item to persist the change.
        """
        xblock.group_access = value
        self.store.update_item(xblock, self.user.id)


class ContainerHandlerViewTest(BaseXBlockContainer):
    """
    Unit tests for the ContainerHandlerView.
    """

    view_name = "container_handler"

    def test_success_response(self):
        """
        Check that endpoint is valid and success response.
        """
        url = self.get_reverse_url(self.vertical.location)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_ancestor_xblocks_response(self):
        """
        Check if the ancestor_xblocks are returned as expected.
        """
        course_key_str = str(self.course.id)
        chapter_usage_key = str(self.chapter.location)
        sequential_usage_key = str(self.sequential.location)

        # URL encode the usage keys for the URLs
        chapter_encoded = quote(chapter_usage_key, safe='')
        sequential_encoded = quote(sequential_usage_key, safe='')

        expected_ancestor_xblocks = [
            {
                'children': [
                    {
                        'url': f'/course/{course_key_str}?show={chapter_encoded}',
                        'display_name': 'Week 1',
                        'usage_key': chapter_usage_key,
                    }
                ],
                'title': 'Week 1',
                'is_last': False,
            },
            {
                'children': [
                    {
                        'url': f'/course/{course_key_str}?show={sequential_encoded}',
                        'display_name': 'Lesson 1',
                        'usage_key': sequential_usage_key,
                    }
                ],
                'title': 'Lesson 1',
                'is_last': True,
            }
        ]

        url = self.get_reverse_url(self.vertical.location)
        response = self.client.get(url)
        response_ancestor_xblocks = response.json().get("ancestor_xblocks", [])

        def sort_key(block):
            return block.get("title", "")

        self.assertEqual(
            sorted(response_ancestor_xblocks, key=sort_key),
            sorted(expected_ancestor_xblocks, key=sort_key)
        )

    def test_not_valid_usage_key_string(self):
        """
        Check that invalid 'usage_key_string' raises Http404.
        """
        usage_key_string = (
            "i4x://InvalidOrg/InvalidCourse/vertical/static/InvalidContent"
        )
        url = self.get_reverse_url(usage_key_string)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ContainerVerticalViewTest(BaseXBlockContainer):
    """
    Unit tests for the ContainerVerticalViewTest.
    """

    view_name = "container_children"

    def test_success_response(self):
        """
        Check that endpoint returns valid response data.
        """
        url = self.get_reverse_url(self.vertical.location)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data["children"]), 2)
        self.assertFalse(data["is_published"])
        self.assertTrue(data["can_paste_component"])
        self.assertEqual(data["display_name"], "Unit")
        self.assertEqual(data["upstream_ready_to_sync_children_info"], [])

    def test_success_response_with_upstream_info(self):
        """
        Check that endpoint returns valid response data using `get_upstream_info` query param
        """
        url = self.get_reverse_url(self.vertical.location)
        response = self.client.get(f"{url}?get_upstream_info=true")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data["children"]), 2)
        self.assertFalse(data["is_published"])
        self.assertTrue(data["can_paste_component"])
        self.assertEqual(data["display_name"], "Unit")
        self.assertEqual(data["upstream_ready_to_sync_children_info"], [{
            "id": str(self.html_unit_second.usage_key),
            "upstream": self.html_block["id"],
            "block_type": "html",
            "downstream_customized": [],
            "name": "Html Content 2",
        }])

    def test_xblock_is_published(self):
        """
        Check that published xBlock container returns.
        """
        self.publish_item(self.store, self.vertical.location)
        url = self.get_reverse_url(self.vertical.location)
        response = self.client.get(url)
        self.assertTrue(response.data["is_published"])

    def test_children_content(self):
        """
        Check that returns valid response with children of vertical container.
        """
        url = self.get_reverse_url(self.vertical.location)
        response = self.client.get(url)

        expected_user_partition_info = {
            "selectable_partitions": [],
            "selected_partition_index": -1,
            "selected_groups_label": "",
        }

        expected_user_partitions = [
            {
                "id": ENROLLMENT_TRACK_PARTITION_ID,
                "name": "Enrollment Track Groups",
                "scheme": "enrollment_track",
                "groups": [
                    {"id": 1, "name": "Audit", "selected": False, "deleted": False}
                ],
            }
        ]

        expected_response = [
            {
                "name": self.html_unit_first.display_name_with_default,
                "block_id": str(self.html_unit_first.location),
                "block_type": self.html_unit_first.location.block_type,
                "upstream_link": None,
                "user_partition_info": expected_user_partition_info,
                "user_partitions": expected_user_partitions,
                "actions": {
                    "can_copy": True,
                    "can_duplicate": True,
                    "can_move": True,
                    "can_manage_access": True,
                    "can_delete": True,
                    "can_manage_tags": True,
                },
                "validation_messages": [],
                "render_error": "",
            },
            {
                "name": self.html_unit_second.display_name_with_default,
                "block_id": str(self.html_unit_second.location),
                "block_type": self.html_unit_second.location.block_type,
                "actions": {
                    "can_copy": True,
                    "can_duplicate": True,
                    "can_move": True,
                    "can_manage_access": True,
                    "can_delete": True,
                    "can_manage_tags": True,
                },
                "upstream_link": {
                    "upstream_ref": self.html_block["id"],
                    "version_synced": 1,
                    "version_available": 2,
                    "version_declined": None,
                    "error_message": None,
                    "ready_to_sync": True,
                    "top_level_parent_key": None,
                    "downstream_customized": [],
                },
                "user_partition_info": expected_user_partition_info,
                "user_partitions": expected_user_partitions,
                "validation_messages": [],
                "render_error": "",
            },
        ]
        self.maxDiff = None
        # Using json() shows meaningful diff in case of error
        self.assertEqual(response.json()["children"], expected_response)

    def test_not_valid_usage_key_string(self):
        """
        Check that invalid 'usage_key_string' raises Http404.
        """
        usage_key_string = (
            "i4x://InvalidOrg/InvalidCourse/vertical/static/InvalidContent"
        )
        url = self.get_reverse_url(usage_key_string)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @override_waffle_flag(DISABLE_TAGGING_FEATURE, True)
    def test_actions_with_turned_off_taxonomy_flag(self):
        """
        Check that action manage_tags for each child item has the same value as taxonomy flag.
        """
        url = self.get_reverse_url(self.vertical.location)
        response = self.client.get(url)
        for children in response.data["children"]:
            self.assertFalse(children["actions"]["can_manage_tags"])

    def test_validation_errors(self):
        """
        Check that child has an error.
        """
        self.course.user_partitions = [
            UserPartition(
                0,
                "first_partition",
                "Test Partition",
                [Group("0", "alpha"), Group("1", "beta")],
            ),
        ]
        self.store.update_item(self.course, self.user.id)

        user_partition = self.course.user_partitions[0]
        vertical = self.store.get_item(self.vertical.location)
        html_unit_first = self.store.get_item(self.html_unit_first.location)

        group_first = user_partition.groups[0]
        group_second = user_partition.groups[1]

        # Set access settings so html will contradict vertical
        self.set_group_access(vertical, {user_partition.id: [group_second.id]})
        self.set_group_access(html_unit_first, {user_partition.id: [group_first.id]})

        # update vertical/html
        vertical = self.store.get_item(self.vertical.location)
        html_unit_first = self.store.get_item(self.html_unit_first.location)

        url = self.get_reverse_url(self.vertical.location)
        response = self.client.get(url)
        children_response = response.data["children"]

        # Verify that html_unit_first access settings contradict its parent's access settings.
        self.assertEqual(children_response[0]["validation_messages"][0]["type"], ValidationMessage.ERROR)

        # Verify that html_unit_second has no validation messages.
        self.assertFalse(children_response[1]["validation_messages"])
