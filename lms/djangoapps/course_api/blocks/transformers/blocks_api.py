"""
Blocks API Transformer
"""


from openedx.core.djangoapps.content.block_structure.transformer import BlockStructureTransformer

from .block_counts import BlockCountsTransformer
from .block_depth import BlockDepthTransformer
from .extra_fields import ExtraFieldsTransformer
from .navigation import BlockNavigationTransformer
from .student_view import StudentViewTransformer
from .video_stream_priority import VideoBlockStreamPriorityTransformer
from .video_urls import VideoBlockURLTransformer


class BlocksAPITransformer(BlockStructureTransformer):
    """
    Umbrella transformer that contains all the transformers needed by the
    Course Blocks API.

    Contained Transformers (processed in this order):
        StudentViewTransformer
        BlockCountsTransformer
        BlockDepthTransformer
        BlockNavigationTransformer
        VideoBlockStreamPriorityTransformer
        ExtraFieldsTransformer

    Note:
        * BlockDepthTransformer must be executed before BlockNavigationTransformer.
        * StudentViewTransformer must be executed before VideoBlockURLTransformer.
    """

    WRITE_VERSION = 1
    READ_VERSION = 1
    STUDENT_VIEW_DATA = 'student_view_data'
    STUDENT_VIEW_MULTI_DEVICE = 'student_view_multi_device'

    def __init__(self, block_types_to_count, requested_student_view_data, depth=None, nav_depth=None):
        self.block_types_to_count = block_types_to_count
        self.requested_student_view_data = requested_student_view_data
        self.depth = depth
        self.nav_depth = nav_depth

    @classmethod
    def name(cls):
        return "blocks_api"

    @classmethod
    def collect(cls, block_structure):
        """
        Collects any information that's necessary to execute this transformer's
        transform method.
        """
        # collect basic xblock fields
        block_structure.request_xblock_fields(
            'graded',
            'format',
            'display_name',
            'category',
            'due',
            'show_correctness',
            'icon_class',
        )

        # collect data from containing transformers
        StudentViewTransformer.collect(block_structure)
        BlockCountsTransformer.collect(block_structure)
        BlockDepthTransformer.collect(block_structure)
        BlockNavigationTransformer.collect(block_structure)
        ExtraFieldsTransformer.collect(block_structure)

        # TODO support olx_data by calling export_to_xml(?)

    def transform(self, usage_info, block_structure):
        """
        Mutates block_structure based on the given usage_info.
        """
        StudentViewTransformer(self.requested_student_view_data).transform(usage_info, block_structure)
        BlockCountsTransformer(self.block_types_to_count).transform(usage_info, block_structure)
        BlockDepthTransformer(self.depth).transform(usage_info, block_structure)
        BlockNavigationTransformer(self.nav_depth).transform(usage_info, block_structure)
        VideoBlockURLTransformer().transform(usage_info, block_structure)
        VideoBlockStreamPriorityTransformer().transform(usage_info, block_structure)
        ExtraFieldsTransformer().transform(usage_info, block_structure)
