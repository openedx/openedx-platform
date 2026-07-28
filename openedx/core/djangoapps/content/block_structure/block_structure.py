"""
Module with family of classes for block structures.
    BlockStructure - responsible for block existence and relations.
    BlockStructureBlockData - responsible for block & transformer data.
    BlockStructureModulestoreData - responsible for xBlock data.

The following internal data structures are implemented:
    _BlockRelations - Data structure for a single block's relations.
    _BlockData - Data structure for a single block's data.
"""


from copy import deepcopy
from functools import partial
from logging import getLogger

from openedx.core.lib.graph_traversals import traverse_post_order, traverse_topologically
from xmodule.block_metadata_utils import get_datetime_field

from .exceptions import TransformerException

logger = getLogger(__name__)  # pylint: disable=invalid-name


# A dictionary key value for storing a transformer's version number.
TRANSFORMER_VERSION_KEY = '_version'


class _BlockRelations:
    """
    Data structure to encapsulate relationships for a single block,
    including its children and parents.
    """
    def __init__(self):

        # List of usage keys of this block's parents.
        # list [UsageKey]
        self.parents = []

        # List of usage keys of this block's children.
        # list [UsageKey]
        self.children = []


class BlockStructure:
    """
    Base class for a block structure.  BlockStructures are constructed
    using the BlockStructureFactory and then used as the currency across
    Transformers.

    This base class keeps track of the block structure's root_block_usage_key,
    the existence of the blocks, and their parents and children
    relationships (graph nodes and edges).
    """
    def __init__(self, root_block_usage_key):

        # The usage key of the root block for this structure.
        # UsageKey
        self.root_block_usage_key = root_block_usage_key

        # Map of a block's usage key to its block relations. The
        # existence of a block in the structure is determined by its
        # presence in this map.
        # dict {UsageKey: _BlockRelations}
        self._block_relations = {}

        # Add the root block.
        self._add_block(self._block_relations, root_block_usage_key)

    def __iter__(self):
        """
        The default iterator for a block structure is get_block_keys()
        since we need to filter blocks as a list.
        A topological traversal can be used to support DAGs.
        """
        return self.get_block_keys()

    def __len__(self):
        return len(self._block_relations)

    #--- Block structure relation methods ---#

    def get_parents(self, usage_key):
        """
        Returns the parents of the block identified by the given
        usage_key.


        Arguments:
            usage_key - The usage key of the block whose parents
                are to be returned.

        Returns:
            [UsageKey] - A list of usage keys of the block's parents.
        """
        return self._block_relations[usage_key].parents if usage_key in self else []

    def get_children(self, usage_key):
        """
        Returns the children of the block identified by the given
        usage_key.

        Arguments:
            usage_key - The usage key of the block whose children
                are to be returned.

        Returns:
            [UsageKey] - A list of usage keys of the block's children.
        """
        return self._block_relations[usage_key].children if usage_key in self else []

    def set_root_block(self, usage_key):
        """
        Sets the given usage key as the new root of the block structure.

        Note: This method does *not* prune the rest of the structure. For
        performance reasons, it is left to the caller to decide when exactly
        to prune.

        Arguments:
            usage_key - The usage key of the block that is to be set as the
                new root of the block structure.
        """
        self.root_block_usage_key = usage_key
        self._block_relations[usage_key].parents = []

    def __contains__(self, usage_key):
        """
        Returns whether a block with the given usage_key is in this
        block structure.

        Arguments:
            usage_key - The usage key of the block whose children
                are to be returned.

        Returns:
            bool - Whether or not a block with the given usage_key
                is present in this block structure.
        """
        return usage_key in self._block_relations

    def get_block_keys(self):
        """
        Returns the block keys in the block structure.

        Returns:
            iterator(UsageKey) - An iterator of the usage
            keys of all the blocks in the block structure.
        """
        return iter(self._block_relations.keys())

    #--- Block structure traversal methods ---#

    def topological_traversal(
            self,
            filter_func=None,
            yield_descendants_of_unyielded=False,
            start_node=None,
    ):
        """
        Performs a topological sort of the block structure and yields
        the usage_key of each block as it is encountered.

        Arguments:
            See the description in
            openedx.core.lib.graph_traversals.traverse_topologically.

        Returns:
            generator - A generator object created from the
                traverse_topologically method.
        """
        return traverse_topologically(
            start_node=start_node or self.root_block_usage_key,
            get_parents=self.get_parents,
            get_children=self.get_children,
            filter_func=filter_func,
            yield_descendants_of_unyielded=yield_descendants_of_unyielded,
        )

    def post_order_traversal(
            self,
            filter_func=None,
            start_node=None,
    ):
        """
        Performs a post-order sort of the block structure and yields
        the usage_key of each block as it is encountered.

        Arguments:
            See the description in
            openedx.core.lib.graph_traversals.traverse_post_order.

        Returns:
            generator - A generator object created from the
                traverse_post_order method.
        """
        return traverse_post_order(
            start_node=start_node or self.root_block_usage_key,
            get_children=self.get_children,
            filter_func=filter_func,
        )

    #--- Internal methods ---#
    # To be used within the block_structure framework or by tests.

    def _prune_unreachable(self):
        """
        Mutates this block structure by removing any unreachable blocks.
        """

        # Create a new block relations map to store only those blocks
        # that are still linked
        pruned_block_relations = {}
        old_block_relations = self._block_relations

        # Build the structure from the leaves up by doing a post-order
        # traversal of the old structure, thereby encountering only
        # reachable blocks.
        for block_key in self.post_order_traversal():
            # If the block is in the old structure,
            if block_key in old_block_relations:
                # Add it to the new pruned structure
                self._add_block(pruned_block_relations, block_key)

                # Add a relationship to only those old children that
                # were also added to the new pruned structure.
                for child in old_block_relations[block_key].children:
                    if child in pruned_block_relations:
                        self._add_to_relations(pruned_block_relations, block_key, child)

        # Replace this structure's relations with the newly pruned one.
        self._block_relations = pruned_block_relations

    def _add_relation(self, parent_key, child_key):
        """
        Adds a parent to child relationship in this block structure.

        Arguments:
            parent_key (UsageKey) - Usage key of the parent block.
            child_key (UsageKey) - Usage key of the child block.
        """
        self._add_to_relations(self._block_relations, parent_key, child_key)

    @staticmethod
    def _add_to_relations(block_relations, parent_key, child_key):
        """
        Adds a parent to child relationship in the given block
        relations map.

        Arguments:
            block_relations (dict({UsageKey: _BlockRelations})) -
                Internal map of a block's usage key to its
                parents/children relations.

            parent_key (UsageKey) - Usage key of the parent block.

            child_key (UsageKey) - Usage key of the child block.
        """
        BlockStructure._add_block(block_relations, parent_key)
        BlockStructure._add_block(block_relations, child_key)

        block_relations[child_key].parents.append(parent_key)
        block_relations[parent_key].children.append(child_key)

    @staticmethod
    def _add_block(block_relations, usage_key):
        """
        Adds the given usage_key to the given block_relations map.

        Arguments:
            block_relations (dict({UsageKey: _BlockRelations})) -
                Internal map of a block's usage key to its
                parents/children relations.

            usage_key (UsageKey) - Usage key of the block that is to
                be added to the given block_relations.
        """
        if usage_key not in block_relations:
            block_relations[usage_key] = _BlockRelations()


