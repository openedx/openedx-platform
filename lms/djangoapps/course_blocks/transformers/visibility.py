"""
Visibility Transformer implementation.
"""


from openedx.core.djangoapps.content.block_structure.transformer import (
    BlockStructureTransformer,
    FilteringTransformerMixin
)

from .utils import collect_merged_boolean_field


class VisibilityTransformer(FilteringTransformerMixin, BlockStructureTransformer):
    """
    A transformer that enforces the visible_to_staff_only field on
    blocks by removing blocks from the block structure for which the
    user does not have access. The visible_to_staff_only field on a
    block is percolated down to its descendants, so that all blocks
    enforce the visibility settings from their ancestors.

    For a block with multiple parents, access is denied only if
    visibility is denied for all its parents.

    Staff users are exempted from visibility rules.
    """
    WRITE_VERSION = 1
    READ_VERSION = 1

    MERGED_VISIBLE_TO_STAFF_ONLY = 'merged_visible_to_staff_only'

    @classmethod
    def name(cls):
        """
        Unique identifier for the transformer's class.
        This must match the entry point name in the package configuration.
        """
        return "visibility"

    @classmethod
    def _get_visible_to_staff_only(cls, block_structure, block_key):
        """
        Returns whether the block with the given block_key in the
        given block_structure should be visible to staff only per
        computed value from ancestry chain.
        """
        return block_structure.get_transformer_block_field(
            block_key, cls, cls.MERGED_VISIBLE_TO_STAFF_ONLY, False
        )

    @classmethod
    def collect(cls, block_structure):
        """
        Collects any information that's necessary to execute this
        transformer's transform method.
        """
        collect_merged_boolean_field(
            block_structure,
            transformer=cls,
            xblock_field_name='visible_to_staff_only',
            merged_field_name=cls.MERGED_VISIBLE_TO_STAFF_ONLY,
        )

    def transform_block_filters(self, usage_info, block_structure):
        # Users with staff access bypass the Visibility check.
        if usage_info.has_staff_access:
            return [block_structure.create_universal_filter()]

        return [
            block_structure.create_removal_filter(
                lambda block_key: self._get_visible_to_staff_only(block_structure, block_key),
            )
        ]
