"""
Module for a collection of BlockStructureTransformers.
"""
from logging import getLogger

from .exceptions import TransformerDataIncompatible, TransformerException
from .transformer import FilteringTransformerMixin, combine_filters
from .transformer_registry import TransformerRegistry

logger = getLogger(__name__)  # pylint: disable=C0103


class BlockStructureTransformers:
    """
    The BlockStructureTransformers class encapsulates an ordered list of block
    structure transformers.  It uses the Transformer Registry to verify the
    the registration status of added transformers and to collect their data.
    It provides aggregate functionality for collection and ordered
    transformation of the transformers.

    Clients are expected to access the list of transformers through the
    class' interface rather than directly.
    """
    def __init__(self, transformers=None, usage_info=None):
        """
        Arguments:
            transformers ([BlockStructureTransformer]) - List of transformers
                to add to the collection.

            usage_info (any negotiated type) - A usage-specific object
                that is passed to the block_structure and forwarded to all
                requested Transformers in order to apply a
                usage-specific transform. For example, an instance of
                usage_info would contain a user object for which the
                transform should be applied.

        Raises:
            TransformerException - if any transformer is not registered in the
                Transformer Registry.
        """
        self.usage_info = usage_info
        self._transformers = {'supports_filter': [], 'no_filter': []}
        if transformers:
            self.__iadd__(transformers)

    def __iadd__(self, transformers):
        """
        Adds the given transformers to the collection.

        Args:
            transformers ([BlockStructureTransformer]) - List of transformers
                to add to the collection.

        Raises:
            TransformerException - if any transformer is not registered in the
                Transformer Registry.
        """
        unregistered_transformers = TransformerRegistry.find_unregistered(transformers)
        if unregistered_transformers:
            raise TransformerException(
                f"The following requested transformers are not registered: {unregistered_transformers}"
            )

        for transformer in transformers:
            if isinstance(transformer, FilteringTransformerMixin):
                self._transformers['supports_filter'].append(transformer)
            else:
                self._transformers['no_filter'].append(transformer)
        return self

    @classmethod
    def collect(cls, block_structure):
        """
        Collects data for each registered transformer.
        """
        for transformer in TransformerRegistry.get_registered_transformers():
            block_structure._add_transformer(transformer)  # pylint: disable=protected-access
            transformer.collect(block_structure)

        # Collect all fields that were requested by the transformers.
        block_structure._collect_requested_xblock_fields()  # pylint: disable=protected-access

    @classmethod
    def verify_versions(cls, block_structure):
        """
        Returns whether the collected data in the block structure is
        incompatible with the current version of the registered Transformers.

        Raises:
            TransformerDataIncompatible with information about all outdated
            Transformers.
        """
        outdated_transformers = []
        for transformer in TransformerRegistry.get_registered_transformers():
            version_in_block_structure = block_structure._get_transformer_data_version(transformer)  # pylint: disable=protected-access
            if transformer.READ_VERSION > version_in_block_structure:
                outdated_transformers.append(transformer)

        if outdated_transformers:
            raise TransformerDataIncompatible(  # pylint: disable=raising-format-tuple
                "Collected Block Structure data for the following transformers is outdated: '%s'.",
                [(transformer.name(), transformer.READ_VERSION) for transformer in outdated_transformers],
            )
        return True

    def transform(self, block_structure):
        """
        The given block structure is transformed by each transformer in the
        collection. Tranformers with filters are combined and run first in a
        single course tree traversal, then remaining transformers are run in
        the order that they were added.
        """
        self._transform_with_filters(block_structure)
        self._transform_without_filters(block_structure)

        # Prune the block structure to remove any unreachable blocks.
        block_structure._prune_unreachable()  # pylint: disable=protected-access

    def _transform_with_filters(self, block_structure):
        """
        Transforms the given block_structure using the transform_block_filters
        method from the given transformers.
        """
        if not self._transformers['supports_filter']:
            return

        filters = []
        for transformer in self._transformers['supports_filter']:
            filters.extend(transformer.transform_block_filters(self.usage_info, block_structure))

        combined_filters = combine_filters(block_structure, filters)
        block_structure.filter_topological_traversal(combined_filters)

    def _transform_without_filters(self, block_structure):
        """
        Transforms the given block_structure using the transform
        method from the given transformers.
        """
        for transformer in self._transformers['no_filter']:
            transformer.transform(self.usage_info, block_structure)
