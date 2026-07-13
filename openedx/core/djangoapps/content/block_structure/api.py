"""
Higher order functions built on the BlockStructureManager to interact with a django cache.
"""


from django.core.cache import cache

from xmodule.modulestore.django import modulestore

from .manager import BlockStructureManager
from .models import BlockStructureModel

BLOCK_STRUCTURE_VERSION_KEY = 'block_structure_version:{}'


def get_block_structure_version(course_key):
    """
    Returns the current block structure version for the given course.
    This version corresponds to the data_version stored in BlockStructureModel
    and changes each time the block structure cache is rebuilt.

    Reads from cache first; on a miss, falls back to the database
    without populating the cache. The cache is populated exclusively by
    _update_block_structure_version after a successful rebuild, which
    prevents readers from accidentally caching a stale version during
    a concurrent rebuild.
    """
    cache_key = BLOCK_STRUCTURE_VERSION_KEY.format(course_key)
    version = cache.get(cache_key)
    if version is None:
        try:
            course_usage_key = modulestore().make_course_usage_key(course_key)
            block_structure_model = BlockStructureModel.objects.get(data_usage_key=course_usage_key)
            version = str(block_structure_model.data_version or '')
        except BlockStructureModel.DoesNotExist:
            version = ''
    return version


def _update_block_structure_version(course_key):
    """
    Reads the current data_version from BlockStructureModel and updates
    the cached block structure version key.
    """
    try:
        course_usage_key = modulestore().make_course_usage_key(course_key)
        block_structure_model = BlockStructureModel.objects.get(data_usage_key=course_usage_key)
        version = str(block_structure_model.data_version or '')
    except BlockStructureModel.DoesNotExist:
        version = ''
    cache.set(BLOCK_STRUCTURE_VERSION_KEY.format(course_key), version, timeout=None)


def get_course_in_cache(course_key):
    """
    A higher order function implemented on top of the
    block_structure.get_collected function that returns the block
    structure in the cache for the given course_key.

    Returns:
        BlockStructureBlockData - The collected block structure,
            starting at root_block_usage_key.
    """
    return get_block_structure_manager(course_key).get_collected()


def update_course_in_cache(course_key):
    """
    A higher order function implemented on top of the
    block_structure.updated_collected function that updates the block
    structure in the cache for the given course_key.
    """
    get_block_structure_manager(course_key).update_collected_if_needed()
    _update_block_structure_version(course_key)


def clear_course_from_cache(course_key):
    """
    A higher order function implemented on top of the
    block_structure.clear_block_cache function that clears the block
    structure from the cache for the given course_key.

    Note: See Note in get_course_blocks. Even after MA-1604 is
    implemented, this implementation should still be valid since the
    entire block structure of the course is cached, even though
    arbitrary access to an intermediate block will be supported.
    """
    get_block_structure_manager(course_key).clear()


def get_block_structure_manager(course_key):
    """
    Returns the manager for managing Block Structures for the given course.
    """
    store = modulestore()
    course_usage_key = store.make_course_usage_key(course_key)
    return BlockStructureManager(course_usage_key, store, get_cache())


def get_cache():
    """
    Returns the storage for caching Block Structures.
    """
    return cache
