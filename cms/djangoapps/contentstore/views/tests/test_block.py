"""Tests for block views."""


import json
import re
from datetime import datetime, timedelta
from unittest.mock import Mock, PropertyMock, patch

import ddt
from bs4 import BeautifulSoup
from django.conf import settings
from django.http import Http404
from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings
from django.urls import reverse
from edx_proctoring.exceptions import ProctoredExamNotFoundException
from opaque_keys import InvalidKeyError
from opaque_keys.edx.asides import AsideUsageKeyV2
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator
from openedx_authz.constants.roles import COURSE_ADMIN, COURSE_AUDITOR, COURSE_EDITOR, COURSE_STAFF
from openedx_events.content_authoring.data import DuplicatedXBlockData
from openedx_events.content_authoring.signals import XBLOCK_DUPLICATED
from openedx_events.testing import OpenEdxEventsTestMixin
from pytz import UTC
from web_fragments.fragment import Fragment
from webob import Response
from xblock.core import XBlockAside
from xblock.exceptions import NoSuchHandlerError
from xblock.fields import Scope, ScopeIds, String
from xblock.runtime import DictKeyValueStore, KvsFieldData
from xblock.test.tools import TestRuntime
from xblock.validation import ValidationMessage

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import duplicate_block, reverse_course_url, reverse_usage_url, update_from_source
from cms.djangoapps.contentstore.xblock_storage_handlers import view_handlers as item_module
from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import (
    ALWAYS,
    VisibilityState,
    _get_metadata_with_problem_defaults,
    _get_source_index,
    _xblock_type_and_display_name,
    add_container_page_publishing_info,
    create_xblock_info,
    get_block_info,
)
from common.djangoapps.student.tests.factories import StaffFactory, UserFactory
from common.djangoapps.xblock_django.models import (
    XBlockConfiguration,
    XBlockStudioConfiguration,
    XBlockStudioConfigurationFlag,
)
from common.djangoapps.xblock_django.user_service import DjangoXBlockUserService
from common.test.utils import assert_dict_contains_subset
from lms.djangoapps.lms_xblock.mixin import NONSENSICAL_ACCESS_RESTRICTION
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.content_tagging import api as tagging_api
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration
from openedx.core.djangoapps.video_config.toggles import PUBLIC_VIDEO_SHARE
from xmodule.course_block import DEFAULT_START_DATE
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, LibraryFactory, check_mongo_calls
from xmodule.partitions.partitions import (
    ENROLLMENT_TRACK_PARTITION_ID,
    MINIMUM_UNUSED_PARTITION_ID,
    Group,
    UserPartition,
)
from xmodule.partitions.tests.test_partitions import MockPartitionService
from xmodule.x_module import STUDENT_VIEW, STUDIO_VIEW

from ..component import DEFAULT_ADVANCED_MODULES, component_handler, get_component_templates


class AsideTest(XBlockAside):
    """
    Test xblock aside class
    """

    FRAG_CONTENT = "<p>Aside Foo rendered</p>"

    field11 = String(default="aside1_default_value1", scope=Scope.content)
    field12 = String(default="aside1_default_value2", scope=Scope.settings)
    field13 = String(default="aside1_default_value3", scope=Scope.parent)

    @XBlockAside.aside_for("student_view")
    def student_view_aside(self, block, context):  # pylint: disable=unused-argument
        """Add to the student view"""
        return Fragment(self.FRAG_CONTENT)


class ItemTest(CourseTestCase):
    """Base test class for create, save, and delete"""

    def setUp(self):
        super().setUp()

        self.course_key = self.course.id
        self.usage_key = self.course.location

    def get_item_from_modulestore(self, usage_key):
        """
        Get the item referenced by the UsageKey from the modulestore
        """
        item = self.store.get_item(usage_key)
        return item

    def response_usage_key(self, response):
        """
        Get the UsageKey from the response payload and verify that the status_code was 200.
        :param response:
        """
        parsed = json.loads(response.content.decode("utf-8"))
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        key = UsageKey.from_string(parsed["locator"])
        if key.course_key.run is None:
            key = key.map_into_course(CourseKey.from_string(parsed["courseKey"]))
        return key

    def create_xblock(
        self, parent_usage_key=None, display_name=None, category=None, boilerplate=None
    ):  # pylint: disable=missing-function-docstring
        data = {
            "parent_locator": str(self.usage_key)
            if parent_usage_key is None
            else str(parent_usage_key),
            "category": category,
        }
        if display_name is not None:
            data["display_name"] = display_name
        if boilerplate is not None:
            data["boilerplate"] = boilerplate
        return self.client.ajax_post(reverse("xblock_handler"), json.dumps(data))

    def _create_vertical(self, parent_usage_key=None):
        """
        Creates a vertical, returning its UsageKey.
        """
        resp = self.create_xblock(
            category="vertical", parent_usage_key=parent_usage_key
        )
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        return self.response_usage_key(resp)


@ddt.ddt
class GetItemTest(ItemTest):
    """Tests for '/xblock' GET url."""

    def _get_preview(self, usage_key, data=None):
        """Makes a request to xblock preview handler"""
        preview_url = reverse_usage_url(
            "xblock_view_handler", usage_key, {"view_name": "container_preview"}
        )
        data = data if data else {}
        resp = self.client.get(preview_url, data, HTTP_ACCEPT="application/json")
        return resp

    def _get_container_preview(self, usage_key, data=None):
        """
        Returns the HTML and resources required for the xblock at the specified UsageKey
        """
        resp = self._get_preview(usage_key, data)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        resp_content = json.loads(resp.content.decode("utf-8"))
        html = resp_content["html"]
        self.assertTrue(html)  # noqa: PT009
        resources = resp_content["resources"]
        self.assertIsNotNone(resources)  # noqa: PT009
        return html, resources

    def _get_container_preview_with_error(
        self, usage_key, expected_code, data=None, content_contains=None
    ):
        """Make request and asserts on response code and response contents"""
        resp = self._get_preview(usage_key, data)
        self.assertEqual(resp.status_code, expected_code)  # noqa: PT009
        if content_contains:
            self.assertContains(resp, content_contains, status_code=expected_code)
        return resp

    def test_get_vertical(self):
        # Add a vertical
        resp = self.create_xblock(category="vertical")
        usage_key = self.response_usage_key(resp)

        # Retrieve it
        resp = self.client.get(reverse_usage_url("xblock_handler", usage_key))
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

    def test_get_empty_container_fragment(self):
        root_usage_key = self._create_vertical()
        html, __ = self._get_container_preview(root_usage_key)

        # XBlock messages are added by the Studio wrapper.
        self.assertIn("wrapper-xblock-message", html)  # noqa: PT009
        # Make sure that "wrapper-xblock" does not appear by itself (without -message at end).
        self.assertNotRegex(html, r"wrapper-xblock[^-]+")  # noqa: PT009

        # Verify that the header and article tags are still added
        self.assertIn('<header class="xblock-header xblock-header-vertical ">', html)  # noqa: PT009
        self.assertIn('<article class="xblock-render">', html)  # noqa: PT009

    def test_get_container_fragment(self):
        root_usage_key = self._create_vertical()

        # Add a problem beneath a child vertical
        child_vertical_usage_key = self._create_vertical(
            parent_usage_key=root_usage_key
        )
        resp = self.create_xblock(
            parent_usage_key=child_vertical_usage_key,
            category="problem",
        )
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

        # Get the preview HTML
        html, __ = self._get_container_preview(root_usage_key)

        # Verify that the Studio nesting wrapper has been added
        self.assertIn("level-nesting", html)  # noqa: PT009
        self.assertIn('<header class="xblock-header xblock-header-vertical ">', html)  # noqa: PT009
        self.assertIn('<article class="xblock-render">', html)  # noqa: PT009

        # Verify that the Studio element wrapper has been added
        self.assertIn("level-element", html)  # noqa: PT009

    def test_get_container_nested_container_fragment(self):
        """
        Test the case of the container page containing a link to another container page.
        """
        # Add a wrapper with child beneath a child vertical
        root_usage_key = self._create_vertical()

        resp = self.create_xblock(parent_usage_key=root_usage_key, category="wrapper")
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        wrapper_usage_key = self.response_usage_key(resp)

        resp = self.create_xblock(
            parent_usage_key=wrapper_usage_key,
            category="problem",
        )
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

        # Get the preview HTML and verify the View -> link is present.
        html, __ = self._get_container_preview(root_usage_key)
        self.assertIn("wrapper-xblock", html)  # noqa: PT009
        self.assertRegex(  # noqa: PT009
            html,
            # The instance of the wrapper class will have an auto-generated ID. Allow any
            # characters after wrapper.
            (  # noqa: UP032
                '"/container/{}" class="action-button xblock-view-action-button">'
                '\\s*<span class="action-button-text">View</span>'
            ).format(re.escape(str(wrapper_usage_key))),
        )

    @patch("cms.djangoapps.contentstore.xblock_storage_handlers.xblock_helpers.get_object_tag_counts")
    def test_tag_count_in_container_fragment(self, mock_get_object_tag_counts):
        root_usage_key = self._create_vertical()

        # Add a problem beneath a child vertical
        child_vertical_usage_key = self._create_vertical(
            parent_usage_key=root_usage_key
        )
        resp = self.create_xblock(
            parent_usage_key=child_vertical_usage_key,
            category="problem",
        )
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        usage_key = self.response_usage_key(resp)

        # Get the preview HTML with tags
        mock_get_object_tag_counts.return_value = {
            str(usage_key): 13,
        }
        html, __ = self._get_container_preview(root_usage_key)
        self.assertIn("wrapper-xblock", html)  # noqa: PT009
        self.assertIn('data-testid="tag-count-button"', html)  # noqa: PT009

    def test_split_test(self):
        """
        Test that a split_test block renders all of its children in Studio.
        """
        root_usage_key = self._create_vertical()
        resp = self.create_xblock(
            category="split_test", parent_usage_key=root_usage_key
        )
        split_test_usage_key = self.response_usage_key(resp)
        resp = self.create_xblock(
            parent_usage_key=split_test_usage_key,
            category="html",
        )
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        resp = self.create_xblock(
            parent_usage_key=split_test_usage_key,
            category="html",
        )
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

    def test_split_test_edited(self):
        """
        Test that rename of a group changes display name of child vertical.
        """
        self.course.user_partitions = [
            UserPartition(
                0,
                "first_partition",
                "First Partition",
                [Group("0", "alpha"), Group("1", "beta")],
            )
        ]
        self.store.update_item(self.course, self.user.id)
        root_usage_key = self._create_vertical()
        resp = self.create_xblock(
            category="split_test", parent_usage_key=root_usage_key
        )
        split_test_usage_key = self.response_usage_key(resp)
        self.client.ajax_post(
            reverse_usage_url("xblock_handler", split_test_usage_key),
            data={"metadata": {"user_partition_id": str(0)}},
        )
        html, __ = self._get_container_preview(split_test_usage_key)
        self.assertIn("alpha", html)  # noqa: PT009
        self.assertIn("beta", html)  # noqa: PT009

        # Rename groups in group configuration
        GROUP_CONFIGURATION_JSON = {
            "id": 0,
            "name": "first_partition",
            "scheme": "random",
            "description": "First Partition",
            "version": UserPartition.VERSION,
            "groups": [
                {"id": 0, "name": "New_NAME_A", "version": 1},
                {"id": 1, "name": "New_NAME_B", "version": 1},
            ],
        }

        response = self.client.put(
            reverse_course_url(
                "group_configurations_detail_handler",
                self.course.id,
                kwargs={"group_configuration_id": 0},
            ),
            data=json.dumps(GROUP_CONFIGURATION_JSON),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 201)  # noqa: PT009
        html, __ = self._get_container_preview(split_test_usage_key)
        self.assertNotIn("alpha", html)  # noqa: PT009
        self.assertNotIn("beta", html)  # noqa: PT009
        self.assertIn("New_NAME_A", html)  # noqa: PT009
        self.assertIn("New_NAME_B", html)  # noqa: PT009

    def test_valid_paging(self):
        """
        Tests that valid paging is passed along to underlying block
        """
        with patch(
            "cms.djangoapps.contentstore.views.block.get_preview_fragment"
        ) as patched_get_preview_fragment:
            retval = Mock()
            type(retval).content = PropertyMock(return_value="Some content")
            type(retval).resources = PropertyMock(return_value=[])
            patched_get_preview_fragment.return_value = retval

            root_usage_key = self._create_vertical()
            _, _ = self._get_container_preview(
                root_usage_key,
                {"enable_paging": "true", "page_number": 0, "page_size": 2},
            )
            call_args = patched_get_preview_fragment.call_args[0]
            _, _, context = call_args
            self.assertIn("paging", context)  # noqa: PT009
            self.assertEqual({"page_number": 0, "page_size": 2}, context["paging"])  # noqa: PT009

    @ddt.data([1, "invalid"], ["invalid", 2])
    @ddt.unpack
    def test_invalid_paging(self, page_number, page_size):
        """
        Tests that valid paging is passed along to underlying block
        """
        root_usage_key = self._create_vertical()
        self._get_container_preview_with_error(
            root_usage_key,
            400,
            data={
                "enable_paging": "true",
                "page_number": page_number,
                "page_size": page_size,
            },
            content_contains="Couldn't parse paging parameters",
        )

    def test_get_user_partitions_and_groups(self):
        # Note about UserPartition and UserPartition Group IDs: these must not conflict with IDs used
        # by dynamic user partitions.
        self.course.user_partitions = [
            UserPartition(
                id=MINIMUM_UNUSED_PARTITION_ID,
                name="Random user partition",
                scheme=UserPartition.get_scheme("random"),
                description="Random user partition",
                groups=[
                    Group(
                        id=MINIMUM_UNUSED_PARTITION_ID + 1, name="Group A"
                    ),  # See note above.
                    Group(
                        id=MINIMUM_UNUSED_PARTITION_ID + 2, name="Group B"
                    ),  # See note above.
                ],
            ),
        ]
        self.store.update_item(self.course, self.user.id)

        # Create an item and retrieve it
        resp = self.create_xblock(category="vertical")
        usage_key = self.response_usage_key(resp)
        resp = self.client.get(reverse_usage_url("xblock_handler", usage_key))
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

        # Check that the partition and group information was returned
        result = json.loads(resp.content.decode("utf-8"))
        self.assertEqual(  # noqa: PT009
            result["user_partitions"],
            [
                {
                    "id": ENROLLMENT_TRACK_PARTITION_ID,
                    "name": "Enrollment Track Groups",
                    "scheme": "enrollment_track",
                    "groups": [
                        {
                            "id": settings.COURSE_ENROLLMENT_MODES["audit"]["id"],
                            "name": "Audit",
                            "selected": False,
                            "deleted": False,
                        }
                    ],
                },
                {
                    "id": MINIMUM_UNUSED_PARTITION_ID,
                    "name": "Random user partition",
                    "scheme": "random",
                    "groups": [
                        {
                            "id": MINIMUM_UNUSED_PARTITION_ID + 1,
                            "name": "Group A",
                            "selected": False,
                            "deleted": False,
                        },
                        {
                            "id": MINIMUM_UNUSED_PARTITION_ID + 2,
                            "name": "Group B",
                            "selected": False,
                            "deleted": False,
                        },
                    ],
                },
            ],
        )
        self.assertEqual(result["group_access"], {})  # noqa: PT009

    @ddt.data("ancestorInfo", "")
    def test_ancestor_info(self, field_type):
        """
        Test that we get correct ancestor info.

        Arguments:
            field_type (string): If field_type=ancestorInfo, fetch ancestor info of the XBlock otherwise not.
        """

        # Create a parent chapter
        chap1 = self.create_xblock(
            parent_usage_key=self.course.location,
            display_name="chapter1",
            category="chapter",
        )
        chapter_usage_key = self.response_usage_key(chap1)

        # create a sequential
        seq1 = self.create_xblock(
            parent_usage_key=chapter_usage_key,
            display_name="seq1",
            category="sequential",
        )
        seq_usage_key = self.response_usage_key(seq1)

        # create a vertical
        vert1 = self.create_xblock(
            parent_usage_key=seq_usage_key,
            display_name="vertical1",
            category="vertical",
        )
        vert_usage_key = self.response_usage_key(vert1)

        # create problem and an html component
        problem1 = self.create_xblock(
            parent_usage_key=vert_usage_key, display_name="problem1", category="problem"
        )
        print(problem1)
        problem_usage_key = self.response_usage_key(problem1)

        def assert_xblock_info(xblock, xblock_info):
            """
            Assert we have correct xblock info.

            Arguments:
                xblock (XBlock): An XBlock item.
                xblock_info (dict): A dict containing xblock information.
            """
            self.assertEqual(str(xblock.location), xblock_info["id"])  # noqa: PT009
            self.assertEqual(xblock.display_name, xblock_info["display_name"])  # noqa: PT009
            self.assertEqual(xblock.category, xblock_info["category"])  # noqa: PT009

        for usage_key in (
            problem_usage_key,
            vert_usage_key,
            seq_usage_key,
            chapter_usage_key,
        ):
            xblock = self.get_item_from_modulestore(usage_key)
            url = (
                reverse_usage_url("xblock_handler", usage_key) + f"?fields={field_type}"
            )
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)  # noqa: PT009
            response = json.loads(response.content.decode("utf-8"))
            if field_type == "ancestorInfo":
                self.assertIn("ancestors", response)  # noqa: PT009
                for ancestor_info in response["ancestors"]:
                    parent_xblock = xblock.get_parent()
                    assert_xblock_info(parent_xblock, ancestor_info)
                    xblock = parent_xblock
            else:
                self.assertNotIn("ancestors", response)  # noqa: PT009
                xblock_info = get_block_info(xblock)
                self.assertEqual(xblock_info, response)  # noqa: PT009


@ddt.ddt
class DeleteItem(ItemTest):
    """Tests for '/xblock' DELETE url."""

    def test_delete_static_page(self):
        course = CourseFactory.create()
        # Add static tab
        resp = self.create_xblock(
            category="static_tab", parent_usage_key=course.location
        )
        usage_key = self.response_usage_key(resp)

        # Now delete it. There was a bug that the delete was failing (static tabs do not exist in draft modulestore).
        resp = self.client.delete(reverse_usage_url("xblock_handler", usage_key))
        self.assertEqual(resp.status_code, 204)  # noqa: PT009


class TestCreateItem(ItemTest):
    """
    Test the create_item handler thoroughly
    """

    def test_create_nicely(self):
        """
        Try the straightforward use cases
        """
        # create a chapter
        display_name = "Nicely created"
        resp = self.create_xblock(display_name=display_name, category="chapter")

        # get the new item and check its category and display_name
        chap_usage_key = self.response_usage_key(resp)
        new_obj = self.get_item_from_modulestore(chap_usage_key)
        self.assertEqual(new_obj.scope_ids.block_type, "chapter")  # noqa: PT009
        self.assertEqual(new_obj.display_name, display_name)  # noqa: PT009
        self.assertEqual(new_obj.location.org, self.course.location.org)  # noqa: PT009
        self.assertEqual(new_obj.location.course, self.course.location.course)  # noqa: PT009

        # get the course and ensure it now points to this one
        course = self.get_item_from_modulestore(self.usage_key)
        self.assertIn(chap_usage_key, course.children)  # noqa: PT009

    def test_create_block_negative(self):
        """
        Negative tests for create_item
        """
        # non-existent boilerplate: creates a default
        resp = self.create_xblock(category="problem")
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

    def test_create_with_future_date(self):
        self.assertEqual(self.course.start, DEFAULT_START_DATE)  # noqa: PT009
        resp = self.create_xblock(category="chapter")
        usage_key = self.response_usage_key(resp)
        obj = self.get_item_from_modulestore(usage_key)
        self.assertEqual(obj.start, DEFAULT_START_DATE)  # noqa: PT009

    def test_static_tabs_initialization(self):
        """
        Test that static tab display names are not being initialized as None.
        """
        # Add a new static tab with no explicit name
        resp = self.create_xblock(category="static_tab")
        usage_key = self.response_usage_key(resp)

        # Check that its name is not None
        new_tab = self.get_item_from_modulestore(usage_key)
        self.assertEqual(new_tab.display_name, "Empty")  # noqa: PT009


