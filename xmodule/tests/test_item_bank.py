"""
Unit tests for ItemBankBlock.
"""
from unittest.mock import MagicMock, Mock, patch
from random import Random

import ddt
from fs.memoryfs import MemoryFS
from lxml import etree
from rest_framework import status
from web_fragments.fragment import Fragment
from xblock.runtime import Runtime as VanillaRuntime

from openedx.core.djangolib.testing.utils import skip_unless_lms, skip_unless_cms
from xmodule.modulestore.tests.factories import CourseFactory
from xmodule.modulestore.tests.utils import MixedSplitTestCase
from xmodule.tests import prepare_block_runtime
from xmodule.validation import StudioValidationMessage
from xmodule.x_module import AUTHOR_VIEW
from xmodule.capa_block import ProblemBlock
from common.djangoapps.student.tests.factories import UserFactory

from ..item_bank_block import ItemBankBlock
from .test_course_block import DummyModuleStoreRuntime

dummy_render = lambda block, _: Fragment(block.data)  # pylint: disable=invalid-name


class ItemBankTestBase(MixedSplitTestCase):
    """
    Base class for tests of ItemBankBlock
    """
    maxDiff = None  # We need all the diff we can get for some of these asserts.

    def setUp(self):
        super().setUp()
        self.user_id = UserFactory().id
        self.course = CourseFactory.create(modulestore=self.store)
        self.chapter = self.make_block(category="chapter", parent_block=self.course, publish_item=True)
        self.sequential = self.make_block(category="sequential", parent_block=self.chapter, publish_item=True)
        self.vertical = self.make_block(category="vertical", parent_block=self.sequential, publish_item=True)
        self.item_bank = self.make_block(
            category="itembank", parent_block=self.vertical, max_count=1, display_name="My Item Bank", publish_item=True
        )
        self.items = [
            self.make_block(
                category="problem", parent_block=self.item_bank, display_name=f"My Item {i}",
                data=f"<p>Hello world from problem {i}</p>",
                publish_item=True,
            )
            for i in range(4)
        ]
        self.publisher = Mock()  # for tests that look at analytics
        self._reload_item_bank()

    def _bind_course_block(self, block):
        """
        Bind a block (part of self.course) so we can access student-specific data.

        (Not clear if this is necessary since XModules are all removed now. It's possible that this
         could be removed without breaking tests.)
        """
        prepare_block_runtime(block.runtime, course_id=block.context_key)

        def get_block(descriptor):
            """Mocks module_system get_block function"""
            prepare_block_runtime(descriptor.runtime, course_id=block.context_key)
            descriptor.runtime.get_block_for_descriptor = get_block
            descriptor.bind_for_student(self.user_id)
            return descriptor

        block.runtime.get_block_for_descriptor = get_block

    def _reload_item_bank(self):
        """
        Reload self.item_bank. Do this if you want its `.children` list to be updated with what's in the db.

        HACK: These test cases don't persist student state, but some tests need `selected` to persist betweem item_bank
        reloads. So, we just transfer it from the old item_bank object instance to the new one, as if it were persisted.
        """
        selected = self.item_bank.selected
        self.item_bank = self.store.get_item(self.item_bank.usage_key)
        self._bind_course_block(self.item_bank)
        if selected:
            self.item_bank.selected = selected
        self.item_bank.runtime.publish = self.publisher


