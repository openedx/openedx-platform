"""
Tests for BlockCountsTransformer.
"""

# pylint: disable=protected-access


from openedx.core.djangoapps.content.block_structure.factory import BlockStructureFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import SampleCourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..block_counts import BlockCountsTransformer


class TestBlockCountsTransformer(ModuleStoreTestCase):
    """
    Test behavior of BlockCountsTransformer
    """

    def setUp(self):
        super().setUp()
        self.course_key = SampleCourseFactory.create().id
        self.course_usage_key = self.store.make_course_usage_key(self.course_key)
        self.block_structure = BlockStructureFactory.create_from_modulestore(self.course_usage_key, self.store)

    def test_transform(self):
        # collect phase
        BlockCountsTransformer.collect(self.block_structure)
        self.block_structure._collect_requested_xblock_fields()

        # transform phase
        BlockCountsTransformer(['problem', 'chapter']).transform(usage_info=None, block_structure=self.block_structure)

        # block_counts
        chapter_x_key = self.course_key.make_usage_key('chapter', 'chapter_x')
        block_counts_for_chapter_x = self.block_structure.get_transformer_block_data(
            chapter_x_key, BlockCountsTransformer,
        )
        block_counts_for_course = self.block_structure.get_transformer_block_data(
            self.course_usage_key, BlockCountsTransformer,
        )

        # verify count of chapters
        assert block_counts_for_course.chapter == 2

        # verify count of problems
        assert block_counts_for_course.problem == 6
        assert block_counts_for_chapter_x.problem == 3

        # verify other block types are not counted
        for block_type in ['course', 'html', 'video']:
            assert not hasattr(block_counts_for_course, block_type)
            assert not hasattr(block_counts_for_chapter_x, block_type)
