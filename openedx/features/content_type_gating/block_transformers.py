"""
Content Type Gate Transformer implementation.
Limits access for certain users to certain types of content.
"""


from django.conf import settings

from lms.djangoapps.course_blocks.transformers.user_partitions import UserPartitionTransformer
from openedx.core.djangoapps.content.block_structure.transformer import BlockStructureTransformer
from openedx.features.content_type_gating.helpers import CONTENT_GATING_PARTITION_ID
from openedx.features.content_type_gating.models import ContentTypeGatingConfig


class ContentTypeGateTransformer(BlockStructureTransformer):
    """
    A transformer that adds a partition condition for all graded content
    so that the content is only visible to verified users.

    This transformer requires that the UserPartitionTransformer also be included in your transformer list.
    """
    WRITE_VERSION = 1
    READ_VERSION = 1

    @classmethod
    def name(cls):
        """
        Unique identifier for the transformer's class.
        This must match the entry point name in the package configuration.
        """
        return "content_type_gate"

    @classmethod
    def collect(cls, block_structure):
        """
        Collects any information that's necessary to execute this
        transformer's transform method.
        """
        block_structure.request_xblock_fields('group_access', 'graded', 'has_score', 'weight')

    def _set_contains_gated_content_on_parents(self, block_structure, block_key):
        """
        This will recursively set a field on all the parents of a block if one of the problems
        inside of it is content gated. `contains_gated_content` can then be used to indicate something
        in the blocks subtree is gated.
        """
        if block_structure.get_xblock_field(block_key, 'contains_gated_content'):
            return
        block_structure.override_xblock_field(block_key, 'contains_gated_content', True)

        for parent_block_key in block_structure.get_parents(block_key):
            self._set_contains_gated_content_on_parents(block_structure, parent_block_key)

    @staticmethod
    def _get_block_group_access(block_structure, block_key):
        """
        Gets the current group_access value for a block, supporting inheritance when possible.
        In order to support inheritance, UserPartitionTransformer must also be used.
        """
        # See user_partitions.py for the code that sets this field.
        merged_access = block_structure.get_transformer_block_field(
            block_key, UserPartitionTransformer, 'merged_group_access', None
        )
        if merged_access:
            # merged_access holds a dictionary of sets, but group_access is a dictionary of lists, so we convert here
            # (sets seem like a better format for this, but existing code already expects lists)
            current_access = {p: list(g) for (p, g) in merged_access.get_allowed_groups().items()}
        else:
            # This fallback code has a bug if UserPartitionTranformer is not being used -- it does not consider
            # inheritance from parent blocks. This is why our class docstring recommends UserPartitionTranformer.
            current_access = block_structure.get_xblock_field(block_key, 'group_access')
        return current_access or {}

    def transform(self, usage_info, block_structure):
        if not ContentTypeGatingConfig.enabled_for_enrollment(
            user=usage_info.user,
            course_key=usage_info.course_key,
        ):
            return

        for block_key in block_structure.topological_traversal():
            graded = block_structure.get_xblock_field(block_key, 'graded')
            has_score = block_structure.get_xblock_field(block_key, 'has_score')
            weight_not_zero = block_structure.get_xblock_field(block_key, 'weight') != 0
            problem_eligible_for_content_gating = graded and has_score and weight_not_zero
            if problem_eligible_for_content_gating:
                current_access = self._get_block_group_access(block_structure, block_key)
                current_access.setdefault(
                    CONTENT_GATING_PARTITION_ID,
                    [settings.CONTENT_TYPE_GATE_GROUP_IDS['full_access']]
                )
                block_structure.override_xblock_field(block_key, 'group_access', current_access)
                if current_access[CONTENT_GATING_PARTITION_ID] == [settings.CONTENT_TYPE_GATE_GROUP_IDS['full_access']]:
                    self._set_contains_gated_content_on_parents(block_structure, block_key)
