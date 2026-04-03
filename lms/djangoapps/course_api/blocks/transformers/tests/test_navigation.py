# pylint: disable=protected-access
"""
Tests for BlockNavigationTransformer.
"""


from unittest import TestCase

import ddt

from lms.djangoapps.course_api.blocks.transformers.block_depth import BlockDepthTransformer
from lms.djangoapps.course_api.blocks.transformers.navigation import BlockNavigationTransformer
from openedx.core.djangoapps.content.block_structure.block_structure import BlockStructureModulestoreData
from openedx.core.djangoapps.content.block_structure.factory import BlockStructureFactory
from openedx.core.djangoapps.content.block_structure.tests.helpers import ChildrenMapTestMixin
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import SampleCourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@ddt.ddt
class BlockNavigationTransformerTestCase(TestCase, ChildrenMapTestMixin):
    """
    Course-agnostic test class for testing the Navigation transformer.
    """

    @ddt.data(
        (0, 0, [], []),

        (0, 0, ChildrenMapTestMixin.LINEAR_CHILDREN_MAP, [[], [], [], []]),
        (None, 0, ChildrenMapTestMixin.LINEAR_CHILDREN_MAP, [[1, 2, 3], [], [], []]),
        (None, 1, ChildrenMapTestMixin.LINEAR_CHILDREN_MAP, [[1], [2, 3], [], []]),
        (None, 2, ChildrenMapTestMixin.LINEAR_CHILDREN_MAP, [[1], [2], [3], []]),
        (None, 3, ChildrenMapTestMixin.LINEAR_CHILDREN_MAP, [[1], [2], [3], []]),
        (None, 4, ChildrenMapTestMixin.LINEAR_CHILDREN_MAP, [[1], [2], [3], []]),
        (1, 4, ChildrenMapTestMixin.LINEAR_CHILDREN_MAP, [[1], [], [], []]),
        (2, 4, ChildrenMapTestMixin.LINEAR_CHILDREN_MAP, [[1], [2], [], []]),

        (0, 0, ChildrenMapTestMixin.DAG_CHILDREN_MAP, [[], [], [], [], [], [], []]),
        (0, 0, ChildrenMapTestMixin.DAG_CHILDREN_MAP, [[], [], [], [], [], [], []]),
        (None, 0, ChildrenMapTestMixin.DAG_CHILDREN_MAP, [[1, 2, 3, 4, 5, 6], [], [], [], [], [], []]),
        (None, 1, ChildrenMapTestMixin.DAG_CHILDREN_MAP, [[1, 2], [3, 5, 6], [3, 4, 5, 6], [], [], [], []]),
        (None, 2, ChildrenMapTestMixin.DAG_CHILDREN_MAP, [[1, 2], [3], [3, 4], [5, 6], [], [], []]),
        (None, 3, ChildrenMapTestMixin.DAG_CHILDREN_MAP, [[1, 2], [3], [3, 4], [5, 6], [], [], []]),
        (None, 4, ChildrenMapTestMixin.DAG_CHILDREN_MAP, [[1, 2], [3], [3, 4], [5, 6], [], [], []]),
        (1, 4, ChildrenMapTestMixin.DAG_CHILDREN_MAP, [[1, 2], [], [], [], [], [], []]),
        (2, 4, ChildrenMapTestMixin.DAG_CHILDREN_MAP, [[1, 2], [3], [3, 4], [], [], [], []]),
    )
    @ddt.unpack
    def test_navigation(self, depth, nav_depth, children_map, expected_nav_map):

        block_structure = self.create_block_structure(children_map, BlockStructureModulestoreData)
        BlockDepthTransformer(depth).transform(usage_info=None, block_structure=block_structure)
        BlockNavigationTransformer(nav_depth).transform(usage_info=None, block_structure=block_structure)
        block_structure._prune_unreachable()

        for block_key, expected_nav in enumerate(expected_nav_map):
            self.assertSetEqual(
                {str(block) for block in expected_nav},
                set(
                    block_structure.get_transformer_block_field(
                        block_key,
                        BlockNavigationTransformer,
                        BlockNavigationTransformer.BLOCK_NAVIGATION,
                        []
                    )
                ),
            )


class BlockNavigationTransformerCourseTestCase(ModuleStoreTestCase):
    """
    Uses SampleCourseFactory and Modulestore to test the Navigation transformer,
    specifically to test enforcement of the hide_from_toc field
    """

    def test_hide_from_toc(self):
        course_key = SampleCourseFactory.create().id
        course_usage_key = self.store.make_course_usage_key(course_key)

        # hide chapter_x from TOC
        chapter_x_key = course_key.make_usage_key('chapter', 'chapter_x')
        chapter_x = self.store.get_item(chapter_x_key)
        chapter_x.hide_from_toc = True
        self.store.update_item(chapter_x, ModuleStoreEnum.UserID.test)

        block_structure = BlockStructureFactory.create_from_modulestore(course_usage_key, self.store)

        # collect phase
        BlockDepthTransformer.collect(block_structure)
        BlockNavigationTransformer.collect(block_structure)
        block_structure._collect_requested_xblock_fields()

        assert chapter_x_key in block_structure

        # transform phase
        BlockDepthTransformer().transform(usage_info=None, block_structure=block_structure)
        BlockNavigationTransformer(0).transform(usage_info=None, block_structure=block_structure)
        block_structure._prune_unreachable()

        assert chapter_x_key in block_structure

        course_descendants = block_structure.get_transformer_block_field(
            course_usage_key,
            BlockNavigationTransformer,
            BlockNavigationTransformer.BLOCK_NAVIGATION,
        )

        # chapter_y and its descendants should be included
        for block_key in [
                course_key.make_usage_key('chapter', 'chapter_y'),
                course_key.make_usage_key('sequential', 'sequential_y1'),
                course_key.make_usage_key('vertical', 'vertical_y1a'),
                course_key.make_usage_key('problem', 'problem_y1a_1'),
        ]:
            assert str(block_key) in course_descendants

        # chapter_x and its descendants should not be included
        for block_key in [
                chapter_x_key,
                course_key.make_usage_key('sequential', 'sequential_x1'),
                course_key.make_usage_key('vertical', 'vertical_x1a'),
                course_key.make_usage_key('problem', 'problem_x1a_1'),
        ]:
            assert str(block_key) not in course_descendants