class FieldData:
    """
    Data structure to encapsulate collected fields.
    """
    def class_field_names(self):
        """
        Returns list of names of fields that are defined directly
        on the class. Can be overridden by subclasses. All other
        fields are assumed to be stored in the self.fields dict.
        """
        return ['fields']

    def __init__(self):
        # Map of field name to the field's value for this block.
        # dict {string: any picklable type}
        self.fields = {}

    def __getattr__(self, field_name):
        if self._is_own_field(field_name):
            return super().__getattr__(field_name)  # pylint: disable=no-member
        try:
            return self.fields[field_name]
        except KeyError:
            raise AttributeError(f"Field {field_name} does not exist")  # pylint: disable=raise-missing-from  # noqa: B904

    def __setattr__(self, field_name, field_value):
        if self._is_own_field(field_name):
            return super().__setattr__(field_name, field_value)
        else:
            self.fields[field_name] = field_value

    def __delattr__(self, field_name):
        if self._is_own_field(field_name):
            return super().__delattr__(field_name)
        else:
            del self.fields[field_name]

    def _is_own_field(self, field_name):
        """
        Returns whether the given field_name is the name of an
        actual field of this class.
        """
        return field_name in self.class_field_names()


class TransformerData(FieldData):
    """
    Data structure to encapsulate collected data for a transformer.
    """


