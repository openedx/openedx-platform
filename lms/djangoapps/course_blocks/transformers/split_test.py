"""
Split Test Block Transformer
"""


from openedx.core.djangoapps.content.block_structure.transformer import (
    BlockStructureTransformer,
    FilteringTransformerMixin
)


class SplitTestTransformer(FilteringTransformerMixin, BlockStructureTransformer):
    """
    A nested transformer of the UserPartitionTransformer that honors the
    block structure pathways created by split_test blocks.

    To avoid code duplication, the implementation transforms its block
    access representation to the representation used by user_partitions.
    Namely, the 'group_id_to_child' field on a split_test block is
    transformed into the, now standard, 'group_access' fields in the
    split_test block's children.

    The implementation therefore relies on the UserPartitionTransformer
    to actually enforce the access using the 'user_partitions' and
    'group_access' fields.
    """
    WRITE_VERSION = 1
    READ_VERSION = 1

    @classmethod
    def name(cls):
        """
        Unique identifier for the transformer's class.
        This must match the entry point name in the package configuration.
        """
        return "split_test"

    @classmethod
    def collect(cls, block_structure):
        """
        Collects any information that's necessary to execute this
        transformer's transform method.
        """

        root_block = block_structure.get_xblock(block_structure.root_block_usage_key)
        user_partitions = getattr(root_block, 'user_partitions', [])

        for block_key in block_structure.topological_traversal(
                filter_func=lambda block_key: block_key.block_type == 'split_test',
                yield_descendants_of_unyielded=True,
        ):
            xblock = block_structure.get_xblock(block_key)
            partition_for_this_block = next(
                (
                    partition for partition in user_partitions
                    if partition.id == xblock.user_partition_id
                ),
                None
            )
            if not partition_for_this_block:
                continue

            # Create dict of child location to group_id, using the
            # group_id_to_child field on the split_test block.
            child_to_group = {
                xblock.group_id_to_child.get(str(group.id), None): group.id
                for group in partition_for_this_block.groups
            }

            # Set group access for each child using its group_access
            # field so the user partitions transformer enforces it.
            for child_location in xblock.children:
                child = block_structure.get_xblock(child_location)
                group = child_to_group.get(child_location, None)
                child.group_access[partition_for_this_block.id] = [group] if group is not None else []

    def transform_block_filters(self, usage_info, block_structure):
        """
        Mutates block_structure based on the given usage_info.
        """

        # The UserPartitionTransformer will enforce group access, so
        # go ahead and remove all extraneous split_test blocks.
        return [
            block_structure.create_removal_filter(
                lambda block_key: block_key.block_type == 'split_test',
                keep_descendants=True,
            )
        ]
