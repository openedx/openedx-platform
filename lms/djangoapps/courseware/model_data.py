"""
Classes to provide the LMS runtime data storage to XBlocks.

:class:`DjangoKeyValueStore`: An XBlock :class:`~KeyValueStore` which
    stores a subset of xblocks scopes as Django ORM objects. It wraps
    :class:`~FieldDataCache` to provide an XBlock-friendly interface.


:class:`FieldDataCache`: A object which provides a read-through prefetch cache
    of data to support XBlock fields within a limited set of scopes.

The remaining classes in this module provide read-through prefetch cache implementations
for specific scopes. The individual classes provide the knowledge of what are the essential
pieces of information for each scope, and thus how to cache, prefetch, and create new field data
entries.

UserStateCache: A cache for Scope.user_state
UserStateSummaryCache: A cache for Scope.user_state_summary
PreferencesCache: A cache for Scope.preferences
UserInfoCache: A cache for Scope.user_info
DjangoOrmFieldCache: A base-class for single-row-per-field caches.
"""


import json
import logging
from abc import ABCMeta, abstractmethod
from collections import defaultdict, namedtuple

from django.db import DatabaseError, IntegrityError, transaction
from opaque_keys.edx.asides import AsideUsageKeyV1, AsideUsageKeyV2
from opaque_keys.edx.block_types import BlockTypeKeyV1
from opaque_keys.edx.keys import LearningContextKey
from xblock.core import XBlockAside
from xblock.exceptions import InvalidScopeError, KeyValueMultiSaveError
from xblock.fields import Scope, UserScope
from xblock.runtime import KeyValueStore

from lms.djangoapps.courseware.user_state_client import DjangoXBlockUserStateClient
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from .models import StudentModule, XModuleStudentInfoField, XModuleStudentPrefsField, XModuleUserStateSummaryField

log = logging.getLogger(__name__)


class InvalidWriteError(Exception):
    """
    Raised to indicate that writing to a particular key
    in the KeyValueStore is disabled
    """


def _all_usage_keys(blocks, aside_types):
    """
    Return a set of all usage_ids for the `blocks` and for
    as all asides in `aside_types` for those blocks.
    """
    usage_ids = set()
    for block in blocks:
        usage_ids.add(block.scope_ids.usage_id)

        for aside_type in aside_types:
            usage_ids.add(AsideUsageKeyV1(block.scope_ids.usage_id, aside_type))
            usage_ids.add(AsideUsageKeyV2(block.scope_ids.usage_id, aside_type))

    return usage_ids


def _all_block_types(blocks, aside_types):
    """
    Return a set of all block_types for the supplied `blocks` and for
    the asides types in `aside_types` associated with those blocks.
    """
    block_types = set()
    for block in blocks:
        block_types.add(BlockTypeKeyV1(block.entry_point, block.scope_ids.block_type))

    for aside_type in aside_types:
        block_types.add(BlockTypeKeyV1(XBlockAside.entry_point, aside_type))

    return block_types


class DjangoKeyValueStore(KeyValueStore):
    """
    This KeyValueStore will read and write data in the following scopes to django models
        Scope.user_state_summary
        Scope.user_state
        Scope.preferences
        Scope.user_info

    Access to any other scopes will raise an InvalidScopeError

    Data for Scope.user_state is stored as StudentModule objects via the django orm.

    Data for the other scopes is stored in individual objects that are named for the
    scope involved and have the field name as a key

    If the key isn't found in the expected table during a read or a delete, then a KeyError will be raised
    """

    _allowed_scopes = (
        Scope.user_state_summary,
        Scope.user_state,
        Scope.preferences,
        Scope.user_info,
    )

    def __init__(self, field_data_cache):  # pylint: disable=super-init-not-called
        self._field_data_cache = field_data_cache

    def get(self, key):
        self._raise_unless_scope_is_allowed(key)
        return self._field_data_cache.get(key)

    def set(self, key, value):
        """
        Set a single value in the KeyValueStore
        """
        self.set_many({key: value})

    def set_many(self, kv_dict):  # pylint: disable=arguments-differ
        """
        Provide a bulk save mechanism.

        `kv_dict`: A dictionary of dirty fields that maps
          xblock.KvsFieldData._key : value

        """
        for key in kv_dict:
            # Check key for validity
            self._raise_unless_scope_is_allowed(key)

        self._field_data_cache.set_many(kv_dict)

    def delete(self, key):
        self._raise_unless_scope_is_allowed(key)
        self._field_data_cache.delete(key)

    def has(self, key):
        self._raise_unless_scope_is_allowed(key)
        return self._field_data_cache.has(key)

    def _raise_unless_scope_is_allowed(self, key):
        """Raise an InvalidScopeError if key.scope is not in self._allowed_scopes."""
        if key.scope not in self._allowed_scopes:
            raise InvalidScopeError(key, self._allowed_scopes)