class TransformerDataMap(dict):
    """
    A map of Transformer name to its corresponding TransformerData.
    The map can be accessed by the Transformer's name or the
    Transformer's class type.
    """
    def __getitem__(self, key):
        key = self._translate_key(key)
        return dict.__getitem__(self, key)

    def __setitem__(self, key, value):
        key = self._translate_key(key)
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        key = self._translate_key(key)
        dict.__delitem__(self, key)

    def get_or_create(self, key):
        """
        Returns the TransformerData associated with the given
        key.  If not found, creates and returns a new TransformerData
        and maps it to the given key.
        """
        try:
            return self[key]
        except KeyError:
            new_transformer_data = TransformerData()
            self[key] = new_transformer_data
            return new_transformer_data

    def _translate_key(self, key):
        """
        Allows the given key to be either the transformer's class or name,
        always returning the transformer's name.  This allows
        TransformerDataMap to be accessed in either of the following ways:

            map[TransformerClass] or
            map['transformer_name']
        """
        try:
            return key.name()
        except AttributeError:
            return key


class BlockData(FieldData):
    """
    Data structure to encapsulate collected data for a single block.
    """
    def class_field_names(self):
        return super().class_field_names() + ['location', 'transformer_data']

    def __init__(self, usage_key):
        super().__init__()

        # Location (or usage key) of the block.
        self.location = usage_key

        # Map of transformer name to its block-specific data.
        self.transformer_data = TransformerDataMap()

    @property
    def usage_key(self):
        return self.location