class DuplicateHelper:
    """
    Helper mixin class for TestDuplicateItem and TestDuplicateItemWithAsides
    """

    def _duplicate_and_verify(
        self, source_usage_key, parent_usage_key, check_asides=False
    ):
        """Duplicates the source, parenting to supplied parent. Then does equality check."""
        usage_key = self._duplicate_item(parent_usage_key, source_usage_key)
        # pylint: disable=no-member
        self.assertTrue(  # noqa: PT009
            self._check_equality(
                source_usage_key, usage_key, parent_usage_key, check_asides=check_asides
            ),
            "Duplicated item differs from original",
        )
        return usage_key

    def _check_equality(
        self,
        source_usage_key,
        duplicate_usage_key,
        parent_usage_key=None,
        check_asides=False,
        is_child=False,
    ):
        """
        Gets source and duplicated items from the modulestore using supplied usage keys.
        Then verifies that they represent equivalent items (modulo parents and other
        known things that may differ).
        """
        # pylint: disable=no-member
        original_item = self.get_item_from_modulestore(source_usage_key)
        duplicated_item = self.get_item_from_modulestore(duplicate_usage_key)

        if check_asides:
            original_asides = original_item.runtime.get_asides(original_item)
            duplicated_asides = duplicated_item.runtime.get_asides(duplicated_item)
            self.assertEqual(len(original_asides), 1)  # noqa: PT009
            self.assertEqual(len(duplicated_asides), 1)  # noqa: PT009
            self.assertEqual(original_asides[0].field11, duplicated_asides[0].field11)  # noqa: PT009
            self.assertEqual(original_asides[0].field12, duplicated_asides[0].field12)  # noqa: PT009
            self.assertNotEqual(  # noqa: PT009
                original_asides[0].field13, duplicated_asides[0].field13
            )
            self.assertEqual(duplicated_asides[0].field13, "aside1_default_value3")  # noqa: PT009

        self.assertNotEqual(  # noqa: PT009
            str(original_item.location),
            str(duplicated_item.location),
            "Location of duplicate should be different from original",
        )

        # Parent will only be equal for root of duplicated structure, in the case
        # where an item is duplicated in-place.
        if parent_usage_key and str(original_item.parent) == str(parent_usage_key):
            self.assertEqual(  # noqa: PT009
                str(parent_usage_key),
                str(duplicated_item.parent),
                "Parent of duplicate should equal parent of source for root xblock when duplicated in-place",
            )
        else:
            self.assertNotEqual(  # noqa: PT009
                str(original_item.parent),
                str(duplicated_item.parent),
                "Parent duplicate should be different from source",
            )

        # Set the location and parent to be the same so we can make sure the rest of the
        # duplicate is equal.
        duplicated_item.location = original_item.location
        duplicated_item.parent = original_item.parent

        # Children will also be duplicated, so for the purposes of testing equality, we will set
        # the children to the original after recursively checking the children.
        if original_item.has_children:
            self.assertEqual(  # noqa: PT009
                len(original_item.children),
                len(duplicated_item.children),
                "Duplicated item differs in number of children",
            )
            for i in range(len(original_item.children)):
                if not self._check_equality(
                    original_item.children[i],
                    duplicated_item.children[i],
                    is_child=True,
                ):
                    return False
            duplicated_item.children = original_item.children
        return self._verify_duplicate_display_name(
            original_item, duplicated_item, is_child
        )

    def _verify_duplicate_display_name(
        self, original_item, duplicated_item, is_child=False
    ):
        """
        Verifies display name of duplicated item.
        """
        if is_child:
            if original_item.display_name is None:
                return duplicated_item.display_name == original_item.category
            return duplicated_item.display_name == original_item.display_name
        if original_item.display_name is not None:
            return (
                duplicated_item.display_name
                == "Duplicate of '{display_name}'".format(  # noqa: UP032
                    display_name=original_item.display_name
                )
            )
        return duplicated_item.display_name == "Duplicate of {display_name}".format(  # noqa: UP032
            display_name=original_item.category
        )

    def _duplicate_item(self, parent_usage_key, source_usage_key, display_name=None):
        """
        Duplicates the source.
        """
        # pylint: disable=no-member
        data = {
            "parent_locator": str(parent_usage_key),
            "duplicate_source_locator": str(source_usage_key),
        }
        if display_name is not None:
            data["display_name"] = display_name

        resp = self.client.ajax_post(reverse("xblock_handler"), json.dumps(data))
        return self.response_usage_key(resp)


class TestDuplicateItem(OpenEdxEventsTestMixin, ItemTest, DuplicateHelper):
    """
    Test the duplicate method.
    """

    ENABLED_OPENEDX_EVENTS = [
        "org.openedx.content_authoring.xblock.duplicated.v1",
    ]

    def setUp(self):
        """Creates the test course structure and a few components to 'duplicate'."""
        super().setUp()
        # Create a parent chapter (for testing children of children).
        resp = self.create_xblock(parent_usage_key=self.usage_key, category="chapter")
        self.chapter_usage_key = self.response_usage_key(resp)

        # create a sequential
        resp = self.create_xblock(
            parent_usage_key=self.chapter_usage_key, category="sequential"
        )
        self.seq_usage_key = self.response_usage_key(resp)

        # create a vertical containing a problem and an html component
        resp = self.create_xblock(
            parent_usage_key=self.seq_usage_key, category="vertical"
        )
        self.vert_usage_key = self.response_usage_key(resp)

        # create problem and an html component
        resp = self.create_xblock(
            parent_usage_key=self.vert_usage_key,
            category="problem",
        )
        self.problem_usage_key = self.response_usage_key(resp)

        resp = self.create_xblock(parent_usage_key=self.vert_usage_key, category="html")
        self.html_usage_key = self.response_usage_key(resp)

        # Create a second sequential just (testing children of children)
        self.create_xblock(parent_usage_key=self.chapter_usage_key, category='sequential')

    def test_duplicate_equality(self):
        """
        Tests that a duplicated xblock is identical to the original,
        except for location and display name.
        """
        self._duplicate_and_verify(self.problem_usage_key, self.vert_usage_key)
        self._duplicate_and_verify(self.html_usage_key, self.vert_usage_key)
        self._duplicate_and_verify(self.vert_usage_key, self.seq_usage_key)
        self._duplicate_and_verify(self.seq_usage_key, self.chapter_usage_key)
        self._duplicate_and_verify(self.chapter_usage_key, self.usage_key)

    def test_duplicate_event(self):
        """
        Check that XBLOCK_DUPLICATED event is sent when xblock is duplicated.
        """
        event_receiver = Mock()
        XBLOCK_DUPLICATED.connect(event_receiver)
        usage_key = self._duplicate_and_verify(self.vert_usage_key, self.seq_usage_key)
        event_receiver.assert_called()
        assert_dict_contains_subset(
            self,
            {
                "signal": XBLOCK_DUPLICATED,
                "sender": None,
                "xblock_info": DuplicatedXBlockData(
                    usage_key=usage_key,
                    block_type=usage_key.block_type,
                    source_usage_key=self.vert_usage_key,
                ),
            },
            event_receiver.call_args.kwargs,
        )

    def test_ordering(self):
        """
        Tests the a duplicated xblock appears immediately after its source
        (if duplicate and source share the same parent), else at the
        end of the children of the parent.
        """

        def verify_order(source_usage_key, parent_usage_key, source_position=None):
            usage_key = self._duplicate_item(parent_usage_key, source_usage_key)
            parent = self.get_item_from_modulestore(parent_usage_key)
            children = parent.children
            if source_position is None:
                self.assertNotIn(  # noqa: PT009
                    source_usage_key,
                    children,
                    "source item not expected in children array",
                )
                self.assertEqual(  # noqa: PT009
                    children[len(children) - 1], usage_key, "duplicated item not at end"
                )
            else:
                self.assertEqual(  # noqa: PT009
                    children[source_position],
                    source_usage_key,
                    "source item at wrong position",
                )
                self.assertEqual(  # noqa: PT009
                    children[source_position + 1],
                    usage_key,
                    "duplicated item not ordered after source item",
                )

        verify_order(self.problem_usage_key, self.vert_usage_key, 0)
        # 2 because duplicate of problem should be located before.
        verify_order(self.html_usage_key, self.vert_usage_key, 2)
        verify_order(self.vert_usage_key, self.seq_usage_key, 0)
        verify_order(self.seq_usage_key, self.chapter_usage_key, 0)

        # Test duplicating something into a location that is not the parent of the original item.
        # Duplicated item should appear at the end.
        verify_order(self.html_usage_key, self.usage_key)

    def test_display_name(self):
        """
        Tests the expected display name for the duplicated xblock.
        """

        def verify_name(
            source_usage_key, parent_usage_key, expected_name, display_name=None
        ):
            usage_key = self._duplicate_item(
                parent_usage_key, source_usage_key, display_name
            )
            duplicated_item = self.get_item_from_modulestore(usage_key)
            self.assertEqual(duplicated_item.display_name, expected_name)  # noqa: PT009
            return usage_key

        # Uses default display_name of 'Text' from HTML component.
        verify_name(self.html_usage_key, self.vert_usage_key, "Duplicate of 'Text'")

        # The sequence does not have a display_name set, so category is shown.
        verify_name(
            self.seq_usage_key, self.chapter_usage_key, "Duplicate of sequential"
        )

        # Now send a custom display name for the duplicate.
        verify_name(
            self.seq_usage_key,
            self.chapter_usage_key,
            "customized name",
            display_name="customized name",
        )

    def test_shallow_duplicate(self):
        """
        Test that duplicate_block(..., shallow=True) can duplicate a block but ignores its children.
        """
        source_course = CourseFactory()
        user = UserFactory.create()
        source_chapter = BlockFactory(
            parent=source_course, category="chapter", display_name="Source Chapter"
        )
        BlockFactory(parent=source_chapter, category="html", display_name="Child")
        # Refresh.
        source_chapter = self.store.get_item(source_chapter.location)
        self.assertEqual(len(source_chapter.get_children()), 1)  # noqa: PT009
        destination_course = CourseFactory()
        destination_location = duplicate_block(
            parent_usage_key=destination_course.location,
            duplicate_source_usage_key=source_chapter.location,
            user=user,
            display_name=source_chapter.display_name,
            shallow=True,
        )
        # Refresh here, too, just to be sure.
        destination_chapter = self.store.get_item(destination_location)
        self.assertEqual(len(destination_chapter.get_children()), 0)  # noqa: PT009
        self.assertEqual(destination_chapter.display_name, "Source Chapter")  # noqa: PT009

    def test_duplicate_library_content_block(self):  # pylint: disable=too-many-statements
        """
        Test the LegacyLibraryContentBlock's special duplication process.
        """
        store = modulestore()

        # Create a library with two blocks (HTML 1 and HTML 2).
        # These are the "Original" version of the blocks.
        lib = LibraryFactory()
        BlockFactory(
            parent=lib,
            category="html",
            display_name="HTML 1 Title (Original)",
            data="HTML 1 Content (Original)",
            publish_item=False,
        )
        BlockFactory(
            parent=lib,
            category="html",
            display_name="HTML 2 Title (Original)",
            data="HTML 2 Content (Original)",
            publish_item=False,
        )
        original_lib_version = store.get_library(
            lib.location.library_key, remove_version=False, remove_branch=False,
        ).location.library_key.version_guid
        assert original_lib_version is not None

        # Create a library content block (lc), point it out our library, and sync it.
        unit = BlockFactory(
            parent_location=self.seq_usage_key,
            category="vertical",
            display_name="Parent Unit of LC and its Dupe",
            publish_item=False,
        )
        lc = BlockFactory(
            parent=unit,
            category="library_content",
            source_library_id=str(lib.location.library_key),
            display_name="LC Block",
            max_count=1,
            publish_item=False,
        )
        lc.sync_from_library()
        lc = store.get_item(lc.location)  # we must reload because sync_from_library happens out-of-thread
        assert lc.source_library_version == str(original_lib_version)
        lc_html_1 = store.get_item(lc.children[0])
        lc_html_2 = store.get_item(lc.children[1])
        assert lc_html_1.display_name == "HTML 1 Title (Original)"
        assert lc_html_2.display_name == "HTML 2 Title (Original)"
        assert lc_html_1.data == "HTML 1 Content (Original)"
        assert lc_html_2.data == "HTML 2 Content (Original)"

        # Override the title and data of HTML 1 under lc ("Course Override").
        # Note that title is settings-scoped and data is content-scoped.
        lc_html_1.display_name = "HTML 1 Title (Course Override)"
        lc_html_1.data = "HTML 1 Content (Course Override)"
        store.update_item(lc_html_1, self.user.id)

        # Now, update the titles and contents of both HTML 1 and HTML 2 ("Lib Update").
        # This will yield a new version of the library (updated_lib_version).
        lib_html_1 = store.get_item(lib.children[0])
        lib_html_2 = store.get_item(lib.children[1])
        assert lib_html_1.display_name == "HTML 1 Title (Original)"
        assert lib_html_2.display_name == "HTML 2 Title (Original)"
        lib_html_1.display_name = "HTML 1 Title (Lib Update)"
        lib_html_2.display_name = "HTML 2 Title (Lib Update)"
        lib_html_1.data = "HTML 1 Content (Lib Update)"
        lib_html_2.data = "HTML 2 Content (Lib Update)"
        store.update_item(lib_html_1, self.user.id)
        store.update_item(lib_html_2, self.user.id)
        updated_lib_version = store.get_library(
            lib.location.library_key, remove_version=False, remove_branch=False,
        ).location.library_key.version_guid
        assert updated_lib_version is not None
        assert updated_lib_version != original_lib_version

        # DUPLICATE lc.
        # Unit should now contain both lc and dupe.
        # All settings should match between lc and dupe.
        dupe = store.get_item(
            self._duplicate_item(
                parent_usage_key=unit.location,
                source_usage_key=lc.location,
                display_name="Dupe LC Block",
            )
        )
        lc = store.get_item(lc.location)
        unit = store.get_item(unit.location)
        assert unit.children == [lc.location, dupe.location]
        assert len(lc.children) == len(dupe.children) == 2
        assert lc.max_count == dupe.max_count == 1
        assert lc.source_library_id == dupe.source_library_id == str(lib.location.library_key)
        assert lc.source_library_version == dupe.source_library_version == str(original_lib_version)

        # The lc block's children should remain unchanged.
        # That is: HTML 1 has overrides, HTML 2 has originals.
        lc_html_1 = store.get_item(lc.children[0])
        assert lc_html_1.display_name == "HTML 1 Title (Course Override)"
        assert lc_html_1.data == "HTML 1 Content (Course Override)"
        lc_html_2 = store.get_item(lc.children[1])
        assert lc_html_2.display_name == "HTML 2 Title (Original)"
        assert lc_html_2.data == "HTML 2 Content (Original)"

        # Now, the dupe's children should copy *settings* overrides over from the lc block,
        # but we don't actually expect it to copy *content* overrides over from the lc block.
        # (Yes, this is weird. One would expect it to copy all fields from the lc block, whether settings or content.
        #  But that's the existing behavior, so we're going to test for it, for now at least.
        #  We may change this in the future: https://github.com/openedx/edx-platform/issues/33739)
        dupe_html_1 = store.get_item(dupe.children[0])
        assert dupe_html_1.display_name == "HTML 1 Title (Course Override)"  # <- as you'd expect
        assert dupe_html_1.data == "HTML 1 Content (Original)"  # <- weird!
        dupe_html_2 = store.get_item(dupe.children[1])
        assert dupe_html_2.display_name == "HTML 2 Title (Original)"  # <- as you'd expect
        assert dupe_html_2.data == "HTML 2 Content (Original)"  # <- as you'd expect

        # Finally, upgrade the dupe's library version, and make sure it pulls in updated library block *content*,
        # whilst preserving *settings overrides* (specifically, HTML 1's title override).
        dupe.sync_from_library(upgrade_to_latest=True)
        dupe = store.get_item(dupe.location)
        assert dupe.source_library_version == str(updated_lib_version)
        assert len(dupe.children) == 2
        dupe_html_1 = store.get_item(dupe.children[0])
        dupe_html_2 = store.get_item(dupe.children[1])
        assert dupe_html_1.display_name == "HTML 1 Title (Course Override)"
        assert dupe_html_1.data == "HTML 1 Content (Lib Update)"
        assert dupe_html_2.display_name == "HTML 2 Title (Lib Update)"
        assert dupe_html_2.data == "HTML 2 Content (Lib Update)"

    def test_duplicate_tags(self):
        """
        Test that duplicating a tagged XBlock also duplicates its content tags.
        """
        source_course = CourseFactory()
        user = UserFactory.create()
        source_chapter = BlockFactory(
            parent=source_course, category="chapter", display_name="Source Chapter"
        )
        source_block = BlockFactory(parent=source_chapter, category="html", display_name="Child")

        # Create a couple of taxonomies with tags
        taxonomyA = tagging_api.create_taxonomy(name="A", export_id="A")
        taxonomyB = tagging_api.create_taxonomy(name="B", export_id="B")
        tagging_api.set_taxonomy_orgs(taxonomyA, all_orgs=True)
        tagging_api.set_taxonomy_orgs(taxonomyB, all_orgs=True)
        tagging_api.add_tag_to_taxonomy(taxonomyA, "one")
        tagging_api.add_tag_to_taxonomy(taxonomyA, "two")
        tagging_api.add_tag_to_taxonomy(taxonomyB, "three")
        tagging_api.add_tag_to_taxonomy(taxonomyB, "four")

        # Tag the chapter
        tagging_api.tag_object(str(source_chapter.location), taxonomyA, ["one", "two"])
        tagging_api.tag_object(str(source_chapter.location), taxonomyB, ["three", "four"])

        # Tag the child block
        tagging_api.tag_object(str(source_block.location), taxonomyA, ["two"],)

        # Duplicate the chapter (and its children)
        dupe_location = duplicate_block(
            parent_usage_key=source_course.location,
            duplicate_source_usage_key=source_chapter.location,
            user=user,
        )
        dupe_chapter = self.store.get_item(dupe_location)
        self.assertEqual(len(dupe_chapter.get_children()), 1)  # noqa: PT009
        dupe_block = dupe_chapter.get_children()[0]

        # Check that the duplicated blocks also duplicated tags
        expected_chapter_tags = [
            f'<ObjectTag> {str(dupe_chapter.location)}: A=one',
            f'<ObjectTag> {str(dupe_chapter.location)}: A=two',
            f'<ObjectTag> {str(dupe_chapter.location)}: B=four',
            f'<ObjectTag> {str(dupe_chapter.location)}: B=three',
        ]
        dupe_chapter_tags = [str(object_tag) for object_tag in tagging_api.get_object_tags(str(dupe_chapter.location))]
        assert dupe_chapter_tags == expected_chapter_tags
        expected_block_tags = [
            f'<ObjectTag> {str(dupe_block.location)}: A=two',
        ]
        dupe_block_tags = [str(object_tag) for object_tag in tagging_api.get_object_tags(str(dupe_block.location))]
        assert dupe_block_tags == expected_block_tags


