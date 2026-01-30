"""
Tests for block_structure_factory.py
"""

import pytest
from django.test import TestCase

from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator
from xmodule.modulestore.exceptions import ItemNotFoundError

from ..exceptions import BlockStructureNotFound
from ..factory import BlockStructureFactory
from ..store import BlockStructureStore
from .helpers import ChildrenMapTestMixin, MockCache, MockModulestoreFactory, MockXBlock, MockModulestore


class TestBlockStructureFactory(TestCase, ChildrenMapTestMixin):
    """
    Tests for BlockStructureFactory
    """

    def block_key_factory(self, block_id):
        """
        Returns a usage_key object for the given block_id.
        This overrides the method in the ChildrenMapTestMixin.
        """
        return CourseKey.from_string("course-v1:org+course+run").make_usage_key("html", str(block_id))

    def setUp(self):
        super().setUp()
        self.children_map = self.SIMPLE_CHILDREN_MAP
        self.modulestore = MockModulestoreFactory.create(self.children_map, self.block_key_factory)

    def test_from_modulestore(self):
        usage_key = CourseKey.from_string("course-v1:org+course+run").make_usage_key("html", "0")
        block_structure = BlockStructureFactory.create_from_modulestore(
            root_block_usage_key=usage_key, modulestore=self.modulestore
        )
        self.assert_block_structure(block_structure, self.children_map)

    def test_from_modulestore_fail(self):
        with pytest.raises(ItemNotFoundError):
            BlockStructureFactory.create_from_modulestore(
                root_block_usage_key=len(self.children_map) + 1,
                modulestore=self.modulestore,
            )

    def test_from_cache(self):
        store = BlockStructureStore(MockCache())
        block_structure = self.create_block_structure(self.children_map)
        store.add(block_structure)
        from_cache_block_structure = BlockStructureFactory.create_from_store(
            block_structure.root_block_usage_key,
            store,
        )
        self.assert_block_structure(from_cache_block_structure, self.children_map)

    def test_from_cache_none(self):
        store = BlockStructureStore(MockCache())
        # Non-existent usage key
        usage_key = CourseKey.from_string("course-v1:org+course+run").make_usage_key("html", "0")
        with pytest.raises(BlockStructureNotFound):
            BlockStructureFactory.create_from_store(
                root_block_usage_key=usage_key,
                block_structure_store=store,
            )

    def test_new(self):
        usage_key = CourseKey.from_string("course-v1:org+course+run").make_usage_key("html", "0")
        block_structure = BlockStructureFactory.create_from_modulestore(
            root_block_usage_key=usage_key, modulestore=self.modulestore
        )
        new_structure = BlockStructureFactory.create_new(
            block_structure.root_block_usage_key,
            block_structure._block_relations,  # pylint: disable=protected-access
            block_structure.transformer_data,
            block_structure._block_data_map,  # pylint: disable=protected-access
        )
        self.assert_block_structure(new_structure, self.children_map)

    def test_from_modulestore_normalizes_locations_with_branch_info(self):
        """
        Test that locations with branch/version information are normalized
        when building block structures.

        This test verifies the fix for PR #37866, which ensures that when
        creating block structures within the published_only branch context,
        locations are normalized by removing branch/version information.
        This prevents comparison mismatches when filtering inaccessible blocks.

        Without the fix, locations with branch info would be stored as-is,
        causing issues when comparing with normalized locations later.
        """
        # Create a course key with branch information to simulate
        # the published_only branch context
        course_key_with_branch = CourseLocator('org', 'course', 'run', branch='published')
        root_usage_key = BlockUsageLocator(
            course_key=course_key_with_branch,
            block_type='html',
            block_id='0'
        )

        # Create a modulestore with xblocks that have locations containing branch info
        modulestore = MockModulestore()
        blocks = {}
        children_map = self.SIMPLE_CHILDREN_MAP

        # Create blocks with branch information in their locations
        for block_id, children in enumerate(children_map):
            # Create location with branch info
            block_location = BlockUsageLocator(
                course_key=course_key_with_branch,
                block_type='html',
                block_id=str(block_id)
            )
            # Create child locations with branch info
            child_locations = [
                BlockUsageLocator(
                    course_key=course_key_with_branch,
                    block_type='html',
                    block_id=str(child_id)
                )
                for child_id in children
            ]
            blocks[block_location] = MockXBlock(
                location=block_location,
                children=child_locations,
                modulestore=modulestore
            )
        modulestore.set_blocks(blocks)

        # Build block structure from modulestore
        block_structure = BlockStructureFactory.create_from_modulestore(
            root_block_usage_key=root_usage_key,
            modulestore=modulestore
        )

        # Verify that all stored block keys are normalized (without branch info)
        # This is the key assertion: with the fix, all keys should be normalized
        for block_key in block_structure:
            # The block_key should equal its normalized version
            normalized_key = block_key.for_branch(None)
            self.assertEqual(
                block_key,
                normalized_key,
                f"Block key {block_key} should be normalized (without branch info). "
                f"Normalized version: {normalized_key}"
            )
            # Verify it doesn't have branch information in the course_key
            if hasattr(block_key.course_key, 'branch'):
                self.assertIsNone(
                    block_key.course_key.branch,
                    f"Block key {block_key} should not have branch information"
                )