class BlockStructureBlockData(BlockStructure):
    """
    Subclass of BlockStructure that is responsible for managing block
    and transformer data.
    """
    # The latest version of the data structure of this class. Incrementally
    # update this value whenever the data structure changes. Dependent storage
    # layers can then use this value when serializing/deserializing block
    # structures, and invalidating any previously cached/stored data.
    VERSION = 2

    def __init__(self, root_block_usage_key):
        super().__init__(root_block_usage_key)

        # Map of a block's usage key to its collected data, including
        # its xBlock fields and block-specific transformer data.
        # dict {UsageKey: BlockData}
        self._block_data_map = {}

        # Map of a transformer's name to its non-block-specific data.
        self.transformer_data = TransformerDataMap()

    def copy(self):
        """
        Returns a new instance of BlockStructureBlockData with a
        deep-copy of this instance's contents.
        """
        from .factory import BlockStructureFactory
        return BlockStructureFactory.create_new(
            self.root_block_usage_key,
            deepcopy(self._block_relations),
            deepcopy(self.transformer_data),
            deepcopy(self._block_data_map),
        )

    def iteritems(self):
        """
        Returns iterator of (UsageKey, BlockData) pairs for all
        blocks in the BlockStructure.
        """
        return iter(self._block_data_map.items())

    def itervalues(self):
        """
        Returns iterator of BlockData for all blocks in the
        BlockStructure.
        """
        return iter(self._block_data_map.values())

    def __getitem__(self, usage_key):
        """
        Returns the BlockData associated with the given key.
        """
        return self._block_data_map[usage_key]

    def get_xblock_field(self, usage_key, field_name, default=None):
        """
        Returns the collected value of the xBlock field for the
        requested block for the requested field_name; returns default if
        not found.

        Arguments:
            usage_key (UsageKey) - Usage key of the block whose xBlock
                field is requested.

            field_name (string) - The name of the field that is
                requested.

            default (any type) - The value to return if a field value is
                not found.
        """
        block_data = self._block_data_map.get(usage_key)
        return get_datetime_field(block_data, field_name, default) if block_data else default

    def override_xblock_field(self, usage_key, field_name, override_data):
        """
        Set value of the XBlock field for the requested block for the requested field_name;

        Arguments:
            usage_key (UsageKey) - Usage key of the block whose xBlock
                field is requested.

            field_name (string) - The name of the field that is
                requested.

            override_data (object) - The data you want to set
        """
        block_data = self._get_or_create_block(usage_key)
        setattr(block_data, field_name, override_data)

    def get_transformer_data(self, transformer, key, default=None):
        """
        Returns the value associated with the given key from the given
        transformer's data dictionary; returns default if not found.

        Arguments:
            transformer (BlockStructureTransformer) - The transformer
                whose collected data is requested.

            key (string) - A dictionary key to the transformer's data
                that is requested.
        """
        try:
            return getattr(self.transformer_data[transformer], key, default)
        except KeyError:
            return default

    def set_transformer_data(self, transformer, key, value):
        """
        Updates the given transformer's data dictionary with the given
        key and value.

        Arguments:
            transformer (BlockStructureTransformer) - The transformer
                whose data is to be updated.

            key (string) - A dictionary key to the transformer's data.

            value (any picklable type) - The value to associate with the
                given key for the given transformer's data.
        """
        setattr(self.transformer_data.get_or_create(transformer), key, value)

    def get_transformer_block_data(self, usage_key, transformer):
        """
        Returns the TransformerData for the given
        transformer for the block identified by the given usage_key.

        Raises KeyError if not found.

        Arguments:
            usage_key (UsageKey) - Usage key of the block whose
                transformer data is requested.

            transformer (BlockStructureTransformer) - The transformer
                whose dictionary data is requested.
        """
        return self._block_data_map[usage_key].transformer_data[transformer]

    def get_transformer_block_field(self, usage_key, transformer, key, default=None):
        """
        Returns the value associated with the given key for the given
        transformer for the block identified by the given usage_key;
        returns default if not found.

        Arguments:
            usage_key (UsageKey) - Usage key of the block whose
                transformer data is requested.

            transformer (BlockStructureTransformer) - The transformer
                whose dictionary data is requested.

            key (string) - A dictionary key to the transformer's data
                that is requested.

            default (any type) - The value to return if a dictionary
                entry is not found.
        """
        try:
            transformer_data = self.get_transformer_block_data(usage_key, transformer)
        except KeyError:
            return default
        return get_datetime_field(transformer_data, key, default)

    def set_transformer_block_field(self, usage_key, transformer, key, value):
        """
        Updates the given transformer's data dictionary with the given
        key and value for the block identified by the given usage_key.

        Arguments:
            usage_key (UsageKey) - Usage key of the block whose
                transformer data is to be updated.

            transformer (BlockStructureTransformer) - The transformer
                whose data is to be updated.

            key (string) - A dictionary key to the transformer's data.

            value (any picklable type) - The value to associate with the
                given key for the given transformer's data for the
                requested block.
        """
        setattr(
            self._get_or_create_block(usage_key).transformer_data.get_or_create(transformer),
            key,
            value,
        )

    def remove_transformer_block_field(self, usage_key, transformer, key):
        """
        Deletes the given transformer's entire data dict for the
        block identified by the given usage_key.

        Arguments:
            usage_key (UsageKey) - Usage key of the block whose
                transformer data is to be deleted.

            transformer (BlockStructureTransformer) - The transformer
                whose data entry is to be deleted.
        """
        try:
            transformer_block_data = self.get_transformer_block_data(usage_key, transformer)
            delattr(transformer_block_data, key)
        except (AttributeError, KeyError):
            pass

    def remove_block(self, usage_key, keep_descendants):
        """
        Removes the block identified by the usage_key and all of its
        related data from the block structure.  If descendants of the
        removed block are to be kept, the structure's relations are
        updated to reconnect the block's parents with its children.

        Note: While the immediate relations of the block are updated
        (removed), all descendants of the block will remain in the
        structure unless the _prune_unreachable method is called.

        Arguments:
            usage_key (UsageKey) - Usage key of the block that is to be
                removed.

            keep_descendants (bool) - If True, the block structure's
                relations (graph edges) are updated such that the
                removed block's children become children of the
                removed block's parents.
        """
        children = self._block_relations[usage_key].children
        parents = self._block_relations[usage_key].parents

        # Remove block from its children.
        for child in children:
            self._block_relations[child].parents.remove(usage_key)

        # Remove block from its parents.
        for parent in parents:
            self._block_relations[parent].children.remove(usage_key)

        # Remove block.
        self._block_relations.pop(usage_key, None)
        self._block_data_map.pop(usage_key, None)

        # Recreate the graph connections if descendants are to be kept.
        if keep_descendants:
            for child in children:
                for parent in parents:
                    self._add_relation(parent, child)

    def create_universal_filter(self):
        """
        Returns a filter function that always returns True for all blocks.
        """
        return lambda block_key: True

    def create_removal_filter(self, removal_condition, keep_descendants=False):
        """
        Returns a filter function that automatically removes blocks that satisfy
        the removal_condition.

        Arguments:
            removal_condition ((usage_key)->bool) - A function that
                takes a block's usage key as input and returns whether
                or not to remove that block from the block structure.

            keep_descendants (bool) - See the description in
                remove_block.
        """
        return partial(
            self.retain_or_remove,
            removal_condition=removal_condition,
            keep_descendants=keep_descendants,
        )

    def retain_or_remove(self, block_key, removal_condition, keep_descendants=False):
        """
        Removes the given block if it satisfies the removal_condition.
        Returns True if the block was retained, and False if the block
        was removed.

        Arguments:
            block_key (usage_key) - Usage key of the block.

            removal_condition ((usage_key)->bool) - A function that
                takes a block's usage key as input and returns whether
                or not to remove that block from the block structure.

            keep_descendants (bool) - See the description in
                remove_block.
        """
        if removal_condition(block_key):
            self.remove_block(block_key, keep_descendants)
            return False
        return True

    def remove_block_traversal(self, removal_condition, keep_descendants=False):
        """
        A higher-order function that traverses the block structure
        using topological sort and removes all blocks satisfying the given
        removal_condition.

        Arguments:
            removal_condition ((usage_key)->bool) - A function that
                takes a block's usage key as input and returns whether
                or not to remove that block from the block structure.

            keep_descendants (bool) - See the description in
                remove_block.
        """
        self.filter_topological_traversal(
            filter_func=self.create_removal_filter(
                removal_condition, keep_descendants
            )
        )

    def filter_topological_traversal(self, filter_func, **kwargs):
        """
        A higher-order function that traverses the block structure
        using topological sort and applies the given filter.

        Arguments:
            filter_func ((usage_key)->bool) - Function that returns
                whether or not to yield the given block key.
                If None, the True function is assumed.

            kwargs (dict) - Optional keyword arguments to be forwarded
                to topological_traversal.
        """

        # Note: For optimization, we remove blocks using the filter
        # function, since the graph traversal method can skip over
        # descendants that are unyielded.  However, note that the
        # optimization is not currently present because of DAGs,
        # but it will be as soon as we remove support for DAGs.
        for _ in self.topological_traversal(filter_func=filter_func, **kwargs):
            pass

    #--- Internal methods ---#
    # To be used within the block_structure framework or by tests.

    def _get_transformer_data_version(self, transformer):
        """
        Returns the version number stored for the given transformer.

        Arguments:
            transformer (BlockStructureTransformer) - The transformer
                whose stored version is requested.
        """

        return self.get_transformer_data(transformer, TRANSFORMER_VERSION_KEY, 0)

    def _add_transformer(self, transformer):
        """
        Adds the given transformer to the block structure by recording
        its current version number.
        """
        if transformer.WRITE_VERSION == 0:
            raise TransformerException('Version attributes are not set on transformer {0}.', transformer.name())  # pylint: disable=raising-format-tuple
        self.set_transformer_data(transformer, TRANSFORMER_VERSION_KEY, transformer.WRITE_VERSION)

    def _get_or_create_block(self, usage_key):
        """
        Returns the BlockData associated with the given usage_key.
        If not found, creates and returns a new BlockData and
        maps it to the given key.
        """
        try:
            return self._block_data_map[usage_key]
        except KeyError:
            block_data = BlockData(usage_key)
            self._block_data_map[usage_key] = block_data
            return block_data