@ddt.ddt
class TestMoveItem(ItemTest):
    """
    Tests for move item.
    """

    def setUp(self):
        """
        Creates the test course structure to build course outline tree.
        """
        super().setUp()
        self.setup_course()

    def setup_course(self, default_store=None):
        """
        Helper method to create the course.
        """
        if not default_store:
            default_store = self.store.default_modulestore.get_modulestore_type()

        course = CourseFactory.create(default_store=default_store)

        # Create group configurations
        course.user_partitions = [
            UserPartition(
                0,
                "first_partition",
                "Test Partition",
                [Group("0", "alpha"), Group("1", "beta")],
            )
        ]
        self.store.update_item(course, self.user.id)

        # Create a parent chapter
        chap1 = self.create_xblock(
            parent_usage_key=course.location,
            display_name="chapter1",
            category="chapter",
        )
        self.chapter_usage_key = self.response_usage_key(chap1)

        chap2 = self.create_xblock(
            parent_usage_key=course.location,
            display_name="chapter2",
            category="chapter",
        )
        self.chapter2_usage_key = self.response_usage_key(chap2)

        # Create a sequential
        seq1 = self.create_xblock(
            parent_usage_key=self.chapter_usage_key,
            display_name="seq1",
            category="sequential",
        )
        self.seq_usage_key = self.response_usage_key(seq1)

        seq2 = self.create_xblock(
            parent_usage_key=self.chapter_usage_key,
            display_name="seq2",
            category="sequential",
        )
        self.seq2_usage_key = self.response_usage_key(seq2)

        # Create a vertical
        vert1 = self.create_xblock(
            parent_usage_key=self.seq_usage_key,
            display_name="vertical1",
            category="vertical",
        )
        self.vert_usage_key = self.response_usage_key(vert1)

        vert2 = self.create_xblock(
            parent_usage_key=self.seq_usage_key,
            display_name="vertical2",
            category="vertical",
        )
        self.vert2_usage_key = self.response_usage_key(vert2)

        # Create problem and an html component
        problem1 = self.create_xblock(
            parent_usage_key=self.vert_usage_key,
            display_name="problem1",
            category="problem",
        )
        self.problem_usage_key = self.response_usage_key(problem1)

        html1 = self.create_xblock(
            parent_usage_key=self.vert_usage_key, display_name="html1", category="html"
        )
        self.html_usage_key = self.response_usage_key(html1)

        # Create a content experiment
        resp = self.create_xblock(
            category="split_test", parent_usage_key=self.vert_usage_key
        )
        self.split_test_usage_key = self.response_usage_key(resp)

        self.course = self.store.get_item(course.location)

    def setup_and_verify_content_experiment(self, partition_id):
        """
        Helper method to set up group configurations to content experiment.

        Arguments:
            partition_id (int): User partition id.
        """
        split_test = self.get_item_from_modulestore(self.split_test_usage_key)

        # Initially, no user_partition_id is set, and the split_test has no children.
        self.assertEqual(split_test.user_partition_id, -1)  # noqa: PT009
        self.assertEqual(len(split_test.children), 0)  # noqa: PT009

        # Set group configuration
        self.client.ajax_post(
            reverse_usage_url("xblock_handler", self.split_test_usage_key),
            data={"metadata": {"user_partition_id": str(partition_id)}},
        )
        split_test = self.get_item_from_modulestore(self.split_test_usage_key)
        self.assertEqual(split_test.user_partition_id, partition_id)  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            len(split_test.children),
            len(self.course.user_partitions[partition_id].groups),
        )
        return split_test

    def _move_component(self, source_usage_key, target_usage_key, target_index=None):
        """
        Helper method to send move request and returns the response.

        Arguments:
            source_usage_key (BlockUsageLocator): Locator of source item.
            target_usage_key (BlockUsageLocator): Locator of target parent.
            target_index (int): If provided, insert source item at the provided index location in target_usage_key item.

        Returns:
            resp (JsonResponse): Response after the move operation is complete.
        """
        data = {
            "move_source_locator": str(source_usage_key),
            "parent_locator": str(target_usage_key),
        }
        if target_index is not None:
            data["target_index"] = target_index

        return self.client.patch(
            reverse("xblock_handler"), json.dumps(data), content_type="application/json"
        )

    def assert_move_item(self, source_usage_key, target_usage_key, target_index=None):
        """
        Assert move component.

        Arguments:
            source_usage_key (BlockUsageLocator): Locator of source item.
            target_usage_key (BlockUsageLocator): Locator of target parent.
            target_index (int): If provided, insert source item at the provided index location in target_usage_key item.
        """
        parent_loc = self.store.get_parent_location(source_usage_key)
        parent = self.get_item_from_modulestore(parent_loc)
        source_index = _get_source_index(source_usage_key, parent)
        expected_index = target_index if target_index is not None else source_index
        response = self._move_component(
            source_usage_key, target_usage_key, target_index
        )
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        response = json.loads(response.content.decode("utf-8"))
        self.assertEqual(response["move_source_locator"], str(source_usage_key))  # noqa: PT009
        self.assertEqual(response["parent_locator"], str(target_usage_key))  # noqa: PT009
        self.assertEqual(response["source_index"], expected_index)  # noqa: PT009

        # Verify parent referance has been changed now.
        new_parent_loc = self.store.get_parent_location(source_usage_key)
        source_item = self.get_item_from_modulestore(source_usage_key)
        self.assertEqual(source_item.parent, new_parent_loc)  # noqa: PT009
        self.assertEqual(new_parent_loc, target_usage_key)  # noqa: PT009
        self.assertNotEqual(parent_loc, new_parent_loc)  # noqa: PT009

        # Assert item is present in children list of target parent and not source parent
        target_parent = self.get_item_from_modulestore(target_usage_key)
        source_parent = self.get_item_from_modulestore(parent_loc)
        self.assertIn(source_usage_key, target_parent.children)  # noqa: PT009
        self.assertNotIn(source_usage_key, source_parent.children)  # noqa: PT009

    def test_move_component(self):
        """
        Test move component with different xblock types.
        """
        self.setup_course()
        for source_usage_key, target_usage_key in [
            (self.html_usage_key, self.vert2_usage_key),
            (self.vert_usage_key, self.seq2_usage_key),
            (self.seq_usage_key, self.chapter2_usage_key),
        ]:
            self.assert_move_item(source_usage_key, target_usage_key)

    def test_move_source_index(self):
        """
        Test moving an item to a particular index.
        """
        parent = self.get_item_from_modulestore(self.vert_usage_key)
        children = parent.get_children()
        self.assertEqual(len(children), 3)  # noqa: PT009

        # Create a component within vert2.
        resp = self.create_xblock(
            parent_usage_key=self.vert2_usage_key, display_name="html2", category="html"
        )
        html2_usage_key = self.response_usage_key(resp)

        # Move html2_usage_key inside vert_usage_key at second position.
        self.assert_move_item(html2_usage_key, self.vert_usage_key, 1)
        parent = self.get_item_from_modulestore(self.vert_usage_key)
        children = parent.get_children()
        self.assertEqual(len(children), 4)  # noqa: PT009
        self.assertEqual(children[1].location, html2_usage_key)  # noqa: PT009

    def test_move_undo(self):
        """
        Test move a component and move it back (undo).
        """
        # Get the initial index of the component
        parent = self.get_item_from_modulestore(self.vert_usage_key)
        original_index = _get_source_index(self.html_usage_key, parent)

        # Move component and verify that response contains initial index
        response = self._move_component(self.html_usage_key, self.vert2_usage_key)
        response = json.loads(response.content.decode("utf-8"))
        self.assertEqual(original_index, response["source_index"])  # noqa: PT009

        # Verify that new parent has the moved component at the last index.
        parent = self.get_item_from_modulestore(self.vert2_usage_key)
        self.assertEqual(self.html_usage_key, parent.children[-1])  # noqa: PT009

        # Verify original and new index is different now.
        source_index = _get_source_index(self.html_usage_key, parent)
        self.assertNotEqual(original_index, source_index)  # noqa: PT009

        # Undo Move to the original index, use the source index fetched from the response.
        response = self._move_component(
            self.html_usage_key, self.vert_usage_key, response["source_index"]
        )
        response = json.loads(response.content.decode("utf-8"))
        self.assertEqual(original_index, response["source_index"])  # noqa: PT009

    def test_move_large_target_index(self):
        """
        Test moving an item at a large index would generate an error message.
        """
        parent = self.get_item_from_modulestore(self.vert2_usage_key)
        parent_children_length = len(parent.children)
        response = self._move_component(
            self.html_usage_key, self.vert2_usage_key, parent_children_length + 10
        )
        self.assertEqual(response.status_code, 400)  # noqa: PT009
        response = json.loads(response.content.decode("utf-8"))

        expected_error = (
            "You can not move {usage_key} at an invalid index ({target_index}).".format(  # noqa: UP032
                usage_key=self.html_usage_key, target_index=parent_children_length + 10
            )
        )
        self.assertEqual(expected_error, response["error"])  # noqa: PT009
        new_parent_loc = self.store.get_parent_location(self.html_usage_key)
        self.assertEqual(new_parent_loc, self.vert_usage_key)  # noqa: PT009

    def test_invalid_move(self):
        """
        Test invalid move.
        """
        parent_loc = self.store.get_parent_location(self.html_usage_key)
        response = self._move_component(self.html_usage_key, self.seq_usage_key)
        self.assertEqual(response.status_code, 400)  # noqa: PT009
        response = json.loads(response.content.decode("utf-8"))

        expected_error = "You can not move {source_type} into {target_type}.".format(  # noqa: UP032
            source_type=self.html_usage_key.block_type,
            target_type=self.seq_usage_key.block_type,
        )
        self.assertEqual(expected_error, response["error"])  # noqa: PT009
        new_parent_loc = self.store.get_parent_location(self.html_usage_key)
        self.assertEqual(new_parent_loc, parent_loc)  # noqa: PT009

    def test_move_current_parent(self):
        """
        Test that a component can not be moved to it's current parent.
        """
        parent_loc = self.store.get_parent_location(self.html_usage_key)
        self.assertEqual(parent_loc, self.vert_usage_key)  # noqa: PT009
        response = self._move_component(self.html_usage_key, self.vert_usage_key)
        self.assertEqual(response.status_code, 400)  # noqa: PT009
        response = json.loads(response.content.decode("utf-8"))

        self.assertEqual(  # noqa: PT009
            response["error"], "Item is already present in target location."
        )
        self.assertEqual(  # noqa: PT009
            self.store.get_parent_location(self.html_usage_key), parent_loc
        )

    def test_can_not_move_into_itself(self):
        """
        Test that a component can not be moved to itself.
        """
        library_content = self.create_xblock(
            parent_usage_key=self.vert_usage_key,
            display_name="library content block",
            category="library_content",
        )
        library_content_usage_key = self.response_usage_key(library_content)
        parent_loc = self.store.get_parent_location(library_content_usage_key)
        self.assertEqual(parent_loc, self.vert_usage_key)  # noqa: PT009
        response = self._move_component(
            library_content_usage_key, library_content_usage_key
        )
        self.assertEqual(response.status_code, 400)  # noqa: PT009
        response = json.loads(response.content.decode("utf-8"))

        self.assertEqual(response["error"], "You can not move an item into itself.")  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            self.store.get_parent_location(self.html_usage_key), parent_loc
        )

    def test_move_library_content(self):
        """
        Test that library content  can be moved to any other valid location.
        """
        library_content = self.create_xblock(
            parent_usage_key=self.vert_usage_key,
            display_name="library content block",
            category="library_content",
        )
        library_content_usage_key = self.response_usage_key(library_content)
        parent_loc = self.store.get_parent_location(library_content_usage_key)
        self.assertEqual(parent_loc, self.vert_usage_key)  # noqa: PT009
        self.assert_move_item(library_content_usage_key, self.vert2_usage_key)

    def test_move_into_library_content(self):
        """
        Test that a component can be moved into library content.
        """
        library_content = self.create_xblock(
            parent_usage_key=self.vert_usage_key,
            display_name="library content block",
            category="library_content",
        )
        library_content_usage_key = self.response_usage_key(library_content)
        self.assert_move_item(self.html_usage_key, library_content_usage_key)

    def test_move_content_experiment(self):
        """
        Test that a content experiment can be moved.
        """
        self.setup_and_verify_content_experiment(0)

        # Move content experiment
        self.assert_move_item(self.split_test_usage_key, self.vert2_usage_key)

    def test_move_content_experiment_components(self):
        """
        Test that component inside content experiment can be moved to any other valid location.
        """
        split_test = self.setup_and_verify_content_experiment(0)

        # Add html component to Group A.
        html1 = self.create_xblock(
            parent_usage_key=split_test.children[0],
            display_name="html1",
            category="html",
        )
        html_usage_key = self.response_usage_key(html1)

        # Move content experiment
        self.assert_move_item(html_usage_key, self.vert2_usage_key)

    def test_move_into_content_experiment_groups(self):
        """
        Test that a component can be moved to content experiment groups.
        """
        split_test = self.setup_and_verify_content_experiment(0)
        self.assert_move_item(self.html_usage_key, split_test.children[0])

    def test_can_not_move_into_content_experiment_level(self):
        """
        Test that a component can not be moved directly to content experiment level.
        """
        self.setup_and_verify_content_experiment(0)
        response = self._move_component(self.html_usage_key, self.split_test_usage_key)
        self.assertEqual(response.status_code, 400)  # noqa: PT009
        response = json.loads(response.content.decode("utf-8"))

        self.assertEqual(  # noqa: PT009
            response["error"],
            "You can not move an item directly into content experiment.",
        )
        self.assertEqual(  # noqa: PT009
            self.store.get_parent_location(self.html_usage_key), self.vert_usage_key
        )

    def test_can_not_move_content_experiment_into_its_children(self):
        """
        Test that a content experiment can not be moved inside any of it's children.
        """
        split_test = self.setup_and_verify_content_experiment(0)

        # Try to move content experiment inside it's child groups.
        for child_vert_usage_key in split_test.children:
            response = self._move_component(
                self.split_test_usage_key, child_vert_usage_key
            )
            self.assertEqual(response.status_code, 400)  # noqa: PT009
            response = json.loads(response.content.decode("utf-8"))

            self.assertEqual(  # noqa: PT009
                response["error"], "You can not move an item into it's child."
            )
            self.assertEqual(  # noqa: PT009
                self.store.get_parent_location(self.split_test_usage_key),
                self.vert_usage_key,
            )

        # Create content experiment inside group A and set it's group configuration.
        resp = self.create_xblock(
            category="split_test", parent_usage_key=split_test.children[0]
        )
        child_split_test_usage_key = self.response_usage_key(resp)
        self.client.ajax_post(
            reverse_usage_url("xblock_handler", child_split_test_usage_key),
            data={"metadata": {"user_partition_id": str(0)}},
        )
        child_split_test = self.get_item_from_modulestore(self.split_test_usage_key)

        # Try to move content experiment further down the level to a child group A nested inside main group A.
        response = self._move_component(
            self.split_test_usage_key, child_split_test.children[0]
        )
        self.assertEqual(response.status_code, 400)  # noqa: PT009
        response = json.loads(response.content.decode("utf-8"))

        self.assertEqual(response["error"], "You can not move an item into it's child.")  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            self.store.get_parent_location(self.split_test_usage_key),
            self.vert_usage_key,
        )

    def test_move_invalid_source_index(self):
        """
        Test moving an item to an invalid index.
        """
        target_index = "test_index"
        parent_loc = self.store.get_parent_location(self.html_usage_key)
        response = self._move_component(
            self.html_usage_key, self.vert2_usage_key, target_index
        )
        self.assertEqual(response.status_code, 400)  # noqa: PT009
        response = json.loads(response.content.decode("utf-8"))

        error = f"You must provide target_index ({target_index}) as an integer."
        self.assertEqual(response["error"], error)  # noqa: PT009
        new_parent_loc = self.store.get_parent_location(self.html_usage_key)
        self.assertEqual(new_parent_loc, parent_loc)  # noqa: PT009

    def test_move_no_target_locator(self):
        """
        Test move an item without specifying the target location.
        """
        data = {"move_source_locator": str(self.html_usage_key)}
        with self.assertRaises(InvalidKeyError):  # noqa: PT027
            self.client.patch(
                reverse("xblock_handler"),
                json.dumps(data),
                content_type="application/json",
            )

    def test_no_move_source_locator(self):
        """
        Test patch request without providing a move source locator.
        """
        response = self.client.patch(reverse("xblock_handler"))
        self.assertEqual(response.status_code, 400)  # noqa: PT009
        response = json.loads(response.content.decode("utf-8"))
        self.assertEqual(  # noqa: PT009
            response["error"],
            "Patch request did not recognise any parameters to handle.",
        )

    def _verify_validation_message(
        self, message, expected_message, expected_message_type
    ):
        """
        Verify that the validation message has the expected validation message and type.
        """
        self.assertEqual(message.text, expected_message)  # noqa: PT009
        self.assertEqual(message.type, expected_message_type)  # noqa: PT009

    def test_move_component_nonsensical_access_restriction_validation(self):
        """
        Test that moving a component with non-contradicting access
        restrictions into a unit that has contradicting access
        restrictions brings up the nonsensical access validation
        message and that the message does not show up when moved
        into a unit where the component's access settings do not
        contradict the unit's access settings.
        """
        group1 = self.course.user_partitions[0].groups[0]
        group2 = self.course.user_partitions[0].groups[1]
        vert1 = self.store.get_item(self.vert_usage_key)
        vert2 = self.store.get_item(self.vert2_usage_key)
        html = self.store.get_item(self.html_usage_key)

        # Inject mock partition service as obtaining the course from the draft modulestore
        # (which is the default for these tests) does not work.
        partitions_service = MockPartitionService(
            self.course,
            course_id=self.course.id,
        )
        html.runtime._services["partitions"] = partitions_service  # pylint: disable=protected-access

        # Set access settings so html will contradict vert2 when moved into that unit
        vert1.group_access = {self.course.user_partitions[0].id: [group2.id]}
        vert2.group_access = {self.course.user_partitions[0].id: [group1.id]}
        html.group_access = {self.course.user_partitions[0].id: [group2.id]}
        vert1 = self.store.update_item(vert1, self.user.id)
        vert2 = self.store.update_item(vert2, self.user.id)
        html = self.store.update_item(html, self.user.id)

        # Verify that there is no warning when html is in a non contradicting unit
        validation = html.validate()
        self.assertEqual(len(validation.messages), 0)  # noqa: PT009

        # Now move it and confirm that the html component has been moved into vertical 2
        self.assert_move_item(self.html_usage_key, self.vert2_usage_key)
        html.parent = self.vert2_usage_key
        html = self.store.update_item(html, self.user.id)
        validation = html.validate()
        self.assertEqual(len(validation.messages), 1)  # noqa: PT009
        self._verify_validation_message(
            validation.messages[0],
            NONSENSICAL_ACCESS_RESTRICTION,
            ValidationMessage.ERROR,
        )

        # Move the html component back and confirm that the warning is gone again
        self.assert_move_item(self.html_usage_key, self.vert_usage_key)
        html.parent = self.vert_usage_key
        html = self.store.update_item(html, self.user.id)
        validation = html.validate()
        self.assertEqual(len(validation.messages), 0)  # noqa: PT009

    @patch("cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers.log")
    def test_move_logging(self, mock_logger):
        """
        Test logging when an item is successfully moved.

        Arguments:
            mock_logger (object):  A mock logger object.
        """
        insert_at = 0
        self.assert_move_item(self.html_usage_key, self.vert2_usage_key, insert_at)
        mock_logger.info.assert_called_with(
            "MOVE: %s moved from %s to %s at %d index",
            str(self.html_usage_key),
            str(self.vert_usage_key),
            str(self.vert2_usage_key),
            insert_at,
        )

    def test_move_and_discard_changes(self):
        """
        Verifies that discard changes operation brings moved component back to source location and removes the component
        from target location.
        """
        self.setup_course()

        old_parent_loc = self.store.get_parent_location(self.html_usage_key)

        # Check that old_parent_loc is not yet published.
        self.assertFalse(  # noqa: PT009
            self.store.has_item(
                old_parent_loc, revision=ModuleStoreEnum.RevisionOption.published_only
            )
        )

        # Publish old_parent_loc unit
        self.client.ajax_post(
            reverse_usage_url("xblock_handler", old_parent_loc),
            data={"publish": "make_public"},
        )

        # Check that old_parent_loc is now published.
        self.assertTrue(  # noqa: PT009
            self.store.has_item(
                old_parent_loc, revision=ModuleStoreEnum.RevisionOption.published_only
            )
        )
        self.assertFalse(self.store.has_changes(self.store.get_item(old_parent_loc)))  # noqa: PT009

        # Move component html_usage_key in vert2_usage_key
        self.assert_move_item(self.html_usage_key, self.vert2_usage_key)

        # Check old_parent_loc becomes in draft mode now.
        self.assertTrue(self.store.has_changes(self.store.get_item(old_parent_loc)))  # noqa: PT009

        # Now discard changes in old_parent_loc
        self.client.ajax_post(
            reverse_usage_url("xblock_handler", old_parent_loc),
            data={"publish": "discard_changes"},
        )

        # Check that old_parent_loc now is reverted to publish. Changes discarded, html_usage_key moved back.
        self.assertTrue(  # noqa: PT009
            self.store.has_item(
                old_parent_loc, revision=ModuleStoreEnum.RevisionOption.published_only
            )
        )
        self.assertFalse(self.store.has_changes(self.store.get_item(old_parent_loc)))  # noqa: PT009

        # Now source item should be back in the old parent.
        source_item = self.get_item_from_modulestore(self.html_usage_key)
        self.assertEqual(source_item.parent, old_parent_loc)  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            self.store.get_parent_location(self.html_usage_key), source_item.parent
        )

        # Also, check that item is not present in target parent but in source parent
        target_parent = self.get_item_from_modulestore(self.vert2_usage_key)
        source_parent = self.get_item_from_modulestore(old_parent_loc)
        self.assertIn(self.html_usage_key, source_parent.children)  # noqa: PT009
        self.assertNotIn(self.html_usage_key, target_parent.children)  # noqa: PT009

    def test_move_item_not_found(self):
        """
        Test that an item not found exception raised when an item is not found when getting the item.
        """
        self.setup_course()

        data = {
            "move_source_locator": str(
                self.usage_key.course_key.make_usage_key("html", "html_test")
            ),
            "parent_locator": str(self.vert2_usage_key),
        }
        with self.assertRaises(ItemNotFoundError):  # noqa: PT027
            self.client.patch(
                reverse("xblock_handler"),
                json.dumps(data),
                content_type="application/json",
            )


class TestDuplicateItemWithAsides(ItemTest, DuplicateHelper):
    """
    Test the duplicate method for blocks with asides.
    """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        """Creates the test course structure and a few components to 'duplicate'."""
        super().setUp()
        # Create a parent chapter
        resp = self.create_xblock(parent_usage_key=self.usage_key, category="chapter")
        self.chapter_usage_key = self.response_usage_key(resp)

        # create a sequential containing a problem and an html component
        resp = self.create_xblock(
            parent_usage_key=self.chapter_usage_key, category="sequential"
        )
        self.seq_usage_key = self.response_usage_key(resp)

        # create problem and an html component
        resp = self.create_xblock(
            parent_usage_key=self.seq_usage_key,
            category="problem",
        )
        self.problem_usage_key = self.response_usage_key(resp)

        resp = self.create_xblock(parent_usage_key=self.seq_usage_key, category="html")
        self.html_usage_key = self.response_usage_key(resp)

    @XBlockAside.register_temp_plugin(AsideTest, "test_aside")
    @patch(
        "xmodule.modulestore.split_mongo.runtime.SplitModuleStoreRuntime.applicable_aside_types",
        lambda self, block: ["test_aside"],
    )
    def test_duplicate_equality_with_asides(self):
        """
        Tests that a duplicated xblock aside is identical to the original
        """

        def create_aside(usage_key, block_type):
            """
            Helper function to create aside
            """
            item = self.get_item_from_modulestore(usage_key)

            key_store = DictKeyValueStore()
            field_data = KvsFieldData(key_store)
            runtime = TestRuntime(services={"field-data": field_data})

            def_id = runtime.id_generator.create_definition(block_type)
            usage_id = runtime.id_generator.create_usage(def_id)

            aside = AsideTest(
                scope_ids=ScopeIds("user", block_type, def_id, usage_id),
                runtime=runtime,
            )
            aside.field11 = "%s_new_value11" % block_type  # noqa: UP031
            aside.field12 = "%s_new_value12" % block_type  # noqa: UP031
            aside.field13 = "%s_new_value13" % block_type  # noqa: UP031

            self.store.update_item(item, self.user.id, asides=[aside])

        create_aside(self.html_usage_key, "html")
        create_aside(self.problem_usage_key, "problem")
        create_aside(self.seq_usage_key, "seq")
        create_aside(self.chapter_usage_key, "chapter")

        self._duplicate_and_verify(
            self.problem_usage_key, self.seq_usage_key, check_asides=True
        )
        self._duplicate_and_verify(
            self.html_usage_key, self.seq_usage_key, check_asides=True
        )
        self._duplicate_and_verify(
            self.seq_usage_key, self.chapter_usage_key, check_asides=True
        )