class DjangoOrmFieldCache(metaclass=ABCMeta):
    """
    Baseclass for Scope-specific field cache objects that are based on
    single-row-per-field Django ORM objects.
    """

    def __init__(self):
        self._cache = {}

    def cache_fields(self, fields, xblocks, aside_types):
        """
        Load all fields specified by ``fields`` for the supplied ``xblocks``
        and ``aside_types`` into this cache.

        Arguments:
            fields (list of str): Field names to cache.
            xblocks (list of :class:`XBlock`): XBlocks to cache fields for.
            aside_types (list of str): Aside types to cache fields for.
        """
        for field_object in self._read_objects(fields, xblocks, aside_types):
            self._cache[self._cache_key_for_field_object(field_object)] = field_object

    def get(self, kvs_key):
        """
        Return the django model object specified by `kvs_key` from
        the cache.

        Arguments:
            kvs_key (`DjangoKeyValueStore.Key`): The field value to delete

        Returns: A django orm object from the cache
        """
        cache_key = self._cache_key_for_kvs_key(kvs_key)
        if cache_key not in self._cache:
            raise KeyError(kvs_key.field_name)

        field_object = self._cache[cache_key]

        return json.loads(field_object.value)

    def set(self, kvs_key, value):
        """
        Set the specified `kvs_key` to the field value `value`.

        Arguments:
            kvs_key (`DjangoKeyValueStore.Key`): The field value to delete
            value: The field value to store
        """
        self.set_many({kvs_key: value})

    def set_many(self, kv_dict):
        """
        Set the specified fields to the supplied values.

        Arguments:
            kv_dict (dict): A dictionary mapping :class:`~DjangoKeyValueStore.Key`
                objects to values to set.
        """
        saved_fields = []
        for kvs_key, value in sorted(kv_dict.items()):
            cache_key = self._cache_key_for_kvs_key(kvs_key)
            field_object = self._cache.get(cache_key)

            try:
                serialized_value = json.dumps(value)
                # It is safe to force an insert or an update, because
                # a) we should have retrieved the object as part of the
                #    prefetch step, so if it isn't in our cache, it doesn't exist yet.
                # b) no other code should be modifying these models out of band of
                #    this cache.
                if field_object is None:
                    field_object = self._create_object(kvs_key, serialized_value)
                    field_object.save(force_insert=True)
                    self._cache[cache_key] = field_object
                else:
                    field_object.value = serialized_value
                    field_object.save(force_update=True)

            except DatabaseError:
                log.exception("Saving field %r failed", kvs_key.field_name)
                raise KeyValueMultiSaveError(saved_fields)  # pylint: disable=raise-missing-from  # noqa: B904

            finally:
                saved_fields.append(kvs_key.field_name)

    def delete(self, kvs_key):
        """
        Delete the value specified by `kvs_key`.

        Arguments:
            kvs_key (`DjangoKeyValueStore.Key`): The field value to delete

        Raises: KeyError if key isn't found in the cache
        """

        cache_key = self._cache_key_for_kvs_key(kvs_key)
        field_object = self._cache.get(cache_key)
        if field_object is None:
            raise KeyError(kvs_key.field_name)

        field_object.delete()
        del self._cache[cache_key]

    def has(self, kvs_key):
        """
        Return whether the specified `kvs_key` is set.

        Arguments:
            kvs_key (`DjangoKeyValueStore.Key`): The field value to delete

        Returns: bool
        """
        return self._cache_key_for_kvs_key(kvs_key) in self._cache

    def last_modified(self, kvs_key):
        """
        Return when the supplied field was changed.

        Arguments:
            kvs_key (`DjangoKeyValueStore.Key`): The field value to delete

        Returns: datetime if there was a modified date, or None otherwise
        """
        field_object = self._cache.get(self._cache_key_for_kvs_key(kvs_key))

        if field_object is None:
            return None
        else:
            return field_object.modified

    def __len__(self):
        return len(self._cache)

    @abstractmethod
    def _create_object(self, kvs_key, value):
        """
        Create a new object to add to the cache (which should record
        the specified field ``value`` for the field identified by
        ``kvs_key``).

        Arguments:
            kvs_key (:class:`DjangoKeyValueStore.Key`): Which field to create an entry for
            value: What value to record in the field
        """
        raise NotImplementedError()

    @abstractmethod
    def _read_objects(self, fields, xblocks, aside_types):
        """
        Return an iterator for all objects stored in the underlying datastore
        for the ``fields`` on the ``xblocks`` and the ``aside_types`` associated
        with them.

        Arguments:
            fields (list of str): Field names to return values for
            xblocks (list of :class:`~XBlock`): XBlocks to load fields for
            aside_types (list of str): Asides to load field for (which annotate the supplied
                xblocks).
        """
        raise NotImplementedError()

    @abstractmethod
    def _cache_key_for_field_object(self, field_object):
        """
        Return the key used in this DjangoOrmFieldCache to store the specified field_object.

        Arguments:
            field_object: A Django model instance that stores the data for fields in this cache
        """
        raise NotImplementedError()

    @abstractmethod
    def _cache_key_for_kvs_key(self, key):
        """
        Return the key used in this DjangoOrmFieldCache for the specified KeyValueStore key.

        Arguments:
            key (:class:`~DjangoKeyValueStore.Key`): The key representing the cached field
        """
        raise NotImplementedError()