class BlockStructureModulestoreData(BlockStructureBlockData):
    """
    Subclass of BlockStructureBlockData that is responsible for managing
    xBlocks and corresponding functionality that should only be called
    during the Collect phase.

    Note: Although this class interface uses xBlock terminology, it is
    designed and implemented generically so it can work with any
    interface and implementation of an xBlock.
    """
    def __init__(self, root_block_usage_key):
        super().__init__(root_block_usage_key)

        # Map of a block's usage key to its instantiated xBlock.
        # dict {UsageKey: XBlock}
        self._xblock_map = {}

        # Set of xBlock field names that have been requested for
        # collection.
        # set(string)
        self._requested_xblock_fields = set()

    def request_xblock_fields(self, *field_names):
        """
        Records request for collecting data for the given xBlock fields.

        A Transformer should call this method when it needs to collect
        data for a common xBlock field that may also be used by other
        transformers.  This minimizes storage usage across transformers.
        Contrast this with each transformer collecting the same xBlock
        data within its own transformer data storage.

        Arguments:
            field_names (list(string)) - A list of names of common
                xBlock fields whose values should be collected.
        """
        self._requested_xblock_fields.update(set(field_names))

    def get_xblock(self, usage_key):
        """
        Returns the instantiated xBlock for the given usage key.

        Arguments:
            usage_key (UsageKey) - Usage key of the block whose
                xBlock object is to be returned.
        """
        return self._xblock_map[usage_key]

    #--- Internal methods ---#
    # To be used within the block_structure framework or by tests.

    def _add_xblock(self, usage_key, xblock):
        """
        Associates the given xBlock object with the given usage_key.

        Arguments:
            usage_key (UsageKey) - Usage key of the given xBlock.  This
                value is passed in separately as opposed to retrieving
                it from the given xBlock since this interface is
                agnostic to and decoupled from the xBlock interface.

            xblock (XBlock) - An instantiated XBlock object that is
                to be stored for later access.
        """
        self._xblock_map[usage_key] = xblock

    def _collect_requested_xblock_fields(self):
        """
        Iterates through all instantiated xBlocks that were added and
        collects all xBlock fields that were requested.
        """
        for xblock_usage_key, xblock in self._xblock_map.items():
            block_data = self._get_or_create_block(xblock_usage_key)
            for field_name in self._requested_xblock_fields:
                self._set_xblock_field(block_data, xblock, field_name)

    def _set_xblock_field(self, block_data, xblock, field_name):
        """
        Updates the given block's xBlock fields data with the xBlock
        value for the given field name.

        Arguments:
            block_data (BlockData) - A BlockStructure BlockData
                object.

            xblock (XBlock) - An instantiated XBlock object whose
                field is being accessed and collected for later
                retrieval.

            field_name (string) - The name of the xBlock field that is
                being collected and stored.
        """
        if hasattr(xblock, field_name):
            setattr(block_data, field_name, getattr(xblock, field_name))