class TestEditItemSetup(ItemTest):
    """
    Setup for xblock update tests.
    """

    def setUp(self):
        """Creates the test course structure and a couple problems to 'edit'."""
        super().setUp()
        # create a chapter
        display_name = "chapter created"
        resp = self.create_xblock(display_name=display_name, category="chapter")
        chap_usage_key = self.response_usage_key(resp)

        # create 2 sequentials
        resp = self.create_xblock(
            parent_usage_key=chap_usage_key, category="sequential"
        )
        self.seq_usage_key = self.response_usage_key(resp)
        self.seq_update_url = reverse_usage_url("xblock_handler", self.seq_usage_key)

        resp = self.create_xblock(
            parent_usage_key=chap_usage_key, category="sequential"
        )
        self.seq2_usage_key = self.response_usage_key(resp)
        self.seq2_update_url = reverse_usage_url("xblock_handler", self.seq2_usage_key)

        # create problem w/ boilerplate
        resp = self.create_xblock(
            parent_usage_key=self.seq_usage_key,
            category="problem",
        )
        self.problem_usage_key = self.response_usage_key(resp)
        self.problem_update_url = reverse_usage_url(
            "xblock_handler", self.problem_usage_key
        )

        self.course_update_url = reverse_usage_url("xblock_handler", self.usage_key)


@ddt.ddt
class TestEditItem(TestEditItemSetup):
    """
    Test xblock update.
    """

    def test_delete_field(self):
        """
        Sending null in for a field 'deletes' it
        """
        self.client.ajax_post(
            self.problem_update_url, data={"metadata": {"rerandomize": "onreset"}}
        )
        problem = self.get_item_from_modulestore(self.problem_usage_key)
        self.assertEqual(problem.rerandomize, 'onreset')  # noqa: PT009
        self.client.ajax_post(
            self.problem_update_url, data={"metadata": {"rerandomize": None}}
        )
        problem = self.get_item_from_modulestore(self.problem_usage_key)
        self.assertEqual(problem.rerandomize, 'never')  # noqa: PT009

    def test_date_fields(self):
        """
        Test setting due & start dates on sequential
        """
        sequential = self.get_item_from_modulestore(self.seq_usage_key)
        self.assertIsNone(sequential.due)  # noqa: PT009
        self.client.ajax_post(
            self.seq_update_url, data={"metadata": {"due": "2010-11-22T04:00Z"}}
        )
        sequential = self.get_item_from_modulestore(self.seq_usage_key)
        self.assertEqual(sequential.due, datetime(2010, 11, 22, 4, 0, tzinfo=UTC))  # noqa: PT009
        self.client.ajax_post(
            self.seq_update_url, data={"metadata": {"start": "2010-09-12T14:00Z"}}
        )
        sequential = self.get_item_from_modulestore(self.seq_usage_key)
        self.assertEqual(sequential.due, datetime(2010, 11, 22, 4, 0, tzinfo=UTC))  # noqa: PT009
        self.assertEqual(sequential.start, datetime(2010, 9, 12, 14, 0, tzinfo=UTC))  # noqa: PT009

    @ddt.data(
        "1000-01-01T00:00Z",
        "0150-11-21T14:45Z",
        "1899-12-31T23:59Z",
        "1789-06-06T22:10Z",
        "1001-01-15T19:32Z",
    )
    def test_xblock_due_date_validity(self, date):
        """
        Test due date for the subsection is not pre-1900
        """
        self.client.ajax_post(self.seq_update_url, data={"metadata": {"due": date}})
        sequential = self.get_item_from_modulestore(self.seq_usage_key)
        xblock_info = create_xblock_info(
            sequential,
            include_child_info=True,
            include_children_predicate=ALWAYS,
            user=self.user,
        )
        # Both display and actual value should be None
        self.assertEqual(xblock_info["due_date"], "")  # noqa: PT009
        self.assertIsNone(xblock_info["due"])  # noqa: PT009

    def test_update_generic_fields(self):
        new_display_name = "New Display Name"
        new_max_attempts = 2
        self.client.ajax_post(
            self.problem_update_url,
            data={
                "fields": {
                    "display_name": new_display_name,
                    "max_attempts": new_max_attempts,
                }
            },
        )
        problem = self.get_item_from_modulestore(self.problem_usage_key)
        self.assertEqual(problem.display_name, new_display_name)  # noqa: PT009
        self.assertEqual(problem.max_attempts, new_max_attempts)  # noqa: PT009

    def test_delete_child(self):
        """
        Test deleting a child.
        """
        # Create 2 children of main course.
        resp_1 = self.create_xblock(display_name="child 1", category="chapter")
        resp_2 = self.create_xblock(display_name="child 2", category="chapter")
        chapter1_usage_key = self.response_usage_key(resp_1)
        chapter2_usage_key = self.response_usage_key(resp_2)

        course = self.get_item_from_modulestore(self.usage_key)
        self.assertIn(chapter1_usage_key, course.children)  # noqa: PT009
        self.assertIn(chapter2_usage_key, course.children)  # noqa: PT009

        # Remove one child from the course.
        resp = self.client.delete(
            reverse_usage_url("xblock_handler", chapter1_usage_key)
        )
        self.assertEqual(resp.status_code, 204)  # noqa: PT009

        # Verify that the child is removed.
        course = self.get_item_from_modulestore(self.usage_key)
        self.assertNotIn(chapter1_usage_key, course.children)  # noqa: PT009
        self.assertIn(chapter2_usage_key, course.children)  # noqa: PT009

    def test_reorder_children(self):
        """
        Test reordering children that can be in the draft store.
        """
        # Create 2 child units and re-order them. There was a bug about @draft getting added
        # to the IDs.
        unit_1_resp = self.create_xblock(
            parent_usage_key=self.seq_usage_key, category="vertical"
        )
        unit_2_resp = self.create_xblock(
            parent_usage_key=self.seq_usage_key, category="vertical"
        )
        unit1_usage_key = self.response_usage_key(unit_1_resp)
        unit2_usage_key = self.response_usage_key(unit_2_resp)

        # The sequential already has a child defined in the setUp (a problem).
        # Children must be on the sequential to reproduce the original bug,
        # as it is important that the parent (sequential) NOT be in the draft store.
        children = self.get_item_from_modulestore(self.seq_usage_key).children
        self.assertEqual(unit1_usage_key, children[1])  # noqa: PT009
        self.assertEqual(unit2_usage_key, children[2])  # noqa: PT009

        resp = self.client.ajax_post(
            self.seq_update_url,
            data={
                "children": [
                    str(self.problem_usage_key),
                    str(unit2_usage_key),
                    str(unit1_usage_key),
                ]
            },
        )
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

        children = self.get_item_from_modulestore(self.seq_usage_key).children
        self.assertEqual(self.problem_usage_key, children[0])  # noqa: PT009
        self.assertEqual(unit1_usage_key, children[2])  # noqa: PT009
        self.assertEqual(unit2_usage_key, children[1])  # noqa: PT009

    def test_move_parented_child(self):
        """
        Test moving a child from one Section to another
        """
        unit_1_key = self.response_usage_key(
            self.create_xblock(
                parent_usage_key=self.seq_usage_key,
                category="vertical",
                display_name="unit 1",
            )
        )
        unit_2_key = self.response_usage_key(
            self.create_xblock(
                parent_usage_key=self.seq2_usage_key,
                category="vertical",
                display_name="unit 2",
            )
        )

        # move unit 1 from sequential1 to sequential2
        resp = self.client.ajax_post(
            self.seq2_update_url, data={"children": [str(unit_1_key), str(unit_2_key)]}
        )
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

        # verify children
        self.assertListEqual(  # noqa: PT009
            self.get_item_from_modulestore(self.seq2_usage_key).children,
            [unit_1_key, unit_2_key],
        )
        self.assertListEqual(  # noqa: PT009
            self.get_item_from_modulestore(self.seq_usage_key).children,
            [self.problem_usage_key],  # problem child created in setUp
        )

    def test_move_orphaned_child_error(self):
        """
        Test moving an orphan returns an error
        """
        unit_1_key = self.store.create_item(
            self.user.id, self.course_key, "vertical", "unit1"
        ).location

        # adding orphaned unit 1 should return an error
        resp = self.client.ajax_post(
            self.seq2_update_url, data={"children": [str(unit_1_key)]}
        )
        self.assertContains(
            resp, "Invalid data, possibly caused by concurrent authors", status_code=400
        )

        # verify children
        self.assertListEqual(  # noqa: PT009
            self.get_item_from_modulestore(self.seq2_usage_key).children, []
        )

    def test_move_child_creates_orphan_error(self):
        """
        Test creating an orphan returns an error
        """
        unit_1_key = self.response_usage_key(
            self.create_xblock(
                parent_usage_key=self.seq2_usage_key,
                category="vertical",
                display_name="unit 1",
            )
        )
        unit_2_key = self.response_usage_key(
            self.create_xblock(
                parent_usage_key=self.seq2_usage_key,
                category="vertical",
                display_name="unit 2",
            )
        )

        # remove unit 2 should return an error
        resp = self.client.ajax_post(
            self.seq2_update_url, data={"children": [str(unit_1_key)]}
        )
        self.assertContains(
            resp, "Invalid data, possibly caused by concurrent authors", status_code=400
        )

        # verify children
        self.assertListEqual(  # noqa: PT009
            self.get_item_from_modulestore(self.seq2_usage_key).children,
            [unit_1_key, unit_2_key],
        )

    def _is_location_published(self, location):
        """
        Returns whether or not the item with given location has a published version.
        """
        return modulestore().has_item(
            location, revision=ModuleStoreEnum.RevisionOption.published_only
        )

    def _verify_published_with_no_draft(self, location):
        """
        Verifies the item with given location has a published version and no draft (unpublished changes).
        """
        self.assertTrue(self._is_location_published(location))  # noqa: PT009
        self.assertFalse(modulestore().has_changes(modulestore().get_item(location)))  # noqa: PT009

    def _verify_published_with_draft(self, location):
        """
        Verifies the item with given location has a published version and also a draft version (unpublished changes).
        """
        self.assertTrue(self._is_location_published(location))  # noqa: PT009
        self.assertTrue(modulestore().has_changes(modulestore().get_item(location)))  # noqa: PT009

    def test_make_public(self):
        """Test making a private problem public (publishing it)."""
        # When the problem is first created, it is only in draft (because of its category).
        self.assertFalse(self._is_location_published(self.problem_usage_key))  # noqa: PT009
        self.client.ajax_post(self.problem_update_url, data={"publish": "make_public"})
        self._verify_published_with_no_draft(self.problem_usage_key)

    def test_make_draft(self):
        """Test creating a draft version of a public problem."""
        self._make_draft_content_different_from_published()

    def test_revert_to_published(self):
        """Test reverting draft content to published"""
        self._make_draft_content_different_from_published()
        self.client.ajax_post(
            self.problem_update_url, data={"publish": "discard_changes"}
        )
        self._verify_published_with_no_draft(self.problem_usage_key)
        published = modulestore().get_item(
            self.problem_usage_key,
            revision=ModuleStoreEnum.RevisionOption.published_only,
        )
        self.assertIsNone(published.due)  # noqa: PT009

    def test_republish(self):
        """Test republishing an item."""
        new_display_name = "New Display Name"

        # When the problem is first created, it is only in draft (because of its category).
        self.assertFalse(self._is_location_published(self.problem_usage_key))  # noqa: PT009

        # Republishing when only in draft will update the draft but not cause a public item to be created.
        self.client.ajax_post(
            self.problem_update_url,
            data={
                "publish": "republish",
                "metadata": {"display_name": new_display_name},
            },
        )
        self.assertFalse(self._is_location_published(self.problem_usage_key))  # noqa: PT009
        draft = self.get_item_from_modulestore(self.problem_usage_key)
        self.assertEqual(draft.display_name, new_display_name)  # noqa: PT009

        # Publish the item
        self.client.ajax_post(self.problem_update_url, data={"publish": "make_public"})

        # Now republishing should update the published version
        new_display_name_2 = "New Display Name 2"
        self.client.ajax_post(
            self.problem_update_url,
            data={
                "publish": "republish",
                "metadata": {"display_name": new_display_name_2},
            },
        )
        self._verify_published_with_no_draft(self.problem_usage_key)
        published = modulestore().get_item(
            self.problem_usage_key,
            revision=ModuleStoreEnum.RevisionOption.published_only,
        )
        self.assertEqual(published.display_name, new_display_name_2)  # noqa: PT009

    def test_direct_only_categories_not_republished(self):
        """Verify that republish is ignored for items in DIRECT_ONLY_CATEGORIES"""
        # Create a vertical child with published and unpublished versions.
        # If the parent sequential is not re-published, then the child problem should also not be re-published.
        resp = self.create_xblock(
            parent_usage_key=self.seq_usage_key,
            display_name="vertical",
            category="vertical",
        )
        vertical_usage_key = self.response_usage_key(resp)
        vertical_update_url = reverse_usage_url("xblock_handler", vertical_usage_key)
        self.client.ajax_post(vertical_update_url, data={"publish": "make_public"})
        self.client.ajax_post(
            vertical_update_url, data={"metadata": {"display_name": "New Display Name"}}
        )

        self._verify_published_with_draft(self.seq_usage_key)
        self.client.ajax_post(self.seq_update_url, data={"publish": "republish"})
        self._verify_published_with_draft(self.seq_usage_key)

    def _make_draft_content_different_from_published(self):
        """
        Helper method to create different draft and published versions of a problem.
        """
        # Make problem public.
        self.client.ajax_post(self.problem_update_url, data={"publish": "make_public"})
        self._verify_published_with_no_draft(self.problem_usage_key)
        published = modulestore().get_item(
            self.problem_usage_key,
            revision=ModuleStoreEnum.RevisionOption.published_only,
        )  # pylint: disable=line-too-long

        # Update the draft version and check that published is different.
        self.client.ajax_post(
            self.problem_update_url, data={"metadata": {"due": "2077-10-10T04:00Z"}}
        )
        updated_draft = self.get_item_from_modulestore(self.problem_usage_key)
        self.assertEqual(updated_draft.due, datetime(2077, 10, 10, 4, 0, tzinfo=UTC))  # noqa: PT009
        self.assertIsNone(published.due)  # noqa: PT009
        # Fetch the published version again to make sure the due date is still unset.
        published = modulestore().get_item(
            published.location, revision=ModuleStoreEnum.RevisionOption.published_only
        )
        self.assertIsNone(published.due)  # noqa: PT009

    def test_make_public_with_update(self):
        """Update a problem and make it public at the same time."""
        self.client.ajax_post(
            self.problem_update_url,
            data={"metadata": {"due": "2077-10-10T04:00Z"}, "publish": "make_public"},
        )
        published = self.get_item_from_modulestore(self.problem_usage_key)
        self.assertEqual(published.due, datetime(2077, 10, 10, 4, 0, tzinfo=UTC))  # noqa: PT009

    def test_published_and_draft_contents_with_update(self):
        """Create a draft and publish it then modify the draft and check that published content is not modified"""

        # Make problem public.
        self.client.ajax_post(self.problem_update_url, data={"publish": "make_public"})
        self._verify_published_with_no_draft(self.problem_usage_key)
        published = modulestore().get_item(
            self.problem_usage_key,
            revision=ModuleStoreEnum.RevisionOption.published_only,
        )

        # Now make a draft
        self.client.ajax_post(
            self.problem_update_url,
            data={
                "id": str(self.problem_usage_key),
                "metadata": {},
                "data": "<p>Problem content draft.</p>",
            },
        )

        # Both published and draft content should be different
        draft = self.get_item_from_modulestore(self.problem_usage_key)
        self.assertNotEqual(draft.data, published.data)  # noqa: PT009

        # Get problem by 'xblock_handler'
        view_url = reverse_usage_url(
            "xblock_view_handler", self.problem_usage_key, {"view_name": STUDENT_VIEW}
        )
        resp = self.client.get(view_url, HTTP_ACCEPT="application/json")
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

        # Activate the editing view
        view_url = reverse_usage_url(
            "xblock_view_handler", self.problem_usage_key, {"view_name": STUDIO_VIEW}
        )
        resp = self.client.get(view_url, HTTP_ACCEPT="application/json")
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

        # Both published and draft content should still be different
        draft = self.get_item_from_modulestore(self.problem_usage_key)
        self.assertNotEqual(draft.data, published.data)  # noqa: PT009
        # Fetch the published version again to make sure the data is correct.
        published = modulestore().get_item(
            published.location, revision=ModuleStoreEnum.RevisionOption.published_only
        )
        self.assertNotEqual(draft.data, published.data)  # noqa: PT009

    def test_publish_states_of_nested_xblocks(self):
        """Test publishing of a unit page containing a nested xblock"""

        resp = self.create_xblock(
            parent_usage_key=self.seq_usage_key,
            display_name="Test Unit",
            category="vertical",
        )
        unit_usage_key = self.response_usage_key(resp)
        resp = self.create_xblock(parent_usage_key=unit_usage_key, category="wrapper")
        wrapper_usage_key = self.response_usage_key(resp)
        resp = self.create_xblock(parent_usage_key=wrapper_usage_key, category="html")
        html_usage_key = self.response_usage_key(resp)

        # The unit and its children should be private initially
        unit_update_url = reverse_usage_url("xblock_handler", unit_usage_key)
        self.assertFalse(self._is_location_published(unit_usage_key))  # noqa: PT009
        self.assertFalse(self._is_location_published(html_usage_key))  # noqa: PT009

        # Make the unit public and verify that the problem is also made public
        resp = self.client.ajax_post(unit_update_url, data={"publish": "make_public"})
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        self._verify_published_with_no_draft(unit_usage_key)
        self._verify_published_with_no_draft(html_usage_key)

    def test_field_value_errors(self):
        """
        Test that if the user's input causes a ValueError on an XBlock field,
        we provide a friendly error message back to the user.
        """
        response = self.create_xblock(
            parent_usage_key=self.seq_usage_key, category="video"
        )
        video_usage_key = self.response_usage_key(response)
        update_url = reverse_usage_url("xblock_handler", video_usage_key)

        response = self.client.ajax_post(
            update_url,
            data={
                "id": str(video_usage_key),
                "metadata": {
                    "saved_video_position": "Not a valid relative time",
                },
            },
        )
        self.assertEqual(response.status_code, 400)  # noqa: PT009
        parsed = json.loads(response.content.decode("utf-8"))
        self.assertIn("error", parsed)  # noqa: PT009
        self.assertIn(  # noqa: PT009
            "Incorrect RelativeTime value", parsed["error"]
        )  # See xmodule/fields.py


