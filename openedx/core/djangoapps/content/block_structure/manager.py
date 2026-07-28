"""
Top-level module for the Block Structure framework with a class for managing
BlockStructures.
"""


from contextlib import contextmanager

from xmodule.modulestore import ModuleStoreEnum

from .exceptions import BlockStructureNotFound, TransformerDataIncompatible, UsageKeyNotInBlockStructure
from .factory import BlockStructureFactory
from .store import BlockStructureStore
from .transformers import BlockStructureTransformers


class BlockStructureManager:
    """
    Top-level class for managing Block Structures.
    """

    def __init__(self, root_block_usage_key, modulestore, cache):
        """
        Arguments:
            root_block_usage_key (UsageKey) - The usage_key for the root
                of the block structure that is being accessed.

            modulestore (ModuleStoreRead) - The modulestore that
                contains the data for the xBlock objects corresponding to
                the block structure.

            cache (django.core.cache.backends.base.BaseCache) - The
                cache to use for storing/retrieving the block structure's
                collected data.
        """
        self.root_block_usage_key = root_block_usage_key
        self.modulestore = modulestore
        self.store = BlockStructureStore(cache)

    def get_transformed(self, transformers, starting_block_usage_key=None, collected_block_structure=None, user=None):
        """
        Returns the transformed Block Structure for the root_block_usage_key,
        starting at starting_block_usage_key, getting block data from the cache
        and modulestore, as needed.

        Details: Similar to the get_collected method, except the transformers'
        transform methods are also called.

        Arguments:
            transformers (BlockStructureTransformers) - Collection of
                transformers to apply.

            starting_block_usage_key (UsageKey) - Specifies the starting block
                in the block structure that is to be transformed.
                If None, root_block_usage_key is used.

            collected_block_structure (BlockStructureBlockData) - A
                block structure retrieved from a prior call to
                get_collected.  Can be optionally provided if already available,
                for optimization.

            user (django.contrib.auth.models.User) - User object for
                which the block structure is to be transformed.

        Returns:
            BlockStructureBlockData - A transformed block structure,
                starting at starting_block_usage_key.
        """
        block_structure = collected_block_structure.copy() if collected_block_structure else self.get_collected(user)

        if starting_block_usage_key:
            # Override the root_block_usage_key so traversals start at the
            # requested location.  The rest of the structure will be pruned
            # as part of the transformation.
            if starting_block_usage_key not in block_structure:
                raise UsageKeyNotInBlockStructure(  # pylint: disable=raising-format-tuple
                    "The requested usage_key '{0}' is not found in the block_structure with root '{1}'",
                    str(starting_block_usage_key),
                    str(self.root_block_usage_key),
                )
            block_structure.set_root_block(starting_block_usage_key)
        transformers.transform(block_structure)
        return block_structure

    def get_collected(self, user=None):
        """
        Returns the collected Block Structure for the root_block_usage_key,
        getting block data from the cache and modulestore, as needed.

        Details: The cache is updated if needed (if outdated or empty),
        the modulestore is accessed if needed (at cache miss), and the
        transformers data is collected if needed.

        In the case of a cache miss, the function bypasses runtime access checks for the current
        user. This is done to prevent inconsistencies in the data, which can occur when
        certain blocks are inaccessible due to access restrictions.

        Returns:
            BlockStructureBlockData - A collected block structure,
                starting at root_block_usage_key, with collected data
                from each registered transformer.
        """
        try:
            block_structure = BlockStructureFactory.create_from_store(
                self.root_block_usage_key,
                self.store,
            )
            BlockStructureTransformers.verify_versions(block_structure)
        except (BlockStructureNotFound, TransformerDataIncompatible):
            if user and getattr(user, "known", True):
                # This bypasses the runtime access checks. When we are populating the course blocks cache,
                # we do not want to perform access checks. Access checks result in inconsistent blocks where
                # inaccessible blocks are missing from the cache. Cached course blocks are then used for all the users.
                user.known = False
                block_structure = self._update_collected()
                user.known = True
            else:
                block_structure = self._update_collected()

        return block_structure

    def update_collected_if_needed(self):
        """
        The store is updated with newly collected transformers data from
        the modulestore, only if the data in the store is outdated.
        """
        with self._bulk_operations():
            if not self.store.is_up_to_date(self.root_block_usage_key, self.modulestore):
                self._update_collected()

    def _update_collected(self):
        """
        The store is updated with newly collected transformers data from
        the modulestore.
        """
        with self._bulk_operations():
            # Always uses published-only branch regardless of CMS or LMS context.
            with self.modulestore.branch_setting(
                ModuleStoreEnum.Branch.published_only,
                self.root_block_usage_key.course_key
            ):
                block_structure = BlockStructureFactory.create_from_modulestore(
                    self.root_block_usage_key,
                    self.modulestore,
                )

            BlockStructureTransformers.collect(block_structure)
            self.store.add(block_structure)
            return block_structure

    def clear(self):
        """
        Removes data for the block structure associated with the given
        root block key.
        """
        self.store.delete(self.root_block_usage_key)

    @contextmanager
    def _bulk_operations(self):
        """
        A context manager for notifying the store of bulk operations.
        """
        try:
            course_key = self.root_block_usage_key.course_key
        except AttributeError:
            course_key = None
        with self.modulestore.bulk_operations(course_key):
            yield
