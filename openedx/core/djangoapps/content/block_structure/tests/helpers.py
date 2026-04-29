"""
Common utilities for tests in block_structure module
"""


from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator

from xmodule.modulestore.exceptions import ItemNotFoundError

from ..api import get_cache
from ..block_structure import BlockStructureBlockData
from ..exceptions import BlockStructureNotFound
from ..models import BlockStructureModel
from ..store import BlockStructureStore
from ..transformer import BlockStructureTransformer, FilteringTransformerMixin
from ..transformer_registry import TransformerRegistry


def is_course_in_block_structure_cache(course_key, store):
    """
    Returns whether the given course is in the Block Structure cache.
    """
    course_usage_key = store.make_course_usage_key(course_key)
    try:
        BlockStructureStore(get_cache()).get(course_usage_key)
        return True
    except BlockStructureNotFound:
        return False


def is_course_in_block_structure_storage(course_key, store):
    """
    Returns whether the given course is in Block Structure storage.
    """
    course_usage_key = store.make_course_usage_key(course_key)
    try:
        BlockStructureModel.get(course_usage_key)
        return True
    except BlockStructureNotFound:
        return False


class MockXBlock:
    """
    A mock XBlock to be used in unit tests, thereby decoupling the
    implementation of the block cache framework from the xBlock
    implementation.  This class provides only the minimum xBlock
    capabilities needed by the block cache framework.
    """
    def __init__(self, location, field_map=None, children=None, modulestore=None):
        self.location = location
        self.field_map = field_map or {}

        self.children = children or []
        self.modulestore = modulestore

    def __getattr__(self, attr):
        try:
            return self.field_map[attr]
        except KeyError:
            raise AttributeError  # pylint: disable=raise-missing-from  # noqa: B904

    def get_children(self):
        """
        Returns the children of the mock XBlock.
        """
        return [self.modulestore.get_item(child) for child in self.children]


class MockModulestore:
    """
    A mock Modulestore to be used in unit tests, providing only the
    minimum methods needed by the block cache framework.
    """
    def __init__(self):
        self.get_items_call_count = 0
        self.blocks = None

    def set_blocks(self, blocks):
        """
        Updates the mock modulestore with a dictionary of blocks.

        Arguments:
            blocks ({block key, MockXBlock}) - A map of block_key
            to its mock xBlock.
        """
        self.blocks = blocks

    def get_item(self, block_key, depth=None, lazy=False):  # pylint: disable=unused-argument
        """
        Returns the mock XBlock (MockXBlock) associated with the
        given block_key.

        Raises ItemNotFoundError if the item is not found.
        """
        self.get_items_call_count += 1
        item = self.blocks.get(block_key)
        if not item:
            raise ItemNotFoundError
        return item

    @contextmanager
    def bulk_operations(self, ignore):  # pylint: disable=unused-argument
        """
        A context manager for notifying the store of bulk operations.
        """
        yield

    @contextmanager
    def branch_setting(self, branch_settings, course_id=None):  # pylint: disable=unused-argument
        """
        A context manager for temporarily setting a store's branch value on the current thread.
        """
        yield


class MockCache:
    """
    A mock Cache object, providing only the minimum features needed
    by the block cache framework.
    """
    def __init__(self):
        # An in-memory map of cache keys to cache values.
        self.map = {}
        self.set_call_count = 0
        self.timeout_from_last_call = 0

    def set(self, key, val, timeout):
        """
        Associates the given key with the given value in the cache.
        """
        self.set_call_count += 1
        self.map[key] = val
        self.timeout_from_last_call = timeout

    def get(self, key, default=None):
        """
        Returns the value associated with the given key in the cache;
        returns default if not found.
        """
        return self.map.get(key, default)

    def delete(self, key):
        """
        Deletes the given key from the cache.
        """
        del self.map[key]


class MockModulestoreFactory:
    """
    A factory for creating MockModulestore objects.
    """
    @classmethod
    def create(cls, children_map, block_key_factory):
        """
        Creates and returns a MockModulestore from the given
        children_map.

        Arguments:
            children_map ({block_key: [block_key]}) - A dictionary
                mapping a block key to a list of block keys of the
                block's corresponding children.
        """
        modulestore = MockModulestore()
        modulestore.set_blocks({
            block_key_factory(block_key): MockXBlock(
                block_key_factory(block_key),
                children=[block_key_factory(child) for child in children],
                modulestore=modulestore,
            )
            for block_key, children in enumerate(children_map)
        })
        return modulestore


class MockTransformer(BlockStructureTransformer):
    """
    A mock BlockStructureTransformer class.
    """
    WRITE_VERSION = 1
    READ_VERSION = 1

    @classmethod
    def name(cls):
        # Use the class' name for Mock transformers.
        return cls.__name__

    def transform(self, usage_info, block_structure):
        pass

    def __repr__(self):
        return self.name()