class TestEditSplitModule(ItemTest):
    """
    Tests around editing instances of the split_test block.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory()

        self.first_user_partition_group_1 = Group(
            str(MINIMUM_UNUSED_PARTITION_ID + 1), "alpha"
        )
        self.first_user_partition_group_2 = Group(
            str(MINIMUM_UNUSED_PARTITION_ID + 2), "beta"
        )
        self.first_user_partition = UserPartition(
            MINIMUM_UNUSED_PARTITION_ID,
            "first_partition",
            "First Partition",
            [self.first_user_partition_group_1, self.first_user_partition_group_2],
        )

        # There is a test point below (test_create_groups) that purposefully wants the group IDs
        # of the 2 partitions to overlap (which is not something that normally happens).
        self.second_user_partition_group_1 = Group(
            str(MINIMUM_UNUSED_PARTITION_ID + 1), "Group 1"
        )
        self.second_user_partition_group_2 = Group(
            str(MINIMUM_UNUSED_PARTITION_ID + 2), "Group 2"
        )
        self.second_user_partition_group_3 = Group(
            str(MINIMUM_UNUSED_PARTITION_ID + 3), "Group 3"
        )
        self.second_user_partition = UserPartition(
            MINIMUM_UNUSED_PARTITION_ID + 10,
            "second_partition",
            "Second Partition",
            [
                self.second_user_partition_group_1,
                self.second_user_partition_group_2,
                self.second_user_partition_group_3,
            ],
        )
        self.course.user_partitions = [
            self.first_user_partition,
            self.second_user_partition,
        ]
        self.store.update_item(self.course, self.user.id)
        root_usage_key = self._create_vertical()
        resp = self.create_xblock(
            category="split_test", parent_usage_key=root_usage_key
        )
        self.split_test_usage_key = self.response_usage_key(resp)
        self.split_test_update_url = reverse_usage_url(
            "xblock_handler", self.split_test_usage_key
        )
        self.request_factory = RequestFactory()
        self.request = self.request_factory.get("/dummy-url")
        self.request.user = self.user

    def _update_partition_id(self, partition_id):
        """
        Helper method that sets the user_partition_id to the supplied value.

        The updated split_test instance is returned.
        """
        self.client.ajax_post(
            self.split_test_update_url,
            # Even though user_partition_id is Scope.content, it will get saved by the Studio editor as
            # metadata. The code in block.py will update the field correctly, even though it is not the
            # expected scope.
            data={"metadata": {"user_partition_id": str(partition_id)}},
        )

        # Verify the partition_id was saved.
        split_test = self.get_item_from_modulestore(self.split_test_usage_key)
        self.assertEqual(partition_id, split_test.user_partition_id)  # noqa: PT009
        return split_test

    def _assert_children(self, expected_number):
        """
        Verifies the number of children of the split_test instance.
        """
        split_test = self.get_item_from_modulestore(self.split_test_usage_key)
        self.assertEqual(expected_number, len(split_test.children))  # noqa: PT009
        return split_test

    def test_create_groups(self):
        """
        Test that verticals are created for the configuration groups when
        a spit test block is edited.
        """
        split_test = self.get_item_from_modulestore(self.split_test_usage_key)
        # Initially, no user_partition_id is set, and the split_test has no children.
        self.assertEqual(-1, split_test.user_partition_id)  # noqa: PT009
        self.assertEqual(0, len(split_test.children))  # noqa: PT009

        # Set the user_partition_id to match the first user_partition.
        split_test = self._update_partition_id(self.first_user_partition.id)

        # Verify that child verticals have been set to match the groups
        self.assertEqual(2, len(split_test.children))  # noqa: PT009
        vertical_0 = self.get_item_from_modulestore(split_test.children[0])
        vertical_1 = self.get_item_from_modulestore(split_test.children[1])
        self.assertEqual("vertical", vertical_0.category)  # noqa: PT009
        self.assertEqual("vertical", vertical_1.category)  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            "Group ID " + str(MINIMUM_UNUSED_PARTITION_ID + 1), vertical_0.display_name
        )
        self.assertEqual(  # noqa: PT009
            "Group ID " + str(MINIMUM_UNUSED_PARTITION_ID + 2), vertical_1.display_name
        )

        # Verify that the group_id_to_child mapping is correct.
        self.assertEqual(2, len(split_test.group_id_to_child))  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            vertical_0.location,
            split_test.group_id_to_child[str(self.first_user_partition_group_1.id)],
        )
        self.assertEqual(  # noqa: PT009
            vertical_1.location,
            split_test.group_id_to_child[str(self.first_user_partition_group_2.id)],
        )

    def test_split_xblock_info_group_name(self):
        """
        Test that concise outline for split test component gives display name as group name.
        """
        split_test = self.get_item_from_modulestore(self.split_test_usage_key)
        # Initially, no user_partition_id is set, and the split_test has no children.
        self.assertEqual(split_test.user_partition_id, -1)  # noqa: PT009
        self.assertEqual(len(split_test.children), 0)  # noqa: PT009
        # Set the user_partition_id to match the first user_partition.
        split_test = self._update_partition_id(self.first_user_partition.id)
        # Verify that child verticals have been set to match the groups
        self.assertEqual(len(split_test.children), 2)  # noqa: PT009

        # Get xblock outline
        xblock_info = create_xblock_info(
            split_test,
            is_concise=True,
            include_child_info=True,
            include_children_predicate=lambda xblock: xblock.has_children,
            course=self.course,
            user=self.request.user,
        )
        self.assertEqual(  # noqa: PT009
            xblock_info["child_info"]["children"][0]["display_name"], "alpha"
        )
        self.assertEqual(  # noqa: PT009
            xblock_info["child_info"]["children"][1]["display_name"], "beta"
        )

    def test_change_user_partition_id(self):
        """
        Test what happens when the user_partition_id is changed to a different groups
        group configuration.
        """
        # Set to first group configuration.
        split_test = self._update_partition_id(self.first_user_partition.id)
        self.assertEqual(2, len(split_test.children))  # noqa: PT009
        initial_vertical_0_location = split_test.children[0]
        initial_vertical_1_location = split_test.children[1]

        # Set to second group configuration
        split_test = self._update_partition_id(self.second_user_partition.id)
        # We don't remove existing children.
        self.assertEqual(5, len(split_test.children))  # noqa: PT009
        self.assertEqual(initial_vertical_0_location, split_test.children[0])  # noqa: PT009
        self.assertEqual(initial_vertical_1_location, split_test.children[1])  # noqa: PT009
        vertical_0 = self.get_item_from_modulestore(split_test.children[2])
        vertical_1 = self.get_item_from_modulestore(split_test.children[3])
        vertical_2 = self.get_item_from_modulestore(split_test.children[4])

        # Verify that the group_id_to child mapping is correct.
        self.assertEqual(3, len(split_test.group_id_to_child))  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            vertical_0.location,
            split_test.group_id_to_child[str(self.second_user_partition_group_1.id)],
        )
        self.assertEqual(  # noqa: PT009
            vertical_1.location,
            split_test.group_id_to_child[str(self.second_user_partition_group_2.id)],
        )
        self.assertEqual(  # noqa: PT009
            vertical_2.location,
            split_test.group_id_to_child[str(self.second_user_partition_group_3.id)],
        )
        self.assertNotEqual(initial_vertical_0_location, vertical_0.location)  # noqa: PT009
        self.assertNotEqual(initial_vertical_1_location, vertical_1.location)  # noqa: PT009

    def test_change_same_user_partition_id(self):
        """
        Test that nothing happens when the user_partition_id is set to the same value twice.
        """
        # Set to first group configuration.
        split_test = self._update_partition_id(self.first_user_partition.id)
        self.assertEqual(2, len(split_test.children))  # noqa: PT009
        initial_group_id_to_child = split_test.group_id_to_child

        # Set again to first group configuration.
        split_test = self._update_partition_id(self.first_user_partition.id)
        self.assertEqual(2, len(split_test.children))  # noqa: PT009
        self.assertEqual(initial_group_id_to_child, split_test.group_id_to_child)  # noqa: PT009

    def test_change_non_existent_user_partition_id(self):
        """
        Test that nothing happens when the user_partition_id is set to a value that doesn't exist.

        The user_partition_id will be updated, but children and group_id_to_child map will not change.
        """
        # Set to first group configuration.
        split_test = self._update_partition_id(self.first_user_partition.id)
        self.assertEqual(2, len(split_test.children))  # noqa: PT009
        initial_group_id_to_child = split_test.group_id_to_child

        # Set to an group configuration that doesn't exist.
        split_test = self._update_partition_id(-50)
        self.assertEqual(2, len(split_test.children))  # noqa: PT009
        self.assertEqual(initial_group_id_to_child, split_test.group_id_to_child)  # noqa: PT009

    def test_add_groups(self):
        """
        Test the "fix up behavior" when groups are missing (after a group is added to a group configuration).

        This test actually belongs over in common, but it relies on a mutable modulestore.
        TODO: move tests that can go over to common after the mixed modulestore work is done.  # pylint: disable=fixme
        """
        # Set to first group configuration.
        split_test = self._update_partition_id(self.first_user_partition.id)

        # Add a group to the first group configuration.
        new_group_id = "1002"
        split_test.user_partitions = [
            UserPartition(
                self.first_user_partition.id,
                "first_partition",
                "First Partition",
                [
                    self.first_user_partition_group_1,
                    self.first_user_partition_group_2,
                    Group(new_group_id, "pie"),
                ],
            )
        ]
        self.store.update_item(split_test, self.user.id)

        # group_id_to_child and children have not changed yet.
        split_test = self._assert_children(2)
        group_id_to_child = split_test.group_id_to_child.copy()
        self.assertEqual(2, len(group_id_to_child))  # noqa: PT009

        # SplitModuleStoreRuntime is used in tests.
        # SplitModuleStoreRuntime doesn't have user service, that's needed for
        # SplitTestBlock. So, in this line of code we add this service manually.
        split_test.runtime._services["user"] = DjangoXBlockUserService(  # pylint: disable=protected-access
            self.user
        )

        # Call add_missing_groups method to add the missing group.
        split_test.add_missing_groups(self.request)
        split_test = self._assert_children(3)
        self.assertNotEqual(group_id_to_child, split_test.group_id_to_child)  # noqa: PT009
        group_id_to_child = split_test.group_id_to_child
        self.assertEqual(split_test.children[2], group_id_to_child[new_group_id])  # noqa: PT009

        # Call add_missing_groups again -- it should be a no-op.
        split_test.add_missing_groups(self.request)
        split_test = self._assert_children(3)
        self.assertEqual(group_id_to_child, split_test.group_id_to_child)  # noqa: PT009


@ddt.ddt
class TestComponentHandler(TestCase):
    """Tests for component handler api"""

    def setUp(self):
        super().setUp()

        self.request_factory = RequestFactory()

        patcher = patch("cms.djangoapps.contentstore.views.component.modulestore")
        self.modulestore = patcher.start()
        self.addCleanup(patcher.stop)

        # component_handler calls modulestore.get_item to get the requested xBlock.
        # Here, we mock the return value of modulestore.get_item so it can be used to mock the handler
        # of the xBlock.
        self.block = self.modulestore.return_value.get_item.return_value

        self.usage_key = BlockUsageLocator(
            CourseLocator("dummy_org", "dummy_course", "dummy_run"),
            "dummy_category",
            "dummy_name",
        )
        self.usage_key_string = str(self.usage_key)
        self.user = StaffFactory(
            course_key=CourseLocator("dummy_org", "dummy_course", "dummy_run")
        )
        self.request = self.request_factory.get("/dummy-url")
        self.request.user = self.user

    def test_invalid_handler(self):
        self.block.handle.side_effect = NoSuchHandlerError

        with self.assertRaises(Http404):  # noqa: PT027
            component_handler(self.request, self.usage_key_string, "invalid_handler")

    @ddt.data("GET", "POST", "PUT", "DELETE")
    def test_request_method(self, method):
        def check_handler(
            handler, request, suffix
        ):  # pylint: disable=unused-argument
            self.assertEqual(request.method, method)  # noqa: PT009
            return Response()

        self.block.handle = check_handler

        # Have to use the right method to create the request to get the HTTP method that we want
        req_factory_method = getattr(self.request_factory, method.lower())
        request = req_factory_method("/dummy-url")
        request.user = self.user
        component_handler(request, self.usage_key_string, "dummy_handler")

    @ddt.data(200, 404, 500)
    def test_response_code(self, status_code):
        def create_response(
            handler, request, suffix
        ):  # pylint: disable=unused-argument
            return Response(status_code=status_code)

        self.block.handle = create_response

        self.assertEqual(  # noqa: PT009
            component_handler(
                self.request, self.usage_key_string, "dummy_handler"
            ).status_code,
            status_code,
        )

    @patch("cms.djangoapps.contentstore.views.component.log")
    def test_submit_studio_edits_checks_author_permission(self, mock_logger):
        """
        Test logging a user without studio write permissions attempts to run a studio submit handler..

        Arguments:
            mock_logger (object):  A mock logger object.
        """

        def create_response(
            handler, request, suffix
        ):  # pylint: disable=unused-argument
            """create dummy response"""
            return Response(status_code=200)

        self.request.user = UserFactory()
        mock_handler = "dummy_handler"

        self.block.handle = create_response

        with patch(
            "cms.djangoapps.contentstore.views.component.is_xblock_aside",
            return_value=False,
        ), patch(
            "cms.djangoapps.contentstore.views.component.webob_to_django_response"
        ):
            component_handler(self.request, self.usage_key_string, mock_handler)

        mock_logger.warning.assert_called_with(
            "%s does not have have studio write permissions on course: %s. write operations not performed on %r",
            self.request.user.id,
            UsageKey.from_string(self.usage_key_string).course_key,
            mock_handler,
        )

    @ddt.data(
        (True, True),
        (False, False),
    )
    @ddt.unpack
    def test_aside(self, is_xblock_aside, is_get_aside_called):
        """
        test get_aside_from_xblock called
        """

        def create_response(
            handler, request, suffix
        ):  # pylint: disable=unused-argument
            """create dummy response"""
            return Response(status_code=200)

        def get_usage_key():
            """return usage key"""
            return (
                str(AsideUsageKeyV2(self.usage_key, "aside"))
                if is_xblock_aside
                else self.usage_key_string
            )

        self.block.handle = create_response

        with patch(
            "cms.djangoapps.contentstore.views.component.is_xblock_aside",
            return_value=is_xblock_aside,
        ), patch(
            "cms.djangoapps.contentstore.views.component.get_aside_from_xblock"
        ) as mocked_get_aside_from_xblock, patch(
            "cms.djangoapps.contentstore.views.component.webob_to_django_response"
        ) as mocked_webob_to_django_response:
            component_handler(self.request, get_usage_key(), "dummy_handler")
            assert mocked_webob_to_django_response.called is True

        assert mocked_get_aside_from_xblock.called is is_get_aside_called


class TestComponentTemplates(CourseTestCase):
    """
    Unit tests for the generation of the component templates for a course.
    """

    def setUp(self):
        super().setUp()
        # Advanced Module support levels.
        XBlockStudioConfiguration.objects.create(
            name="poll", enabled=True, support_level="fs"
        )
        XBlockStudioConfiguration.objects.create(
            name="survey", enabled=True, support_level="ps"
        )
        XBlockStudioConfiguration.objects.create(
            name="annotatable", enabled=True, support_level="us"
        )
        # Basic component support levels.
        XBlockStudioConfiguration.objects.create(
            name="html", enabled=True, support_level="fs"
        )
        XBlockStudioConfiguration.objects.create(
            name="discussion", enabled=True, support_level="ps"
        )
        XBlockStudioConfiguration.objects.create(
            name="problem", enabled=True, support_level="us"
        )
        XBlockStudioConfiguration.objects.create(
            name="video", enabled=True, support_level="us"
        )
        # ORA Block has it's own category.
        XBlockStudioConfiguration.objects.create(
            name="openassessment", enabled=True, support_level="us"
        )
        # Library Content block has its own category.
        XBlockStudioConfiguration.objects.create(
            name="library_content", enabled=True, support_level="fs"
        )
        # XBlock masquerading as a problem
        XBlockStudioConfiguration.objects.create(
            name="drag-and-drop-v2", enabled=True, support_level="fs"
        )
        XBlockStudioConfiguration.objects.create(
            name="staffgradedxblock", enabled=True, support_level="us"
        )

        self.templates = get_component_templates(self.course)

        self.default_advanced_modules_titles = sorted([
            "Google Calendar",
            "Google Document",
            "LTI Consumer",
            "Poll",
            "Content Experiment",
            "Survey",
            "Word cloud",
        ])

    def get_templates_of_type(self, template_type):
        """
        Returns the templates for the specified type, or None if none is found.
        """
        template_dict = self._get_template_dict_of_type(template_type)
        return template_dict.get("templates") if template_dict else None

    def get_display_name_of_type(self, template_type):
        """
        Returns the display name for the specified type, or None if none found.
        """
        template_dict = self._get_template_dict_of_type(template_type)
        return template_dict.get("display_name") if template_dict else None

    def _get_template_dict_of_type(self, template_type):
        """
        Returns a dictionary of values for a category type.
        """
        return next(
            (
                template
                for template in self.templates
                if template.get("type") == template_type
            ),
            None,
        )

    def get_template(self, templates, display_name):
        """
        Returns the template which has the specified display name.
        """
        return next(
            (
                template
                for template in templates
                if template.get("display_name") == display_name
            ),
            None,
        )

    def test_basic_components(self):
        """
        Test the handling of the basic component templates.
        """
        self._verify_basic_component("discussion", "Discussion")
        self._verify_basic_component("video", "Video")
        self._verify_basic_component("openassessment", "Peer Assessment Only", True, 5)
        self._verify_basic_component_display_name("discussion", "Discussion")
        self._verify_basic_component_display_name("video", "Video")
        self._verify_basic_component_display_name("openassessment", "Open Response")
        self.assertGreater(len(self.get_templates_of_type("library")), 0)  # noqa: PT009
        self.assertGreater(len(self.get_templates_of_type("html")), 0)  # noqa: PT009
        self.assertGreater(len(self.get_templates_of_type("problem")), 0)  # noqa: PT009

        # Check for default advanced modules
        advanced_templates = self.get_templates_of_type("advanced")
        advanced_module_keys = [t['category'] for t in advanced_templates]
        self.assertCountEqual(advanced_module_keys, DEFAULT_ADVANCED_MODULES)  # noqa: PT009

        # Now fully disable video through XBlockConfiguration
        XBlockConfiguration.objects.create(name="video", enabled=False)
        self.templates = get_component_templates(self.course)
        self.assertIsNone(self.get_templates_of_type("video"))  # noqa: PT009

    def test_basic_components_support_levels(self):
        """
        Test that support levels can be set on basic component templates.
        """
        XBlockStudioConfigurationFlag.objects.create(enabled=True)
        self.templates = get_component_templates(self.course)
        self._verify_basic_component("discussion", "Discussion", "ps")
        self.assertEqual([], self.get_templates_of_type("video"))  # noqa: PT009
        supported_problem_templates = [
            {
                "boilerplate_name": None,
                "category": "drag-and-drop-v2",
                "display_name": "Drag and Drop",
                "hinted": False,
                "support_level": "fs",
                "tab": "advanced",
            }
        ]
        self.assertEqual(  # noqa: PT009
            supported_problem_templates, self.get_templates_of_type("problem")
        )

        self.course.allow_unsupported_xblocks = True
        self.templates = get_component_templates(self.course)
        self._verify_basic_component("video", "Video", "us")

        # Now fully disable video through XBlockConfiguration
        XBlockConfiguration.objects.create(name="video", enabled=False)
        self.templates = get_component_templates(self.course)
        self.assertIsNone(self.get_templates_of_type("video"))  # noqa: PT009

    def test_advanced_components(self):
        """
        Test the handling of advanced component templates.
        """
        self.course.advanced_modules.append("done")
        EXPECTED_ADVANCED_MODULES_LENGTH = len(DEFAULT_ADVANCED_MODULES) + 1
        self.templates = get_component_templates(self.course)
        advanced_templates = self.get_templates_of_type("advanced")
        self.assertEqual(len(advanced_templates), EXPECTED_ADVANCED_MODULES_LENGTH)  # noqa: PT009
        done_template = advanced_templates[0]
        self.assertEqual(done_template.get("category"), "done")  # noqa: PT009
        self.assertEqual(done_template.get("display_name"), "Completion")  # noqa: PT009
        self.assertIsNone(done_template.get("boilerplate_name", None))  # noqa: PT009

        # Verify that components are not added twice
        self.course.advanced_modules.append("video")
        self.course.advanced_modules.append("drag-and-drop-v2")
        # Already defined advanced modules
        self.course.advanced_modules.append("poll")
        self.course.advanced_modules.append("google-document")
        self.course.advanced_modules.append("survey")

        self.templates = get_component_templates(self.course)
        advanced_templates = self.get_templates_of_type("advanced")
        self.assertEqual(len(advanced_templates), EXPECTED_ADVANCED_MODULES_LENGTH)  # noqa: PT009
        only_template = advanced_templates[0]
        self.assertNotEqual(only_template.get("category"), "video")  # noqa: PT009
        self.assertNotEqual(only_template.get("category"), "drag-and-drop-v2")  # noqa: PT009
        self.assertNotEqual(only_template.get("category"), "poll")  # noqa: PT009
        self.assertNotEqual(only_template.get("category"), "google-document")  # noqa: PT009
        self.assertNotEqual(only_template.get("category"), "survey")  # noqa: PT009

        # Now fully disable done through XBlockConfiguration
        XBlockConfiguration.objects.create(name="done", enabled=False)
        self.templates = get_component_templates(self.course)
        self.assertTrue((not any(item.get("category") == "done" for item in self.get_templates_of_type("advanced"))))  # noqa: PT009, UP034  # pylint: disable=line-too-long

    def test_deprecated_no_advance_component_button(self):
        """
        Test that there will be no `Advanced` button on unit page if xblocks have disabled
        Studio support given that they are the only modules in `Advanced Module List`
        """
        # Update poll and survey to have "enabled=False".
        XBlockStudioConfiguration.objects.create(
            name="poll", enabled=False, support_level="fs"
        )
        XBlockStudioConfiguration.objects.create(
            name="survey", enabled=False, support_level="fs"
        )
        XBlockStudioConfigurationFlag.objects.create(enabled=True)
        self.course.advanced_modules.extend(["poll", "survey"])
        templates = get_component_templates(self.course)
        button_names = [template["display_name"] for template in templates]
        self.assertNotIn("Advanced", button_names)  # noqa: PT009

    def test_cannot_create_deprecated_problems(self):
        """
        Test that xblocks that have Studio support disabled do not show on the "new component" menu.
        """
        # Update poll to have "enabled=False".
        XBlockStudioConfiguration.objects.create(
            name="poll", enabled=False, support_level="fs"
        )
        XBlockStudioConfigurationFlag.objects.create(enabled=True)
        self.course.advanced_modules.extend(["annotatable", "poll", "survey"])
        # Annotatable doesn't show up because it is unsupported (in test setUp).
        self._verify_advanced_xblocks(["Survey"], ["ps"])

        # Now enable unsupported components.
        self.course.allow_unsupported_xblocks = True
        self._verify_advanced_xblocks(["Annotation", "Survey"], ["us", "ps"])

        # Now disable Annotatable completely through XBlockConfiguration
        XBlockConfiguration.objects.create(name="annotatable", enabled=False)
        self._verify_advanced_xblocks(["Survey"], ["ps"])

    def test_create_support_level_flag_off(self):
        """
        Test that we can create any advanced xblock (that isn't completely disabled through
        XBlockConfiguration) if XBlockStudioConfigurationFlag is False.
        """
        XBlockStudioConfigurationFlag.objects.create(enabled=False)
        self.course.advanced_modules.extend(["annotatable", "done"])
        expected_xblocks = ["Annotation", "Completion"] + self.default_advanced_modules_titles
        self._verify_advanced_xblocks(expected_xblocks, [True] * len(expected_xblocks))

    def test_xblock_masquerading_as_problem(self):
        """
        Test the integration of xblocks masquerading as problems.
        """

        def get_xblock_problem(label):
            """
            Helper method to get the template of any XBlock in the problems list
            """
            self.templates = get_component_templates(self.course)
            problem_templates = self.get_templates_of_type("problem")
            return self.get_template(problem_templates, label)

        def verify_staffgradedxblock_present(support_level):
            """
            Helper method to verify that staffgradedxblock template is present
            """
            sgp = get_xblock_problem("Staff Graded Points")
            self.assertIsNotNone(sgp)  # noqa: PT009
            self.assertEqual(sgp.get("category"), "staffgradedxblock")  # noqa: PT009
            self.assertEqual(sgp.get("support_level"), support_level)  # noqa: PT009

        def verify_dndv2_present(support_level):
            """
            Helper method to verify that DnDv2 template is present
            """
            dndv2 = get_xblock_problem("Drag and Drop")
            self.assertIsNotNone(dndv2)  # noqa: PT009
            self.assertEqual(dndv2.get("category"), "drag-and-drop-v2")  # noqa: PT009
            self.assertEqual(dndv2.get("support_level"), support_level)  # noqa: PT009

        verify_dndv2_present(True)
        verify_staffgradedxblock_present(True)

        # Now enable XBlockStudioConfigurationFlag. The staffgradedxblock block is marked
        # unsupported, so will no longer show up, but DnDv2 will continue to appear.
        XBlockStudioConfigurationFlag.objects.create(enabled=True)
        self.assertIsNone(get_xblock_problem("Staff Graded Points"))  # noqa: PT009
        self.assertIsNotNone(get_xblock_problem("Drag and Drop"))  # noqa: PT009

        # Now allow unsupported components.
        self.course.allow_unsupported_xblocks = True
        verify_staffgradedxblock_present("us")
        verify_dndv2_present("fs")

        # Now disable the blocks completely through XBlockConfiguration
        XBlockConfiguration.objects.create(name="staffgradedxblock", enabled=False)
        XBlockConfiguration.objects.create(name="drag-and-drop-v2", enabled=False)
        self.assertIsNone(get_xblock_problem("Staff Graded Points"))  # noqa: PT009
        self.assertIsNone(get_xblock_problem("Drag and Drop"))  # noqa: PT009

    def test_discussion_button_present_no_provider(self):
        """
        Test the Discussion button present when no discussion provider configured for course
        """
        templates = get_component_templates(self.course)
        button_names = [template["display_name"] for template in templates]
        assert "Discussion" in button_names

    def test_discussion_button_present_legacy_provider(self):
        """
        Test the Discussion button present when legacy discussion provider configured for course
        """
        course_key = self.course.location.course_key

        # Create a discussion configuration with discussion provider set as legacy
        DiscussionsConfiguration.objects.create(
            context_key=course_key, enabled=True, provider_type="legacy"
        )

        templates = get_component_templates(self.course)
        button_names = [template["display_name"] for template in templates]
        assert "Discussion" in button_names

    def test_discussion_button_absent_non_legacy_provider(self):
        """
        Test the Discussion button not present when non-legacy discussion provider configured for course
        """
        course_key = self.course.location.course_key

        # Create a discussion configuration with discussion provider set as legacy
        DiscussionsConfiguration.objects.create(
            context_key=course_key, enabled=False, provider_type="ed-discuss"
        )

        templates = get_component_templates(self.course)
        button_names = [template["display_name"] for template in templates]
        assert "Discussion" not in button_names

    def _verify_advanced_xblocks(self, expected_xblocks, expected_support_levels):
        """
        Verify the names of the advanced xblocks showing in the "new component" menu.
        """
        templates = get_component_templates(self.course)
        button_names = [template["display_name"] for template in templates]
        self.assertIn("Advanced", button_names)  # noqa: PT009
        self.assertEqual(len(templates[-1]["templates"]), len(expected_xblocks))  # noqa: PT009
        template_display_names = [
            template["display_name"] for template in templates[-1]["templates"]
        ]
        self.assertEqual(template_display_names, expected_xblocks)  # noqa: PT009
        template_support_levels = [
            template["support_level"] for template in templates[-1]["templates"]
        ]
        self.assertEqual(template_support_levels, expected_support_levels)  # noqa: PT009

    def _verify_basic_component(
        self, component_type, display_name, support_level=True, no_of_templates=1
    ):
        """
        Verify the display name and support level of basic components (that have no boilerplates).
        """
        templates = self.get_templates_of_type(component_type)
        self.assertEqual(no_of_templates, len(templates))  # noqa: PT009
        self.assertEqual(display_name, templates[0]["display_name"])  # noqa: PT009
        self.assertEqual(support_level, templates[0]["support_level"])  # noqa: PT009

    def _verify_basic_component_display_name(self, component_type, display_name):
        """
        Verify the display name of basic components.
        """
        component_display_name = self.get_display_name_of_type(component_type)
        self.assertEqual(display_name, component_display_name)  # noqa: PT009


@ddt.ddt
class TestXBlockInfo(ItemTest):
    """
    Unit tests for XBlock's outline handling.
    """

    def setUp(self):
        super().setUp()
        user_id = self.user.id
        self.chapter = BlockFactory.create(
            parent_location=self.course.location,
            category="chapter",
            display_name="Week 1",
            user_id=user_id,
            highlights=["highlight"],
        )
        self.sequential = BlockFactory.create(
            parent_location=self.chapter.location,
            category="sequential",
            display_name="Lesson 1",
            user_id=user_id,
        )
        self.vertical = BlockFactory.create(
            parent_location=self.sequential.location,
            category="vertical",
            display_name="Unit 1",
            user_id=user_id,
        )
        self.video = BlockFactory.create(
            parent_location=self.vertical.location,
            category="video",
            display_name="My Video",
            user_id=user_id,
        )

    def test_json_responses(self):
        outline_url = reverse_usage_url("xblock_outline_handler", self.usage_key)
        resp = self.client.get(outline_url, HTTP_ACCEPT="application/json")
        json_response = json.loads(resp.content.decode("utf-8"))
        self.validate_course_xblock_info(json_response, course_outline=True)

    def test_xblock_outline_handler_mongo_calls(self):
        course = CourseFactory.create()
        chapter = BlockFactory.create(
            parent_location=course.location, category='chapter', display_name='Week 1'
        )
        outline_url = reverse_usage_url('xblock_outline_handler', chapter.location)
        with check_mongo_calls(3):
            self.client.get(outline_url, HTTP_ACCEPT='application/json')

        sequential = BlockFactory.create(
            parent_location=chapter.location, category='sequential', display_name='Sequential 1'
        )

        BlockFactory.create(
            parent_location=sequential.location, category='vertical', display_name='Vertical 1'
        )
        # calls should be same after adding two new children for split only.
        with check_mongo_calls(3):
            self.client.get(outline_url, HTTP_ACCEPT='application/json')

    def test_entrance_exam_chapter_xblock_info(self):
        chapter = BlockFactory.create(
            parent_location=self.course.location,
            category="chapter",
            display_name="Entrance Exam",
            user_id=self.user.id,
            is_entrance_exam=True,
        )
        chapter = modulestore().get_item(chapter.location)
        xblock_info = create_xblock_info(
            chapter,
            include_child_info=True,
            include_children_predicate=ALWAYS,
        )
        # entrance exam chapter should not be deletable, draggable and childAddable.
        actions = xblock_info["actions"]
        self.assertEqual(actions["deletable"], False)  # noqa: PT009
        self.assertEqual(actions["draggable"], False)  # noqa: PT009
        self.assertEqual(actions["childAddable"], False)  # noqa: PT009
        self.assertEqual(xblock_info["display_name"], "Entrance Exam")  # noqa: PT009
        self.assertIsNone(xblock_info.get("is_header_visible", None))  # noqa: PT009

    def test_none_entrance_exam_chapter_xblock_info(self):
        chapter = BlockFactory.create(
            parent_location=self.course.location,
            category="chapter",
            display_name="Test Chapter",
            user_id=self.user.id,
        )
        chapter = modulestore().get_item(chapter.location)
        xblock_info = create_xblock_info(
            chapter,
            include_child_info=True,
            include_children_predicate=ALWAYS,
        )

        # chapter should be deletable, draggable and childAddable if not an entrance exam.
        actions = xblock_info["actions"]
        self.assertEqual(actions["deletable"], True)  # noqa: PT009
        self.assertEqual(actions["draggable"], True)  # noqa: PT009
        self.assertEqual(actions["childAddable"], True)  # noqa: PT009
        # chapter xblock info should not contains the key of 'is_header_visible'.
        self.assertIsNone(xblock_info.get("is_header_visible", None))  # noqa: PT009

    def test_entrance_exam_sequential_xblock_info(self):
        chapter = BlockFactory.create(
            parent_location=self.course.location,
            category="chapter",
            display_name="Entrance Exam",
            user_id=self.user.id,
            is_entrance_exam=True,
            in_entrance_exam=True,
        )

        subsection = BlockFactory.create(
            parent_location=chapter.location,
            category="sequential",
            display_name="Subsection - Entrance Exam",
            user_id=self.user.id,
            in_entrance_exam=True,
        )
        subsection = modulestore().get_item(subsection.location)
        xblock_info = create_xblock_info(
            subsection, include_child_info=True, include_children_predicate=ALWAYS
        )
        # in case of entrance exam subsection, header should be hidden.
        self.assertEqual(xblock_info["is_header_visible"], False)  # noqa: PT009
        self.assertEqual(xblock_info["display_name"], "Subsection - Entrance Exam")  # noqa: PT009

    def test_none_entrance_exam_sequential_xblock_info(self):
        subsection = BlockFactory.create(
            parent_location=self.chapter.location,
            category="sequential",
            display_name="Subsection - Exam",
            user_id=self.user.id,
        )
        subsection = modulestore().get_item(subsection.location)
        xblock_info = create_xblock_info(
            subsection,
            include_child_info=True,
            include_children_predicate=ALWAYS,
            parent_xblock=self.chapter,
        )
        # sequential xblock info should not contains the key of 'is_header_visible'.
        self.assertIsNone(xblock_info.get("is_header_visible", None))  # noqa: PT009

    def test_chapter_xblock_info(self):
        chapter = modulestore().get_item(self.chapter.location)
        xblock_info = create_xblock_info(
            chapter,
            include_child_info=True,
            include_children_predicate=ALWAYS,
        )
        self.validate_chapter_xblock_info(xblock_info)

    def test_sequential_xblock_info(self):
        sequential = modulestore().get_item(self.sequential.location)
        xblock_info = create_xblock_info(
            sequential,
            include_child_info=True,
            include_children_predicate=ALWAYS,
        )
        self.validate_sequential_xblock_info(xblock_info)

    def test_vertical_xblock_info(self):
        vertical = modulestore().get_item(self.vertical.location)

        xblock_info = create_xblock_info(
            vertical,
            include_child_info=True,
            include_children_predicate=ALWAYS,
            include_ancestor_info=True,
            user=self.user,
        )
        add_container_page_publishing_info(vertical, xblock_info)
        self.validate_vertical_xblock_info(xblock_info)

    def test_component_xblock_info(self):
        video = modulestore().get_item(self.video.location)
        xblock_info = create_xblock_info(
            video, include_child_info=True, include_children_predicate=ALWAYS
        )
        self.validate_component_xblock_info(xblock_info)

    def test_validate_start_date(self):
        """
        Validate if start-date year is less than 1900 reset the date to DEFAULT_START_DATE.
        """
        course = CourseFactory.create()
        chapter = BlockFactory.create(
            parent_location=course.location, category='chapter', display_name='Week 1'
        )

        chapter.start = datetime(year=1899, month=1, day=1, tzinfo=UTC)

        xblock_info = create_xblock_info(
            chapter,
            include_child_info=True,
            include_children_predicate=ALWAYS,
            include_ancestor_info=True,
            user=self.user
        )

        self.assertEqual(xblock_info['start'], DEFAULT_START_DATE.strftime('%Y-%m-%dT%H:%M:%SZ'))  # noqa: PT009

    def test_highlights_enabled(self):
        self.course.highlights_enabled_for_messaging = True
        self.store.update_item(self.course, None)
        course_xblock_info = create_xblock_info(self.course)
        self.assertTrue(course_xblock_info["highlights_enabled_for_messaging"])  # noqa: PT009

    def test_xblock_public_video_sharing_enabled(self):
        """
        Public video sharing is included in the xblock info when enable.
        """
        self.course.video_sharing_options = "all-on"
        with patch.object(PUBLIC_VIDEO_SHARE, "is_enabled", return_value=True):
            self.store.update_item(self.course, None)
            course_xblock_info = create_xblock_info(self.course)
            self.assertTrue(course_xblock_info["video_sharing_enabled"])  # noqa: PT009
            self.assertEqual(course_xblock_info["video_sharing_options"], "all-on")  # noqa: PT009

    def test_xblock_public_video_sharing_disabled(self):
        """
        Public video sharing not is included in the xblock info when disabled.
        """
        self.course.video_sharing_options = "arbitrary"
        with patch.object(PUBLIC_VIDEO_SHARE, "is_enabled", return_value=False):
            self.store.update_item(self.course, None)
            course_xblock_info = create_xblock_info(self.course)
            self.assertNotIn("video_sharing_enabled", course_xblock_info)  # noqa: PT009
            self.assertNotIn("video_sharing_options", course_xblock_info)  # noqa: PT009

    def validate_course_xblock_info(
        self, xblock_info, has_child_info=True, course_outline=False
    ):
        """
        Validate that the xblock info is correct for the test course.
        """
        self.assertEqual(xblock_info["category"], "course")  # noqa: PT009
        self.assertEqual(xblock_info["id"], str(self.course.location))  # noqa: PT009
        self.assertEqual(xblock_info["display_name"], self.course.display_name)  # noqa: PT009
        self.assertTrue(xblock_info["published"])  # noqa: PT009
        self.assertFalse(xblock_info["highlights_enabled_for_messaging"])  # noqa: PT009

        # Finally, validate the entire response for consistency
        self.validate_xblock_info_consistency(
            xblock_info, has_child_info=has_child_info, course_outline=course_outline
        )

    def validate_chapter_xblock_info(self, xblock_info, has_child_info=True):
        """
        Validate that the xblock info is correct for the test chapter.
        """
        self.assertEqual(xblock_info["category"], "chapter")  # noqa: PT009
        self.assertEqual(xblock_info["id"], str(self.chapter.location))  # noqa: PT009
        self.assertEqual(xblock_info["display_name"], "Week 1")  # noqa: PT009
        self.assertTrue(xblock_info["published"])  # noqa: PT009
        self.assertIsNone(xblock_info.get("edited_by", None))  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            xblock_info["course_graders"],
            ["Homework", "Lab", "Midterm Exam", "Final Exam"],
        )
        self.assertEqual(xblock_info["start"], DEFAULT_START_DATE.strftime('%Y-%m-%dT%H:%M:%SZ'))  # noqa: PT009
        self.assertEqual(xblock_info["graded"], False)  # noqa: PT009
        self.assertEqual(xblock_info["due"], None)  # noqa: PT009
        self.assertEqual(xblock_info["format"], None)  # noqa: PT009
        self.assertEqual(xblock_info["highlights"], self.chapter.highlights)  # noqa: PT009
        self.assertTrue(xblock_info["highlights_enabled"])  # noqa: PT009

        # Finally, validate the entire response for consistency
        self.validate_xblock_info_consistency(
            xblock_info, has_child_info=has_child_info
        )

    def validate_sequential_xblock_info(self, xblock_info, has_child_info=True):
        """
        Validate that the xblock info is correct for the test sequential.
        """
        self.assertEqual(xblock_info["category"], "sequential")  # noqa: PT009
        self.assertEqual(xblock_info["id"], str(self.sequential.location))  # noqa: PT009
        self.assertEqual(xblock_info["display_name"], "Lesson 1")  # noqa: PT009
        self.assertTrue(xblock_info["published"])  # noqa: PT009
        self.assertIsNone(xblock_info.get("edited_by", None))  # noqa: PT009

        # Finally, validate the entire response for consistency
        self.validate_xblock_info_consistency(
            xblock_info, has_child_info=has_child_info
        )

    def validate_vertical_xblock_info(self, xblock_info):
        """
        Validate that the xblock info is correct for the test vertical.
        """
        self.assertEqual(xblock_info["category"], "vertical")  # noqa: PT009
        self.assertEqual(xblock_info["id"], str(self.vertical.location))  # noqa: PT009
        self.assertEqual(xblock_info["display_name"], "Unit 1")  # noqa: PT009
        self.assertTrue(xblock_info["published"])  # noqa: PT009
        self.assertEqual(xblock_info["edited_by"], "testuser")  # noqa: PT009

        # Validate that the correct ancestor info has been included
        ancestor_info = xblock_info.get("ancestor_info", None)
        self.assertIsNotNone(ancestor_info)  # noqa: PT009
        ancestors = ancestor_info["ancestors"]
        self.assertEqual(len(ancestors), 3)  # noqa: PT009
        self.validate_sequential_xblock_info(ancestors[0], has_child_info=True)
        self.validate_chapter_xblock_info(ancestors[1], has_child_info=False)
        self.validate_course_xblock_info(ancestors[2], has_child_info=False)

        # Finally, validate the entire response for consistency
        self.validate_xblock_info_consistency(
            xblock_info, has_child_info=True, has_ancestor_info=True
        )

    def validate_component_xblock_info(self, xblock_info):
        """
        Validate that the xblock info is correct for the test component.
        """
        self.assertEqual(xblock_info["category"], "video")  # noqa: PT009
        self.assertEqual(xblock_info["id"], str(self.video.location))  # noqa: PT009
        self.assertEqual(xblock_info["display_name"], "My Video")  # noqa: PT009
        self.assertTrue(xblock_info["published"])  # noqa: PT009
        self.assertIsNone(xblock_info.get("edited_by", None))  # noqa: PT009

        # Finally, validate the entire response for consistency
        self.validate_xblock_info_consistency(xblock_info)

    def validate_xblock_info_consistency(
        self,
        xblock_info,
        has_ancestor_info=False,
        has_child_info=False,
        course_outline=False,
    ):
        """
        Validate that the xblock info is internally consistent.
        """
        self.assertIsNotNone(xblock_info["display_name"])  # noqa: PT009
        self.assertIsNotNone(xblock_info["id"])  # noqa: PT009
        self.assertIsNotNone(xblock_info["category"])  # noqa: PT009
        self.assertTrue(xblock_info["published"])  # noqa: PT009
        if has_ancestor_info:
            self.assertIsNotNone(xblock_info.get("ancestor_info", None))  # noqa: PT009
            ancestors = xblock_info["ancestor_info"]["ancestors"]
            for ancestor in xblock_info["ancestor_info"]["ancestors"]:
                self.validate_xblock_info_consistency(
                    ancestor,
                    has_child_info=(
                        ancestor == ancestors[0]
                    ),  # Only the direct ancestor includes children
                    course_outline=course_outline,
                )
        else:
            self.assertIsNone(xblock_info.get("ancestor_info", None))  # noqa: PT009
        if has_child_info:
            self.assertIsNotNone(xblock_info.get("child_info", None))  # noqa: PT009
            if xblock_info["child_info"].get("children", None):
                for child_response in xblock_info["child_info"]["children"]:
                    self.validate_xblock_info_consistency(
                        child_response,
                        has_child_info=(
                            child_response.get("child_info", None) is not None
                        ),
                        course_outline=course_outline,
                    )
        else:
            self.assertIsNone(xblock_info.get("child_info", None))  # noqa: PT009


@ddt.ddt
class TestXBlockOutlineHandlerAuthz(CourseAuthoringAuthzTestMixin, ItemTest):
    """
    Unit tests for xblock_outline_handler authorization functionality.
    """

    def setUp(self):
        super().setUp()
        user_id = self.user.id
        self.chapter = BlockFactory.create(
            parent_location=self.course.location,
            category="chapter",
            display_name="Week 1",
            user_id=user_id,
        )
        self.sequential = BlockFactory.create(
            parent_location=self.chapter.location,
            category="sequential",
            display_name="Lesson 1",
            user_id=user_id,
        )
        self.vertical = BlockFactory.create(
            parent_location=self.sequential.location,
            category="vertical",
            display_name="Unit 1",
            user_id=user_id,
        )
        # Assign COURSE_STAFF role to authorized_user for the course
        self.add_user_to_role_in_course(
            self.authorized_user,
            COURSE_STAFF.external_key,
            self.course.id
        )

    def test_authorized_user_gets_json_response(self):
        """
        Test that authorized user gets JSON response from xblock_outline_handler.
        """
        outline_url = reverse_usage_url("xblock_outline_handler", self.usage_key)

        self.client.login(username=self.authorized_user.username, password=self.password)
        resp = self.client.get(outline_url, HTTP_ACCEPT="application/json")

        assert resp.status_code == 200
        json_response = json.loads(resp.content.decode("utf-8"))
        assert "id" in json_response
        assert "display_name" in json_response
        assert "child_info" in json_response

    @ddt.data(
        COURSE_ADMIN.external_key,
        COURSE_AUDITOR.external_key,
        COURSE_EDITOR.external_key,
    )
    def test_other_course_roles_can_view_outline(self, role_key):
        """
        Test that course_admin, course_auditor, and course_editor roles
        can access the outline (all have COURSES_VIEW_COURSE).
        """
        role_user = UserFactory(password=self.password)
        self.add_user_to_role_in_course(role_user, role_key, self.course.id)

        outline_url = reverse_usage_url("xblock_outline_handler", self.usage_key)
        self.client.login(username=role_user.username, password=self.password)
        resp = self.client.get(outline_url, HTTP_ACCEPT="application/json")

        assert resp.status_code == 200

    def test_unauthorized_user_gets_permission_denied(self):
        """
        Test that unauthorized user gets 403 response from xblock_outline_handler.
        """
        outline_url = reverse_usage_url("xblock_outline_handler", self.usage_key)

        self.client.login(username=self.unauthorized_user.username, password=self.password)
        resp = self.client.get(outline_url, HTTP_ACCEPT="application/json")

        assert resp.status_code == 403

    def test_superuser_gets_json_response(self):
        """
        Test that superuser gets JSON response from xblock_outline_handler.
        """
        outline_url = reverse_usage_url("xblock_outline_handler", self.usage_key)

        self.client.login(username=self.super_user.username, password=self.password)
        resp = self.client.get(outline_url, HTTP_ACCEPT="application/json")

        assert resp.status_code == 200
        json_response = json.loads(resp.content.decode("utf-8"))
        assert "id" in json_response
        assert "display_name" in json_response
        assert "child_info" in json_response

    def test_staff_user_gets_json_response(self):
        """
        Test that staff user gets JSON response from xblock_outline_handler.
        """
        outline_url = reverse_usage_url("xblock_outline_handler", self.usage_key)

        self.client.login(username=self.staff_user.username, password=self.password)
        resp = self.client.get(outline_url, HTTP_ACCEPT="application/json")

        assert resp.status_code == 200
        json_response = json.loads(resp.content.decode("utf-8"))
        assert "id" in json_response
        assert "display_name" in json_response
        assert "child_info" in json_response

    def test_authorized_chapter_outline(self):
        """
        Test that authorized user can access chapter-level outline.
        """
        outline_url = reverse_usage_url("xblock_outline_handler", self.chapter.location)

        self.client.login(username=self.authorized_user.username, password=self.password)
        resp = self.client.get(outline_url, HTTP_ACCEPT="application/json")

        assert resp.status_code == 200
        json_response = json.loads(resp.content.decode("utf-8"))
        assert json_response["display_name"] == "Week 1"
        assert "child_info" in json_response
        # Verify that children are included (should have the sequential)
        children = json_response["child_info"]["children"]
        assert len(children) > 0
        assert children[0]["display_name"] == "Lesson 1"

    def test_unauthorized_chapter_outline(self):
        """
        Test that unauthorized user cannot access chapter-level outline.
        """
        outline_url = reverse_usage_url("xblock_outline_handler", self.chapter.location)

        self.client.login(username=self.unauthorized_user.username, password=self.password)
        resp = self.client.get(outline_url, HTTP_ACCEPT="application/json")

        assert resp.status_code == 403


class TestGetMetadataWithProblemDefaults(ModuleStoreTestCase):
    """
    Unit tests for _get_metadata_with_problem_defaults.

    The helper must inject a ``weight`` value (derived from ``max_score()``) for
    problem xblocks that have never had ``weight`` explicitly saved, while leaving
    every other combination untouched.
    """

    def _make_problem(self, **kwargs):
        """Create and return a problem xblock from the modulestore."""
        course = CourseFactory.create()
        block = BlockFactory.create(
            parent_location=course.location,
            category='problem',
            display_name='A Problem',
            **kwargs,
        )
        return modulestore().get_item(block.location)

    # ------------------------------------------------------------------
    # Problem blocks – weight absent from stored metadata
    # ------------------------------------------------------------------

    def test_problem_without_weight_adds_weight_from_max_score(self):
        """
        When weight is absent and max_score() > 0, it is injected into metadata.
        """
        xblock = self._make_problem()
        with patch.object(xblock, 'max_score', return_value=3.0):
            metadata = _get_metadata_with_problem_defaults(xblock)
        assert metadata.get('weight') == 3.0

    def test_problem_without_weight_max_score_zero_does_not_inject(self):
        """
        A zero max_score will not inject a weight.
        """
        xblock = self._make_problem()
        with patch.object(xblock, 'max_score', return_value=0):
            metadata = _get_metadata_with_problem_defaults(xblock)
        assert 'weight' not in metadata

    # ------------------------------------------------------------------
    # Problem blocks – weight already present in stored metadata
    # ------------------------------------------------------------------

    def test_problem_with_explicit_weight_is_preserved(self):
        """
        When weight is already explicitly set, it will not be overwritten.
        """
        xblock = self._make_problem(weight=5.0)
        with patch.object(xblock, 'max_score', return_value=2.0):
            metadata = _get_metadata_with_problem_defaults(xblock)
        assert metadata.get('weight') == 5.0

    # ------------------------------------------------------------------
    # Non-problem blocks
    # ------------------------------------------------------------------

    def test_non_problem_block_is_unmodified(self):
        """
        Non-problem blocks must pass through untouched even if a max_score
        method is available on them.
        """
        course = CourseFactory.create()
        video = BlockFactory.create(
            parent_location=course.location,
            category='video',
            display_name='A Video',
        )
        xblock = modulestore().get_item(video.location)
        metadata_before = dict(get_block_info(xblock).get('metadata', {}))
        metadata_result = _get_metadata_with_problem_defaults(xblock)
        assert metadata_result == metadata_before
        assert 'weight' not in metadata_result


@patch.dict("django.conf.settings.FEATURES", {"ENABLE_SPECIAL_EXAMS": True})
@ddt.ddt
class TestSpecialExamXBlockInfo(ItemTest):
    """
    Unit tests for XBlock outline handling, specific to special exam XBlocks.
    """

    patch_get_exam_configuration_dashboard_url = patch.object(
        item_module, "get_exam_configuration_dashboard_url", return_value="test_url"
    )
    patch_does_backend_support_onboarding = patch.object(
        item_module, "does_backend_support_onboarding", return_value=True
    )
    patch_get_exam_by_content_id_success = patch.object(
        item_module,
        "get_exam_by_content_id",
        return_value={"external_id": "test_external_id"},
    )
    patch_get_exam_by_content_id_not_found = patch.object(
        item_module,
        "get_exam_by_content_id",
        side_effect=ProctoredExamNotFoundException,
    )

    def setUp(self):
        super().setUp()
        user_id = self.user.id
        self.chapter = BlockFactory.create(
            parent_location=self.course.location,
            category="chapter",
            display_name="Week 1",
            user_id=user_id,
            highlights=["highlight"],
        )
        # get updated course
        self.course = self.store.get_item(self.course.location)
        self.course.enable_proctored_exams = True
        self.course.save()
        self.course = self.store.update_item(self.course, self.user.id)

    def test_proctoring_is_enabled_for_course(self):
        course = modulestore().get_item(self.course.location)
        xblock_info = create_xblock_info(
            course,
            include_child_info=True,
            include_children_predicate=ALWAYS,
        )
        # exam proctoring should be enabled and time limited.
        assert xblock_info["enable_proctored_exams"]

    @patch_get_exam_configuration_dashboard_url
    @patch_does_backend_support_onboarding
    @patch_get_exam_by_content_id_success
    def test_special_exam_xblock_info(
        self,
        mock_get_exam_by_content_id,
        _mock_does_backend_support_onboarding,  # noqa: PT019
        mock_get_exam_configuration_dashboard_url,
    ):
        sequential = BlockFactory.create(
            parent_location=self.chapter.location,
            category="sequential",
            display_name="Test Lesson 1",
            user_id=self.user.id,
            is_proctored_enabled=True,
            is_time_limited=True,
            default_time_limit_minutes=100,
            is_onboarding_exam=False,
        )
        sequential = modulestore().get_item(sequential.location)
        xblock_info = create_xblock_info(
            sequential,
            include_child_info=True,
            include_children_predicate=ALWAYS,
        )
        # exam proctoring should be enabled and time limited.
        assert xblock_info["is_proctored_exam"] is True
        assert xblock_info["was_exam_ever_linked_with_external"] is True
        assert xblock_info["is_time_limited"] is True
        assert xblock_info["default_time_limit_minutes"] == 100
        assert xblock_info["proctoring_exam_configuration_link"] == "test_url"
        assert xblock_info["supports_onboarding"] is True
        assert xblock_info["is_onboarding_exam"] is False
        assert xblock_info["show_review_rules"] is True
        mock_get_exam_configuration_dashboard_url.assert_called_with(
            self.course.id, xblock_info["id"]
        )

    @patch_get_exam_configuration_dashboard_url
    @patch_does_backend_support_onboarding
    @patch_get_exam_by_content_id_success
    @override_settings(
        PROCTORING_BACKENDS={
            "DEFAULT": "null",
            # By default "show_review_rules" is True unless you explicitly set it to False.
            "test_proctoring_provider": {"show_review_rules": False},
        }
    )
    def test_show_review_rules_xblock_info(
        self,
        mock_get_exam_by_content_id,
        _mock_does_backend_support_onboarding,  # noqa: PT019
        mock_get_exam_configuration_dashboard_url,
    ):
        # Set course.proctoring_provider to test_proctoring_provider
        self.course.proctoring_provider = 'test_proctoring_provider'
        sequential = BlockFactory.create(
            parent_location=self.chapter.location,
            category="sequential",
            display_name="Test Lesson 1",
            user_id=self.user.id,
            is_proctored_enabled=True,
            is_time_limited=True,
            default_time_limit_minutes=100,
            is_onboarding_exam=False,
        )
        sequential = modulestore().get_item(sequential.location)
        xblock_info = create_xblock_info(
            sequential,
            include_child_info=True,
            include_children_predicate=ALWAYS,
            course=self.course,
        )

        assert xblock_info["show_review_rules"] is False

    @patch_get_exam_configuration_dashboard_url
    @patch_does_backend_support_onboarding
    @patch_get_exam_by_content_id_success
    @ddt.data(
        ("lti_external", False, None),
        ("other_proctoring_backend", True, "test_url"),
    )
    @ddt.unpack
    def test_proctoring_values_correct_depending_on_lti_external(
        self,
        external_id,
        expected_supports_onboarding_value,
        expected_proctoring_link,
        mock_get_exam_by_content_id,
        mock_does_backend_support_onboarding,
        _mock_get_exam_configuration_dashboard_url,  # noqa: PT019
    ):
        sequential = BlockFactory.create(
            parent_location=self.chapter.location,
            category="sequential",
            display_name="Test Lesson 1",
            user_id=self.user.id,
            is_proctored_enabled=True,
            is_time_limited=True,
            default_time_limit_minutes=100,
            is_onboarding_exam=False,
        )

        # set course.proctoring_provider to lti_external
        self.course.proctoring_provider = external_id
        mock_get_exam_by_content_id.return_value = {"external_id": external_id}

        # mock_does_backend_support_onboarding returns True
        mock_does_backend_support_onboarding.return_value = True
        sequential = modulestore().get_item(sequential.location)
        xblock_info = create_xblock_info(
            sequential,
            include_child_info=True,
            include_children_predicate=ALWAYS,
            course=self.course,
        )
        assert xblock_info["supports_onboarding"] is expected_supports_onboarding_value
        assert xblock_info["proctoring_exam_configuration_link"] == expected_proctoring_link

    @patch_get_exam_configuration_dashboard_url
    @patch_does_backend_support_onboarding
    @patch_get_exam_by_content_id_success
    @ddt.data(
        ("test_external_id", True),
        (None, False),
    )
    @ddt.unpack
    def test_xblock_was_ever_linked_to_external_exam(
        self,
        external_id,
        expected_value,
        mock_get_exam_by_content_id,
        _mock_does_backend_support_onboarding_patch,  # noqa: PT019
        _mock_get_exam_configuration_dashboard_url,  # noqa: PT019
    ):
        sequential = BlockFactory.create(
            parent_location=self.chapter.location,
            category="sequential",
            display_name="Test Lesson 1",
            user_id=self.user.id,
            is_proctored_enabled=False,
            is_time_limited=False,
            is_onboarding_exam=False,
        )
        mock_get_exam_by_content_id.return_value = {"external_id": external_id}
        sequential = modulestore().get_item(sequential.location)
        xblock_info = create_xblock_info(
            sequential,
            include_child_info=True,
            include_children_predicate=ALWAYS,
        )
        assert xblock_info["was_exam_ever_linked_with_external"] is expected_value
        assert mock_get_exam_by_content_id.call_count == 1

    @patch_get_exam_configuration_dashboard_url
    @patch_does_backend_support_onboarding
    @patch_get_exam_by_content_id_not_found
    def test_xblock_was_never_linked_to_external_exam(
        self,
        mock_get_exam_by_content_id,
        _mock_does_backend_support_onboarding_patch,  # noqa: PT019
        _mock_get_exam_configuration_dashboard_url,  # noqa: PT019
    ):
        sequential = BlockFactory.create(
            parent_location=self.chapter.location,
            category="sequential",
            display_name="Test Lesson 1",
            user_id=self.user.id,
            is_proctored_enabled=False,
            is_time_limited=False,
            is_onboarding_exam=False,
        )
        sequential = modulestore().get_item(sequential.location)
        xblock_info = create_xblock_info(
            sequential,
            include_child_info=True,
            include_children_predicate=ALWAYS,
        )
        assert xblock_info["was_exam_ever_linked_with_external"] is False
        assert mock_get_exam_by_content_id.call_count == 1

    @patch_get_exam_configuration_dashboard_url
    @patch_does_backend_support_onboarding
    @patch_get_exam_by_content_id_success
    def test_special_exam_xblock_info_get_dashboard_error(
        self,
        mock_get_exam_by_content_id,
        _mock_does_backend_support_onboarding,  # noqa: PT019
        mock_get_exam_configuration_dashboard_url,
    ):
        sequential = BlockFactory.create(
            parent_location=self.chapter.location,
            category="sequential",
            display_name="Test Lesson 1",
            user_id=self.user.id,
            is_proctored_enabled=True,
            is_time_limited=True,
            default_time_limit_minutes=100,
            is_onboarding_exam=False,
        )
        sequential = modulestore().get_item(sequential.location)
        mock_get_exam_configuration_dashboard_url.side_effect = Exception("proctoring error")
        xblock_info = create_xblock_info(
            sequential,
            include_child_info=True,
            include_children_predicate=ALWAYS,
        )

        # no errors should be raised and proctoring_exam_configuration_link is None
        assert xblock_info["is_proctored_exam"] is True
        assert xblock_info["was_exam_ever_linked_with_external"] is True
        assert xblock_info["is_time_limited"] is True
        assert xblock_info["default_time_limit_minutes"] == 100
        assert xblock_info["proctoring_exam_configuration_link"] is None
        assert xblock_info["supports_onboarding"] is True
        assert xblock_info["is_onboarding_exam"] is False
        assert xblock_info["show_review_rules"] is True


class TestLibraryXBlockInfo(ModuleStoreTestCase):
    """
    Unit tests for XBlock Info for XBlocks in a content library
    """

    def setUp(self):
        super().setUp()
        user_id = self.user.id
        self.library = LibraryFactory.create()
        self.top_level_html = BlockFactory.create(
            parent_location=self.library.location,
            category="html",
            user_id=user_id,
            publish_item=False,
        )
        self.vertical = BlockFactory.create(
            parent_location=self.library.location,
            category="vertical",
            user_id=user_id,
            publish_item=False,
        )
        self.child_html = BlockFactory.create(
            parent_location=self.vertical.location,
            category="html",
            display_name="Test HTML Child Block",
            user_id=user_id,
            publish_item=False,
        )

    def test_lib_xblock_info(self):
        html_block = modulestore().get_item(self.top_level_html.location)
        xblock_info = create_xblock_info(html_block)
        self.validate_component_xblock_info(xblock_info, html_block)
        self.assertIsNone(xblock_info.get("child_info", None))  # noqa: PT009

    def test_lib_child_xblock_info(self):
        html_block = modulestore().get_item(self.child_html.location)
        xblock_info = create_xblock_info(
            html_block, include_ancestor_info=True, include_child_info=True
        )
        self.validate_component_xblock_info(xblock_info, html_block)
        self.assertIsNone(xblock_info.get("child_info", None))  # noqa: PT009
        ancestors = xblock_info["ancestor_info"]["ancestors"]
        self.assertEqual(len(ancestors), 2)  # noqa: PT009
        self.assertEqual(ancestors[0]["category"], "vertical")  # noqa: PT009
        self.assertEqual(ancestors[0]["id"], str(self.vertical.location))  # noqa: PT009
        self.assertEqual(ancestors[1]["category"], "library")  # noqa: PT009

    def validate_component_xblock_info(self, xblock_info, original_block):
        """
        Validate that the xblock info is correct for the test component.
        """
        self.assertEqual(xblock_info["category"], original_block.category)  # noqa: PT009
        self.assertEqual(xblock_info["id"], str(original_block.location))  # noqa: PT009
        self.assertEqual(xblock_info["display_name"], original_block.display_name)  # noqa: PT009
        self.assertIsNone(xblock_info.get("has_changes", None))  # noqa: PT009
        self.assertIsNone(xblock_info.get("published", None))  # noqa: PT009
        self.assertIsNone(xblock_info.get("published_on", None))  # noqa: PT009
        self.assertIsNone(xblock_info.get("graders", None))  # noqa: PT009


class TestLibraryXBlockCreation(ItemTest):
    """
    Tests the adding of XBlocks to Library
    """

    def test_add_xblock(self):
        """
        Verify we can add an XBlock to a Library.
        """
        lib = LibraryFactory.create()
        self.create_xblock(
            parent_usage_key=lib.location, display_name="Test", category="html"
        )
        lib = self.store.get_library(lib.location.library_key)
        self.assertTrue(lib.children)  # noqa: PT009
        xblock_locator = lib.children[0]
        self.assertEqual(self.store.get_item(xblock_locator).display_name, "Test")  # noqa: PT009

    def test_no_add_discussion(self):
        """
        Verify we cannot add a discussion block to a Library.
        """
        lib = LibraryFactory.create()
        response = self.create_xblock(
            parent_usage_key=lib.location, display_name="Test", category="discussion"
        )
        self.assertEqual(response.status_code, 400)  # noqa: PT009
        lib = self.store.get_library(lib.location.library_key)
        self.assertFalse(lib.children)  # noqa: PT009

    def test_no_add_advanced(self):
        lib = LibraryFactory.create()
        lib.advanced_modules = ["lti"]
        lib.save()
        response = self.create_xblock(
            parent_usage_key=lib.location, display_name="Test", category="lti"
        )
        self.assertEqual(response.status_code, 400)  # noqa: PT009
        lib = self.store.get_library(lib.location.library_key)
        self.assertFalse(lib.children)  # noqa: PT009


@ddt.ddt
class TestXBlockPublishingInfo(ItemTest):
    """
    Unit tests for XBlock's outline handling.
    """

    FIRST_SUBSECTION_PATH = [0]
    FIRST_UNIT_PATH = [0, 0]
    SECOND_UNIT_PATH = [0, 1]

    def _create_child(
        self, parent, category, display_name, publish_item=False, staff_only=False
    ):
        """
        Creates a child xblock for the given parent.
        """
        child = BlockFactory.create(
            parent_location=parent.location,
            category=category,
            display_name=display_name,
            user_id=self.user.id,
            publish_item=publish_item,
        )
        if staff_only:
            self._enable_staff_only(child.location)
        # In case the staff_only state was set, return the updated xblock.
        return modulestore().get_item(child.location)

    def _get_child_xblock_info(self, xblock_info, index):
        """
        Returns the child xblock info at the specified index.
        """
        children = xblock_info["child_info"]["children"]
        self.assertGreater(len(children), index)  # noqa: PT009
        return children[index]

    def _get_xblock_info(self, location):
        """
        Returns the xblock info for the specified location.
        """
        return create_xblock_info(
            modulestore().get_item(location),
            include_child_info=True,
            include_children_predicate=ALWAYS,
        )

    def _get_xblock_outline_info(self, location):
        """
        Returns the xblock info for the specified location as neeeded for the course outline page.
        """
        return create_xblock_info(
            modulestore().get_item(location),
            include_child_info=True,
            include_children_predicate=ALWAYS,
            course_outline=True,
        )

    def _set_release_date(self, location, start):
        """
        Sets the release date for the specified xblock.
        """
        xblock = modulestore().get_item(location)
        xblock.start = start
        self.store.update_item(xblock, self.user.id)

    def _enable_staff_only(self, location):
        """
        Enables staff only for the specified xblock.
        """
        xblock = modulestore().get_item(location)
        xblock.visible_to_staff_only = True
        self.store.update_item(xblock, self.user.id)

    def _set_display_name(self, location, display_name):
        """
        Sets the display name for the specified xblock.
        """
        xblock = modulestore().get_item(location)
        xblock.display_name = display_name
        self.store.update_item(xblock, self.user.id)

    def _verify_xblock_info_state(
        self,
        xblock_info,
        xblock_info_field,
        expected_state,
        path=None,
        should_equal=True,
    ):
        """
        Verify the state of an xblock_info field. If no path is provided then the root item will be verified.
        If should_equal is True, assert that the current state matches the expected state, otherwise assert that they
        do not match.
        """
        if path:
            direct_child_xblock_info = self._get_child_xblock_info(xblock_info, path[0])
            remaining_path = path[1:] if len(path) > 1 else None
            self._verify_xblock_info_state(
                direct_child_xblock_info,
                xblock_info_field,
                expected_state,
                remaining_path,
                should_equal,
            )
        else:
            if should_equal:
                self.assertEqual(xblock_info[xblock_info_field], expected_state)  # noqa: PT009
            else:
                self.assertNotEqual(xblock_info[xblock_info_field], expected_state)  # noqa: PT009

    def _verify_has_staff_only_message(self, xblock_info, expected_state, path=None):
        """
        Verify the staff_only_message field of xblock_info.
        """
        self._verify_xblock_info_state(
            xblock_info, "staff_only_message", expected_state, path
        )

    def _verify_visibility_state(
        self, xblock_info, expected_state, path=None, should_equal=True
    ):
        """
        Verify the publish state of an item in the xblock_info.
        """
        self._verify_xblock_info_state(
            xblock_info, "visibility_state", expected_state, path, should_equal
        )

    def _verify_explicit_staff_lock_state(
        self, xblock_info, expected_state, path=None, should_equal=True
    ):
        """
        Verify the explicit staff lock state of an item in the xblock_info.
        """
        self._verify_xblock_info_state(
            xblock_info, "has_explicit_staff_lock", expected_state, path, should_equal
        )

    def test_empty_chapter(self):
        empty_chapter = self._create_child(self.course, "chapter", "Empty Chapter")
        xblock_info = self._get_xblock_info(empty_chapter.location)
        self._verify_visibility_state(xblock_info, VisibilityState.unscheduled)

    def test_chapter_self_paced_default_start_date(self):
        course = CourseFactory.create()
        course.self_paced = True
        self.store.update_item(course, self.user.id)
        chapter = self._create_child(course, "chapter", "Test Chapter")
        sequential = self._create_child(chapter, "sequential", "Test Sequential")
        self._create_child(sequential, "vertical", "Published Unit", publish_item=True)
        self._set_release_date(chapter.location, DEFAULT_START_DATE)
        xblock_info = self._get_xblock_info(chapter.location)
        self._verify_visibility_state(xblock_info, VisibilityState.live)

    def test_empty_sequential(self):
        chapter = self._create_child(self.course, "chapter", "Test Chapter")
        self._create_child(chapter, "sequential", "Empty Sequential")
        xblock_info = self._get_xblock_info(chapter.location)
        self._verify_visibility_state(xblock_info, VisibilityState.unscheduled)
        self._verify_visibility_state(
            xblock_info, VisibilityState.unscheduled, path=self.FIRST_SUBSECTION_PATH
        )

    def test_published_unit(self):
        """
        Tests the visibility state of a published unit with release date in the future.
        """
        chapter = self._create_child(self.course, "chapter", "Test Chapter")
        sequential = self._create_child(chapter, "sequential", "Test Sequential")
        self._create_child(sequential, "vertical", "Published Unit", publish_item=True)
        self._create_child(sequential, "vertical", "Staff Only Unit", staff_only=True)
        self._set_release_date(chapter.location, datetime.now(UTC) + timedelta(days=1))
        xblock_info = self._get_xblock_info(chapter.location)
        self._verify_visibility_state(xblock_info, VisibilityState.ready)
        self._verify_visibility_state(
            xblock_info, VisibilityState.ready, path=self.FIRST_SUBSECTION_PATH
        )
        self._verify_visibility_state(
            xblock_info, VisibilityState.ready, path=self.FIRST_UNIT_PATH
        )
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, path=self.SECOND_UNIT_PATH
        )

    def test_released_unit(self):
        """
        Tests the visibility state of a published unit with release date in the past.
        """
        chapter = self._create_child(self.course, "chapter", "Test Chapter")
        sequential = self._create_child(chapter, "sequential", "Test Sequential")
        self._create_child(sequential, "vertical", "Published Unit", publish_item=True)
        self._create_child(sequential, "vertical", "Staff Only Unit", staff_only=True)
        self._set_release_date(chapter.location, datetime.now(UTC) - timedelta(days=1))
        xblock_info = self._get_xblock_info(chapter.location)
        self._verify_visibility_state(xblock_info, VisibilityState.live)
        self._verify_visibility_state(
            xblock_info, VisibilityState.live, path=self.FIRST_SUBSECTION_PATH
        )
        self._verify_visibility_state(
            xblock_info, VisibilityState.live, path=self.FIRST_UNIT_PATH
        )
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, path=self.SECOND_UNIT_PATH
        )

    def test_unpublished_changes(self):
        """
        Tests the visibility state of a published unit with draft (unpublished) changes.
        """
        chapter = self._create_child(self.course, "chapter", "Test Chapter")
        sequential = self._create_child(chapter, "sequential", "Test Sequential")
        unit = self._create_child(
            sequential, "vertical", "Published Unit", publish_item=True
        )
        self._create_child(sequential, "vertical", "Staff Only Unit", staff_only=True)
        # Setting the display name creates a draft version of unit.
        self._set_display_name(unit.location, "Updated Unit")
        xblock_info = self._get_xblock_info(chapter.location)
        self._verify_visibility_state(xblock_info, VisibilityState.needs_attention)
        self._verify_visibility_state(
            xblock_info,
            VisibilityState.needs_attention,
            path=self.FIRST_SUBSECTION_PATH,
        )
        self._verify_visibility_state(
            xblock_info, VisibilityState.needs_attention, path=self.FIRST_UNIT_PATH
        )
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, path=self.SECOND_UNIT_PATH
        )

    def test_partially_released_section(self):
        chapter = self._create_child(self.course, 'chapter', "Test Chapter")
        released_sequential = self._create_child(chapter, 'sequential', "Released Sequential")
        self._create_child(released_sequential, 'vertical', "Released Unit", publish_item=True)
        self._create_child(released_sequential, 'vertical', "Staff Only Unit 1", staff_only=True)
        self._set_release_date(chapter.location, datetime.now(UTC) - timedelta(days=1))
        published_sequential = self._create_child(chapter, 'sequential', "Published Sequential")
        self._create_child(published_sequential, 'vertical', "Published Unit", publish_item=True)
        self._create_child(published_sequential, 'vertical', "Staff Only Unit 2", staff_only=True)
        self._set_release_date(published_sequential.location, datetime.now(UTC) + timedelta(days=1))
        xblock_info = self._get_xblock_info(chapter.location)

        # Verify the state of the released sequential
        self._verify_visibility_state(xblock_info, VisibilityState.live, path=[0])
        self._verify_visibility_state(xblock_info, VisibilityState.live, path=[0, 0])
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, path=[0, 1]
        )

        # Verify the state of the published sequential
        self._verify_visibility_state(xblock_info, VisibilityState.ready, path=[1])
        self._verify_visibility_state(xblock_info, VisibilityState.ready, path=[1, 0])
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, path=[1, 1]
        )

        # Finally verify the state of the chapter
        self._verify_visibility_state(xblock_info, VisibilityState.ready)

    def test_staff_only_section(self):
        """
        Tests that an explicitly staff-locked section and all of its children are visible to staff only.
        """
        chapter = self._create_child(
            self.course, "chapter", "Test Chapter", staff_only=True
        )
        sequential = self._create_child(chapter, "sequential", "Test Sequential")
        vertical = self._create_child(sequential, "vertical", "Unit")
        xblock_info = self._get_xblock_info(chapter.location)
        self._verify_visibility_state(xblock_info, VisibilityState.staff_only)
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, path=self.FIRST_SUBSECTION_PATH
        )
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, path=self.FIRST_UNIT_PATH
        )

        self._verify_explicit_staff_lock_state(xblock_info, True)
        self._verify_explicit_staff_lock_state(
            xblock_info, False, path=self.FIRST_SUBSECTION_PATH
        )
        self._verify_explicit_staff_lock_state(
            xblock_info, False, path=self.FIRST_UNIT_PATH
        )

        vertical_info = self._get_xblock_info(vertical.location)
        add_container_page_publishing_info(vertical, vertical_info)
        self.assertEqual(  # noqa: PT009
            _xblock_type_and_display_name(chapter), vertical_info["staff_lock_from"]
        )

    def test_no_staff_only_section(self):
        """
        Tests that a section with a staff-locked subsection and a visible subsection is not staff locked itself.
        """
        chapter = self._create_child(self.course, "chapter", "Test Chapter")
        self._create_child(chapter, "sequential", "Test Visible Sequential")
        self._create_child(
            chapter, "sequential", "Test Staff Locked Sequential", staff_only=True
        )
        xblock_info = self._get_xblock_info(chapter.location)
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, should_equal=False
        )
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, path=[0], should_equal=False
        )
        self._verify_visibility_state(xblock_info, VisibilityState.staff_only, path=[1])

    def test_staff_only_subsection(self):
        """
        Tests that an explicitly staff-locked subsection and all of its children are visible to staff only.
        In this case the parent section is also visible to staff only because all of its children are staff only.
        """
        chapter = self._create_child(self.course, "chapter", "Test Chapter")
        sequential = self._create_child(
            chapter, "sequential", "Test Sequential", staff_only=True
        )
        vertical = self._create_child(sequential, "vertical", "Unit")
        xblock_info = self._get_xblock_info(chapter.location)
        self._verify_visibility_state(xblock_info, VisibilityState.staff_only)
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, path=self.FIRST_SUBSECTION_PATH
        )
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, path=self.FIRST_UNIT_PATH
        )

        self._verify_explicit_staff_lock_state(xblock_info, False)
        self._verify_explicit_staff_lock_state(
            xblock_info, True, path=self.FIRST_SUBSECTION_PATH
        )
        self._verify_explicit_staff_lock_state(
            xblock_info, False, path=self.FIRST_UNIT_PATH
        )

        vertical_info = self._get_xblock_info(vertical.location)
        add_container_page_publishing_info(vertical, vertical_info)
        self.assertEqual(  # noqa: PT009
            _xblock_type_and_display_name(sequential), vertical_info["staff_lock_from"]
        )

    def test_no_staff_only_subsection(self):
        """
        Tests that a subsection with a staff-locked unit and a visible unit is not staff locked itself.
        """
        chapter = self._create_child(self.course, "chapter", "Test Chapter")
        sequential = self._create_child(chapter, "sequential", "Test Sequential")
        self._create_child(sequential, "vertical", "Unit")
        self._create_child(sequential, "vertical", "Locked Unit", staff_only=True)
        xblock_info = self._get_xblock_info(chapter.location)
        self._verify_visibility_state(
            xblock_info,
            VisibilityState.staff_only,
            self.FIRST_SUBSECTION_PATH,
            should_equal=False,
        )
        self._verify_visibility_state(
            xblock_info,
            VisibilityState.staff_only,
            self.FIRST_UNIT_PATH,
            should_equal=False,
        )
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, self.SECOND_UNIT_PATH
        )

    def test_staff_only_unit(self):
        chapter = self._create_child(self.course, "chapter", "Test Chapter")
        sequential = self._create_child(chapter, "sequential", "Test Sequential")
        vertical = self._create_child(sequential, "vertical", "Unit", staff_only=True)
        xblock_info = self._get_xblock_info(chapter.location)
        self._verify_visibility_state(xblock_info, VisibilityState.staff_only)
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, path=self.FIRST_SUBSECTION_PATH
        )
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, path=self.FIRST_UNIT_PATH
        )

        self._verify_explicit_staff_lock_state(xblock_info, False)
        self._verify_explicit_staff_lock_state(
            xblock_info, False, path=self.FIRST_SUBSECTION_PATH
        )
        self._verify_explicit_staff_lock_state(
            xblock_info, True, path=self.FIRST_UNIT_PATH
        )

        vertical_info = self._get_xblock_info(vertical.location)
        add_container_page_publishing_info(vertical, vertical_info)
        self.assertEqual(  # noqa: PT009
            _xblock_type_and_display_name(vertical), vertical_info["staff_lock_from"]
        )

    def test_unscheduled_section_with_live_subsection(self):
        chapter = self._create_child(self.course, "chapter", "Test Chapter")
        sequential = self._create_child(chapter, "sequential", "Test Sequential")
        self._create_child(sequential, "vertical", "Published Unit", publish_item=True)
        self._create_child(sequential, "vertical", "Staff Only Unit", staff_only=True)
        self._set_release_date(
            sequential.location, datetime.now(UTC) - timedelta(days=1)
        )
        xblock_info = self._get_xblock_info(chapter.location)
        self._verify_visibility_state(xblock_info, VisibilityState.needs_attention)
        self._verify_visibility_state(
            xblock_info, VisibilityState.live, path=self.FIRST_SUBSECTION_PATH
        )
        self._verify_visibility_state(
            xblock_info, VisibilityState.live, path=self.FIRST_UNIT_PATH
        )
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, path=self.SECOND_UNIT_PATH
        )

    def test_unreleased_section_with_live_subsection(self):
        chapter = self._create_child(self.course, "chapter", "Test Chapter")
        sequential = self._create_child(chapter, "sequential", "Test Sequential")
        self._create_child(sequential, "vertical", "Published Unit", publish_item=True)
        self._create_child(sequential, "vertical", "Staff Only Unit", staff_only=True)
        self._set_release_date(chapter.location, datetime.now(UTC) + timedelta(days=1))
        self._set_release_date(
            sequential.location, datetime.now(UTC) - timedelta(days=1)
        )
        xblock_info = self._get_xblock_info(chapter.location)
        self._verify_visibility_state(xblock_info, VisibilityState.needs_attention)
        self._verify_visibility_state(
            xblock_info, VisibilityState.live, path=self.FIRST_SUBSECTION_PATH
        )
        self._verify_visibility_state(
            xblock_info, VisibilityState.live, path=self.FIRST_UNIT_PATH
        )
        self._verify_visibility_state(
            xblock_info, VisibilityState.staff_only, path=self.SECOND_UNIT_PATH
        )

    def test_locked_section_staff_only_message(self):
        """
        Tests that a locked section has a staff only message and its descendants do not.
        """
        chapter = self._create_child(
            self.course, "chapter", "Test Chapter", staff_only=True
        )
        sequential = self._create_child(chapter, "sequential", "Test Sequential")
        self._create_child(sequential, "vertical", "Unit")
        xblock_info = self._get_xblock_outline_info(chapter.location)
        self._verify_has_staff_only_message(xblock_info, True)
        self._verify_has_staff_only_message(
            xblock_info, False, path=self.FIRST_SUBSECTION_PATH
        )
        self._verify_has_staff_only_message(
            xblock_info, False, path=self.FIRST_UNIT_PATH
        )

    def test_locked_unit_staff_only_message(self):
        """
        Tests that a lone locked unit has a staff only message along with its ancestors.
        """
        chapter = self._create_child(self.course, "chapter", "Test Chapter")
        sequential = self._create_child(chapter, "sequential", "Test Sequential")
        self._create_child(sequential, "vertical", "Unit", staff_only=True)
        xblock_info = self._get_xblock_outline_info(chapter.location)
        self._verify_has_staff_only_message(xblock_info, True)
        self._verify_has_staff_only_message(
            xblock_info, True, path=self.FIRST_SUBSECTION_PATH
        )
        self._verify_has_staff_only_message(
            xblock_info, True, path=self.FIRST_UNIT_PATH
        )

    def test_self_paced_item_visibility_state(self):
        """
        Test that in self-paced course, item has `live` visibility state.
        Test that when item was initially in `scheduled` state in instructor mode, change course pacing to self-paced,
        now in self-paced course, item should have `live` visibility state.
        """

        # Create course, chapter and setup future release date to make chapter in scheduled state
        course = CourseFactory.create()
        chapter = self._create_child(course, "chapter", "Test Chapter")
        self._set_release_date(chapter.location, datetime.now(UTC) + timedelta(days=1))

        # Check that chapter has scheduled state
        xblock_info = self._get_xblock_info(chapter.location)
        self._verify_visibility_state(xblock_info, VisibilityState.ready)
        self.assertFalse(course.self_paced)  # noqa: PT009

        # Change course pacing to self paced
        course.self_paced = True
        self.store.update_item(course, self.user.id)
        self.assertTrue(course.self_paced)  # noqa: PT009

        # Check that in self paced course content has live state now
        xblock_info = self._get_xblock_info(chapter.location)
        self._verify_visibility_state(xblock_info, VisibilityState.live)


@patch(
    "xmodule.modulestore.split_mongo.runtime.SplitModuleStoreRuntime.applicable_aside_types",
    lambda self, block: ["test_aside"],
)
class TestUpdateFromSource(ModuleStoreTestCase):
    """
    Test update_from_source.
    """

    def setUp(self):
        """
        Set up the runtime for tests.
        """
        super().setUp()
        key_store = DictKeyValueStore()
        field_data = KvsFieldData(key_store)
        self.runtime = TestRuntime(services={"field-data": field_data})

    def create_source_block(self, course):
        """
        Create a chapter with all the fixings.
        """
        source_block = BlockFactory(
            parent=course,
            category="course_info",
            display_name="Source Block",
            metadata={"due": datetime(2010, 11, 22, 4, 0, tzinfo=UTC)},
        )

        def_id = self.runtime.id_generator.create_definition("html")
        usage_id = self.runtime.id_generator.create_usage(def_id)

        aside = AsideTest(
            scope_ids=ScopeIds("user", "html", def_id, usage_id), runtime=self.runtime
        )
        aside.field11 = "html_new_value1"

        # The data attribute is handled in a special manner and should be updated.
        source_block.data = "<div>test</div>"
        # This field is set on the content scope (definition_data), which should be updated.
        source_block.items = ["test", "beep"]

        self.store.update_item(source_block, self.user.id, asides=[aside])

        # quick sanity checks
        source_block = self.store.get_item(source_block.location)
        self.assertEqual(source_block.due, datetime(2010, 11, 22, 4, 0, tzinfo=UTC))  # noqa: PT009
        self.assertEqual(source_block.display_name, "Source Block")  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            source_block.runtime.get_asides(source_block)[0].field11, "html_new_value1"
        )
        self.assertEqual(source_block.data, "<div>test</div>")  # noqa: PT009
        self.assertEqual(source_block.items, ["test", "beep"])  # noqa: PT009

        return source_block

    def check_updated(self, source_block, destination_key):
        """
        Check that the destination block has been updated to match our source block.
        """
        revised = self.store.get_item(destination_key)
        self.assertEqual(source_block.display_name, revised.display_name)  # noqa: PT009
        self.assertEqual(source_block.due, revised.due)  # noqa: PT009
        self.assertEqual(revised.data, source_block.data)  # noqa: PT009
        self.assertEqual(revised.items, source_block.items)  # noqa: PT009

        self.assertEqual(  # noqa: PT009
            revised.runtime.get_asides(revised)[0].field11,
            source_block.runtime.get_asides(source_block)[0].field11,
        )

    @XBlockAside.register_temp_plugin(AsideTest, "test_aside")
    def test_update_from_source(self):
        """
        Test that update_from_source updates the destination block.
        """
        course = CourseFactory()
        user = UserFactory.create()

        source_block = self.create_source_block(course)

        destination_block = BlockFactory(
            parent=course, category="course_info", display_name="Destination Problem"
        )
        update_from_source(
            source_block=source_block,
            destination_block=destination_block,
            user_id=user.id,
        )
        self.check_updated(source_block, destination_block.location)

    @XBlockAside.register_temp_plugin(AsideTest, "test_aside")
    def test_update_clobbers(self):
        """
        Verify that our update replaces all settings on the block.
        """
        course = CourseFactory()
        user = UserFactory.create()

        source_block = self.create_source_block(course)

        destination_block = BlockFactory(
            parent=course,
            category="course_info",
            display_name="Destination Chapter",
            metadata={"due": datetime(2025, 10, 21, 6, 5, tzinfo=UTC)},
        )

        def_id = self.runtime.id_generator.create_definition("html")
        usage_id = self.runtime.id_generator.create_usage(def_id)
        aside = AsideTest(
            scope_ids=ScopeIds("user", "html", def_id, usage_id), runtime=self.runtime
        )
        aside.field11 = "Other stuff"
        destination_block.data = "<div>other stuff</div>"
        destination_block.items = ["other stuff", "boop"]
        self.store.update_item(destination_block, user.id, asides=[aside])

        update_from_source(
            source_block=source_block,
            destination_block=destination_block,
            user_id=user.id,
        )
        self.check_updated(source_block, destination_block.location)


class TestXblockEditView(CourseTestCase):
    """
    Test xblock_edit_view.
    """

    def setUp(self):
        super().setUp()
        self.chapter = self._create_block(self.course, "chapter", "Week 1")
        self.sequential = self._create_block(self.chapter, "sequential", "Lesson 1")
        self.vertical = self._create_block(self.sequential, "vertical", "Unit")
        self.html = self._create_block(self.vertical, "html", "HTML")
        self.child_container = self._create_block(
            self.vertical, "split_test", "Split Test"
        )
        self.child_vertical = self._create_block(
            self.child_container, "vertical", "Child Vertical"
        )
        self.video = self._create_block(self.child_vertical, "video", "My Video")
        self.store = modulestore()

        self.store.publish(self.vertical.location, self.user.id)

    def _create_block(self, parent, category, display_name, **kwargs):
        """
        creates a block in the module store, without publishing it.
        """
        return BlockFactory.create(
            parent=parent,
            category=category,
            display_name=display_name,
            publish_item=False,
            user_id=self.user.id,
            **kwargs,
        )

    def test_xblock_edit_view(self):
        url = reverse_usage_url("xblock_edit_handler", self.video.location)
        resp = self.client.get_html(url)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

        html_content = resp.content.decode(resp.charset)
        self.assertIn("var decodedActionName = 'edit';", html_content)  # noqa: PT009

    def test_xblock_edit_view_contains_resources(self):
        url = reverse_usage_url("xblock_edit_handler", self.video.location)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

        html_content = resp.content.decode(resp.charset)
        soup = BeautifulSoup(html_content, "html.parser")

        resource_links = [link["href"] for link in soup.find_all("link", {"rel": "stylesheet"})]
        script_sources = [script["src"] for script in soup.find_all("script") if script.get("src")]

        self.assertGreater(len(resource_links), 0, f"No CSS resources found in HTML. Found: {resource_links}")  # noqa: PT009  # pylint: disable=line-too-long
        self.assertGreater(len(script_sources), 0, f"No JS resources found in HTML. Found: {script_sources}")  # noqa: PT009  # pylint: disable=line-too-long