class UserStateCache:
    """
    Cache for Scope.user_state xblock field data.
    """
    def __init__(self, user, course_id):
        self._cache = defaultdict(dict)
        self.course_id = course_id
        self.user = user
        self._client = DjangoXBlockUserStateClient(self.user)

    def cache_fields(self, fields, xblocks, aside_types):  # pylint: disable=unused-argument
        """
        Load all fields specified by ``fields`` for the supplied ``xblocks``
        and ``aside_types`` into this cache.

        Arguments:
            fields (list of str): Field names to cache.
            xblocks (list of :class:`XBlock`): XBlocks to cache fields for.
            aside_types (list of str): Aside types to cache fields for.
        """
        block_field_state = self._client.get_many(
            self.user.username,
            _all_usage_keys(xblocks, aside_types),
        )
        for user_state in block_field_state:
            self._cache[user_state.block_key] = user_state.state

    def set(self, kvs_key, value):
        """
        Set the specified `kvs_key` to the field value `value`.

        Arguments:
            kvs_key (`DjangoKeyValueStore.Key`): The field value to delete
            value: The field value to store
        """
        self.set_many({kvs_key: value})

    def last_modified(self, kvs_key):
        """
        Return when the supplied field was changed.

        Arguments:
            kvs_key (`DjangoKeyValueStore.Key`): The key representing the cached field

        Returns: datetime if there was a modified date, or None otherwise
        """
        try:
            return self._client.get(
                self.user.username,
                kvs_key.block_scope_id,
                fields=[kvs_key.field_name],
            ).updated
        except self._client.DoesNotExist:
            return None

    def set_many(self, kv_dict):
        """
        Set the specified fields to the supplied values.

        Arguments:
            kv_dict (dict): A dictionary mapping :class:`~DjangoKeyValueStore.Key`
                objects to values to set.
        """
        pending_updates = defaultdict(dict)
        for kvs_key, value in kv_dict.items():
            cache_key = self._cache_key_for_kvs_key(kvs_key)

            pending_updates[cache_key][kvs_key.field_name] = value

        try:
            self._client.set_many(
                self.user.username,
                pending_updates
            )
        except DatabaseError:
            log.exception("Saving user state failed for %s", self.user.username)
            raise KeyValueMultiSaveError([])  # pylint: disable=raise-missing-from  # noqa: B904
        finally:
            self._cache.update(pending_updates)

    def get(self, kvs_key):
        """
        Return the django model object specified by `kvs_key` from
        the cache.

        Arguments:
            kvs_key (`DjangoKeyValueStore.Key`): The field value to delete

        Returns: A django orm object from the cache
        """
        cache_key = self._cache_key_for_kvs_key(kvs_key)
        if cache_key not in self._cache:
            raise KeyError(kvs_key.field_name)

        return self._cache[cache_key][kvs_key.field_name]

    def delete(self, kvs_key):
        """
        Delete the value specified by `kvs_key`.

        Arguments:
            kvs_key (`DjangoKeyValueStore.Key`): The field value to delete

        Raises: KeyError if key isn't found in the cache
        """
        cache_key = self._cache_key_for_kvs_key(kvs_key)
        if cache_key not in self._cache:
            raise KeyError(kvs_key.field_name)

        field_state = self._cache[cache_key]

        if kvs_key.field_name not in field_state:
            raise KeyError(kvs_key.field_name)

        self._client.delete(self.user.username, cache_key, fields=[kvs_key.field_name])
        del field_state[kvs_key.field_name]

    def has(self, kvs_key):
        """
        Return whether the specified `kvs_key` is set.

        Arguments:
            kvs_key (`DjangoKeyValueStore.Key`): The field value to delete

        Returns: bool
        """
        cache_key = self._cache_key_for_kvs_key(kvs_key)

        return (
            cache_key in self._cache and
            kvs_key.field_name in self._cache[cache_key]
        )

    def __len__(self):
        return len(self._cache)

    def _cache_key_for_kvs_key(self, key):
        """
        Return the key used in this DjangoOrmFieldCache for the specified KeyValueStore key.

        Arguments:
            key (:class:`~DjangoKeyValueStore.Key`): The key representing the cached field
        """
        return key.block_scope_id


