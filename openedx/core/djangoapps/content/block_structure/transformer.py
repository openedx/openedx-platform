"""
This module provides the abstract base class for all Block Structure
Transformers.
"""


from abc import abstractmethod
import functools


class BlockStructureTransformer:
    """
    Abstract base class for all block structure transformers.
    """

    # All Transformers are expected to maintain version-related class
    # attributes.  While the values for the base class is set to 0,
    # the values for each concrete transformer should be 1 or higher.
    #
    # A transformer's version attributes are used by the block_structure
    # framework in order to determine whether any collected data for a
    # transformer is outdated because of a data schema change by the
    # transformer.
    #
    # The WRITE_VERSION number is stored along with the transformer's
    # data when it is collected and cached (during the collect phase).
    # The READ_VERSION number is then verified to be less than or equal
    # to the version associated with the collected data when the
    # collected data is accessed (during the transform phase).
    #
    # We distinguish between WRITE_VERSION and READ_VERSION numbers in
    # order to:
    # 1. support blue-green deployments where new and previous versions
    #    of the code base are simultaneously executing on different
    #    workers for a period of time.
    #
    #    A 2-phase deployment is used to stagger read and write changes.
    #
    # 2. scale for large deployments where it is costly to recompute
    #    block structures for all courses when a transformer's collected
    #    data schema changes.
    #
    #    A background management command is run to prime the new data.
    #
    # See the following document for further information:
    # https://openedx.atlassian.net/wiki/display/MA/Block+Structure+Cache+Invalidation+Proposal
    #
    # The WRITE_VERSION number of a Transformer should be incremented
    # when it's collect implementation is additively changed. Backward
    # compatibility should be maintained with previous READ_VERSIONs
    # until all readers are updated.
    #
    # The READ_VERSION number of a Transformer should be incremented
    # when its transform implementation is updated to make use of the
    # newly collected data - and released only after all collected
    # block structures are updated with the new WRITE_VERSION.
    #
    WRITE_VERSION = 0
    READ_VERSION = 0

    @classmethod
    def name(cls):
        """
        Unique identifier for the transformer's class. It is used to
        identify the transformer's cached data. So it should be unique
        and not conflict with other transformers. Consider using the
        same name that is used in the Transformer Registry. For example,
        for Stevedore, it is specified in the package configuration (pyproject.toml).

        Once the transformer is in use and its data is cached, do not
        modify this name value without consideration of backward
        compatibility with previously collected data.
        """
        raise NotImplementedError

    @classmethod
    def collect(cls, block_structure):
        """
        Collects and stores any xBlock and modulestore data into the
        block_structure that's necessary for later execution of the
        transformer's transform method. Transformers should store such
        data in the block_structure using the following methods:
            set_transformer_data
            set_transformer_block_field
            request_xblock_fields

        Transformers can call block_structure.request_xblock_fields for
        any common xBlock fields that should be collected by the
        framework.

        Any full block tree traversals should be implemented in this
        collect phase, leaving the transform phase for fast and direct
        access to a sub-block. If a block's transform output is
        dependent on its ancestors' data, the ancestor's data should be
        percolated down to the descendants. So when a (non-root) block
        is directly accessed in the transform, all of its relevant data
        is readily available (without needing to access its ancestors).

        Traversals of the block_structure can be implemented using the
        following methods:
            topological_traversal
            post_order_traversal

        Arguments:
            block_structure (BlockStructureModulestoreData) - A mutable
                block structure that is to be modified with collected
                data to be cached for the transformer.
        """

    @abstractmethod
    def transform(self, usage_info, block_structure):
        """
        Transforms the given block_structure for the given usage_info,
        assuming the block_structure contains cached data from a prior
        call to the collect method of the latest version of the
        Transformer.

        No access to the modulestore nor instantiation of xBlocks should
        be performed during the execution of this method. However,
        accesses to user-specific data (outside of the modulestore and
        not via xBlocks) is permitted in order to apply the transform
        for the given usage_info.

        Note: The root of the given block_structure is not necessarily
        the same as the root of the block_structure passed to the prior
        collect method. The collect method is given the top-most root
        of the structure, while the transform method may be called upon
        any sub-structure or even a single block within the originally
        collected structure.

        A Transformer may choose to remove entire sub-structures during
        the transform method and may do so using the remove_block and
        filter_with_removal methods.

        Amongst the many methods available for a block_structure, the
        following methods are commonly used during transforms:
            get_xblock_field
            get_transformer_data
            get_transformer_block_field
            remove_block_traversal
            filter_with_removal
            filter_topological_traversal
            topological_traversal
            post_order_traversal

        Arguments:
            usage_info (any negotiated type) - A usage-specific object
                that is passed to the block_structure and forwarded to all
                requested Transformers in order to apply a
                usage-specific transform. For example, an instance of
                usage_info would contain a user object for which the
                transform should be applied.

            block_structure (BlockStructureBlockData) - A mutable
                block structure, with already collected data for the
                transformer, that is to be transformed in place.
        """
        raise NotImplementedError


class FilteringTransformerMixin(BlockStructureTransformer):
    """
    Transformers may optionally choose to implement this mixin if their
    transform logic can be broken apart into a lambda for optimization of
    combined tree traversals.

    For performance reasons, developers should try to implement this mixin
    whenever possible - with this alternative, traversal of the entire block
    structure happens only once for all transformers that implement
    FilteringTransformerMixin.
    """

    def transform(self, usage_info, block_structure):
        """
        By defining this method, FilteringTransformers can be run individually
        if desired. In normal operations, the filters returned from multiple
        transform_block_filters calls will be combined and used in a single
        tree traversal.
        """
        filters = self.transform_block_filters(usage_info, block_structure)
        block_structure.filter_topological_traversal(combine_filters(block_structure, filters))

    @abstractmethod
    def transform_block_filters(self, usage_info, block_structure):
        """
        This is an alternative to the standard transform method.

        Returns a list of filter functions to be used for filtering out
        any unwanted blocks in the given block_structure.

        In addition to the commonly used methods listed above, the following
        methods are commonly used by implementations of transform_block_filters:
            create_universal_filter
            create_removal_filter

        Note: Transformers that implement this alternative should be
        independent of all other registered transformers as they may not
        be applied in the order in which they were listed in the registry.

        Arguments:
            usage_info (any negotiated type) - A usage-specific object
                that is passed to the block_structure and forwarded to all
                requested Transformers in order to apply a
                usage-specific transform. For example, an instance of
                usage_info would contain a user object for which the
                transform should be applied.

            block_structure (BlockStructureBlockData) - A mutable
                block structure, with already collected data for the
                transformer, that is to be transformed in place.
        """
        raise NotImplementedError


def combine_filters(block_structure, filters):
    return functools.reduce(
        _filter_chain,
        filters,
        block_structure.create_universal_filter()
    )


def _filter_chain(accumulated, additional):
    """
    Given two functions that take a block_key and return a boolean, yield
    a function that takes a block key, and 'ands' the functions together
    """
    return lambda block_key: accumulated(block_key) and additional(block_key)
