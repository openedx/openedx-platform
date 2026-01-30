"""
Visibility Transformer implementation.
"""


from datetime import datetime

from pytz import utc

from openedx.core.djangoapps.content.block_structure.transformer import BlockStructureTransformer
from xmodule.seq_block import SequenceBlock  # lint-amnesty, pylint: disable=wrong-import-order

from .utils import collect_merged_boolean_field, collect_merged_date_field

MAXIMUM_DATE = utc.localize(datetime.max)


class HiddenContentTransformer(BlockStructureTransformer):
    """
    A transformer that enforces the hide_after_due field on
    blocks by removing children blocks from the block structure for
    which the user does not have access. The hide_after_due
    field on a block is percolated down to its descendants, so that
    all blocks enforce the hidden content settings from their ancestors.

    For a block with multiple parents, access is denied only if
    access is denied from all its parents.

    Staff users are exempted from hidden content rules.

    IMPORTANT: Must be run _after_ the DateOverrideTransformer from edx-when
    in case the 'due' date on a block has been shifted for a user.
    """
    WRITE_VERSION = 4
    READ_VERSION = 4
    MERGED_HIDE_AFTER_DUE = 'merged_hide_after_due'
    MERGED_END_DATE = 'merged_end_date'

    @classmethod
    def name(cls):
        """
        Unique identifier for the transformer's class.
        This must match the entry point name in the package configuration.
        """
        return "hidden_content"

    @classmethod
    def _get_merged_hide_after_due(cls, block_structure, block_key):
        """
        Returns whether the block with the given block_key in the
        given block_structure should be hidden after due date per
        computed value from ancestry chain.
        """
        return block_structure.get_transformer_block_field(
            block_key, cls, cls.MERGED_HIDE_AFTER_DUE, False
        )

    @classmethod
    def _get_merged_end_date(cls, block_structure, block_key):
        """
        Returns the merged value for the end date for the block with
        the given block_key in the given block_structure.
        """
        return block_structure.get_transformer_block_field(
            block_key, cls, cls.MERGED_END_DATE
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
            xblock_field_name='hide_after_due',
            merged_field_name=cls.MERGED_HIDE_AFTER_DUE,
        )
        collect_merged_date_field(
            block_structure,
            transformer=cls,
            xblock_field_name='end',
            merged_field_name=cls.MERGED_END_DATE,
            default_date=MAXIMUM_DATE
        )

        block_structure.request_xblock_fields('self_paced', 'due')

    def transform(self, usage_info, block_structure):
        # Users with staff access bypass the Visibility check.
        if usage_info.has_staff_access:
            return [block_structure.create_universal_filter()]

        block_structure.remove_block_traversal(lambda block_key: self._is_block_hidden(block_structure, block_key))

    def _is_block_hidden(self, block_structure, block_key):
        """
        Returns whether the block with the given block_key should
        be hidden, given the current time.
        """
        hide_after_due = self._get_merged_hide_after_due(block_structure, block_key)
        self_paced = block_structure[block_structure.root_block_usage_key].self_paced
        if self_paced:
            hidden_date = self._get_merged_end_date(block_structure, block_key)
        else:
            # Important Note:
            # A small subtlety of grabbing the due date here is that this transformer relies on the
            # DateOverrideTransformer (located in edx-when repo) to first set any overrides (one
            # example is a user receiving an extension on an assignment).
            hidden_date = block_structure.get_xblock_field(block_key, 'due', None) or MAXIMUM_DATE
        return not SequenceBlock.verify_current_content_visibility(hidden_date, hide_after_due)