class UserStateSummaryCache(DjangoOrmFieldCache):
    """
    Cache for Scope.user_state_summary xblock field data.
    """
    def __init__(self, course_id):
        super().__init__()
        self.course_id = course_id

    def _create_object(self, kvs_key, value):
        """
        Create a new object to add to the cache (which should record
        the specified field ``value`` for the field identified by
        ``kvs_key``).

        Arguments:
            kvs_key (:class:`DjangoKeyValueStore.Key`): Which field to create an entry for
            value: The value to assign to the new field object
        """
        return XModuleUserStateSummaryField(
            field_name=kvs_key.field_name,
            usage_id=kvs_key.block_scope_id,
            value=value,
        )

    def _read_objects(self, fields, xblocks, aside_types):
        """
        Return an iterator for all objects stored in the underlying datastore
        for the ``fields`` on the ``xblocks`` and the ``aside_types`` associated
        with them.

        Arguments:
            fields (list of :class:`~Field`): Fields to return values for
            xblocks (list of :class:`~XBlock`): XBlocks to load fields for
            aside_types (list of str): Asides to load field for (which annotate the supplied
                xblocks).
        """
        return XModuleUserStateSummaryField.objects.chunked_filter(
            'usage_id__in',
            _all_usage_keys(xblocks, aside_types),
            field_name__in={field.name for field in fields},
        )

    def _cache_key_for_field_object(self, field_object):
        """
        Return the key used in this DjangoOrmFieldCache to store the specified field_object.

        Arguments:
            field_object: A Django model instance that stores the data for fields in this cache
        """
        return field_object.usage_id.map_into_course(self.course_id), field_object.field_name

    def _cache_key_for_kvs_key(self, key):
        """
        Return the key used in this DjangoOrmFieldCache for the specified KeyValueStore key.

        Arguments:
            key (:class:`~DjangoKeyValueStore.Key`): The key representing the cached field
        """
        return key.block_scope_id, key.field_name


