"""
Module for factory class for BlockStructure objects.
"""
from .block_structure import BlockStructureBlockData, BlockStructureModulestoreData


class BlockStructureFactory:
    """
    Factory class for BlockStructure objects.
    """
    @classmethod
    def create_from_modulestore(cls, root_block_usage_key, modulestore):
        """
        Creates and returns a block structure from the modulestore
        starting at the given root_block_usage_key.

        Arguments:
            root_block_usage_key (UsageKey) - The usage_key for the root
                of the block structure that is to be created.

            modulestore (ModuleStoreRead) - The modulestore that
                contains the data for the xBlocks within the block
                structure starting at root_block_usage_key.

        Returns:
            BlockStructureModulestoreData - The created block structure
                with instantiated xBlocks from the given modulestore
                starting at root_block_usage_key.

        Raises:
            xmodule.modulestore.exceptions.ItemNotFoundError if a block for
                root_block_usage_key is not found in the modulestore.
        """
        root_xblock = modulestore.get_item(root_block_usage_key, depth=None, lazy=False)
        block_structure = BlockStructureModulestoreData(root_block_usage_key.for_branch(None))
        blocks_visited = set()

        def build_block_structure(xblock):
            """
            Recursively update the block structure with the given xBlock
            and its descendants.
            """
            # Check if the xblock was already visited (can happen in
            # DAGs).
            # Normalize location to remove branch/version information
            # When create_from_modulestore is wrapped in published_only branch decorator,
            # "xblock being changed" location contains branch and version info which causes
            # mismatch when removing inaccessible blocks in
            # CourseNavigationBlocksView.filter_inaccessible_blocks
            # while fetching course navigation.
            location = xblock.location.for_branch(None)
            if location in blocks_visited:
                return

            # Add the xBlock.
            blocks_visited.add(location)
            block_structure._add_xblock(location, xblock)  # pylint: disable=protected-access

            # Add relations with its children and recurse.
            for child in xblock.get_children():
                child_location = child.location.for_branch(None)
                block_structure._add_relation(location, child_location)  # pylint: disable=protected-access
                build_block_structure(child)

        build_block_structure(root_xblock)
        return block_structure

    @classmethod
    def create_from_store(cls, root_block_usage_key, block_structure_store):
        """
        Deserializes and returns the block structure starting at
        root_block_usage_key from the given store, if it's found in the store.

        The given root_block_usage_key must equate the root_block_usage_key
        previously passed to serialize_to_cache.

        Arguments:
            root_block_usage_key (UsageKey) - The usage_key for the root
                of the block structure that is to be deserialized from
                the given cache.

            block_structure_store (BlockStructureStore) - The
                store from which the block structure is to be
                deserialized.

        Returns:
            BlockStructure - The deserialized block structure starting
                at root_block_usage_key, if found in the cache.

        Raises:
            BlockStructureNotFound - If the root_block_usage_key is not found
                in the store.
        """
        return block_structure_store.get(root_block_usage_key)

    @classmethod
    def create_new(cls, root_block_usage_key, block_relations, transformer_data, block_data_map):
        """
        Returns a new block structure for given the arguments.
        """
        block_structure = BlockStructureBlockData(root_block_usage_key)
        block_structure._block_relations = block_relations  # pylint: disable=protected-access
        block_structure.transformer_data = transformer_data
        block_structure._block_data_map = block_data_map  # pylint: disable=protected-access
        return block_structure
