"""
Tests for block_structure.py
"""


import itertools

# pylint: disable=protected-access
from collections import namedtuple
from copy import deepcopy
from datetime import datetime
from unittest import TestCase

import ddt

from openedx.core.lib.graph_traversals import traverse_post_order

from ..block_structure import BlockStructure, BlockStructureModulestoreData
from ..exceptions import TransformerException
from .helpers import ChildrenMapTestMixin, MockTransformer, MockXBlock


@ddt.ddt
class TestBlockStructure(TestCase, ChildrenMapTestMixin):
    """
    Tests for BlockStructure
    """

    @ddt.data(
        [],
        ChildrenMapTestMixin.SIMPLE_CHILDREN_MAP,
        ChildrenMapTestMixin.LINEAR_CHILDREN_MAP,
        ChildrenMapTestMixin.DAG_CHILDREN_MAP,
    )
    def test_relations(self, children_map):
        block_structure = self.create_block_structure(children_map, BlockStructure)

        # get_children
        for parent, children in enumerate(children_map):
            self.assertSetEqual(set(block_structure.get_children(parent)), set(children))

        # get_parents
        for child, parents in enumerate(self.get_parents_map(children_map)):
            self.assertSetEqual(set(block_structure.get_parents(child)), set(parents))

        # __contains__
        for node in range(len(children_map)):
            assert node in block_structure
        assert (len(children_map) + 1) not in block_structure