@skip_unless_cms
class TestItemBankForCms(ItemBankTestBase):
    """
    Test Studio ItemBank behaviors -- export/import, validation, author-facing views.
    """
    def test_xml_export_import_cycle(self):
        """
        Test the export-import cycle.
        """
        # Export self.item_bank to the virtual filesystem
        export_fs = MemoryFS()
        self.item_bank.runtime.export_fs = export_fs  # pylint: disable=protected-access
        node = etree.Element("unknown_root")
        self.item_bank.add_xml_to_node(node)

        # Read back the itembank OLX
        with export_fs.open('{dir}/{file_name}.xml'.format(
            dir=self.item_bank.scope_ids.usage_id.block_type,
            file_name=self.item_bank.scope_ids.usage_id.block_id
        )) as f:
            actual_olx_export = f.read()

        # And compare.
        expected_olx_export = (
            '<itembank display_name="My Item Bank" max_count="1">\n'
            '  <problem url_name="My_Item_0"/>\n'
            '  <problem url_name="My_Item_1"/>\n'
            '  <problem url_name="My_Item_2"/>\n'
            '  <problem url_name="My_Item_3"/>\n'
            '</itembank>\n'
        )
        assert actual_olx_export == expected_olx_export
        olx_element = etree.fromstring(actual_olx_export)

        # Re-import the OLX.
        runtime = DummyModuleStoreRuntime(load_error_blocks=True, course_id=self.item_bank.context_key)
        runtime.resources_fs = export_fs
        imported_item_bank = ItemBankBlock.parse_xml(olx_element, runtime, None)

        # And make sure the result looks right.
        self._verify_xblock_properties(imported_item_bank)

    def _verify_xblock_properties(self, imported_item_bank):
        """
        Check the new XBlock has the same properties as the old one.
        """
        assert imported_item_bank.display_name == self.item_bank.display_name
        assert imported_item_bank.max_count == self.item_bank.max_count
        assert len(imported_item_bank.children) == len(self.item_bank.children)

    def test_max_count_validation(self):
        """
        Test that the validation method of ItemBankBlocks can warn the user about problems with settings (max_count).
        """
        # Ensure we're starting with clean validation
        assert self.item_bank.validate()

        # Ensure that setting the max_count too high (> than # of children) raises a validation warning.
        self.item_bank.max_count = 50
        assert len(self.item_bank.selected_children()) == 4
        assert not (result := self.item_bank.validate())
        assert StudioValidationMessage.WARNING == result.summary.type
        assert 'configured to show 50 problems, but only 4 have been selected' in result.summary.text

        # Now set max_count to valid value (<= than # of children), and ensure the validation error goes away.
        self.item_bank.max_count = 3
        assert len(self.item_bank.selected_children()) == 3
        assert self.item_bank.validate()

        # Ensure that setting max_count to 0 raises a validation error.
        self.item_bank.max_count = 0
        assert len(self.item_bank.selected_children()) == 0
        assert not (result := self.item_bank.validate())
        assert StudioValidationMessage.ERROR == result.summary.type
        assert 'configured to show 0 problems. Please specify' in result.summary.text

        # Finally, set max_count to -1, and ensure the validation error goes away.
        self.item_bank.max_count = -1
        assert len(self.item_bank.selected_children()) == 4
        assert self.item_bank.validate()

    @patch(
        'xmodule.modulestore.split_mongo.runtime.SplitModuleStoreRuntime.render',
        VanillaRuntime.render,
    )
    @patch('xmodule.capa_block.ProblemBlock.author_view', dummy_render, create=True)
    @patch('xmodule.x_module.ModuleStoreRuntime.applicable_aside_types', lambda self, block: [])
    def test_preview_view(self):
        """ Test preview view rendering """
        self._bind_course_block(self.item_bank)
        rendered = self.item_bank.render(AUTHOR_VIEW, {'root_xblock': self.item_bank})
        assert '<p>Hello world from problem 0</p>' in rendered.content
        assert '<p>Hello world from problem 1</p>' in rendered.content
        assert '<p>Hello world from problem 2</p>' in rendered.content
        assert '<p>Hello world from problem 3</p>' in rendered.content

    @patch(
        'xmodule.modulestore.split_mongo.runtime.SplitModuleStoreRuntime.render',
        VanillaRuntime.render,
    )
    @patch('xmodule.capa_block.ProblemBlock.author_view', dummy_render, create=True)
    @patch('xmodule.x_module.ModuleStoreRuntime.applicable_aside_types', lambda self, block: [])
    def test_author_view(self):
        """ Test author view rendering """
        self._bind_course_block(self.item_bank)
        rendered = self.item_bank.render(AUTHOR_VIEW, {})
        assert 'Learners will see 1 of the 4 selected components' in rendered.content
        assert '<li>My Item 0</li>' in rendered.content
        assert '<li>My Item 1</li>' in rendered.content
        assert '<li>My Item 2</li>' in rendered.content
        assert '<li>My Item 3</li>' in rendered.content