class PreferencesCache(DjangoOrmFieldCache):
    """
    Cache for Scope.preferences xblock field data.
    """
    def __init__(self, user):
        super().__init__()
        self.user = user

    def _create_object(self, kvs_key, value):
        """
        Create a new object to add to the cache (which should record
        the specified field ``value`` for the field identified by
        ``kvs_key``).

        Arguments:
            kvs_key (:class:`DjangoKeyValueStore.Key`): Which field to create an entry for
            value: The value to assign to the new field object
        """
        return XModuleStudentPrefsField(
            field_name=kvs_key.field_name,
            module_type=BlockTypeKeyV1(kvs_key.block_family, kvs_key.block_scope_id),
            student_id=kvs_key.user_id,
            value=value,
        )

    def _read_objects(self, fields, xblocks, aside_types):
        """
        Return an iterator for all objects stored in the underlying datastore
        for the ``fields`` on the ``xblocks`` and the ``aside_types`` associated
        with them.

        Arguments:
            fields (list of str): Field names to return values for
            xblocks (list of :class:`~XBlock`): XBlocks to load fields for
            aside_types (list of str): Asides to load field for (which annotate the supplied
                xblocks).
        """
        return XModuleStudentPrefsField.objects.chunked_filter(
            'module_type__in',
            _all_block_types(xblocks, aside_types),
            student=self.user.pk,
            field_name__in={field.name for field in fields},
        )

    def _cache_key_for_field_object(self, field_object):
        """
        Return the key used in this DjangoOrmFieldCache to store the specified field_object.

        Arguments:
            field_object: A Django model instance that stores the data for fields in this cache
        """
        return field_object.module_type, field_object.field_name

    def _cache_key_for_kvs_key(self, key):
        """
        Return the key used in this DjangoOrmFieldCache for the specified KeyValueStore key.

        Arguments:
            key (:class:`~DjangoKeyValueStore.Key`): The key representing the cached field
        """
        return BlockTypeKeyV1(key.block_family, key.block_scope_id), key.field_name


class UserInfoCache(DjangoOrmFieldCache):
    """
    Cache for Scope.user_info xblock field data
    """
    def __init__(self, user):
        super().__init__()
        self.user = user

    def _create_object(self, kvs_key, value):
        """
        Create a new object to add to the cache (which should record
        the specified field ``value`` for the field identified by
        ``kvs_key``).

        Arguments:
            kvs_key (:class:`DjangoKeyValueStore.Key`): Which field to create an entry for
            value: The value to assign to the new field object
        """
        return XModuleStudentInfoField(
            field_name=kvs_key.field_name,
            student_id=kvs_key.user_id,
            value=value,
        )

    def _read_objects(self, fields, xblocks, aside_types):
        """
        Return an iterator for all objects stored in the underlying datastore
        for the ``fields`` on the ``xblocks`` and the ``aside_types`` associated
        with them.

        Arguments:
            fields (list of str): Field names to return values for
            xblocks (list of :class:`~XBlock`): XBlocks to load fields for
            aside_types (list of str): Asides to load field for (which annotate the supplied
                xblocks).
        """
        return XModuleStudentInfoField.objects.filter(
            student=self.user.pk,
            field_name__in={field.name for field in fields},
        )

    def _cache_key_for_field_object(self, field_object):
        """
        Return the key used in this DjangoOrmFieldCache to store the specified field_object.

        Arguments:
            field_object: A Django model instance that stores the data for fields in this cache
        """
        return field_object.field_name

    def _cache_key_for_kvs_key(self, key):
        """
        Return the key used in this DjangoOrmFieldCache for the specified KeyValueStore key.

        Arguments:
            key (:class:`~DjangoKeyValueStore.Key`): The key representing the cached field
        """
        return key.field_name


