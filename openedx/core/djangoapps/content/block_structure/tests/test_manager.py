"""
Tests for manager.py
"""

from unittest.mock import MagicMock

import ddt
import pytest
from django.test import TestCase

from xmodule.modulestore import ModuleStoreEnum

from ..block_structure import BlockStructureBlockData
from ..exceptions import UsageKeyNotInBlockStructure
from ..manager import BlockStructureManager
from ..transformers import BlockStructureTransformers
from .helpers import (
    ChildrenMapTestMixin,
    MockCache,
    MockModulestoreFactory,
    MockTransformer,
    UsageKeyFactoryMixin,
    mock_registered_transformers,
)


class TestTransformer1(MockTransformer):
    """
    Test Transformer class with basic functionality to verify collected and
    transformed data.
    """
    collect_data_key = 't1.collect'
    transform_data_key = 't1.transform'
    collect_call_count = 0

    @classmethod
    def collect(cls, block_structure):
        """
        Collects block data for the block structure.
        """
        cls._set_block_values(block_structure, cls.collect_data_key)
        cls.collect_call_count += 1

    def transform(self, usage_info, block_structure):
        """
        Transforms the block structure.
        """
        self._set_block_values(block_structure, self.transform_data_key)

    @classmethod
    def assert_collected(cls, block_structure):
        """
        Asserts data was collected for the block structure.
        """
        cls._assert_block_values(block_structure, cls.collect_data_key)

    @classmethod
    def assert_transformed(cls, block_structure):
        """
        Asserts the block structure was transformed.
        """
        cls._assert_block_values(block_structure, cls.transform_data_key)

    @classmethod
    def _set_block_values(cls, block_structure, data_key):
        """
        Sets a value for each block in the given structure, using the given
        data key.
        """
        for block_key in block_structure.topological_traversal():
            block_structure.set_transformer_block_field(
                block_key, cls, data_key, cls._create_block_value(block_key, data_key)
            )

    @classmethod
    def _assert_block_values(cls, block_structure, data_key):
        """
        Verifies the value for each block in the given structure, for the given
        data key.
        """
        for block_key in block_structure.topological_traversal():
            assert (
                block_structure.get_transformer_block_field(
                    block_key,
                    cls,
                    data_key,
                ) == cls._create_block_value(block_key, data_key)
            )

    @classmethod
    def _create_block_value(cls, block_key, data_key):
        """
        Returns a unique deterministic value for the given block key
        and data key.
        """
        return data_key + 't1.val1.' + str(block_key)


