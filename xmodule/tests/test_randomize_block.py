"""
Test cases covering workflows and behaviors for the Randomize XModule
"""
from unittest.mock import Mock

from fs.memoryfs import MemoryFS
from lxml import etree

from xmodule.modulestore.tests.factories import CourseFactory
from xmodule.modulestore.tests.utils import MixedSplitTestCase
from xmodule.randomize_block import RandomizeBlock
from xmodule.tests import prepare_block_runtime

from .test_course_block import DummyModuleStoreRuntime


class RandomizeBlockTest(MixedSplitTestCase):
    """
    Base class for tests of RandomizeBlock (randomize_block.py)
    """
    maxDiff = None

    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create(modulestore=self.store)
        self.chapter = self.make_block("chapter", self.course)
        self.sequential = self.make_block("sequential", self.chapter)
        self.vertical = self.make_block("vertical", self.sequential)
        self.randomize_block = self.make_block(
            "randomize",
            self.vertical,
            display_name="Hello Randomize",
        )
        self.child_blocks = [
            self.make_block("html", self.randomize_block, display_name=f"Hello HTML {i}")
            for i in range(1, 4)
        ]

    def _bind_module_system(self, block, user_id):
        """
        Bind module system to block so we can access student-specific data.
        """
        user = Mock(name='get_test_system.user', id=user_id, is_staff=False)
        prepare_block_runtime(block.runtime, course_id=block.location.course_key, user=user)

    def test_xml_export_import_cycle(self):
        """
        Test the export-import cycle.
        """
        randomize_block = self.store.get_item(self.randomize_block.location)

        expected_olx = (
            '<randomize display_name="{block.display_name}">\n'
            '  <html url_name="{block.children[0].block_id}"/>\n'
            '  <html url_name="{block.children[1].block_id}"/>\n'
            '  <html url_name="{block.children[2].block_id}"/>\n'
            '</randomize>\n'
        ).format(
            block=randomize_block,
        )

        export_fs = MemoryFS()
        # Set the virtual FS to export the olx to.
        randomize_block.runtime.export_fs = export_fs  # pylint: disable=protected-access

        # Export the olx.
        node = etree.Element("unknown_root")
        randomize_block.add_xml_to_node(node)

        # Read it back
        with export_fs.open('{dir}/{file_name}.xml'.format(
            dir=randomize_block.scope_ids.usage_id.block_type,
            file_name=randomize_block.scope_ids.usage_id.block_id
        )) as f:
            exported_olx = f.read()

        # And compare.
        assert exported_olx == expected_olx

        runtime = DummyModuleStoreRuntime(load_error_blocks=True, course_id=randomize_block.location.course_key)
        runtime.resources_fs = export_fs

        # Now import it.
        olx_element = etree.fromstring(exported_olx)
        imported_randomize_block = RandomizeBlock.parse_xml(olx_element, runtime, None)

        # Check the new XBlock has the same properties as the old one.
        assert imported_randomize_block.display_name == randomize_block.display_name
        assert len(imported_randomize_block.children) == 3
        assert imported_randomize_block.children == randomize_block.children

    def test_children_seen_by_a_user(self):
        """
        Test that each student sees only one block as a child of the LibraryContent block.
        """
        randomize_block = self.store.get_item(self.randomize_block.location)
        self._bind_module_system(randomize_block, 3)

        # Make sure the runtime knows that the block's children vary per-user:
        assert randomize_block.has_dynamic_children()

        assert len(randomize_block.children) == 3

        # Check how many children each user will see:
        assert len(randomize_block.get_child_blocks()) == 1
        assert randomize_block.get_child_blocks()[0].display_name == 'Hello HTML 1'
        # Check that get_content_titles() doesn't return titles for hidden/unused children
        # get_content_titles() is not overridden in RandomizeBlock so titles of the 3 children are returned.
        assert len(randomize_block.get_content_titles()) == 3

        # Bind to another user and check a different child block is displayed to user.
        randomize_block = self.store.get_item(self.randomize_block.location)
        self._bind_module_system(randomize_block, 1)
        assert randomize_block.get_child_blocks()[0].display_name == 'Hello HTML 2'