@ddt.ddt
class TestBlockStructureData(TestCase, ChildrenMapTestMixin):
    """
    Tests for BlockStructureBlockData and BlockStructureModulestoreData
    """

    def test_non_versioned_transformer(self):
        class TestNonVersionedTransformer(MockTransformer):
            """
            Test transformer with default version number (0).
            """
            WRITE_VERSION = 0
            READ_VERSION = 0

        block_structure = BlockStructureModulestoreData(root_block_usage_key=0)

        with self.assertRaisesRegex(TransformerException, "Version attributes are not set"):
            block_structure._add_transformer(TestNonVersionedTransformer())

    def test_transformer_data(self):
        # transformer test cases
        TransformerInfo = namedtuple("TransformerInfo", "transformer structure_wide_data block_specific_data")  # pylint: disable=invalid-name
        transformers_info = [
            TransformerInfo(
                transformer=MockTransformer(),
                structure_wide_data=[("t1.global1", "t1.g.val1"), ("t1.global2", "t1.g.val2")],
                block_specific_data={
                    "B1": [("t1.key1", "t1.b1.val1"), ("t1.key2", "t1.b1.val2")],
                    "B2": [("t1.key1", "t1.b2.val1"), ("t1.key2", "t1.b2.val2")],
                    "B3": [("t1.key1", True), ("t1.key2", False)],
                    "B4": [("t1.key1", None), ("t1.key2", False)],
                },
            ),
            TransformerInfo(
                transformer=MockTransformer(),
                structure_wide_data=[("t2.global1", "t2.g.val1"), ("t2.global2", "t2.g.val2")],
                block_specific_data={
                    "B1": [("t2.key1", "t2.b1.val1"), ("t2.key2", "t2.b1.val2")],
                    "B2": [("t2.key1", "t2.b2.val1"), ("t2.key2", "t2.b2.val2")],
                },
            ),
        ]

        # create block structure
        block_structure = BlockStructureModulestoreData(root_block_usage_key=0)

        # set transformer data
        for t_info in transformers_info:
            block_structure._add_transformer(t_info.transformer)
            for key, val in t_info.structure_wide_data:
                block_structure.set_transformer_data(t_info.transformer, key, val)
            for block, block_data in t_info.block_specific_data.items():
                for key, val in block_data:
                    block_structure.set_transformer_block_field(block, t_info.transformer, key, val)

        # verify transformer data
        for t_info in transformers_info:
            assert block_structure._get_transformer_data_version(t_info.transformer) == MockTransformer.WRITE_VERSION
            for key, val in t_info.structure_wide_data:
                assert block_structure.get_transformer_data(t_info.transformer, key) == val
            for block, block_data in t_info.block_specific_data.items():
                for key, val in block_data:
                    assert block_structure.get_transformer_block_field(block, t_info.transformer, key) == val

    def test_xblock_data(self):
        # block test cases
        blocks = [
            MockXBlock("A", {}),
            MockXBlock("B", {"field1": "B.val1"}),
            MockXBlock("C", {"field1": "C.val1", "field2": "C.val2"}),
            MockXBlock("D", {"field1": True, "field2": False}),
            MockXBlock("E", {"field1": None, "field2": False}),
        ]

        # add each block
        block_structure = BlockStructureModulestoreData(root_block_usage_key=0)
        for block in blocks:
            block_structure._add_xblock(block.location, block)
            block_structure._get_or_create_block(block.location)

        # request fields
        fields = ["field1", "field2", "field3"]
        block_structure.request_xblock_fields(*fields)

        # verify fields have not been collected yet
        for block in blocks:
            bs_block = block_structure[block.location]
            for field in fields:
                assert getattr(bs_block, field, None) is None

        # collect fields
        block_structure._collect_requested_xblock_fields()

        # verify values of collected fields
        for block in blocks:
            bs_block = block_structure[block.location]
            for field in fields:
                assert getattr(bs_block, field, None) == block.field_map.get(field)

    def test_xblock_field_override(self):
        # block field override test cases
        attribute = {
            "start": datetime(2017, 3, 23, 16, 38, 46, 271475),
            "display_name": "Foo block",
            "due": datetime(2017, 5, 23, 16, 38, 46, 271475)
        }
        override_due_date = datetime(2018, 2, 23, 16, 38, 46, 271475)
        block = MockXBlock("Foo", attribute)

        # add each block
        block_structure = BlockStructureModulestoreData(root_block_usage_key=0)
        block_structure._add_xblock(block.location, block)
        block_structure._get_or_create_block(block.location)

        fields = list(attribute.keys())
        block_structure.request_xblock_fields(*fields)

        # collect fields
        block_structure._collect_requested_xblock_fields()

        # verify values of collected fields
        bs_block = block_structure[block.location]
        for field in fields:
            assert getattr(bs_block, field, None) == block.field_map.get(field)

        block_structure.override_xblock_field(
            block.location,
            "due",
            override_due_date
        )
        assert block_structure.get_xblock_field(block.location, 'due', None) == override_due_date

    @ddt.data(
        *itertools.product(
            [True, False],
            list(range(7)),
            [
                ChildrenMapTestMixin.SIMPLE_CHILDREN_MAP,
                ChildrenMapTestMixin.LINEAR_CHILDREN_MAP,
                ChildrenMapTestMixin.DAG_CHILDREN_MAP,
            ],
        )
    )
    @ddt.unpack
    def test_remove_block(self, keep_descendants, block_to_remove, children_map):
        ### skip test if invalid
        if (block_to_remove >= len(children_map)) or (keep_descendants and block_to_remove == 0):
            return

        ### create structure
        block_structure = self.create_block_structure(children_map)
        parents_map = self.get_parents_map(children_map)

        ### verify blocks pre-exist
        self.assert_block_structure(block_structure, children_map)

        ### remove block
        block_structure.remove_block(block_to_remove, keep_descendants)
        missing_blocks = [block_to_remove]

        ### compute and verify updated children_map
        removed_children_map = deepcopy(children_map)
        removed_children_map[block_to_remove] = []
        for parent in parents_map[block_to_remove]:
            removed_children_map[parent].remove(block_to_remove)

        if keep_descendants:
            # update the graph connecting the old parents to the old children
            for child in children_map[block_to_remove]:
                for parent in parents_map[block_to_remove]:
                    removed_children_map[parent].append(child)

        self.assert_block_structure(block_structure, removed_children_map, missing_blocks)

        ### prune the structure
        block_structure._prune_unreachable()

        ### compute and verify updated children_map
        pruned_children_map = deepcopy(removed_children_map)

        if not keep_descendants:
            pruned_parents_map = self.get_parents_map(pruned_children_map)
            # update all descendants
            for child in children_map[block_to_remove]:
                # if the child has another parent, continue
                if pruned_parents_map[child]:
                    continue
                for block in traverse_post_order(child, get_children=lambda block: pruned_children_map[block]):
                    # add descendant to missing blocks and empty its
                    # children
                    missing_blocks.append(block)
                    pruned_children_map[block] = []

        self.assert_block_structure(block_structure, pruned_children_map, missing_blocks)

    def test_remove_block_traversal(self):
        block_structure = self.create_block_structure(ChildrenMapTestMixin.LINEAR_CHILDREN_MAP)
        block_structure.remove_block_traversal(lambda block: block == 2)
        self.assert_block_structure(block_structure, [[1], [], [], []], missing_blocks=[2])

    def test_copy(self):
        def _set_value(structure, value):
            """
            Sets a test transformer block field to the given value in the given structure.
            """
            structure.set_transformer_block_field(1, 'transformer', 'test_key', value)

        def _get_value(structure):
            """
            Returns the value of the test transformer block field in the given structure.
            """
            return structure[1].transformer_data['transformer'].test_key

        # create block structure and verify blocks pre-exist
        block_structure = self.create_block_structure(ChildrenMapTestMixin.LINEAR_CHILDREN_MAP)
        self.assert_block_structure(block_structure, [[1], [2], [3], []])
        _set_value(block_structure, 'original_value')

        # create a new copy of the structure and verify they are equivalent
        new_copy = block_structure.copy()
        assert block_structure.root_block_usage_key == new_copy.root_block_usage_key
        for block in block_structure:
            assert block in new_copy
            assert block_structure.get_parents(block) == new_copy.get_parents(block)
            assert block_structure.get_children(block) == new_copy.get_children(block)
            assert _get_value(block_structure) == _get_value(new_copy)

        # verify edits to original block structure do not affect the copy
        block_structure.remove_block(2, keep_descendants=True)
        self.assert_block_structure(block_structure, [[1], [3], [], []], missing_blocks=[2])
        self.assert_block_structure(new_copy, [[1], [2], [3], []])

        _set_value(block_structure, 'edit1')
        assert _get_value(block_structure) == 'edit1'
        assert _get_value(new_copy) == 'original_value'

        # verify edits to copy do not affect the original
        new_copy.remove_block(3, keep_descendants=True)
        self.assert_block_structure(block_structure, [[1], [3], [], []], missing_blocks=[2])
        self.assert_block_structure(new_copy, [[1], [2], [], []], missing_blocks=[3])

        _set_value(new_copy, 'edit2')
        assert _get_value(block_structure) == 'edit1'
        assert _get_value(new_copy) == 'edit2'
