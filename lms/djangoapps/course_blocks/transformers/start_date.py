"""
Start Date Transformer implementation.
"""

from datetime import datetime

from pytz import UTC

from lms.djangoapps.courseware.access_utils import check_start_date
from openedx.core.djangoapps.content.block_structure.transformer import (
    BlockStructureTransformer,
    FilteringTransformerMixin
)
from xmodule.course_metadata_utils import DEFAULT_START_DATE  # lint-amnesty, pylint: disable=wrong-import-order

from .utils import collect_merged_date_field


class StartDateTransformer(FilteringTransformerMixin, BlockStructureTransformer):
    """
    A transformer that enforces the 'start' and 'days_early_for_beta'
    fields on blocks by removing blocks from the block structure for
    which the user does not have access. The 'start' field on a
    block is percolated down to its descendants, so that all blocks
    enforce the 'start' field from their ancestors.  The assumed
    'start' value for a block is then the maximum of its parent and its
    own.

    For a block with multiple parents, the assumed parent start date
    value is a computed minimum of the start dates of all its parents.
    So as long as one parent chain allows access, the block has access.

    Staff users are exempted from visibility rules.
    """
    WRITE_VERSION = 1
    READ_VERSION = 1
    MERGED_START_DATE = 'merged_start_date'

    @classmethod
    def name(cls):
        """
        Unique identifier for the transformer's class.
        This must match the entry point name in the package configuration.
        """
        return "start_date"

    @classmethod
    def _check_has_scheduled_content(cls, block_structure, scheduled_content_condition):
        '''
        Returns a block structure where the root course block has been
        updated to include a has_scheduled_content field (True if the course
        has any blocks with release dates in the future, False otherwise).
        '''
        has_scheduled_content = False
        for block_key in block_structure.topological_traversal():
            if scheduled_content_condition(block_key):
                has_scheduled_content = True
                break

        block_structure.override_xblock_field(
            block_structure.root_block_usage_key, 'has_scheduled_content', has_scheduled_content
        )

    @classmethod
    def _get_merged_start_date(cls, block_structure, block_key):
        """
        Returns the merged value for the start date for the block with
        the given block_key in the given block_structure.
        """
        return block_structure.get_transformer_block_field(
            block_key, cls, cls.MERGED_START_DATE, False
        )

    @classmethod
    def collect(cls, block_structure):
        """
        Collects any information that's necessary to execute this
        transformer's transform method.
        """
        block_structure.request_xblock_fields('days_early_for_beta')

        collect_merged_date_field(
            block_structure,
            transformer=cls,
            xblock_field_name='start',
            merged_field_name=cls.MERGED_START_DATE,
            default_date=DEFAULT_START_DATE,
            func_merge_parents=min,
            func_merge_ancestors=max,
        )

    def transform_block_filters(self, usage_info, block_structure):
        # Users with staff access bypass the Start Date check.
        if usage_info.has_staff_access or usage_info.allow_start_dates_in_future:
            return [block_structure.create_universal_filter()]

        now = datetime.now(UTC)

        def _removal_condition(block_key):
            return not check_start_date(
                usage_info.user,
                block_structure.get_xblock_field(block_key, 'days_early_for_beta'),
                self._get_merged_start_date(block_structure, block_key),
                usage_info.course_key,
                now=now
            )

        if usage_info.include_has_scheduled_content:
            self._check_has_scheduled_content(block_structure, _removal_condition)

        return [block_structure.create_removal_filter(_removal_condition)]