class FieldDataCache:
    """
    A cache of django model objects needed to supply the data
    for a block and its descendants
    """
    def __init__(self, blocks, course_id, user, asides=None, read_only=False):
        """
        Find any courseware.models objects that are needed by any block
        in blocks. Attempts to minimize the number of queries to the database.
        Note: Only blocks that have store_state = True or have shared
        state will have a StudentModule.

        Arguments
        blocks: A list of XBlocks.
        course_id: The id of the current course
        user: The user for which to cache data
        asides: The list of aside types to load, or None to prefetch no asides.
        read_only: We should not perform writes (they become a no-op).
        """
        if asides is None:
            self.asides = []
        else:
            self.asides = asides

        assert isinstance(course_id, LearningContextKey)
        self.course_id = course_id
        self.user = user
        self.read_only = read_only

        self.cache = {
            Scope.user_state: UserStateCache(
                self.user,
                self.course_id,
            ),
            Scope.user_info: UserInfoCache(
                self.user,
            ),
            Scope.preferences: PreferencesCache(
                self.user,
            ),
            Scope.user_state_summary: UserStateSummaryCache(
                self.course_id,
            ),
        }
        self.scorable_locations = set()
        self.add_blocks_to_cache(blocks)

    def add_blocks_to_cache(self, blocks):
        """
        Add all `blocks` to this FieldDataCache.
        """
        if self.user.is_authenticated:
            self.scorable_locations.update(block.location for block in blocks if block.has_score)
            for scope, fields in self._fields_to_cache(blocks).items():
                if scope not in self.cache:
                    continue

                self.cache[scope].cache_fields(fields, blocks, self.asides)

    def add_block_descendents(self, block, depth=None, block_filter=lambda block: True):
        """
        Add all descendants of `block` to this FieldDataCache.

        Arguments:
            block: An XBlock
            depth is the number of levels of descendant blocks to load StudentModules for, in addition to
                the supplied block. If depth is None, load all descendant StudentModules
            block_filter is a function that accepts a block and return whether the field data
                should be cached
        """

        def get_child_blocks(block, depth, block_filter):
            """
            Return a list of all child blocks down to the specified depth
            that match the block filter. Includes `block`

            block: The parent to search inside
            depth: The number of levels to descend, or None for infinite depth
            block_filter(block): A function that returns True
                if block should be included in the results
            """
            if block_filter(block):
                blocks = [block]
            else:
                blocks = []

            if depth is None or depth > 0:
                new_depth = depth - 1 if depth is not None else depth

                for child in block.get_children() + block.get_required_block_descriptors():
                    blocks.extend(get_child_blocks(child, new_depth, block_filter))

            return blocks

        with modulestore().bulk_operations(block.location.course_key):
            blocks = get_child_blocks(block, depth, block_filter)

        self.add_blocks_to_cache(blocks)

    @classmethod
    def cache_for_block_descendents(cls, course_id, user, block, depth=None,
                                    block_filter=lambda block: True,
                                    asides=None, read_only=False):
        """
        course_id: the course in the context of which we want StudentModules.
        user: the django user for whom to load modules.
        block: An XBlock
        depth is the number of levels of descendant blocks to load StudentModules for, in addition to
            the supplied block. If depth is None, load all descendant StudentModules
        block_filter is a function that accepts a block and return whether the field data
            should be cached
        """
        cache = FieldDataCache([], course_id, user, asides=asides, read_only=read_only)
        cache.add_block_descendents(block, depth, block_filter)
        return cache

    def _fields_to_cache(self, blocks):
        """
        Returns a map of scopes to fields in that scope that should be cached
        """
        scope_map = defaultdict(set)
        for block in blocks:
            for field in block.fields.values():
                scope_map[field.scope].add(field)
        return scope_map

    def get(self, key):
        """
        Load the field value specified by `key`.

        Arguments:
            key (`DjangoKeyValueStore.Key`): The field value to load

        Returns: The found value
        Raises: KeyError if key isn't found in the cache
        """

        if key.scope.user == UserScope.ONE and not self.user.is_anonymous:
            # If we're getting user data, we expect that the key matches the
            # user we were constructed for.
            assert key.user_id == self.user.id

        if key.scope not in self.cache:
            raise KeyError(key.field_name)

        return self.cache[key.scope].get(key)

    def set_many(self, kv_dict):
        """
        Set all of the fields specified by the keys of `kv_dict` to the values
        in that dict.

        Arguments:
            kv_dict (dict): dict mapping from `DjangoKeyValueStore.Key`s to field values
        Raises: DatabaseError if any fields fail to save
        """
        if self.read_only:
            return

        saved_fields = []
        by_scope = defaultdict(dict)
        for key, value in kv_dict.items():

            if key.scope.user == UserScope.ONE and not self.user.is_anonymous:
                # If we're getting user data, we expect that the key matches the
                # user we were constructed for.
                assert key.user_id == self.user.id

            if key.scope not in self.cache:
                continue

            by_scope[key.scope][key] = value

        for scope, set_many_data in by_scope.items():
            try:
                self.cache[scope].set_many(set_many_data)
                # If save is successful on these fields, add it to
                # the list of successful saves
                saved_fields.extend(key.field_name for key in set_many_data)
            except KeyValueMultiSaveError as exc:
                log.exception('Error saving fields %r', [key.field_name for key in set_many_data])
                raise KeyValueMultiSaveError(saved_fields + exc.saved_field_names)  # pylint: disable=raise-missing-from  # noqa: B904

    def delete(self, key):
        """
        Delete the value specified by `key`.

        Arguments:
            key (`DjangoKeyValueStore.Key`): The field value to delete

        Raises: KeyError if key isn't found in the cache
        """
        if self.read_only:
            return

        if key.scope.user == UserScope.ONE and not self.user.is_anonymous:
            # If we're getting user data, we expect that the key matches the
            # user we were constructed for.
            assert key.user_id == self.user.id

        if key.scope not in self.cache:
            raise KeyError(key.field_name)

        self.cache[key.scope].delete(key)

    def has(self, key):
        """
        Return whether the specified `key` is set.

        Arguments:
            key (`DjangoKeyValueStore.Key`): The field value to delete

        Returns: bool
        """

        if key.scope.user == UserScope.ONE and not self.user.is_anonymous:
            # If we're getting user data, we expect that the key matches the
            # user we were constructed for.
            assert key.user_id == self.user.id

        if key.scope not in self.cache:
            return False

        return self.cache[key.scope].has(key)

    def last_modified(self, key):
        """
        Return when the supplied field was changed.

        Arguments:
            key (`DjangoKeyValueStore.Key`): The field value to delete

        Returns: datetime if there was a modified date, or None otherwise
        """
        if key.scope.user == UserScope.ONE and not self.user.is_anonymous:
            # If we're getting user data, we expect that the key matches the
            # user we were constructed for.
            assert key.user_id == self.user.id

        if key.scope not in self.cache:
            return None

        return self.cache[key.scope].last_modified(key)

    def __len__(self):
        return sum(len(cache) for cache in self.cache.values())