class MockFilteringTransformer(FilteringTransformerMixin, BlockStructureTransformer):
    """
    A mock FilteringTransformerMixin class.
    """
    WRITE_VERSION = 1
    READ_VERSION = 1

    @classmethod
    def name(cls):
        # Use the class' name for Mock transformers.
        return cls.__name__

    def transform_block_filters(self, usage_info, block_structure):
        return [block_structure.create_universal_filter()]


def clear_registered_transformers_cache():
    """
    Test helper to clear out any cached values of registered transformers.
    """
    TransformerRegistry.get_write_version_hash.cache.clear()  # pylint: disable=no-member


@contextmanager
def mock_registered_transformers(transformers):
    """
    Context manager for mocking the transformer registry to return the given transformers.
    """
    clear_registered_transformers_cache()
    with patch(
        'openedx.core.djangoapps.content.block_structure.transformer_registry.'
        'TransformerRegistry.get_registered_transformers'
    ) as mock_available_transforms:
        mock_available_transforms.return_value = set(transformers)
        yield


class ChildrenMapTestMixin:
    """
    A Test Mixin with utility methods for testing with block structures
    created and manipulated using children_map and parents_map.
    """

    #     0
    #    / \
    #   1  2
    #  / \
    # 3   4
    SIMPLE_CHILDREN_MAP = [[1, 2], [3, 4], [], [], []]

    #       0
    #      /
    #     1
    #    /
    #   2
    #  /
    # 3
    LINEAR_CHILDREN_MAP = [[1], [2], [3], []]

    #     0
    #    / \
    #   1  2
    #   \ / \
    #    3  4
    #   / \
    #  5  6
    DAG_CHILDREN_MAP = [[1, 2], [3], [3, 4], [5, 6], [], [], []]

    def block_key_factory(self, block_id):
        """
        Returns a block key object for the given block_id.
        Override this method if the block_key should be anything
        different from the index integer values in the Children Maps.
        """
        return block_id

    def create_block_structure(self, children_map, block_structure_cls=BlockStructureBlockData):
        """
        Factory method for creating and returning a block structure
        for the given children_map.
        """
        # create empty block structure
        block_structure = block_structure_cls(root_block_usage_key=self.block_key_factory(0))

        # _add_relation and blocks
        for parent, children in enumerate(children_map):
            if isinstance(block_structure, BlockStructureBlockData):
                block_structure._get_or_create_block(self.block_key_factory(parent))  # pylint: disable=protected-access
            for child in children:
                block_structure._add_relation(self.block_key_factory(parent), self.block_key_factory(child))  # pylint: disable=protected-access
                if isinstance(block_structure, BlockStructureBlockData):
                    block_structure._get_or_create_block(self.block_key_factory(child))  # pylint: disable=protected-access
        return block_structure

    def get_parents_map(self, children_map):
        """
        Converts and returns the given children_map to a parents_map.
        """
        parent_map = [[] for _ in children_map]
        for parent, children in enumerate(children_map):
            for child in children:
                parent_map[child].append(parent)
        return parent_map

    def assert_block_structure(self, block_structure, children_map, missing_blocks=None):
        """
        Verifies that the relations in the given block structure
        equate the relations described in the children_map. Use the
        missing_blocks parameter to pass in any blocks that were removed
        from the block structure but still have a positional entry in
        the children_map.
        """
        if not missing_blocks:
            missing_blocks = []

        for block_key, children in enumerate(children_map):
            # Verify presence
            assert (self.block_key_factory(block_key) in block_structure) == (block_key not in missing_blocks),\
                'Expected presence in block_structure for block_key {} to match absence in missing_blocks.'\
                .format(str(block_key))  # noqa: UP032

            # Verify children
            if block_key not in missing_blocks:
                assert set(block_structure.get_children(self.block_key_factory(block_key))) ==\
                       {self.block_key_factory(child) for child in children}

        # Verify parents
        parents_map = self.get_parents_map(children_map)
        for block_key, parents in enumerate(parents_map):
            if block_key not in missing_blocks:
                assert set(block_structure.get_parents(self.block_key_factory(block_key))) ==\
                       {self.block_key_factory(parent) for parent in parents}


class UsageKeyFactoryMixin:
    """
    Test Mixin that provides a block_key_factory to create OpaqueKey objects
    for block_ids rather than simple integers. By default, the children maps in
    ChildrenMapTestMixin use integers for block_ids.
    """
    def setUp(self):
        super().setUp()
        self.course_key = CourseLocator('org', 'course', str(uuid4()))

    def block_key_factory(self, block_id):
        """
        Returns a block key object for the given block_id.
        """
        return BlockUsageLocator(course_key=self.course_key, block_type='course', block_id=str(block_id))