@skip_unless_lms
@ddt.ddt
class TestItemBankForLms(ItemBankTestBase):
    """
    Test LMS ItemBank features: selection, analytics, resetting problems.
    """

    def _assert_event_was_published(self, event_type):
        """
        Check that a LegacyLibraryContentBlock analytics event was published by self.item_bank.
        """
        assert self.publisher.called
        assert len(self.publisher.call_args[0]) == 3  # pylint:disable=unsubscriptable-object
        _, event_name, event_data = self.publisher.call_args[0]  # pylint:disable=unsubscriptable-object
        assert event_name == f'edx.itembankblock.content.{event_type}'
        assert event_data['location'] == str(self.item_bank.usage_key)
        return event_data

    def test_children_seen_by_a_user(self):
        """
        Test that each student sees only one block as a child of the LibraryContent block.
        """
        self._bind_course_block(self.item_bank)
        # Make sure the runtime knows that the block's children vary per-user:
        assert self.item_bank.has_dynamic_children()

        assert len(self.item_bank.children) == len(self.items)

        # Check how many children each user will see:
        assert len(self.item_bank.get_child_blocks()) == 1
        # Check that get_content_titles() doesn't return titles for hidden/unused children
        assert len(self.item_bank.get_content_titles()) == 1

    def test_overlimit_blocks_chosen_randomly(self):
        """
        Tests that blocks to remove from selected children are chosen
        randomly when len(selected) > max_count.
        """
        blocks_seen = set()
        total_tries, max_tries = 0, 100

        self._bind_course_block(self.item_bank)

        # Eventually, we should see every child block selected
        while len(blocks_seen) != len(self.items):
            self._change_count_and_reselect_children(len(self.items))
            # Now set the number of selections to 1
            selected = self._change_count_and_reselect_children(1)
            blocks_seen.update(selected)
            total_tries += 1
            if total_tries >= max_tries:
                # The chance that this happens by accident is (4 * (3/4)^100) ~= 1/10^12
                assert False, "Max tries exceeded before seeing all blocks."
                break

    def _change_count_and_reselect_children(self, count):
        """
        Helper method that changes the max_count of self.item_bank, reselects
        children, and asserts that the number of selected children equals the count provided.
        """
        self.item_bank.max_count = count
        selected = self.item_bank.get_child_blocks()
        assert len(selected) == count
        return selected

    @ddt.data(
        # User resets selected children with reset button on content block
        (True, 5),
        # User resets selected children without reset button on content block
        (False, 5),
        # User resets selected children with reset button on content block when all library blocks should be selected.
        (True, -1),
    )
    @ddt.unpack
    def test_reset_selected_children_capa_blocks(self, allow_resetting_children, max_count):
        """
        Tests that the `reset_selected_children` method of a content block resets only
        XBlocks that have a `reset_problem` attribute when `allow_resetting_children` is True

        This test block has 4 HTML XBlocks and 4 Problem XBlocks. Therefore, if we ensure
        that the `reset_problem` has been called len(self.problem_types) times, then
        it means that this is working correctly
        """
        # Add a non-ProblemBlock just to make sure that this setting doesn't break with it.
        self.make_block(category="html", parent_block=self.item_bank)
        self._reload_item_bank()

        self.item_bank.allow_resetting_children = allow_resetting_children
        self.item_bank.max_count = max_count

        # Mock the student view to return an empty dict to be returned as response
        self.item_bank.student_view = MagicMock()
        self.item_bank.student_view.return_value.content = {}

        with patch.object(ProblemBlock, 'reset_problem', return_value={'success': True}) as reset_problem:
            response = self.item_bank.reset_selected_children(None, None)

        if allow_resetting_children:
            self.item_bank.student_view.assert_called_once_with({})
            assert reset_problem.call_count == 4  # the # of problems in self.items
            assert response.status_code == status.HTTP_200_OK
            assert response.content_type == "text/html"
            assert response.body == b"{}"
        else:
            reset_problem.assert_not_called()
            assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_assigned_event(self):
        """
        Test the "assigned" event emitted when a student is assigned specific blocks.
        """
        # In the beginning was the itembank and it assigned one child to the student:
        child = self.item_bank.get_child_blocks()[0]
        event_data = self._assert_event_was_published("assigned")
        block_info = {
            "usage_key": str(child.usage_key),
        }
        assert event_data ==\
               {'location': str(self.item_bank.usage_key),
                'added': [block_info],
                'result': [block_info],
                'previous_count': 0, 'max_count': 1}
        self.publisher.reset_mock()

        # Now increase max_count so that one more child will be added:
        self.item_bank.max_count = 2
        children = self.item_bank.get_child_blocks()
        assert len(children) == 2
        child, new_child = children if children[0].usage_key == child.usage_key else reversed(children)
        event_data = self._assert_event_was_published("assigned")
        assert event_data['added'][0]['usage_key'] == str(new_child.usage_key)
        assert len(event_data['result']) == 2
        assert event_data['previous_count'] == 1
        assert event_data['max_count'] == 2

    def test_removed_overlimit(self):
        """
        Test the "removed" event emitted when we un-assign blocks previously assigned to a student.
        We go from one blocks assigned to none because max_count has been decreased.
        """
        # Decrease max_count to 1, causing the block to be overlimit:
        self.item_bank.get_child_blocks()  # This line is needed in the test environment or the change has no effect
        self.publisher.reset_mock()  # Clear the "assigned" event that was just published.
        self.item_bank.max_count = 0

        # Check that the event says that one block was removed, leaving no blocks left:
        children = self.item_bank.get_child_blocks()
        assert len(children) == 0
        event_data = self._assert_event_was_published("removed")
        assert len(event_data['removed']) == 1
        assert event_data['result'] == []
        assert event_data['reason'] == 'overlimit'

    @ddt.data(
        (
            [("problem", "My_Item_1"), ("problem", "My_Item_2")],
            ("problem", "My_Item_1"),
            [("problem", "My_Item_2"), ("problem", "My_Item_3")],
        ),
        (
            [("problem", "My_Item_1"), ("problem", "My_Item_2")],
            ("problem", "My_Item_2"),
            [("problem", "My_Item_1"), ("problem", "My_Item_3")],
        ),
        (
            [("problem", "My_Item_3"), ("problem", "My_Item_0")],
            ("problem", "My_Item_3"),
            [("problem", "My_Item_2"), ("problem", "My_Item_3")],
        ),
        (
            [("problem", "My_Item_3"), ("problem", "My_Item_0")],
            ("problem", "My_Item_0"),
            [("problem", "My_Item_3"), ("problem", "My_Item_2")],
        ),
    )
    @ddt.unpack
    def test_removed_invalid(self, to_select_initial, to_drop, to_select_new):
        """
        Test the "removed" event emitted when we un-assign blocks previously assigned to a student.

        In this test, we keep `.max_count==2`, but do a series of two removals from `.children`.

        * Initial condition: 4 children, 2 assigned.                       0  [1] [2]  3
        * First deletion: one of the assigned blocks, e.g. block 1.
        * New condition: 3 children, 2 assigned.                           0   _  [2] [3]
        * Selecond deletion: the other two assigned blocks (2 and 3).
        * Final condition:  1 child, 1 assigned.                          [0]  _   _   _

        The grid on the right shows how the test should go for our first ddt case.
        """
        # pylint: disable=too-many-statements

        # Start by assigning two blocks to the student:
        self.item_bank.max_count = 2
        self.store.update_item(self.item_bank, self.user_id)

        # Initial selection
        assert len(self.item_bank.children) == 4
        children_initial = [(child.block_type, child.block_id) for child in self.item_bank.children]
        with patch.object(Random, "sample", _make_mock_sample(children_initial, to_select_initial)):
            with patch.object(Random, "shuffle", _make_mock_shuffle(to_select_initial)):
                selected_initial = self.item_bank.selected_children()
        assert len(selected_initial) == 2
        self.publisher.reset_mock()  # Clear the "assigned" event that was just published.

        # Now make sure that one of the assigned blocks will have to be un-assigned.
        # To cause an "invalid" event, we delete exactly one of the currently-assigned children:
        (to_keep,) = set(selected_initial) - set([to_drop])
        to_keep_usage_key = self.course.context_key.make_usage_key(*to_keep)
        to_drop_usage_key = self.course.context_key.make_usage_key(*to_drop)
        self.store.delete_item(to_drop_usage_key, self.user_id)
        self._reload_item_bank()
        assert len(self.item_bank.children) == 3  # Sanity: We had 4 blocks, we deleted 1, should be 3 left.

        # Because there are 3 available children and max_count==2, when we reselect children for assignment,
        # we should get 2. To maximize stability from the student's perspective, we expect that one of those children
        # was the one that was previously assigned (to_keep).
        remaining_selectable = set(children_initial) - {to_keep, to_drop}
        to_add = set(to_select_new) & remaining_selectable
        assert len(to_add) == 1  # sanity check
        with patch.object(Random, "sample", _make_mock_sample(remaining_selectable, to_add)):
            with patch.object(Random, "shuffle", _make_mock_shuffle(to_select_new)):
                selected_new = self.item_bank.selected_children()
        assert len(selected_new) == 2
        assert to_keep in selected_new
        assert to_drop not in selected_new
        selected_new_usage_keys = [self.course.context_key.make_usage_key(*sel) for sel in selected_new]

        # and, obviously, the one block that was added to the selection should be one of the remaining 3 children.
        (added_usage_key,) = set(selected_new_usage_keys) - set([to_keep_usage_key])
        added = (added_usage_key.block_type, added_usage_key.block_id)
        assert added_usage_key in self.item_bank.children

        # Check that the event says that one block was removed and one was added
        assert self.publisher.call_count == 2
        _, removed_event_name, removed_event_data = self.publisher.call_args_list[0][0]
        assert removed_event_name == "edx.itembankblock.content.removed"
        assert removed_event_data == {
            "location": str(self.item_bank.usage_key),
            "result": [{"usage_key": str(uk)} for uk in selected_new_usage_keys],
            "previous_count": 2,
            "max_count": 2,
            "removed": [{"usage_key": str(to_drop_usage_key)}],
            "reason": "invalid",
        }
        _, assigned_event_name, assigned_event_data = self.publisher.call_args_list[1][0]
        assert assigned_event_name == "edx.itembankblock.content.assigned"
        assert assigned_event_data == {
            "location": str(self.item_bank.usage_key),
            "result": [{"usage_key": str(uk)} for uk in selected_new_usage_keys],
            "previous_count": 2,
            "max_count": 2,
            "added": [{"usage_key": str(added_usage_key)}],
        }
        self.publisher.reset_mock()  # Clear these events

        # Now drop both of the selected blocks, so that only 1 remains (less than max_count).
        for selected in selected_new:
            self.store.delete_item(self.course.id.make_usage_key(*selected), self.user_id)
        self._reload_item_bank()
        assert len(self.item_bank.children) == 1  # Sanity: We had 3 blocks, we deleted 2, should be 1 left.

        # The remaining block should be one of the itembank's children, and it shouldn't be one of the ones that we had
        # removed from the children.
        (final,) = self.item_bank.selected_children()
        final_usage_key = self.course.context_key.make_usage_key(*final)
        assert final_usage_key in self.item_bank.children
        assert final in children_initial
        assert final not in {to_keep, to_drop, added}

        # Check that the event says that two blocks were removed and one added
        assert self.publisher.call_count == 2
        _, removed_event_name, removed_event_data = self.publisher.call_args_list[0][0]
        assert removed_event_name == "edx.itembankblock.content.removed"
        assert removed_event_data == {
            "location": str(self.item_bank.usage_key),
            "result": [{"usage_key": str(final_usage_key)}],
            "previous_count": 2,
            "max_count": 2,
            "removed": [{"usage_key": str(uk)} for uk in sorted(selected_new_usage_keys)],
            "reason": "invalid",
        }
        _, assigned_event_name, assigned_event_data = self.publisher.call_args_list[1][0]
        assert assigned_event_name == "edx.itembankblock.content.assigned"
        assert assigned_event_data == {
            "location": str(self.item_bank.usage_key),
            "result": [{"usage_key": str(final_usage_key)}],
            "previous_count": 1,
            "max_count": 2,
            "added": [{"usage_key": str(final_usage_key)}],
        }


def _make_mock_sample(expected_pool, mock_sample):
    """
    A replacement for Random.sample that confirms that the pool and sample size are as expected.
    """
    def sample(_self, pool, desired_sample_size):
        """
        The mock implementation.
        """
        assert set(pool) == set(expected_pool)
        assert len(mock_sample) == desired_sample_size
        return mock_sample

    return sample


def _make_mock_shuffle(mock_result):
    """
    A replacement for Random.shuffle which confirms that the provided mock_result has the same set of items as
    the selection we're shuffling.
    """
    def shuffle(_self, selection):
        """
        The mock implementation.
        """
        assert set(selection) == set(mock_result)
        return mock_result

    return shuffle