class ScoresClient:
    """
    Basic client interface for retrieving Score information.

    Eventually, this should read and write scores, but at the moment it only
    handles the read side of things.
    """
    Score = namedtuple('Score', 'correct total created')

    def __init__(self, course_key, user_id):
        self.course_key = course_key
        self.user_id = user_id
        self._locations_to_scores = {}
        self._has_fetched = False

    def __contains__(self, location):
        """Return True if we have a score for this location."""
        return location in self._locations_to_scores

    def fetch_scores(self, locations):
        """Grab score information."""
        scores_qset = StudentModule.objects.filter(
            student_id=self.user_id,
            course_id=self.course_key,
            module_state_key__in=set(locations),
        )
        # Locations in StudentModule don't necessarily have course key info
        # attached to them (since old mongo identifiers don't include runs).
        # So we have to add that info back in before we put it into our lookup.
        self._locations_to_scores.update({
            location.map_into_course(self.course_key): self.Score(correct, total, created)
            for location, correct, total, created
            in scores_qset.values_list('module_state_key', 'grade', 'max_grade', 'created')
        })
        self._has_fetched = True

    def get(self, location):
        """
        Get the score for a given location, if it exists.

        If we don't have a score for that location, return `None`. Note that as
        convention, you should be passing in a location with full course run
        information.
        """
        if not self._has_fetched:
            raise ValueError(
                "Tried to fetch location {} from ScoresClient before fetch_scores() has run."  # noqa: UP032
                .format(location)
            )
        return self._locations_to_scores.get(location.replace(version=None, branch=None))

    @classmethod
    def create_for_locations(cls, course_id, user_id, scorable_locations):
        """Create a ScoresClient with pre-fetched data for the given locations."""
        client = cls(course_id, user_id)
        client.fetch_scores(scorable_locations)
        return client


def set_score(user_id, usage_key, score, max_score):
    """
    Set the score and max_score for the specified user and xblock usage.
    """
    created = False
    kwargs = {"student_id": user_id, "module_state_key": usage_key, "course_id": usage_key.context_key}
    try:
        with transaction.atomic():
            student_module, created = StudentModule.objects.get_or_create(
                defaults={
                    'grade': score,
                    'max_grade': max_score,
                    'module_type': usage_key.block_type,
                },
                **kwargs
            )
    except IntegrityError:
        # log information for duplicate entry and get the record as above command failed.
        log.exception(
            'set_score: IntegrityError for student %s - course_id %s - usage_key %s having '
            'score %d and max_score %d',
            str(user_id), usage_key.context_key, usage_key, score, max_score
        )
        student_module = StudentModule.objects.get(**kwargs)

    if not created:
        student_module.grade = score
        student_module.max_grade = max_score
        student_module.save()
    return student_module.modified


def get_score(user_id, usage_key):
    """
    Get the score and max_score for the specified user and xblock usage.
    Returns None if not found.
    """
    try:
        student_module = StudentModule.objects.get(
            student_id=user_id,
            module_state_key=usage_key,
            course_id=usage_key.course_key,
        )
    except StudentModule.DoesNotExist:
        return None
    else:
        return student_module