@ddt.ddt
class TestBlockStructureManager(UsageKeyFactoryMixin, ChildrenMapTestMixin, TestCase):
    """
    Test class for BlockStructureManager.
    """

    def setUp(self):
        super().setUp()

        TestTransformer1.collect_call_count = 0
        self.registered_transformers = [TestTransformer1()]
        with mock_registered_transformers(self.registered_transformers):
            self.transformers = BlockStructureTransformers(self.registered_transformers)

        self.children_map = self.SIMPLE_CHILDREN_MAP
        self.modulestore = MockModulestoreFactory.create(self.children_map, self.block_key_factory)
        self.cache = MockCache()
        self.bs_manager = BlockStructureManager(self.block_key_factory(0), self.modulestore, self.cache)

    def collect_and_verify(self, expect_modulestore_called, expect_cache_updated):
        """
        Calls the manager's get_collected method and verifies its result
        and behavior.
        """
        self.modulestore.get_items_call_count = 0
        self.cache.set_call_count = 0
        with mock_registered_transformers(self.registered_transformers):
            block_structure = self.bs_manager.get_collected()
        self.assert_block_structure(block_structure, self.children_map)
        TestTransformer1.assert_collected(block_structure)
        if expect_modulestore_called:
            assert self.modulestore.get_items_call_count > 0
        else:
            assert self.modulestore.get_items_call_count == 0
        expected_count = 1 if expect_cache_updated else 0
        assert self.cache.set_call_count == expected_count

    def test_get_transformed(self):
        with mock_registered_transformers(self.registered_transformers):
            block_structure = self.bs_manager.get_transformed(self.transformers)
        self.assert_block_structure(block_structure, self.children_map)
        TestTransformer1.assert_collected(block_structure)
        TestTransformer1.assert_transformed(block_structure)

    def test_get_transformed_with_starting_block(self):
        with mock_registered_transformers(self.registered_transformers):
            block_structure = self.bs_manager.get_transformed(
                self.transformers,
                starting_block_usage_key=self.block_key_factory(1),
            )
        substructure_of_children_map = [[], [3, 4], [], [], []]
        self.assert_block_structure(block_structure, substructure_of_children_map, missing_blocks=[0, 2])
        TestTransformer1.assert_collected(block_structure)
        TestTransformer1.assert_transformed(block_structure)

    def test_get_transformed_with_collected(self):
        with mock_registered_transformers(self.registered_transformers):
            collected_block_structure = self.bs_manager.get_collected()

        # using the same collected block structure,
        # transform at different starting blocks
        for (starting_block, expected_structure, expected_missing_blocks) in [
                (0, [[1, 2], [3, 4], [], [], []], []),
                (1, [[], [3, 4], [], [], []], [0, 2]),
                (2, [[], [], [], [], []], [0, 1, 3, 4]),
        ]:
            block_structure = self.bs_manager.get_transformed(
                self.transformers,
                starting_block_usage_key=self.block_key_factory(starting_block),
                collected_block_structure=collected_block_structure,
            )
            self.assert_block_structure(block_structure, expected_structure, missing_blocks=expected_missing_blocks)

    def test_get_transformed_with_nonexistent_starting_block(self):
        with mock_registered_transformers(self.registered_transformers):
            with pytest.raises(UsageKeyNotInBlockStructure):
                self.bs_manager.get_transformed(self.transformers, starting_block_usage_key=100)

    def test_get_collected_cached(self):
        self.collect_and_verify(expect_modulestore_called=True, expect_cache_updated=True)
        self.collect_and_verify(expect_modulestore_called=False, expect_cache_updated=False)
        assert TestTransformer1.collect_call_count == 1

    def test_update_collected_if_needed(self):
        with mock_registered_transformers(self.registered_transformers):
            assert TestTransformer1.collect_call_count == 0

            self.bs_manager.update_collected_if_needed()
            assert TestTransformer1.collect_call_count == 1

            self.bs_manager.update_collected_if_needed()
            expected_count = 1
            assert TestTransformer1.collect_call_count == expected_count

            self.collect_and_verify(expect_modulestore_called=False, expect_cache_updated=False)

    def test_get_collected_transformer_version(self):
        self.collect_and_verify(expect_modulestore_called=True, expect_cache_updated=True)

        # transformer code writes new schema version; data not re-collected
        TestTransformer1.WRITE_VERSION += 1
        self.collect_and_verify(expect_modulestore_called=False, expect_cache_updated=False)

        # transformer code requires new schema version; data re-collected
        TestTransformer1.READ_VERSION += 1
        self.collect_and_verify(expect_modulestore_called=True, expect_cache_updated=True)

        # old transformer code can read new schema version; data not re-collected
        TestTransformer1.READ_VERSION -= 1
        self.collect_and_verify(expect_modulestore_called=False, expect_cache_updated=False)

        assert TestTransformer1.collect_call_count == 2

    def test_get_collected_structure_version(self):
        self.collect_and_verify(expect_modulestore_called=True, expect_cache_updated=True)
        BlockStructureBlockData.VERSION += 1
        self.collect_and_verify(expect_modulestore_called=False, expect_cache_updated=False)
        assert TestTransformer1.collect_call_count == 1

    def test_clear(self):
        self.collect_and_verify(expect_modulestore_called=True, expect_cache_updated=True)
        self.bs_manager.clear()
        self.collect_and_verify(expect_modulestore_called=True, expect_cache_updated=True)
        assert TestTransformer1.collect_call_count == 2

    def test_update_collected_branch_context_integration(self):
        """
        Integration test to verify the published-only branch context works end-to-end.
        """
        # Track branch setting calls on our mock modulestore
        attr_name = 'branch_setting'
        original_branch_setting = getattr(self.modulestore, attr_name, None)
        branch_setting_calls = []

        def mock_branch_setting(branch, course_key):
            branch_setting_calls.append((branch, course_key))
            # Return a proper context manager that does nothing
            return MagicMock(__enter__=MagicMock(), __exit__=MagicMock())

        # Add the branch_setting method to our mock modulestore
        setattr(self.modulestore, attr_name, mock_branch_setting)

        try:
            with mock_registered_transformers(self.registered_transformers):
                self.bs_manager.get_collected()

            # Verify branch_setting was called with the correct parameters
            self.assertEqual(len(branch_setting_calls), 1)
            branch, course_key = branch_setting_calls[0]
            self.assertEqual(branch, ModuleStoreEnum.Branch.published_only)
            self.assertEqual(course_key, self.block_key_factory(0).course_key)

        finally:
            # Restore original method if it existed
            if original_branch_setting is not None:
                setattr(self.modulestore, attr_name, original_branch_setting)
            elif hasattr(self.modulestore, attr_name):
                delattr(self.modulestore, attr_name)
