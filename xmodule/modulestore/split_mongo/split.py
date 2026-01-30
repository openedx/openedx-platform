"""
Provides full versioning CRUD and representation for collections of xblocks (e.g., courses, blocks, etc).

Representation:
* course_index: a dictionary:
    ** '_id': a unique id which cannot change,
    ** 'org': the org's id. Only used for searching not identity,
    ** 'course': the course's catalog number
    ** 'run': the course's run id,
    ** 'edited_by': user_id of user who created the original entry,
    ** 'edited_on': the datetime of the original creation,
    ** 'versions': versions_dict: {branch_id: structure_id, ...}
    ** 'search_targets': a dict of search key and value. For example, wiki_slug. Add any fields whose edits
        should change the search targets to SplitMongoModuleStore.SEARCH_TARGET dict
* structure:
    ** '_id': an ObjectId (guid),
    ** 'root': BlockKey (the block_type and block_id of the root block in the 'blocks' dictionary)
    ** 'previous_version': the structure from which this one was derived. For published courses, this
    points to the previously published version of the structure not the draft published to this.
    ** 'original_version': the original structure id in the previous_version relation. Is a pseudo object
    identifier enabling quick determination if 2 structures have any shared history,
    ** 'edited_by': user_id of the user whose change caused the creation of this structure version,
    ** 'edited_on': the datetime for the change causing this creation of this structure version,
    ** 'blocks': dictionary of xblocks in this structure:
        *** BlockKey: key mapping to each BlockData:
        *** BlockData: object containing the following attributes:
            **** 'block_type': the xblock type id
            **** 'definition': the db id of the record containing the content payload for this xblock
            **** 'fields': the Scope.settings and children field values
                ***** 'children': This is stored as a list of (block_type, block_id) pairs
            **** 'defaults': Scope.settings default values copied from a template block (used e.g. when
                blocks are copied from a library to a course)
            **** 'edit_info': EditInfo object:
                ***** 'edited_on': when was this xblock's fields last changed (will be edited_on value of
                update_version structure)
                ***** 'edited_by': user_id for who changed this xblock last (will be edited_by value of
                update_version structure)
                ***** 'update_version': the guid for the structure where this xblock got its current field
                values. This may point to a structure not in this structure's history (e.g., to a draft
                branch from which this version was published.)
                ***** 'previous_version': the guid for the structure which previously changed this xblock
                (will be the previous value of update_version; so, may point to a structure not in this
                structure's history.)
                ***** 'source_version': the guid for the structure was copied/published into this block
* definition: shared content with revision history for xblock content fields
    ** '_id': definition_id (guid),
    ** 'block_type': xblock type id
    ** 'fields': scope.content (and possibly other) field values.
    ** 'edit_info': dictionary:
        *** 'edited_by': user_id whose edit caused this version of the definition,
        *** 'edited_on': datetime of the change causing this version
        *** 'previous_version': the definition_id of the previous version of this definition
        *** 'original_version': definition_id of the root of the previous version relation on this
        definition. Acts as a pseudo-object identifier.
"""


import copy
import datetime
import logging
from collections import defaultdict
from importlib import import_module
from zoneinfo import ZoneInfo

from bson.objectid import ObjectId
from ccx_keys.locator import CCXBlockUsageLocator, CCXLocator
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import (
    BlockUsageLocator,
    CourseLocator,
    DefinitionLocator,
    LibraryLocator,
    LocalId,
)
from path import Path as path
from xblock.core import XBlock
from xblock.fields import Reference, ReferenceList, ReferenceValueDict, Scope

from xmodule.assetstore import AssetMetadata
from xmodule.course_block import CourseSummary
from xmodule.error_block import ErrorBlock
from xmodule.errortracker import null_error_tracker
from xmodule.library_content_block import LegacyLibrarySummary
from xmodule.modulestore import (
    BlockData,
    BulkOperationsMixin,
    BulkOpsRecord,
    ModuleStoreEnum,
    ModuleStoreWriteBase,
    SortedAssetList,
    inheritance
)
from xmodule.modulestore.exceptions import (
    DuplicateCourseError,
    DuplicateItemError,
    InsufficientSpecificationError,
    MultipleCourseBlocksFound,
    MultipleLibraryBlocksFound,
    VersionConflictError
)
from xmodule.modulestore.split_mongo import CourseEnvelope
from xmodule.modulestore.split_mongo.mongo_connection import DuplicateKeyError, DjangoFlexPersistenceBackend
from xmodule.modulestore.store_utilities import DETACHED_XBLOCK_TYPES
from xmodule.partitions.partitions_service import PartitionService
from xmodule.util.misc import get_library_or_course_attribute
from xmodule.util.keys import BlockKey, derive_key

from ..exceptions import ItemNotFoundError
from .runtime import SplitModuleStoreRuntime

log = logging.getLogger(__name__)

# ==============================================================================
#
# Known issue:
#    Inheritance for cached kvs doesn't work on edits. Use case.
#     1) attribute foo is inheritable
#     2) g.children = [p], p.children = [a]
#     3) g.foo = 1 on load
#     4) if g.foo > 0, if p.foo > 0, if a.foo > 0 all eval True
#     5) p.foo = -1
#     6) g.foo > 0, p.foo <= 0 all eval True BUT
#     7) BUG: a.foo > 0 still evals True but should be False
#     8) reread and everything works right
#     9) p.del(foo), p.foo > 0 is True! works
#    10) BUG: a.foo < 0!
#   Local fix wont' permanently work b/c xblock may cache a.foo...
#
# ==============================================================================

# When blacklists are this, all children should be excluded
EXCLUDE_ALL = '*'


class SplitBulkWriteRecord(BulkOpsRecord):  # lint-amnesty, pylint: disable=missing-class-docstring
    def __init__(self):
        super().__init__()
        self.initial_index = None
        self.index = None
        self.structures = {}
        self.structures_in_db = set()
        # dict(version_guid, dict(BlockKey, block))
        self.modules = defaultdict(dict)
        self.definitions = {}
        self.definitions_in_db = set()
        self.course_key = None

    # TODO: This needs to track which branches have actually been modified/versioned,
    # so that copying one branch to another doesn't update the original branch.
    @property
    def dirty_branches(self):
        """
        Return a list of which branch version ids differ from what was stored
        in the database at the beginning of this bulk operation.
        """
        # If no course index has been set, then no branches have changed
        if self.index is None:
            return []

        # If there was no index in the database to start with, then all branches
        # are dirty by definition
        if self.initial_index is None:
            return list(self.index.get('versions', {}).keys())

        # Return branches whose ids differ between self.index and self.initial_index
        return [
            branch
            for branch, _id
            in self.index.get('versions', {}).items()
            if self.initial_index.get('versions', {}).get(branch) != _id
        ]

    def structure_for_branch(self, branch):
        return self.structures.get(self.index.get('versions', {}).get(branch))

    def set_structure_for_branch(self, branch, structure):
        if self.index is not None:
            self.index.setdefault('versions', {})[branch] = structure['_id']
        self.structures[structure['_id']] = structure

    def __repr__(self):
        return "SplitBulkWriteRecord<{!r}, {!r}, {!r}, {!r}, {!r}>".format(
            self._active_count,
            self.initial_index,
            self.index,
            self.structures,
            self.structures_in_db,
        )


class SplitBulkWriteMixin(BulkOperationsMixin):
    """
    This implements the :meth:`bulk_operations` modulestore semantics for the :class:`SplitMongoModuleStore`.

    In particular, it implements :meth:`_begin_bulk_operation` and
    :meth:`_end_bulk_operation` to provide the external interface, and then exposes a set of methods
    for interacting with course_indexes and structures that can be used by :class:`SplitMongoModuleStore`.

    Internally, this mixin records the set of all active bulk operations (keyed on the active course),
    and only writes those values to ``self.mongo_connection`` when :meth:`_end_bulk_operation` is called.
    If a bulk write operation isn't active, then the changes are immediately written to the underlying
    mongo_connection.
    """
    _bulk_ops_record_type = SplitBulkWriteRecord

    def _get_bulk_ops_record(self, course_key, ignore_case=False):
        """
        Return the :class:`.SplitBulkWriteRecord` for this course.
        """
        # handle split specific things and defer to super otherwise
        if course_key is None:
            return self._bulk_ops_record_type()

        if not isinstance(course_key, (CourseLocator, LibraryLocator)):
            raise TypeError(f'{course_key!r} is not a CourseLocator or LibraryLocator')
        # handle version_guid based retrieval locally
        if course_key.org is None or get_library_or_course_attribute(course_key) is None or course_key.run is None:
            return self._active_bulk_ops.records[
                course_key.replace(org=None, course=None, run=None, branch=None)
            ]

        # handle ignore case and general use
        return super()._get_bulk_ops_record(
            course_key.replace(branch=None, version_guid=None), ignore_case
        )

    def _clear_bulk_ops_record(self, course_key):
        """
        Clear the record for this course
        """
        if not isinstance(course_key, (CourseLocator, LibraryLocator)):
            raise TypeError(f'{course_key!r} is not a CourseLocator or LibraryLocator')

        if course_key.org and get_library_or_course_attribute(course_key) and course_key.run:
            del self._active_bulk_ops.records[course_key.replace(branch=None, version_guid=None)]
        else:
            del self._active_bulk_ops.records[
                course_key.replace(org=None, course=None, run=None, branch=None)
            ]

    def _start_outermost_bulk_operation(self, bulk_write_record, course_key, ignore_case=False):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Begin a bulk write operation on course_key.
        """
        bulk_write_record.initial_index = self.db_connection.get_course_index(course_key, ignore_case=ignore_case)
        # Ensure that any edits to the index don't pollute the initial_index
        bulk_write_record.index = copy.deepcopy(bulk_write_record.initial_index)
        bulk_write_record.course_key = course_key

    def _end_outermost_bulk_operation(self, bulk_write_record, structure_key):  # lint-amnesty, pylint: disable=arguments-differ
        """
        End the active bulk write operation on structure_key (course or library key).
        """

        dirty = False

        # If the content is dirty, then update the database
        for _id in bulk_write_record.structures.keys() - bulk_write_record.structures_in_db:
            dirty = True

            try:
                self.db_connection.insert_structure(bulk_write_record.structures[_id], bulk_write_record.course_key)
            except DuplicateKeyError:
                # We may not have looked up this structure inside this bulk operation, and thus
                # didn't realize that it was already in the database. That's OK, the store is
                # append only, so if it's already been written, we can just keep going.
                log.debug("Attempted to insert duplicate structure %s", _id)

        for _id in bulk_write_record.definitions.keys() - bulk_write_record.definitions_in_db:
            dirty = True

            try:
                self.db_connection.insert_definition(bulk_write_record.definitions[_id], bulk_write_record.course_key)
            except DuplicateKeyError:
                # We may not have looked up this definition inside this bulk operation, and thus
                # didn't realize that it was already in the database. That's OK, the store is
                # append only, so if it's already been written, we can just keep going.
                log.debug("Attempted to insert duplicate definition %s", _id)

        if bulk_write_record.index is not None and bulk_write_record.index != bulk_write_record.initial_index:
            dirty = True

            if bulk_write_record.initial_index is None:
                self.db_connection.insert_course_index(bulk_write_record.index, bulk_write_record.course_key)
            else:
                self.db_connection.update_course_index(
                    bulk_write_record.index,
                    from_index=bulk_write_record.initial_index,
                    course_context=bulk_write_record.course_key
                )

        return dirty

    def get_course_index(self, course_key, ignore_case=False):
        """
        Return the index for course_key.
        """
        if self._is_in_bulk_operation(course_key, ignore_case):
            return self._get_bulk_ops_record(course_key, ignore_case).index
        else:
            return self.db_connection.get_course_index(course_key, ignore_case)

    def delete_course_index(self, course_key):
        """
        Delete the course index from cache and the db
        """
        if self._is_in_bulk_operation(course_key, False):
            self._clear_bulk_ops_record(course_key)

        self.db_connection.delete_course_index(course_key)

    def insert_course_index(self, course_key, index_entry):  # lint-amnesty, pylint: disable=missing-function-docstring
        bulk_write_record = self._get_bulk_ops_record(course_key)
        if bulk_write_record.active:
            bulk_write_record.index = index_entry
        else:
            self.db_connection.insert_course_index(index_entry, course_key)

    def update_course_index(self, course_key, updated_index_entry):
        """
        Change the given course's index entry.

        Note, this operation can be dangerous and break running courses.

        Does not return anything useful.
        """
        bulk_write_record = self._get_bulk_ops_record(course_key)
        if bulk_write_record.active:
            bulk_write_record.index = updated_index_entry
        else:
            self.db_connection.update_course_index(updated_index_entry, course_context=course_key)

    def get_structure(self, course_key, version_guid):  # lint-amnesty, pylint: disable=missing-function-docstring
        bulk_write_record = self._get_bulk_ops_record(course_key)
        if bulk_write_record.active:
            structure = bulk_write_record.structures.get(version_guid)

            # The structure hasn't been loaded from the db yet, so load it
            if structure is None:
                structure = self.db_connection.get_structure(version_guid, course_key)
                bulk_write_record.structures[version_guid] = structure
                if structure is not None:
                    bulk_write_record.structures_in_db.add(version_guid)

            return structure
        else:
            # cast string to ObjectId if necessary
            version_guid = course_key.as_object_id(version_guid)
            return self.db_connection.get_structure(version_guid, course_key)

    def update_structure(self, course_key, structure):
        """
        Update a course structure, respecting the current bulk operation status
        (no data will be written to the database if a bulk operation is active.)
        """
        self._clear_cache(structure['_id'])
        bulk_write_record = self._get_bulk_ops_record(course_key)
        if bulk_write_record.active:
            bulk_write_record.structures[structure['_id']] = structure
        else:
            self.db_connection.insert_structure(structure, course_key)

    def get_cached_block(self, course_key, version_guid, block_id):
        """
        If there's an active bulk_operation, see if it's cached this block and just return it
        Don't do any extra work to get the ones which are not cached. Make the caller do the work & cache them.
        """
        bulk_write_record = self._get_bulk_ops_record(course_key)
        if bulk_write_record.active:
            return bulk_write_record.modules[version_guid].get(block_id, None)
        else:
            return None

    def cache_block(self, course_key, version_guid, block_key, block):
        """
        The counterpart to :method `get_cached_block` which caches a block.
        Returns nothing.
        """
        bulk_write_record = self._get_bulk_ops_record(course_key)
        if bulk_write_record.active:
            bulk_write_record.modules[version_guid][block_key] = block

    def decache_block(self, course_key, version_guid, block_key):
        """
        Write operations which don't write from blocks must remove the target blocks from the cache.
        Returns nothing.
        """
        bulk_write_record = self._get_bulk_ops_record(course_key)
        if bulk_write_record.active:
            try:
                del bulk_write_record.modules[version_guid][block_key]
            except KeyError:
                pass

    def get_definition(self, course_key, definition_guid):
        """
        Retrieve a single definition by id, respecting the active bulk operation
        on course_key.

        Args:
            course_key (:class:`.CourseKey`): The course being operated on
            definition_guid (str or ObjectID): The id of the definition to load
        """
        bulk_write_record = self._get_bulk_ops_record(course_key)
        if bulk_write_record.active:
            definition = bulk_write_record.definitions.get(definition_guid)

            # The definition hasn't been loaded from the db yet, so load it
            if definition is None:
                definition = self.db_connection.get_definition(definition_guid, course_key)
                bulk_write_record.definitions[definition_guid] = definition
                if definition is not None:
                    bulk_write_record.definitions_in_db.add(definition_guid)

            return definition
        else:
            # cast string to ObjectId if necessary
            definition_guid = course_key.as_object_id(definition_guid)
            return self.db_connection.get_definition(definition_guid, course_key)

    def get_definitions(self, course_key, ids):
        """
        Return all definitions that specified in ``ids``.

        If a definition with the same id is in both the cache and the database,
        the cached version will be preferred.

        Arguments:
            course_key (:class:`.CourseKey`): The course that these definitions are being loaded
                for (to respect bulk operations).
            ids (list): A list of definition ids
        """
        definitions = []
        ids = set(ids)

        bulk_write_record = self._get_bulk_ops_record(course_key)
        if bulk_write_record.active:
            # Only query for the definitions that aren't already cached.
            for definition in bulk_write_record.definitions.values():
                definition_id = definition.get('_id')
                if definition_id in ids:
                    ids.remove(definition_id)
                    definitions.append(definition)

        if len(ids):  # lint-amnesty, pylint: disable=len-as-condition
            # Query the db for the definitions.
            defs_from_db = list(self.db_connection.get_definitions(list(ids), course_key))
            defs_dict = {d.get('_id'): d for d in defs_from_db}
            # Add the retrieved definitions to the cache.
            bulk_write_record.definitions_in_db.update(defs_dict.keys())
            bulk_write_record.definitions.update(defs_dict)
            definitions.extend(defs_from_db)
        return definitions

    def update_definition(self, course_key, definition):
        """
        Update a definition, respecting the current bulk operation status
        (no data will be written to the database if a bulk operation is active.)
        """
        bulk_write_record = self._get_bulk_ops_record(course_key)
        if bulk_write_record.active:
            bulk_write_record.definitions[definition['_id']] = definition
        else:
            self.db_connection.insert_definition(definition, course_key)

    def version_structure(self, course_key, structure, user_id):
        """
        Copy the structure and update the history info (edited_by, edited_on, previous_version)
        """
        if course_key.branch is None:
            raise InsufficientSpecificationError(course_key)

        bulk_write_record = self._get_bulk_ops_record(course_key)

        # If we have an active bulk write, and it's already been edited, then just use that structure
        if bulk_write_record.active and course_key.branch in bulk_write_record.dirty_branches:
            return bulk_write_record.structure_for_branch(course_key.branch)

        # Otherwise, make a new structure
        new_structure = copy.deepcopy(structure)
        new_structure['_id'] = ObjectId()
        new_structure['previous_version'] = structure['_id']
        new_structure['edited_by'] = user_id
        new_structure['edited_on'] = datetime.datetime.now(ZoneInfo("UTC"))
        new_structure['schema_version'] = self.SCHEMA_VERSION

        # If we're in a bulk write, update the structure used there, and mark it as dirty
        if bulk_write_record.active:
            bulk_write_record.set_structure_for_branch(course_key.branch, new_structure)

        return new_structure

    def version_block(self, block_data, user_id, update_version):
        """
        Update the block_data object based on it having been edited.
        """
        if block_data.edit_info.update_version == update_version:
            return

        original_usage = block_data.edit_info.original_usage
        original_usage_version = block_data.edit_info.original_usage_version
        block_data.edit_info.edited_on = datetime.datetime.now(ZoneInfo("UTC"))
        block_data.edit_info.edited_by = user_id
        block_data.edit_info.previous_version = block_data.edit_info.update_version
        block_data.edit_info.update_version = update_version
        if original_usage:
            block_data.edit_info.original_usage = original_usage
            block_data.edit_info.original_usage_version = original_usage_version

    def find_matching_course_indexes(self, branch=None, search_targets=None, org_target=None, course_keys=None):
        """
        Find the course_indexes which have the specified branch and search_targets. An optional org_target
        can be specified to apply an ORG filter to return only the courses that are part of
        that ORG.

        Returns:
            a Cursor if there are no changes in flight or a list if some have changed in current bulk op
        """
        indexes = self.db_connection.find_matching_course_indexes(
            branch,
            search_targets,
            org_target,
            course_keys=course_keys)

        indexes = self._add_indexes_from_active_records(
            indexes,
            branch,
            search_targets,
            org_target,
            course_keys=course_keys
        )

        return indexes

    def _add_indexes_from_active_records(
            self,
            course_indexes,
            branch=None,
            search_targets=None,
            org_target=None,
            course_keys=None
    ):
        """

        Add any being built but not yet persisted or in the process of being updated
        """
        def _replace_or_append_index(altered_index):
            """
            If the index is already in indexes, replace it. Otherwise, append it.
            """
            for index, existing in enumerate(course_indexes):
                if all(existing[attr] == altered_index[attr] for attr in ['org', 'course', 'run']):
                    course_indexes[index] = altered_index
                    return
            course_indexes.append(altered_index)

        for _, record in self._active_records:
            if branch and branch not in record.index.get('versions', {}):
                continue

            if search_targets:
                if any(
                    'search_targets' not in record.index or
                    field not in record.index['search_targets'] or
                    record.index['search_targets'][field] != value
                    for field, value in search_targets.items()
                ):
                    continue
            # if we've specified a filter by org,
            # make sure we've honored that filter when
            # integrating in-transit records
            if org_target:
                if record.index['org'] != org_target:
                    continue

            if course_keys:
                index_exists_in_active_records = False
                for course_key in course_keys:
                    if all(record.index[key_attr] == getattr(course_key, key_attr)
                           for key_attr in ['org', 'course', 'run']):
                        index_exists_in_active_records = True
                        break
                if not index_exists_in_active_records:
                    continue

            if not hasattr(course_indexes, 'append'):  # Just in time conversion to list from cursor
                course_indexes = list(course_indexes)

            _replace_or_append_index(record.index)

        return course_indexes

    def find_courselike_blocks_by_id(self, ids, block_type):
        """
        Find all structures that specified in `ids`. Return blocks matching with block_type.
        Arguments:
            ids (list): A list of structure ids
            block_type: type of block to return
        """
        ids = set(ids)
        return self.db_connection.find_courselike_blocks_by_id(list(ids), block_type)

    def find_structures_by_id(self, ids):
        """
        Return all structures that specified in ``ids``.

        If a structure with the same id is in both the cache and the database,
        the cached version will be preferred.

        Arguments:
            ids (list): A list of structure ids
        """
        structures = []
        ids = set(ids)

        for _, record in self._active_records:
            for structure in record.structures.values():
                structure_id = structure.get('_id')
                if structure_id in ids:
                    ids.remove(structure_id)
                    structures.append(structure)

        structures.extend(self.db_connection.find_structures_by_id(list(ids)))
        return structures


class SplitMongoModuleStore(SplitBulkWriteMixin, ModuleStoreWriteBase):
    """
    A Mongodb backed ModuleStore supporting versions, inheritance,
    and sharing.
    """

    SCHEMA_VERSION = 1
    # a list of field names to store in course index search_targets. Note, this will
    # only record one value per key. If branches disagree, the last one set wins.
    # It won't recompute the value on operations such as update_course_index (e.g., to revert to a prev
    # version) but those functions will have an optional arg for setting these.
    SEARCH_TARGET_DICT = ['wiki_slug']

    DEFAULT_ROOT_LIBRARY_BLOCK_TYPE = 'library'
    DEFAULT_ROOT_COURSE_BLOCK_TYPE = 'course'

    def __init__(self, contentstore, doc_store_config, fs_root, render_template,
                 default_class=None,
                 error_tracker=null_error_tracker,
                 i18n_service=None, fs_service=None, user_service=None,
                 services=None, signal_handler=None, **kwargs):
        """
        :param doc_store_config: must have a host, db, and collection entries. Other common entries: port, tz_aware.
        """

        super().__init__(contentstore, **kwargs)

        self.db_connection = DjangoFlexPersistenceBackend(**doc_store_config)

        if default_class is not None:
            module_path, __, class_name = default_class.rpartition('.')
            try:
                class_ = getattr(import_module(module_path), class_name)
            except (ImportError, AttributeError):
                fallback_module_path = "xmodule.hidden_block"
                fallback_class_name = "HiddenBlock"
                log.exception(
                    "Failed to import the default store class. "
                    f"Falling back to {fallback_module_path}.{fallback_class_name}"
                )
                class_ = getattr(import_module(fallback_module_path), fallback_class_name)
            self.default_class = class_
        else:
            self.default_class = None
        self.fs_root = path(fs_root)
        self.error_tracker = error_tracker
        self.render_template = render_template
        self.services = services or {}
        if i18n_service is not None:
            self.services["i18n"] = i18n_service

        if fs_service is not None:
            self.services["fs"] = fs_service

        if user_service is not None:
            self.services["user"] = user_service

        if self.request_cache is not None:
            self.services["request_cache"] = self.request_cache

        self.signal_handler = signal_handler

    def close_connections(self):
        """
        Closes any open connections to the underlying databases
        """
        self.db_connection.close_connections()

    def _drop_database(self, database=True, collections=True, connections=True):
        """
        A destructive operation to drop the underlying database and close all connections.
        Intended to be used by test code for cleanup.

        If database is True, then this should drop the entire database.
        Otherwise, if collections is True, then this should drop all of the collections used
        by this modulestore.
        Otherwise, the modulestore should remove all data from the collections.

        If connections is True, then close the connection to the database as well.
        """
        # drop the assets
        super()._drop_database(database, collections, connections)

        self.db_connection._drop_database(database, collections, connections)  # pylint: disable=protected-access

    def cache_items(self, system, base_block_ids, course_key, depth=0, lazy=True):
        """
        Handles caching of items once inheritance and any other one time
        per course per fetch operations are done.

        Arguments:
            system: a SplitModuleStoreRuntime
            base_block_ids: list of BlockIds to fetch
            course_key: the destination course providing the context
            depth: how deep below these to prefetch
            lazy: whether to load definitions now or later
        """
        with self.bulk_operations(course_key, emit_signals=False):
            new_block_data = {}
            for block_id in base_block_ids:
                new_block_data = self.descendants(
                    system.course_entry.structure['blocks'],
                    block_id,
                    depth,
                    new_block_data
                )

            # This method supports lazy loading, where the descendent definitions aren't loaded
            # until they're actually needed.
            if not lazy:
                # Non-lazy loading: Load all descendants by id.
                descendent_definitions = self.get_definitions(
                    course_key,
                    [
                        block.definition
                        for block in new_block_data.values()
                    ]
                )
                # Turn definitions into a map.
                definitions = {definition['_id']: definition
                               for definition in descendent_definitions}

                for block in new_block_data.values():
                    if block.definition in definitions:
                        definition = definitions[block.definition]
                        # convert_fields gets done later in the runtime's xblock_from_json
                        block.fields.update(definition.get('fields'))
                        block.definition_loaded = True

            system.module_data.update(new_block_data)
            return system.module_data

    def _load_items(self, course_entry, block_keys, depth=0, **kwargs):
        """
        Load & cache the given blocks from the course. May return the blocks in any order.

        Load the definitions into each block if lazy is in kwargs and is False;
        otherwise, do not load the definitions - they'll be loaded later when needed.
        """
        lazy = kwargs.pop('lazy', True)
        should_cache_items = not lazy

        runtime = self._get_cache(course_entry.structure['_id'])
        if runtime is None:
            runtime = self.create_runtime(course_entry, lazy)
            self._add_cache(course_entry.structure['_id'], runtime)
            should_cache_items = True

        if should_cache_items:
            self.cache_items(runtime, block_keys, course_entry.course_key, depth, lazy)

        with self.bulk_operations(course_entry.course_key, emit_signals=False):
            return [runtime.load_item(block_key, course_entry, **kwargs) for block_key in block_keys]

    def _get_cache(self, course_version_guid):
        """
        Find the block cache for this course if it exists
        :param course_version_guid:
        """
        if self.request_cache is None:
            return None

        return self.request_cache.data.setdefault('course_cache', {}).get(course_version_guid)

    def _add_cache(self, course_version_guid, system):
        """
        Save this cache for subsequent access
        :param course_version_guid:
        :param system:
        """
        if self.request_cache is not None:
            self.request_cache.data.setdefault('course_cache', {})[course_version_guid] = system
        return system

    def _clear_cache(self, course_version_guid=None):
        """
        Should only be used by testing or something which implements transactional boundary semantics.
        :param course_version_guid: if provided, clear only this entry
        """
        if self.request_cache is None:
            return

        if course_version_guid:
            try:
                del self.request_cache.data.setdefault('course_cache', {})[course_version_guid]
            except KeyError:
                pass
        else:
            self.request_cache.data['course_cache'] = {}

    def _lookup_course(self, course_key, head_validation=True):
        """
        Decode the locator into the right series of db access. Does not
        return the CourseBlock! It returns the actual db json from
        structures.

        Semantics: if course id and branch given, then it will get that branch. If
        also give a version_guid, it will see if the current head of that branch == that guid. If not
        it raises VersionConflictError (the version now differs from what it was when you got your
        reference) unless you specify head_validation = False, in which case it will return the
        revision (if specified) by the course_key.

        :param course_key: any subclass of CourseLocator
        """
        if not course_key.version_guid:
            head_validation = True
        if head_validation and course_key.org and get_library_or_course_attribute(course_key) and course_key.run:
            if course_key.branch is None:
                raise InsufficientSpecificationError(course_key)

            # use the course id
            index = self.get_course_index(course_key)

            if index is None:
                raise ItemNotFoundError(course_key)
            if course_key.branch not in index['versions']:
                raise ItemNotFoundError(course_key)

            version_guid = index['versions'][course_key.branch]

            if course_key.version_guid is not None and version_guid != course_key.version_guid:
                # This may be a bit too touchy but it's hard to infer intent
                raise VersionConflictError(course_key, version_guid)

        elif course_key.version_guid is None:
            raise InsufficientSpecificationError(course_key)
        else:
            # TODO should this raise an exception if branch was provided?
            version_guid = course_key.version_guid

        entry = self.get_structure(course_key, version_guid)
        if entry is None:
            raise ItemNotFoundError(f'Structure: {version_guid}')

        # b/c more than one course can use same structure, the 'org', 'course',
        # 'run', and 'branch' are not intrinsic to structure
        # and the one assoc'd w/ it by another fetch may not be the one relevant to this fetch; so,
        # add it in the envelope for the structure.
        return CourseEnvelope(course_key.replace(version_guid=version_guid), entry)

    def _get_courselike_blocks_for_branch(self, branch, **kwargs):
        """
        Internal generator for fetching lists of courselike without loading them.
        """
        version_guids, id_version_map = self.collect_ids_from_matching_indexes(branch, **kwargs)

        if not version_guids:
            return

        block_type = SplitMongoModuleStore.DEFAULT_ROOT_LIBRARY_BLOCK_TYPE \
            if branch == 'library' else SplitMongoModuleStore.DEFAULT_ROOT_COURSE_BLOCK_TYPE

        for entry in self.find_courselike_blocks_by_id(version_guids, block_type):
            for course_index in id_version_map[entry['_id']]:
                yield entry, course_index

    def _get_structures_for_branch(self, branch, **kwargs):
        """
        Internal generator for fetching lists of courses, libraries, etc.
        """
        version_guids, id_version_map = self.collect_ids_from_matching_indexes(branch, **kwargs)

        if not version_guids:
            return

        for entry in self.find_structures_by_id(version_guids):
            for course_index in id_version_map[entry['_id']]:
                yield entry, course_index

    def collect_ids_from_matching_indexes(self, branch, **kwargs):
        """
        Find the course_indexes which have the specified branch. Extract `version_guids`
        from the course_indexes.

        """
        matching_indexes = self.find_matching_course_indexes(
            branch,
            search_targets=None,
            org_target=kwargs.get('org'),
            course_keys=kwargs.get('course_keys')
        )

        # collect ids and then query for those
        version_guids = []
        id_version_map = defaultdict(list)
        for course_index in matching_indexes:
            version_guid = course_index['versions'][branch]
            version_guids.append(version_guid)
            id_version_map[version_guid].append(course_index)
        return version_guids, id_version_map

    def _get_structures_for_branch_and_locator(self, branch, locator_factory, **kwargs):
        """
        Internal generator for fetching lists of courses, libraries, etc.
        :param str branch: Branch to fetch structures from
        :param type locator_factory: Factory to create locator from structure info and branch
        """
        result = []
        for entry, structure_info in self._get_structures_for_branch(branch, **kwargs):
            locator = locator_factory(structure_info, branch)
            envelope = CourseEnvelope(locator, entry)
            root = entry['root']
            structures_list = self._load_items(envelope, [root], depth=0, **kwargs)
            if not isinstance(structures_list[0], ErrorBlock):
                result.append(structures_list[0])
        return result

    def _create_course_locator(self, course_info, branch):
        """
        Creates course locator using course_info dict and branch
        """
        return CourseLocator(
            org=course_info['org'],
            course=course_info['course'],
            run=course_info['run'],
            branch=branch,
        )

    def _create_library_locator(self, library_info, branch):
        """
        Creates library locator using library_info dict and branch
        """
        return LibraryLocator(
            org=library_info['org'],
            library=library_info['course'],
            branch=branch,
        )

    def get_courses(self, branch, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Returns a list of course blocks matching any given qualifiers.

        qualifiers should be a dict of keywords matching the db fields or any
        legal query for mongo to use against the active_versions collection.

        Note, this is to find the current head of the named branch type.
        To get specific versions via guid use get_course.

        :param branch: the branch for which to return courses.
        """
        # get the blocks for each course index (s/b the root)
        return self._get_structures_for_branch_and_locator(branch, self._create_course_locator, **kwargs)

    def get_course_summaries(self, branch, **kwargs):
        """
        Returns a list of `CourseSummary` which matching any given qualifiers.

        qualifiers should be a dict of keywords matching the db fields or any
        legal query for mongo to use against the active_versions collection.

        Note, this is to find the current head of the named branch type.
        To get specific versions via guid use get_course.

        :param branch: the branch for which to return courses.
        """
        def extract_course_summary(course):
            """
            Extract course information from the course block for split.
            """
            return {
                field: course.fields[field]
                for field in CourseSummary.course_info_fields
                if field in course.fields
            }

        courses_summaries = []
        for entry, structure_info in self._get_courselike_blocks_for_branch(branch, **kwargs):
            course_locator = self._create_course_locator(structure_info, branch=None)
            course_block = [
                block_data
                for block_key, block_data in entry['blocks'].items()
                if block_key.type == "course"
            ]
            if not course_block:
                raise ItemNotFoundError

            if len(course_block) > 1:
                raise MultipleCourseBlocksFound(
                    "Expected 1 course block to be found in the course, but found {}".format(len(course_block))
                )
            course_summary = extract_course_summary(course_block[0])
            courses_summaries.append(
                CourseSummary(course_locator, **course_summary)
            )
        return courses_summaries

    def get_library_keys(self):
        """
        Returns a list of all unique content library keys in the Split
        modulestore.

        Returns: list[LibraryLocator]
        """
        return list({
            self._create_library_locator(library_index, branch=None)
            for library_index
            in self.find_matching_course_indexes(branch="library")
        })

    def get_library_summaries(self, **kwargs):
        """
        Returns a list of `LegacyLibrarySummary` objects.
        kwargs can be valid db fields to match against active_versions
        collection e.g org='example_org'.
        """
        branch = 'library'
        libraries_summaries = []
        for entry, structure_info in self._get_courselike_blocks_for_branch(branch, **kwargs):
            library_locator = self._create_library_locator(structure_info, branch=None)
            library_block = [
                block_data
                for block_key, block_data in entry['blocks'].items()
                if block_key.type == "library"
            ]
            if not library_block:
                raise ItemNotFoundError

            if len(library_block) > 1:
                raise MultipleLibraryBlocksFound(
                    "Expected 1 library block, but found {}".format(len(library_block))
                )

            library_block_fields = library_block[0].fields
            display_name = ''

            if 'display_name' in library_block_fields:
                display_name = library_block_fields['display_name']

            libraries_summaries.append(
                LegacyLibrarySummary(library_locator, display_name)
            )

        return libraries_summaries

    def get_libraries(self, branch="library", **kwargs):
        """
        Returns a list of "library" root blocks matching any given qualifiers.

        TODO: better way of identifying library index entry vs. course index entry.
        """
        return self._get_structures_for_branch_and_locator(branch, self._create_library_locator, **kwargs)

    def make_course_key(self, org, course, run):
        """
        Return a valid :class:`~opaque_keys.edx.keys.CourseKey` for this modulestore
        that matches the supplied `org`, `course`, and `run`.

        This key may represent a course that doesn't exist in this modulestore.
        """
        return CourseLocator(org, course, run)

    def make_course_usage_key(self, course_key):
        """
        Return a valid :class:`~opaque_keys.edx.keys.UsageKey` for this modulestore
        that matches the supplied course_key.
        """
        locator_cls = CCXBlockUsageLocator if isinstance(course_key, CCXLocator) else BlockUsageLocator
        return locator_cls(course_key, 'course', 'course')

    def _get_structure(self, structure_id, depth, head_validation=True, **kwargs):
        """
        Gets Course or Library by locator
        """
        structure_entry = self._lookup_course(structure_id, head_validation=head_validation)
        root = structure_entry.structure['root']
        result = self._load_items(structure_entry, [root], depth, **kwargs)
        return result[0]

    def get_course(self, course_id, depth=0, **kwargs):
        """
        Gets the course block for the course identified by the locator
        """
        if not isinstance(course_id, CourseLocator) or course_id.deprecated:
            # The supplied CourseKey is of the wrong type, so it can't possibly be stored in this modulestore.
            raise ItemNotFoundError(course_id)
        return self._get_structure(course_id, depth, **kwargs)

    def get_library(self, library_id, depth=0, head_validation=True, **kwargs):
        """
        Gets the 'library' root block for the library identified by the locator
        """
        if not isinstance(library_id, LibraryLocator):
            # The supplied CourseKey is of the wrong type, so it can't possibly be stored in this modulestore.
            raise ItemNotFoundError(library_id)
        return self._get_structure(library_id, depth, head_validation=head_validation, **kwargs)

    def has_course(self, course_id, ignore_case=False, **kwargs):
        """
        Does this course exist in this modulestore. This method does not verify that the branch &/or
        version in the course_id exists. Use get_course_index_info to check that.

        Returns the course_id of the course if it was found, else None
        Note: we return the course_id instead of a boolean here since the found course may have
           a different id than the given course_id when ignore_case is True.
        """
        if not isinstance(course_id, CourseLocator) or course_id.deprecated:
            # The supplied CourseKey is of the wrong type, so it can't possibly be stored in this modulestore.
            return False

        course_index = self.get_course_index(course_id, ignore_case)
        return CourseLocator(course_index['org'], course_index['course'], course_index['run'], course_id.branch) if course_index else None  # lint-amnesty, pylint: disable=line-too-long

    def has_library(self, library_id, ignore_case=False, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
        """
        Does this library exist in this modulestore. This method does not verify that the branch &/or
        version in the library_id exists.

        Returns the library_id of the course if it was found, else None.
        """
        if not isinstance(library_id, LibraryLocator):
            return None

        index = self.get_course_index(library_id, ignore_case)
        if index:
            return LibraryLocator(index['org'], index['course'], library_id.branch)
        return None

    def has_item(self, usage_key):
        """
        Returns True if usage_key exists in its course. Returns false if
        the course or the block w/in the course do not exist for the given version.
        raises InsufficientSpecificationError if the usage_key does not id a block
        """
        if not isinstance(usage_key, BlockUsageLocator) or usage_key.deprecated:
            # The supplied UsageKey is of the wrong type, so it can't possibly be stored in this modulestore.
            return False

        if usage_key.block_id is None:
            raise InsufficientSpecificationError(usage_key)
        try:
            course_structure = self._lookup_course(usage_key.course_key).structure
        except ItemNotFoundError:
            # this error only occurs if the course does not exist
            return False

        return self._get_block_from_structure(course_structure, BlockKey.from_usage_key(usage_key)) is not None

    def get_item(self, usage_key, depth=0, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ
        """
        depth (int): An argument that some module stores may use to prefetch
            descendants of the queried blocks for more efficient results later
            in the request. The depth is counted in the number of
            calls to get_children() to cache. None indicates to cache all
            descendants.
        raises InsufficientSpecificationError or ItemNotFoundError
        """
        if not isinstance(usage_key, BlockUsageLocator) or usage_key.deprecated:
            # The supplied UsageKey is of the wrong type, so it can't possibly be stored in this modulestore.
            raise ItemNotFoundError(usage_key)

        with self.bulk_operations(usage_key.course_key):
            course = self._lookup_course(usage_key.course_key)
            items = self._load_items(course, [BlockKey.from_usage_key(usage_key)], depth, **kwargs)
            if len(items) == 0:  # lint-amnesty, pylint: disable=no-else-raise
                raise ItemNotFoundError(usage_key)
            elif len(items) > 1:
                log.debug(f"Found more than one item for '{usage_key}'")
            return items[0]

    def get_items(self, course_locator, settings=None, content=None, qualifiers=None, include_orphans=True, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Returns:
            list of XModuleDescriptor instances for the matching items within the course with
            the given course_locator

        NOTE: don't use this to look for courses as the course_locator is required. Use get_courses.

        Args:
            course_locator (CourseLocator): the course identifier
            settings (dict): fields to look for which have settings scope. Follows same syntax
                and rules as qualifiers below
            content (dict): fields to look for which have content scope. Follows same syntax and
                rules as qualifiers below.
            qualifiers (dict): what to look for within the course.
                Common qualifiers are ``category`` or any field name. if the target field is a list,
                then it searches for the given value in the list not list equivalence.
                For substring matching pass a regex object.
                For split,
                you can search by ``edited_by``, ``edited_on`` providing a function testing limits.
            include_orphans (boolean): Returns all items in a course, including orphans if present.
                True - This would return all items irrespective of course in tree checking. It may fetch orphans
                if present in the course.
                False - if we want only those items which are in the course tree. This would ensure no orphans are
                fetched.
        """
        if not isinstance(course_locator, CourseKey) or course_locator.deprecated:
            # The supplied courselike key is of the wrong type, so it can't possibly be stored in this modulestore.
            return []

        course = self._lookup_course(course_locator)
        items = []
        qualifiers = qualifiers.copy() if qualifiers else {}  # copy the qualifiers (destructively manipulated here)

        def _block_matches_all(block_data):
            """
            Check that the block matches all the criteria
            """
            # do the checks which don't require loading any additional data
            if (
                self._block_matches(block_data, qualifiers) and
                self._block_matches(block_data.fields, settings)
            ):
                if content:
                    definition_block = self.get_definition(course_locator, block_data.definition)
                    return self._block_matches(definition_block['fields'], content)
                else:
                    return True

        if settings is None:
            settings = {}
        if 'name' in qualifiers:
            # odd case where we don't search just confirm
            block_name = qualifiers.pop('name')
            block_ids = []
            for block_id, block in course.structure['blocks'].items():
                # Don't do an in comparison blindly; first check to make sure
                # that the name qualifier we're looking at isn't a plain string;
                # if it is a string, then it should match exactly. If it's other
                # than a string, we check whether it contains the block ID; this
                # is so a list or other iterable can be passed with multiple
                # valid qualifiers.
                if isinstance(block_name, str):
                    name_matches = block_id.id == block_name
                else:
                    name_matches = block_id.id in block_name
                if name_matches and _block_matches_all(block):
                    block_ids.append(block_id)

            return self._load_items(course, block_ids, **kwargs)

        if 'category' in qualifiers:
            qualifiers['block_type'] = qualifiers.pop('category')

        # don't expect caller to know that children are in fields
        if 'children' in qualifiers:
            settings['children'] = qualifiers.pop('children')

        # No need of these caches unless include_orphans is set to False
        path_cache = None
        parents_cache = None

        if not include_orphans:
            path_cache = {}
            parents_cache = self.build_block_key_to_parents_mapping(course.structure)

        for block_id, value in course.structure['blocks'].items():
            if _block_matches_all(value):
                if not include_orphans:
                    if (
                        block_id.type in DETACHED_XBLOCK_TYPES or
                        self.has_path_to_root(block_id, course, path_cache, parents_cache)
                    ):
                        items.append(block_id)
                else:
                    items.append(block_id)

        if len(items) > 0:
            return self._load_items(course, items, depth=0, **kwargs)
        else:
            return []

    def build_block_key_to_parents_mapping(self, structure):
        """
        Given a structure, builds block_key to parents mapping for all block keys in structure
        and returns it

        :param structure: db json of course structure

        :return dict: a dictionary containing mapping of block_keys against their parents.
        """
        children_to_parents = defaultdict(list)
        for parent_key, value in structure['blocks'].items():
            for child_key in value.fields.get('children', []):
                children_to_parents[child_key].append(parent_key)

        return children_to_parents

    def has_path_to_root(self, block_key, course, path_cache=None, parents_cache=None):
        """
        Check recursively if an xblock has a path to the course root

        :param block_key: BlockKey of the component whose path is to be checked
        :param course: actual db json of course from structures
        :param path_cache: a dictionary that records which blocks have a path to the root so that we don't have to
        double count blocks if we're computing this for a list of blocks in a course.
        :param parents_cache: a dictionary containing mapping of block_key to list of its parents. Optionally, this
        should be built for course structure to make this method faster.

        :return Bool: whether or not component has path to the root
        """

        if path_cache and block_key in path_cache:
            return path_cache[block_key]

        if parents_cache is None:
            xblock_parents = self._get_parents_from_structure(block_key, course.structure)
        else:
            xblock_parents = parents_cache[block_key]

        if len(xblock_parents) == 0 and block_key.type in ["course", "library"]:
            # Found, xblock has the path to the root
            if path_cache is not None:
                path_cache[block_key] = True

            return True

        has_path = any(
            self.has_path_to_root(xblock_parent, course, path_cache, parents_cache)
            for xblock_parent in xblock_parents
        )

        if path_cache is not None:
            path_cache[block_key] = has_path

        return has_path

    def get_parent_location(self, locator, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Return the location (Locators w/ block_ids) for the parent of this location in this
        course. Could use get_items(location, {'children': block_id}) but this is slightly faster.
        NOTE: the locator must contain the block_id, and this code does not actually ensure block_id exists

        :param locator: BlockUsageLocator restricting search scope
        """
        if not isinstance(locator, BlockUsageLocator) or locator.deprecated:
            # The supplied locator is of the wrong type, so it can't possibly be stored in this modulestore.
            raise ItemNotFoundError(locator)

        course = self._lookup_course(locator.course_key)
        all_parent_ids = self._get_parents_from_structure(BlockKey.from_usage_key(locator), course.structure)

        # Check and verify the found parent_ids are not orphans; Remove parent which has no valid path
        # to the course root
        parent_ids = [
            valid_parent
            for valid_parent in all_parent_ids
            if self.has_path_to_root(valid_parent, course)
        ]

        if len(parent_ids) == 0:
            return None

        # find alphabetically least
        parent_ids.sort(key=lambda parent: (parent.type, parent.id))
        return BlockUsageLocator.make_relative(
            locator,
            block_type=parent_ids[0].type,
            block_id=parent_ids[0].id,
        )

    def get_orphans(self, course_key, **kwargs):
        """
        Return an array of all of the orphans in the course.
        """
        if not isinstance(course_key, CourseLocator) or course_key.deprecated:
            # The supplied CourseKey is of the wrong type, so it can't possibly be stored in this modulestore.
            raise ItemNotFoundError(course_key)

        detached_categories = [name for name, __ in XBlock.load_tagged_classes("detached")]
        course = self._lookup_course(course_key)
        items = set(course.structure['blocks'].keys())
        items.remove(course.structure['root'])
        blocks = course.structure['blocks']
        for block_id, block_data in blocks.items():
            items.difference_update(BlockKey(*child) for child in block_data.fields.get('children', []))
            if block_data.block_type in detached_categories:
                items.discard(block_id)
        return [
            course_key.make_usage_key(block_type=block_id.type, block_id=block_id.id)
            for block_id in items
        ]

    def get_course_index_info(self, course_key):
        """
        The index records the initial creation of the indexed course and tracks the current version
        heads. This function is primarily for test verification but may serve some
        more general purpose.
        :param course_key: must have a org, course, and run set
        :return {'org': string,
            versions: {'draft': the head draft version id,
                'published': the head published version id if any,
            },
            'edited_by': who created the course originally (named edited for consistency),
            'edited_on': when the course was originally created
        }
        """
        if not isinstance(course_key, CourseLocator) or course_key.deprecated:
            # The supplied CourseKey is of the wrong type, so it can't possibly be stored in this modulestore.
            raise ItemNotFoundError(course_key)

        if not (course_key.course and course_key.run and course_key.org):
            return None
        index = self.get_course_index(course_key)
        return index

    # TODO figure out a way to make this info accessible from the course block
    def get_course_history_info(self, course_key):
        """
        Because xblocks doesn't give a means to separate the course structure's meta information from
        the course xblock's, this method will get that info for the structure as a whole.
        :param course_key:
        :return {'original_version': the version guid of the original version of this course,
            'previous_version': the version guid of the previous version,
            'edited_by': who made the last change,
            'edited_on': when the change was made
        }
        """
        if not isinstance(course_key, CourseLocator) or course_key.deprecated:
            # The supplied CourseKey is of the wrong type, so it can't possibly be stored in this modulestore.
            raise ItemNotFoundError(course_key)

        course = self._lookup_course(course_key).structure
        return {
            'original_version': course['original_version'],
            'previous_version': course['previous_version'],
            'edited_by': course['edited_by'],
            'edited_on': course['edited_on']
        }

    def get_definition_history_info(self, definition_locator, course_context=None):
        """
        Because xblocks doesn't give a means to separate the definition's meta information from
        the usage xblock's, this method will get that info for the definition
        :return {'original_version': the version guid of the original version of this course,
            'previous_version': the version guid of the previous version,
            'edited_by': who made the last change,
            'edited_on': when the change was made
        }
        """
        if not isinstance(definition_locator, DefinitionLocator) or definition_locator.deprecated:
            # The supplied locator is of the wrong type, so it can't possibly be stored in this modulestore.
            raise ItemNotFoundError(definition_locator)

        definition = self.db_connection.get_definition(definition_locator.definition_id, course_context)
        if definition is None:
            return None
        return definition['edit_info']

    def get_definition_successors(self, definition_locator, version_history_depth=1):
        """
        Find the version_history_depth next versions of this definition. Return as a VersionTree
        """
        # TODO implement
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    def get_block_original_usage(self, usage_key):
        """
        If a block was inherited into another structure using copy_from_template,
        this will return the original block usage locator and version from
        which the copy was inherited.

        Returns usage_key, version if the data is available, otherwise returns (None, None)
        """
        blocks = self._lookup_course(usage_key.course_key).structure['blocks']
        block = blocks.get(BlockKey.from_usage_key(usage_key))
        if block and block.edit_info.original_usage is not None:
            usage_key = BlockUsageLocator.from_string(block.edit_info.original_usage)
            return usage_key, block.edit_info.original_usage_version
        return None, None

    def create_definition_from_data(self, course_key, new_def_data, category, user_id):
        """
        Pull the definition fields out of block and save to the db as a new definition
        w/o a predecessor and return the new id.

        :param user_id: request.user object
        """
        new_def_data = self._serialize_fields(category, new_def_data)
        new_id = ObjectId()
        document = {
            '_id': new_id,
            "block_type": category,
            "fields": new_def_data,
            "edit_info": {
                "edited_by": user_id,
                "edited_on": datetime.datetime.now(ZoneInfo("UTC")),
                "previous_version": None,
                "original_version": new_id,
            },
            'schema_version': self.SCHEMA_VERSION,
        }
        self.update_definition(course_key, document)
        definition_locator = DefinitionLocator(category, new_id)
        return definition_locator

    def update_definition_from_data(self, course_key, definition_locator, new_def_data, user_id):
        """
        See if new_def_data differs from the persisted version. If so, update
        the persisted version and return the new id.

        :param user_id: request.user
        """
        def needs_saved():
            for key, value in new_def_data.items():
                if key not in old_definition['fields'] or value != old_definition['fields'][key]:
                    return True
            for key, value in old_definition.get('fields', {}).items():
                if key not in new_def_data:
                    return True

        # if this looks in cache rather than fresh fetches, then it will probably not detect
        # actual change b/c the block and cache probably point to the same objects
        old_definition = self.get_definition(course_key, definition_locator.definition_id)
        if old_definition is None:
            raise ItemNotFoundError(definition_locator)

        new_def_data = self._serialize_fields(old_definition['block_type'], new_def_data)
        if needs_saved():
            definition_locator = self._update_definition_from_data(course_key, old_definition, new_def_data, user_id)
            return definition_locator, True
        else:
            return definition_locator, False

    def _update_definition_from_data(self, course_key, old_definition, new_def_data, user_id):
        """
        Update the persisted version of the given definition and return the
        locator of the new definition. Does not check if data differs from the
        previous version.
        """
        new_definition = copy.deepcopy(old_definition)
        new_definition['_id'] = ObjectId()
        new_definition['fields'] = new_def_data
        new_definition['edit_info']['edited_by'] = user_id
        new_definition['edit_info']['edited_on'] = datetime.datetime.now(ZoneInfo("UTC"))
        # previous version id
        new_definition['edit_info']['previous_version'] = old_definition['_id']
        new_definition['schema_version'] = self.SCHEMA_VERSION
        self.update_definition(course_key, new_definition)
        return DefinitionLocator(new_definition['block_type'], new_definition['_id'])

    def _generate_block_key(self, course_blocks, category):
        """
        Generate a somewhat readable block id unique w/in this course using the category
        :param course_blocks: the current list of blocks.
        :param category:
        """
        # NOTE: a potential bug is that a block is deleted and another created which gets the old
        # block's id. a possible fix is to cache the last serial in a dict in the structure
        # {category: last_serial...}
        # A potential confusion is if the name incorporates the parent's name, then if the child
        # moves, its id won't change and will be confusing
        serial = 1
        while True:
            potential_key = BlockKey(category, f"{category}{serial}")
            if potential_key not in course_blocks:
                return potential_key
            serial += 1

    def create_item(self, user_id, course_key, block_type, block_id=None, definition_locator=None, fields=None,  # lint-amnesty, pylint: disable=arguments-differ
                    asides=None, force=False, **kwargs):
        """
        Add a block to persistence as an element
        of the course. Return the resulting post saved version with populated locators.

        :param course_key: If it has a version_guid and a course org + course + run + branch, this
        method ensures that the version is the head of the given course branch before making the change.

        raises InsufficientSpecificationError if there is no course locator.
        raises VersionConflictError if the version_guid of the course_or_parent_locator is not the head
            of the its course unless force is true.
        :param force: fork the structure and don't update the course draftVersion if the above
        :param continue_revision: for multistep transactions, continue revising the given version rather than creating
        a new version. Setting force to True conflicts with setting this to True and will cause a VersionConflictError

        :param definition_locator: should either be None to indicate this is a brand new definition or
        a pointer to the existing definition to which this block should point or from which this was derived
        or a LocalId to indicate that it's new.
        If fields does not contain any Scope.content, then definition_locator must have a value meaning that this
        block points
        to the existing definition. If fields contains Scope.content and definition_locator is not None, then
        the Scope.content fields are assumed to be a new payload for definition_locator.

        :param block_id: if provided, must not already exist in the structure. Provides the block id for the
        new item in this structure. Otherwise, one is computed using the category appended w/ a few digits.

        This method creates a new version of the course structure unless the course has a bulk_write operation
        active.
        It creates and inserts the new block, makes the block point
        to the definition which may be new or a new version of an existing or an existing.

        Rules for course locator:

        * If the course locator specifies a org and course and run and either it doesn't
          specify version_guid or the one it specifies == the current head of the branch,
          it progresses the course to point
          to the new head and sets the active version to point to the new head
        * If the locator has a org and course and run but its version_guid != current head, it raises VersionConflictError.  # lint-amnesty, pylint: disable=line-too-long

        NOTE: using a version_guid will end up creating a new version of the course. Your new item won't be in
        the course id'd by version_guid but instead in one w/ a new version_guid. Ensure in this case that you get
        the new version_guid from the locator in the returned object!
        """
        with self.bulk_operations(course_key):
            # split handles all the fields in one dict not separated by scope
            fields = fields or {}
            fields.update(kwargs.pop('metadata', {}) or {})
            definition_data = kwargs.pop('definition_data', {})
            if definition_data:
                if not isinstance(definition_data, dict):
                    definition_data = {'data': definition_data}  # backward compatibility to mongo's hack
                fields.update(definition_data)

            # find course_index entry if applicable and structures entry
            index_entry = self._get_index_if_valid(course_key, force)
            structure = self._lookup_course(course_key).structure

            partitioned_fields = self.partition_fields_by_scope(block_type, fields)
            new_def_data = partitioned_fields.get(Scope.content, {})
            # persist the definition if persisted != passed
            if definition_locator is None or isinstance(definition_locator.definition_id, LocalId):
                definition_locator = self.create_definition_from_data(course_key, new_def_data, block_type, user_id)
            elif new_def_data:
                definition_locator, _ = self.update_definition_from_data(course_key, definition_locator, new_def_data, user_id)  # lint-amnesty, pylint: disable=line-too-long

            # copy the structure and modify the new one
            new_structure = self.version_structure(course_key, structure, user_id)

            new_id = new_structure['_id']

            # generate usage id
            if block_id is not None:
                block_key = BlockKey(block_type, block_id)
                if block_key in new_structure['blocks']:
                    raise DuplicateItemError(block_id, self, 'structures')
            else:
                block_key = self._generate_block_key(new_structure['blocks'], block_type)

            block_fields = partitioned_fields.get(Scope.settings, {})
            if Scope.children in partitioned_fields:
                block_fields.update(partitioned_fields[Scope.children])
            self._update_block_in_structure(new_structure, block_key, self._new_block(
                user_id,
                block_type,
                block_fields,
                definition_locator.definition_id,
                new_id,
                asides=asides
            ))

            self.update_structure(course_key, new_structure)

            # update the index entry if appropriate
            if index_entry is not None:
                # see if any search targets changed
                if fields is not None:
                    self._update_search_targets(index_entry, fields)
                self._update_head(course_key, index_entry, course_key.branch, new_id)
                item_loc = BlockUsageLocator(
                    course_key.version_agnostic(),
                    block_type=block_type,
                    block_id=block_key.id,
                )
            else:
                item_loc = BlockUsageLocator(
                    CourseLocator(version_guid=new_id),
                    block_type=block_type,
                    block_id=block_key.id,
                )

            if isinstance(course_key, LibraryLocator):
                self._flag_library_updated_event(course_key)

            # reconstruct the new_item from the cache
            return self.get_item(item_loc)

    def create_child(self, user_id, parent_usage_key, block_type, block_id=None, fields=None, asides=None, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Creates and saves a new xblock that as a child of the specified block

        Returns the newly created item.

        Args:
            user_id: ID of the user creating and saving the xblock
            parent_usage_key: a :class:`~opaque_key.edx.UsageKey` identifying the
                block that this item should be parented under
            block_type: The typo of block to create
            block_id: a unique identifier for the new item. If not supplied,
                a new identifier will be generated
            fields (dict): A dictionary specifying initial values for some or all fields
                in the newly created block
            asides (dict): A dictionary specifying initial values for some or all aside fields
                in the newly created block
        """
        with self.bulk_operations(parent_usage_key.course_key):
            xblock = self.create_item(
                user_id, parent_usage_key.course_key, block_type, block_id=block_id, fields=fields, asides=asides,
                **kwargs)

            # skip attach to parent if xblock has 'detached' tag
            if 'detached' in xblock._class_tags:    # pylint: disable=protected-access
                return xblock

            # don't version the structure as create_item handled that already.
            new_structure = self._lookup_course(xblock.location.course_key).structure

            # add new block as child and update parent's version
            block_id = BlockKey.from_usage_key(parent_usage_key)
            if block_id not in new_structure['blocks']:
                raise ItemNotFoundError(parent_usage_key)

            parent = new_structure['blocks'][block_id]

            # Originally added to support entrance exams (settings.FEATURES.get('ENTRANCE_EXAMS'))
            if kwargs.get('position') is None:
                parent.fields.setdefault('children', []).append(BlockKey.from_usage_key(xblock.location))
            else:
                parent.fields.setdefault('children', []).insert(
                    kwargs.get('position'),
                    BlockKey.from_usage_key(xblock.location)
                )

            if parent.edit_info.update_version != new_structure['_id']:
                # if the parent hadn't been previously changed in this bulk transaction, indicate that it's
                # part of the bulk transaction
                self.version_block(parent, user_id, new_structure['_id'])
            self.decache_block(parent_usage_key.course_key, new_structure['_id'], block_id)

            # db update
            self.update_structure(parent_usage_key.course_key, new_structure)

        # don't need to update the index b/c create_item did it for this version
        return xblock

    def clone_course(self, source_course_id, dest_course_id, user_id, fields=None, **kwargs):
        """
        See :meth: `.ModuleStoreWrite.clone_course` for documentation.

        In split, other than copying the assets, this is cheap as it merely creates a new version of the
        existing course.
        """
        source_index = self.get_course_index_info(source_course_id)
        if source_index is None:
            raise ItemNotFoundError(f"Cannot find a course at {source_course_id}. Aborting")

        with self.bulk_operations(dest_course_id):
            new_course = self.create_course(
                dest_course_id.org, dest_course_id.course, dest_course_id.run,
                user_id,
                fields=fields,
                versions_dict=source_index['versions'],
                search_targets=source_index['search_targets'],
                skip_auto_publish=True,
                **kwargs
            )
            # don't copy assets until we create the course in case something's awry
            super().clone_course(source_course_id, dest_course_id, user_id, fields, **kwargs)
            return new_course

    DEFAULT_ROOT_COURSE_BLOCK_ID = 'course'
    DEFAULT_ROOT_LIBRARY_BLOCK_ID = 'library'

    def create_course(  # lint-amnesty, pylint: disable=arguments-differ
        self, org, course, run, user_id, master_branch=None, fields=None,
        versions_dict=None, search_targets=None, root_category='course',
        root_block_id=None, **kwargs
    ):
        """
        Create a new entry in the active courses index which points to an existing or new structure. Returns
        the course root of the resulting entry (the location has the course id)

        Arguments:

            org (str): the organization that owns the course
            course (str): the course number of the course
            run (str): the particular run of the course (e.g. 2013_T1)
            user_id: id of the user creating the course
            fields (dict): Fields to set on the course at initialization
            kwargs: Any optional arguments understood by a subset of modulestores to customize instantiation

        course + run: If there are duplicates, this method will raise DuplicateCourseError

        fields: if scope.settings fields provided, will set the fields of the root course object in the
        new course. If both
        settings fields and a starting version are provided (via versions_dict), it will generate a successor version
        to the given version,
        and update the settings fields with any provided values (via update not setting).

        fields (content): if scope.content fields provided, will update the fields of the new course
        xblock definition to this. Like settings fields,
        if provided, this will cause a new version of any given version as well as a new version of the
        definition (which will point to the existing one if given a version). If not provided and given
        a version_dict, it will reuse the same definition as that version's course
        (obvious since it's reusing the
        course). If not provided and no version_dict is given, it will be empty and get the field defaults
        when
        loaded.

        master_branch: the tag (key) for the version name in the dict which is the DRAFT version. Not the actual
        version guid, but what to call it.

        search_targets: a dict of search key and value. For example, wiki_slug. Add any fields whose edits
        should change the search targets to SplitMongoModuleStore.SEARCH_TARGET dict

        versions_dict: the starting version ids where the keys are the tags such as DRAFT and PUBLISHED
        and the values are structure guids. If provided, the new course will reuse this version (unless you also
        provide any fields overrides, see above). if not provided, will create a mostly empty course
        structure with just a category course root xblock.
        """
        # either need to assert this or have a default
        assert master_branch is not None
        # check course and run's uniqueness
        locator = CourseLocator(org=org, course=course, run=run, branch=master_branch)
        return self._create_courselike(
            locator, user_id, master_branch, fields, versions_dict,
            search_targets, root_category, root_block_id, **kwargs
        )

    def _create_courselike(  # lint-amnesty, pylint: disable=too-many-statements
        self, locator, user_id, master_branch, fields=None,
        versions_dict=None, search_targets=None, root_category='course',
        root_block_id=None, **kwargs
    ):
        """
        Internal code for creating a course or library
        """
        index = self.get_course_index(locator, ignore_case=True)
        if index is not None:
            raise DuplicateCourseError(locator, index)

        partitioned_fields = self.partition_fields_by_scope(root_category, fields)
        block_fields = partitioned_fields[Scope.settings]
        if Scope.children in partitioned_fields:
            block_fields.update(partitioned_fields[Scope.children])
        definition_fields = self._serialize_fields(root_category, partitioned_fields.get(Scope.content, {}))

        # build from inside out: definition, structure, index entry
        # if building a wholly new structure
        if versions_dict is None or master_branch not in versions_dict:
            # create new definition and structure
            definition_id = self.create_definition_from_data(locator, definition_fields, root_category, user_id).definition_id  # lint-amnesty, pylint: disable=line-too-long

            draft_structure = self._new_structure(
                user_id,
                BlockKey(
                    root_category,
                    root_block_id or SplitMongoModuleStore.DEFAULT_ROOT_COURSE_BLOCK_ID,
                ),
                block_fields,
                definition_id
            )
            new_id = draft_structure['_id']

            if versions_dict is None:
                versions_dict = {master_branch: new_id}
            else:
                versions_dict[master_branch] = new_id

        elif block_fields or definition_fields:  # pointing to existing course w/ some overrides
            # just get the draft_version structure
            draft_version = CourseLocator(version_guid=versions_dict[master_branch])
            draft_structure = self._lookup_course(draft_version).structure
            draft_structure = self.version_structure(locator, draft_structure, user_id)
            new_id = draft_structure['_id']
            root_block = draft_structure['blocks'][draft_structure['root']]
            if block_fields is not None:
                root_block.fields.update(self._serialize_fields(root_category, block_fields))
            if definition_fields is not None:
                old_def = self.get_definition(locator, root_block.definition)
                new_fields = old_def['fields']
                new_fields.update(definition_fields)
                definition_id = self._update_definition_from_data(locator, old_def, new_fields, user_id).definition_id
                root_block.definition = definition_id
                root_block.edit_info.edited_on = datetime.datetime.now(ZoneInfo("UTC"))
                root_block.edit_info.edited_by = user_id
                root_block.edit_info.previous_version = root_block.edit_info.update_version
                root_block.edit_info.update_version = new_id

            versions_dict[master_branch] = new_id
        else:  # Pointing to an existing course structure
            new_id = versions_dict[master_branch]
            draft_version = CourseLocator(version_guid=new_id)
            draft_structure = self._lookup_course(draft_version).structure

        locator = locator.replace(version_guid=new_id)
        with self.bulk_operations(locator):
            self.update_structure(locator, draft_structure)
            index_entry = {
                '_id': ObjectId(),
                'org': locator.org,
                'course': get_library_or_course_attribute(locator),
                'run': locator.run,
                'edited_by': user_id,
                'edited_on': datetime.datetime.now(ZoneInfo("UTC")),
                'versions': versions_dict,
                'schema_version': self.SCHEMA_VERSION,
                'search_targets': search_targets or {},
            }
            if fields is not None:
                self._update_search_targets(index_entry, fields)
            self.insert_course_index(locator, index_entry)

            # expensive hack to persist default field values set in __init__ method (e.g., wiki_slug)
            if isinstance(locator, LibraryLocator):
                course = self.get_library(locator, **kwargs)
            else:
                course = self.get_course(locator, **kwargs)
            return self.update_item(course, user_id, **kwargs)

    def create_library(self, org, library, user_id, fields, **kwargs):
        """
        Create a new library. Arguments are similar to create_course().
        """
        kwargs["fields"] = fields
        kwargs["master_branch"] = kwargs.get("master_branch", ModuleStoreEnum.BranchName.library)
        kwargs["root_category"] = kwargs.get("root_category", "library")
        kwargs["root_block_id"] = kwargs.get("root_block_id", "library")
        locator = LibraryLocator(org=org, library=library, branch=kwargs["master_branch"])
        return self._create_courselike(locator, user_id, **kwargs)

    def update_item(self, block, user_id, allow_not_found=False, force=False, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Save the block's fields. it doesn't descend the course dag to save the children.
        Return the new block (updated location).

        raises ItemNotFoundError if the location does not exist.

        Creates a new course version. If the block's location has a org and course and run, it moves the course head  # lint-amnesty, pylint: disable=line-too-long
        pointer. If the version_guid of the block points to a non-head version and there's been an intervening
        change to this item, it raises a VersionConflictError unless force is True. In the force case, it forks
        the course but leaves the head pointer where it is (this change will not be in the course head).

        The implementation tries to detect which, if any changes, actually need to be saved and thus won't version
        the definition, structure, nor course if they didn't change.
        """

        partitioned_fields = self.partition_xblock_fields_by_scope(block)
        definition_locator = getattr(block, "definition_locator", None)
        if definition_locator is None and not allow_not_found:
            raise AttributeError("block is missing expected definition_locator from caching descriptor system")
        return self._update_item_from_fields(
            user_id, block.location.course_key, BlockKey.from_usage_key(block.location),
            partitioned_fields, definition_locator, allow_not_found, force, **kwargs
        ) or block

    def _update_item_from_fields(self, user_id, course_key, block_key, partitioned_fields,    # pylint: disable=too-many-statements
                                 definition_locator, allow_not_found, force, asides=None, **kwargs):
        """
        Broke out guts of update_item for short-circuited internal use only
        """
        with self.bulk_operations(course_key):
            if allow_not_found and isinstance(block_key.id, (LocalId, type(None))):
                fields = {}
                for subfields in partitioned_fields.values():
                    fields.update(subfields)
                return self.create_item(
                    user_id, course_key, block_key.type, fields=fields, asides=asides, force=force
                )

            original_structure = self._lookup_course(course_key).structure
            index_entry = self._get_index_if_valid(course_key, force)

            original_entry = self._get_block_from_structure(original_structure, block_key)
            if original_entry is None:
                if allow_not_found:
                    fields = {}
                    for subfields in partitioned_fields.values():
                        fields.update(subfields)
                    return self.create_item(user_id, course_key, block_key.type, block_id=block_key.id, fields=fields,
                                            asides=asides, force=force)
                else:
                    raise ItemNotFoundError(course_key.make_usage_key(block_key.type, block_key.id))

            is_updated = False
            definition_fields = partitioned_fields[Scope.content]
            if definition_locator is None:
                definition_locator = DefinitionLocator(original_entry.block_type, original_entry.definition)
            if definition_fields:
                definition_locator, is_updated = self.update_definition_from_data(
                    course_key, definition_locator, definition_fields, user_id
                )

            # check metadata
            settings = partitioned_fields[Scope.settings]
            settings = self._serialize_fields(block_key.type, settings)
            if not is_updated:
                is_updated = self._compare_settings(settings, original_entry.fields)

            # check children
            if partitioned_fields.get(Scope.children, {}):  # purposely not 'is not None'
                serialized_children = [BlockKey.from_usage_key(child) for child in partitioned_fields[Scope.children]['children']]  # lint-amnesty, pylint: disable=line-too-long
                is_updated = is_updated or original_entry.fields.get('children', []) != serialized_children
                if is_updated:
                    settings['children'] = serialized_children

            asides_data_to_update = None
            if asides:
                asides_data_to_update, asides_updated = self._get_asides_to_update_from_structure(original_structure,
                                                                                                  block_key, asides)
            else:
                asides_updated = False

            # if updated, rev the structure
            if is_updated or asides_updated:
                new_structure = self.version_structure(course_key, original_structure, user_id)
                block_data = self._get_block_from_structure(new_structure, block_key)

                block_data.definition = definition_locator.definition_id
                block_data.fields = settings

                if asides_updated:
                    block_data.asides = asides_data_to_update

                new_id = new_structure['_id']

                # source_version records which revision a block was copied from. In this method, we're updating
                # the block, so it's no longer a direct copy, and we can remove the source_version reference.
                block_data.edit_info.source_version = None

                self.version_block(block_data, user_id, new_id)
                self.update_structure(course_key, new_structure)
                # update the index entry if appropriate
                if index_entry is not None:
                    self._update_search_targets(index_entry, definition_fields)
                    self._update_search_targets(index_entry, settings)
                    if isinstance(course_key, LibraryLocator):
                        course_key = LibraryLocator(
                            org=index_entry['org'],
                            library=index_entry['course'],
                            branch=course_key.branch,
                            version_guid=new_id
                        )
                    else:
                        course_key = CourseLocator(
                            org=index_entry['org'],
                            course=index_entry['course'],
                            run=index_entry['run'],
                            branch=course_key.branch,
                            version_guid=new_id
                        )
                    self._update_head(course_key, index_entry, course_key.branch, new_id)
                elif isinstance(course_key, LibraryLocator):
                    course_key = LibraryLocator(version_guid=new_id)
                else:
                    course_key = CourseLocator(version_guid=new_id)

                if isinstance(course_key, LibraryLocator):
                    self._flag_library_updated_event(course_key)

                # fetch and return the new item--fetching is unnecessary but a good qc step
                new_locator = course_key.make_usage_key(block_key.type, block_key.id)
                return self.get_item(new_locator, **kwargs)
            else:
                return None

    def create_xblock(
            self, runtime, course_key, block_type, block_id=None, fields=None,
            definition_id=None, parent_xblock=None, **kwargs
    ):
        """
        This method instantiates the correct subclass of XModuleDescriptor based
        on the contents of json_data. It does not persist it and can create one which
        has no usage id.

        parent_xblock is used to compute inherited metadata as well as to append the new xblock.

        json_data:
        - 'block_type': the xblock block_type
        - 'fields': a dict of locally set fields (not inherited) in json format not pythonic typed format!
        - 'definition': the object id of the existing definition
        """
        assert runtime is not None

        xblock_class = runtime.load_block_type(block_type)
        json_data = {
            'block_type': block_type,
            'fields': {},
        }
        if definition_id is not None:
            json_data['definition'] = definition_id
        if parent_xblock is None:
            # If no parent, then nothing to inherit.
            inherited_settings = {}
        else:
            inherited_settings = parent_xblock.xblock_kvs.inherited_settings.copy()
            if fields is not None:
                for field_name in inheritance.InheritanceMixin.fields:  # lint-amnesty, pylint: disable=not-an-iterable
                    if field_name in fields:
                        inherited_settings[field_name] = fields[field_name]

        new_block = runtime.xblock_from_json(
            xblock_class,
            course_key,
            BlockKey(block_type, block_id) if block_id else None,
            BlockData(**json_data),
            **kwargs
        )
        for field_name, value in (fields or {}).items():
            setattr(new_block, field_name, value)

        if parent_xblock is not None:
            parent_xblock.children.append(new_block.scope_ids.usage_id)
            # decache pending children field settings
            parent_xblock.save()
        return new_block

    def persist_xblock_dag(self, xblock, user_id, force=False):
        """
        create or update the xblock and all of its children. The xblock's location must specify a course.
        If it doesn't specify a usage_id, then it's presumed to be new and need creation. This function
        descends the children performing the same operation for any that are xblocks. Any children which
        are block_ids just update the children pointer.

        All updates go into the same course version (bulk updater).

        Updates the objects which came in w/ updated location and definition_location info.

        returns the post-persisted version of the incoming xblock. Note that its children will be ids not
        objects.

        :param xblock: the head of the dag
        :param user_id: who's doing the change
        """
        # find course_index entry if applicable and structures entry
        course_key = xblock.location.course_key
        with self.bulk_operations(course_key):
            index_entry = self._get_index_if_valid(course_key, force)
            structure = self._lookup_course(course_key).structure
            new_structure = self.version_structure(course_key, structure, user_id)
            new_id = new_structure['_id']
            is_updated = self._persist_subdag(course_key, xblock, user_id, new_structure['blocks'], new_id)

            if is_updated:
                self.update_structure(course_key, new_structure)

                # update the index entry if appropriate
                if index_entry is not None:
                    self._update_head(course_key, index_entry, xblock.location.branch, new_id)

                # fetch and return the new item--fetching is unnecessary but a good qc step
                return self.get_item(xblock.location.for_version(new_id))
            else:
                return xblock

    def _persist_subdag(self, course_key, xblock, user_id, structure_blocks, new_id):  # lint-amnesty, pylint: disable=missing-function-docstring
        # persist the definition if persisted != passed
        partitioned_fields = self.partition_xblock_fields_by_scope(xblock)
        new_def_data = self._serialize_fields(xblock.category, partitioned_fields[Scope.content])
        is_updated = False
        current_definition_locator = getattr(xblock, "definition_locator", xblock.scope_ids.def_id)
        if current_definition_locator is None or isinstance(current_definition_locator.definition_id, LocalId):
            xblock.definition_locator = self.create_definition_from_data(
                course_key, new_def_data, xblock.category, user_id
            )
            is_updated = True
        elif new_def_data:
            xblock.definition_locator, is_updated = self.update_definition_from_data(
                course_key, xblock.definition_locator, new_def_data, user_id
            )

        if isinstance(xblock.scope_ids.usage_id.block_id, LocalId):
            # generate an id
            is_new = True
            is_updated = True
            block_id = getattr(xblock.scope_ids.usage_id.block_id, 'block_id', None)
            if block_id is None:
                block_key = self._generate_block_key(structure_blocks, xblock.scope_ids.block_type)
            else:
                block_key = BlockKey(xblock.scope_ids.block_type, block_id)
            new_usage_id = xblock.scope_ids.usage_id.replace(block_id=block_key.id)
            xblock.scope_ids = xblock.scope_ids._replace(usage_id=new_usage_id)
        else:
            is_new = False
            block_key = BlockKey(xblock.scope_ids.block_type, xblock.scope_ids.usage_id.block_id)

        children = []
        if xblock.has_children:
            for child in xblock.children:
                if isinstance(child.block_id, LocalId):
                    child_block = xblock.runtime.get_block(child)
                    is_updated = self._persist_subdag(course_key, child_block, user_id, structure_blocks, new_id) or is_updated  # lint-amnesty, pylint: disable=line-too-long
                    children.append(BlockKey.from_usage_key(child_block.location))
                else:
                    children.append(BlockKey.from_usage_key(child))
            is_updated = is_updated or structure_blocks[block_key].fields['children'] != children

        block_fields = partitioned_fields[Scope.settings]
        block_fields = self._serialize_fields(xblock.category, block_fields)
        if not is_new and not is_updated:
            is_updated = self._compare_settings(block_fields, structure_blocks[block_key].fields)
        if children:
            block_fields['children'] = children

        if is_updated:
            if is_new:
                block_info = self._new_block(
                    user_id,
                    xblock.category,
                    block_fields,
                    xblock.definition_locator.definition_id,
                    new_id,
                    raw=True
                )
            else:
                block_info = structure_blocks[block_key]
                block_info.fields = block_fields
                block_info.definition = xblock.definition_locator.definition_id
                self.version_block(block_info, user_id, new_id)

            structure_blocks[block_key] = block_info

        return is_updated

    def _compare_settings(self, settings, original_fields):
        """
        Return True if the settings are not == to the original fields
        :param settings:
        :param original_fields:
        """
        original_keys = list(original_fields.keys())
        if 'children' in original_keys:
            original_keys.remove('children')
        if len(settings) != len(original_keys):
            return True
        else:
            new_keys = list(settings.keys())
            for key in original_keys:
                if key not in new_keys or original_fields[key] != settings[key]:
                    return True

    def copy(self, user_id, source_course, destination_course, subtree_list=None, blacklist=None):
        """
        Copies each xblock in subtree_list and those blocks descendants excluding blacklist
        from source_course to destination_course.

        To delete a block in the destination_course, copy its parent and blacklist the other
        sibs to keep them from being copies. You can also just call delete_item on the destination.

        Ensures that each subtree occurs in the same place in destination as it does in source. If any
        of the source's subtree parents are missing from destination, it raises ItemNotFound([parent_ids]).
        To determine the same relative order vis-a-vis published siblings,
        publishing may involve changing the order of previously published siblings. For example,
        if publishing `[c, d]` and source parent has children `[a, b, c, d, e]` and destination parent
        currently has children `[e, b]`, there's no obviously correct resulting order; thus, publish will
        reorder destination to `[b, c, d, e]` to make it conform with the source.

        :param source_course: a CourseLocator (can be a version or course w/ branch)

        :param destination_course: a CourseLocator which must be an existing course but branch doesn't have
        to exist yet. (The course must exist b/c Locator doesn't have everything necessary to create it).
        Note, if the branch doesn't exist, then the source_course structure's root must be in subtree_list;
        otherwise, the publish will violate the parents must exist rule.

        :param subtree_list: a list of usage keys whose subtrees to publish.

        :param blacklist: a list of usage keys to not change in the destination: i.e., don't add
        if not there, don't update if there.

        Raises:
            ItemNotFoundError: if it cannot find the course. if the request is to publish a
                subtree but the ancestors up to and including the course root are not published.
        """
        # get the destination's index, and source and destination structures.
        with self.bulk_operations(source_course):
            source_structure = self._lookup_course(source_course).structure

        with self.bulk_operations(destination_course):
            index_entry = self.get_course_index(destination_course)
            if index_entry is None:
                # brand new course
                raise ItemNotFoundError(destination_course)
            if destination_course.branch not in index_entry['versions']:
                # must be copying the dag root if there's no current dag
                root_block_key = source_structure['root']
                if not any(root_block_key == BlockKey.from_usage_key(subtree) for subtree in subtree_list):
                    raise ItemNotFoundError(f'Must publish course root {root_block_key}')
                root_source = source_structure['blocks'][root_block_key]
                # create branch
                destination_structure = self._new_structure(
                    user_id, root_block_key,
                    # leave off the fields b/c the children must be filtered
                    definition_id=root_source.definition,
                )
            else:
                destination_structure = self._lookup_course(destination_course).structure
                destination_structure = self.version_structure(destination_course, destination_structure, user_id)

            if blacklist != EXCLUDE_ALL:
                blacklist = [BlockKey.from_usage_key(shunned) for shunned in blacklist or []]
            # iterate over subtree list filtering out blacklist.
            orphans = set()
            destination_blocks = destination_structure['blocks']
            for subtree_root in subtree_list:
                if BlockKey.from_usage_key(subtree_root) != source_structure['root']:
                    # find the parents and put root in the right sequence
                    parents = self._get_parents_from_structure(BlockKey.from_usage_key(subtree_root), source_structure)
                    parent_found = False
                    for parent in parents:
                        # If a parent isn't found in the destination_blocks, it's possible it was renamed
                        # in the course export. Continue and only throw an exception if *no* parents are found.
                        if parent in destination_blocks:
                            parent_found = True
                            orphans.update(
                                self._sync_children(
                                    source_structure['blocks'][parent],
                                    destination_blocks[parent],
                                    BlockKey.from_usage_key(subtree_root)
                                )
                            )
                    if len(parents) and not parent_found:  # lint-amnesty, pylint: disable=len-as-condition
                        raise ItemNotFoundError(parents)
                # update/create the subtree and its children in destination (skipping blacklist)
                orphans.update(
                    self._copy_subdag(
                        user_id, destination_structure['_id'],
                        BlockKey.from_usage_key(subtree_root),
                        source_structure['blocks'],
                        destination_blocks,
                        blacklist
                    )
                )
            # remove any remaining orphans
            for orphan in orphans:
                # orphans will include moved as well as deleted xblocks. Only delete the deleted ones.
                self._delete_if_true_orphan(orphan, destination_structure)

            # update the db
            self.update_structure(destination_course, destination_structure)
            self._update_head(destination_course, index_entry, destination_course.branch, destination_structure['_id'])

    def copy_from_template(self, source_keys, dest_usage, user_id, head_validation=True):
        """
        Flexible mechanism for inheriting content from an external course/library/etc.

        Will copy all of the XBlocks whose keys are passed as `source_course` so that they become
        children of the XBlock whose key is `dest_usage`. Any previously existing children of
        `dest_usage` that haven't been replaced/updated by this copy_from_template operation will
        be deleted.

        Unlike `copy()`, this does not care whether the resulting blocks are positioned similarly
        in their new course/library. However, the resulting blocks will be in the same relative
        order as `source_keys`.

        If any of the blocks specified already exist as children of the destination block, they
        will be updated rather than duplicated or replaced. If they have Scope.settings field values
        overriding inherited default values, those overrides will be preserved.

        IMPORTANT: This method does not preserve block_id - in other words, every block that is
        copied will be assigned a new block_id. This is because we assume that the same source block
        may be copied into one course in multiple places. However, it *is* guaranteed that every
        time this method is called for the same source block and dest_usage, the same resulting
        block id will be generated.

        Note also that this function does not override any of the attributes on the destination
        block-- it only replaces the destination block's children.

        :param source_keys: a list of BlockUsageLocators. Order is preserved.

        :param dest_usage: The BlockUsageLocator that will become the parent of an inherited copy
        of all the xblocks passed in `source_keys`.

        :param user_id: The user who will get credit for making this change.
        """
        # Preload the block structures for all source courses/libraries/etc.
        # so that we can access descendant information quickly
        source_structures = {}
        for key in source_keys:
            course_key = key.course_key
            if course_key.branch is None:
                raise ItemNotFoundError("branch is required for all source keys when using copy_from_template")
            if course_key not in source_structures:
                with self.bulk_operations(course_key):
                    source_structures[course_key] = self._lookup_course(
                        course_key, head_validation=head_validation
                    ).structure

        destination_course = dest_usage.course_key
        with self.bulk_operations(destination_course):
            index_entry = self.get_course_index(destination_course)
            if index_entry is None:
                raise ItemNotFoundError(destination_course)
            dest_structure = self._lookup_course(destination_course).structure
            old_dest_structure_version = dest_structure['_id']
            dest_structure = self.version_structure(destination_course, dest_structure, user_id)

            # Set of all descendent block IDs of dest_usage that are to be replaced:
            block_key = BlockKey(dest_usage.block_type, dest_usage.block_id)
            orig_descendants = set(self.descendants(dest_structure['blocks'], block_key, depth=None, descendent_map={}))
            # The descendants() method used above adds the block itself, which we don't consider a descendant.
            orig_descendants.remove(block_key)
            new_descendants = self._copy_from_template(
                source_structures, source_keys, dest_structure, block_key, user_id, head_validation
            )

            # Update the edit info:
            dest_info = dest_structure['blocks'][block_key]

            # Update the edit_info:
            dest_info.edit_info.previous_version = dest_info.edit_info.update_version
            dest_info.edit_info.update_version = old_dest_structure_version
            dest_info.edit_info.edited_by = user_id
            dest_info.edit_info.edited_on = datetime.datetime.now(ZoneInfo("UTC"))

            orphans = orig_descendants - new_descendants
            for orphan in orphans:
                del dest_structure['blocks'][orphan]

            self.update_structure(destination_course, dest_structure)
            self._update_head(destination_course, index_entry, destination_course.branch, dest_structure['_id'])
        # Return usage locators for all the new children:
        return [
            destination_course.make_usage_key(*k)
            for k in dest_structure['blocks'][block_key].fields['children']
        ]

    def _copy_from_template(
            self, source_structures, source_keys, dest_structure, new_parent_block_key, user_id, head_validation
    ):
        """
        Internal recursive implementation of copy_from_template()

        Returns the new set of BlockKeys that are the new descendants of the block with key 'block_key'
        """
        new_blocks = set()

        new_children = []  # ordered list of the new children of new_parent_block_key

        for usage_key in source_keys:
            src_course_key = usage_key.course_key
            block_key = BlockKey(usage_key.block_type, usage_key.block_id)
            source_structure = source_structures[src_course_key]

            if block_key not in source_structure['blocks']:
                raise ItemNotFoundError(usage_key)
            source_block_info = source_structure['blocks'][block_key]

            new_block_key = derive_key(usage_key, new_parent_block_key)

            # Now clone block_key to new_block_key:
            new_block_info = copy.deepcopy(source_block_info)
            # Note that new_block_info now points to the same definition ID entry as source_block_info did
            existing_block_info = dest_structure['blocks'].get(new_block_key, BlockData())
            # Inherit the Scope.settings values from 'fields' to 'defaults'
            new_block_info.defaults = new_block_info.fields

            # <workaround>
            # CAPA blocks store their 'markdown' value (an alternate representation of their content)
            # in Scope.settings rather than Scope.content :-/
            # markdown is a field that really should not be overridable - it fundamentally changes the content.
            # capa blocks also use a custom editor that always saves their markdown field to the metadata,
            # even if it hasn't changed, which breaks our override system.
            # So until capa blocks are fixed, we special-case them and remove their markdown fields,
            # forcing the inherited version to use XML only.
            if usage_key.block_type == 'problem' and 'markdown' in new_block_info.defaults:
                del new_block_info.defaults['markdown']
            # </workaround>

            # Preserve any existing overrides
            new_block_info.fields = existing_block_info.fields

            if 'children' in new_block_info.defaults:
                del new_block_info.defaults['children']  # Will be set later

            new_block_info.edit_info = existing_block_info.edit_info
            new_block_info.edit_info.previous_version = new_block_info.edit_info.update_version
            new_block_info.edit_info.update_version = dest_structure['_id']
            # Note we do not set 'source_version' - it's only used for copying identical blocks
            # from draft to published as part of publishing workflow.
            # Setting it to the source_block_info structure version here breaks split_draft's has_changes() method.
            new_block_info.edit_info.edited_by = user_id
            new_block_info.edit_info.edited_on = datetime.datetime.now(ZoneInfo("UTC"))
            new_block_info.edit_info.original_usage = str(usage_key.replace(branch=None, version_guid=None))
            new_block_info.edit_info.original_usage_version = source_block_info.edit_info.update_version
            dest_structure['blocks'][new_block_key] = new_block_info

            children = source_block_info.fields.get('children')
            if children:
                children = [src_course_key.make_usage_key(child.type, child.id) for child in children]
                new_blocks |= self._copy_from_template(
                    source_structures, children, dest_structure, new_block_key, user_id, head_validation
                )

            new_blocks.add(new_block_key)
            # And add new_block_key to the list of new_parent_block_key's new children:
            new_children.append(new_block_key)

        # Update the children of new_parent_block_key
        dest_structure['blocks'][new_parent_block_key].fields['children'] = new_children

        return new_blocks

    def delete_item(self, usage_locator, user_id, force=False):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Delete the block or tree rooted at block (if delete_children) and any references w/in the course to the block
        from a new version of the course structure.

        returns CourseLocator for new version

        raises ItemNotFoundError if the location does not exist.
        raises ValueError if usage_locator points to the structure root

        Creates a new course version. If the block's location has a org, a course, and a run, it moves the course head  # lint-amnesty, pylint: disable=line-too-long
        pointer. If the version_guid of the block points to a non-head version and there's been an intervening
        change to this item, it raises a VersionConflictError unless force is True. In the force case, it forks
        the course but leaves the head pointer where it is (this change will not be in the course head).
        """
        if not isinstance(usage_locator, BlockUsageLocator) or usage_locator.deprecated:
            # The supplied UsageKey is of the wrong type, so it can't possibly be stored in this modulestore.
            raise ItemNotFoundError(usage_locator)

        with self.bulk_operations(usage_locator.course_key):
            original_structure = self._lookup_course(usage_locator.course_key).structure
            block_key = BlockKey.from_usage_key(usage_locator)
            if original_structure['root'] == block_key:
                raise ValueError("Cannot delete the root of a course")
            if block_key not in original_structure['blocks']:
                # When user move a full sub-section to another section
                # These changes are in draft-branch only and when user delete this moved
                # section, we need to delete it from draft-branch
                draft_course_key = usage_locator.course_key.for_branch(ModuleStoreEnum.BranchName.draft)
                try:
                    draft_structure = self._lookup_course(draft_course_key).structure
                    if block_key in draft_structure['blocks']:
                        # Block exists in draft, use draft structure instead
                        original_structure = draft_structure
                        log.info("Block %s found in draft branch, proceeding with deletion from draft", block_key)
                    else:
                        raise ValueError(
                            (
                                "Cannot delete block_key {} from course {}, "
                                "because that block does not exist in either branch."
                            ).format(
                                block_key,
                                usage_locator,
                            )
                        )
                except ItemNotFoundError as exc:
                    raise ValueError(
                        (
                            "Cannot delete block_key {} from course {}, "
                            "because that block does not exist."
                        ).format(
                            block_key,
                            usage_locator,
                        )
                    ) from exc
            index_entry = self._get_index_if_valid(usage_locator.course_key, force)
            new_structure = self.version_structure(usage_locator.course_key, original_structure, user_id)
            new_blocks = new_structure['blocks']
            new_id = new_structure['_id']
            parent_block_keys = self._get_parents_from_structure(block_key, original_structure)
            for parent_block_key in parent_block_keys:
                parent_block = new_blocks[parent_block_key]
                parent_block.fields['children'].remove(block_key)
                parent_block.edit_info.edited_on = datetime.datetime.now(ZoneInfo("UTC"))
                parent_block.edit_info.edited_by = user_id
                parent_block.edit_info.previous_version = parent_block.edit_info.update_version
                parent_block.edit_info.update_version = new_id
                # remove the source_version reference
                parent_block.edit_info.source_version = None
                self.decache_block(usage_locator.course_key, new_id, parent_block_key)

            self._remove_subtree(BlockKey.from_usage_key(usage_locator), new_blocks)

            # update index if appropriate and structures
            self.update_structure(usage_locator.course_key, new_structure)

            if index_entry is not None:
                # update the index entry if appropriate
                self._update_head(usage_locator.course_key, index_entry, usage_locator.branch, new_id)
                result = usage_locator.course_key.for_version(new_id)
            else:
                result = CourseLocator(version_guid=new_id)

            if isinstance(usage_locator.course_key, LibraryLocator):
                self._flag_library_updated_event(usage_locator.course_key)

            self._emit_item_deleted_signal(usage_locator, user_id)

            return result

    def _remove_subtree(self, root_block_key, blocks):
        """
        Remove the subtree rooted at root_block_key
        We do this breadth-first to make sure that we don't remove
        any children that may have parents that we don't want to delete.
        """
        # create mapping from each child's key to its parents' keys
        child_parent_map = defaultdict(set)
        for block_key, block_data in blocks.items():
            for child in block_data.fields.get('children', []):
                child_parent_map[BlockKey(*child)].add(block_key)

        to_delete = {root_block_key}
        tier = {root_block_key}
        while tier:
            next_tier = set()
            for block_key in tier:
                for child in blocks[block_key].fields.get('children', []):
                    child_block_key = BlockKey(*child)
                    parents = child_parent_map[child_block_key]
                    # Make sure we want to delete all of the child's parents
                    # before slating it for deletion
                    if parents.issubset(to_delete):
                        next_tier.add(child_block_key)
            tier = next_tier
            to_delete.update(tier)

        for block_key in to_delete:
            del blocks[block_key]

    def delete_course(self, course_key, user_id):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Remove the given course from the course index.

        Only removes the course from the index. The data remains. You can use create_course
        with a versions hash to restore the course; however, the edited_on and
        edited_by won't reflect the originals, of course.
        """
        # this is the only real delete in the system. should it do something else?
        log.info("deleting course from split-mongo: %s", course_key)
        self.delete_course_index(course_key)

        # We do NOT call the super class here since we need to keep the assets
        # in case the course is later restored.
        # super(SplitMongoModuleStore, self).delete_course(course_key, user_id)

        self._emit_course_deleted_signal(course_key)

    def inherit_settings(
        self, block_map, block_key, inherited_settings_map, inheriting_settings=None, inherited_from=None
    ):
        """
        Updates block_data with any inheritable setting set by an ancestor and recurses to children.
        """
        if block_key not in block_map:
            return
        block_data = block_map[block_key]

        if inheriting_settings is None:
            inheriting_settings = {}

        if inherited_from is None:
            inherited_from = []

        # the currently passed down values take precedence over any previously cached ones
        # NOTE: this should show the values which all fields would have if inherited: i.e.,
        # not set to the locally defined value but to value set by nearest ancestor who sets it
        inherited_settings_map.setdefault(block_key, {}).update(inheriting_settings)

        # update the inheriting w/ what should pass to children
        inheriting_settings = inherited_settings_map[block_key].copy()
        block_fields = block_data.fields
        for field_name in inheritance.InheritanceMixin.fields:  # lint-amnesty, pylint: disable=not-an-iterable
            if field_name in block_fields:
                inheriting_settings[field_name] = block_fields[field_name]

        for child in block_fields.get('children', []):
            try:
                if child in inherited_from:
                    raise Exception(f'Infinite loop detected when inheriting to {child}, having already inherited from {inherited_from}')  # lint-amnesty, pylint: disable=line-too-long
                self.inherit_settings(
                    block_map,
                    BlockKey(*child),
                    inherited_settings_map,
                    inheriting_settings,
                    inherited_from + [child]
                )
            except KeyError:
                # here's where we need logic for looking up in other structures when we allow cross pointers
                # but it's also getting this during course creation if creating top down w/ children set or
                # migration where the old mongo published had pointers to privates
                pass

    def descendants(self, block_map, block_id, depth, descendent_map):
        """
        adds block and its descendants out to depth to descendent_map
        Depth specifies the number of levels of descendants to return
        (0 => this usage only, 1 => this usage and its children, etc...)
        A depth of None returns all descendants
        """
        if block_id not in block_map:
            return descendent_map

        if block_id not in descendent_map:
            descendent_map[block_id] = block_map[block_id]

        if depth is None or depth > 0:
            depth = depth - 1 if depth is not None else None
            for child in descendent_map[block_id].fields.get('children', []):
                descendent_map = self.descendants(block_map, child, depth, descendent_map)

        return descendent_map

    def get_modulestore_type(self, course_key=None):  # lint-amnesty, pylint: disable=arguments-differ, unused-argument
        """
        Returns an enumeration-like type reflecting the type of this modulestore, per ModuleStoreEnum.Type.

        Args:
            course_key: just for signature compatibility
        """
        return ModuleStoreEnum.Type.split

    def _find_course_assets(self, course_key):
        """
        Split specific lookup
        """
        try:
            course_assets = self._lookup_course(course_key).structure.get('assets', {})
        except (InsufficientSpecificationError, VersionConflictError) as err:  # lint-amnesty, pylint: disable=unused-variable
            log.warning('Error finding assets for org "%s" course "%s" on asset '
                        'request. Either version of course_key is None or invalid.',
                        course_key.org, course_key.course)
            return {}

        return course_assets

    def _update_course_assets(self, user_id, asset_key, update_function):
        """
        A wrapper for functions wanting to manipulate assets. Gets and versions the structure,
        passes the mutable array for either 'assets' or 'thumbnails' as well as the idx to the function for it to
        update, then persists the changed data back into the course.

        The update function can raise an exception if it doesn't want to actually do the commit. The
        surrounding method probably should catch that exception.
        """
        with self.bulk_operations(asset_key.course_key):
            original_structure = self._lookup_course(asset_key.course_key).structure
            index_entry = self._get_index_if_valid(asset_key.course_key)
            new_structure = self.version_structure(asset_key.course_key, original_structure, user_id)
            course_assets = new_structure.setdefault('assets', {})

            asset_type = asset_key.asset_type
            all_assets = SortedAssetList(iterable=course_assets.setdefault(asset_type, []))
            asset_idx = all_assets.find(asset_key)

            all_assets_updated = update_function(all_assets, asset_idx)
            new_structure['assets'][asset_type] = list(all_assets_updated)

            # update index if appropriate and structures
            self.update_structure(asset_key.course_key, new_structure)

            if index_entry is not None:
                # update the index entry if appropriate
                self._update_head(asset_key.course_key, index_entry, asset_key.branch, new_structure['_id'])

    def save_asset_metadata_list(self, asset_metadata_list, user_id, import_only=False):
        """
        Saves a list of AssetMetadata to the modulestore. The list can be composed of multiple
        asset types. This method is optimized for multiple inserts at once - it only re-saves the structure
        at the end of all saves/updates.
        """
        # Determine course key to use in bulk operation. Use the first asset assuming that
        # all assets will be for the same course.
        asset_key = asset_metadata_list[0].asset_id
        course_key = asset_key.course_key

        with self.bulk_operations(course_key):
            original_structure = self._lookup_course(course_key).structure
            index_entry = self._get_index_if_valid(course_key)
            new_structure = self.version_structure(course_key, original_structure, user_id)
            course_assets = new_structure.setdefault('assets', {})

            assets_by_type = self._save_assets_by_type(
                course_key, asset_metadata_list, course_assets, user_id, import_only
            )

            for asset_type, assets in assets_by_type.items():
                new_structure['assets'][asset_type] = list(assets)

            # update index if appropriate and structures
            self.update_structure(course_key, new_structure)

            if index_entry is not None:
                # update the index entry if appropriate
                self._update_head(course_key, index_entry, asset_key.branch, new_structure['_id'])

    def save_asset_metadata(self, asset_metadata, user_id, import_only=False):
        """
        Saves or updates a single asset. Simply makes it a list and calls the list save above.
        """
        return self.save_asset_metadata_list([asset_metadata, ], user_id, import_only)

    def set_asset_metadata_attrs(self, asset_key, attr_dict, user_id):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Add/set the given dict of attrs on the asset at the given location. Value can be any type which pymongo accepts.

        Arguments:
            asset_key (AssetKey): asset identifier
            attr_dict (dict): attribute: value pairs to set

        Raises:
            ItemNotFoundError if no such item exists
            AttributeError is attr is one of the build in attrs.
        """
        def _internal_method(all_assets, asset_idx):
            """
            Update the found item
            """
            if asset_idx is None:
                raise ItemNotFoundError(asset_key)

            # Form an AssetMetadata.
            mdata = AssetMetadata(asset_key, asset_key.path)
            mdata.from_storable(all_assets[asset_idx])
            mdata.update(attr_dict)

            # Generate a Mongo doc from the metadata and update the course asset info.
            all_assets.insert_or_update(mdata)
            return all_assets

        self._update_course_assets(user_id, asset_key, _internal_method)

    def delete_asset_metadata(self, asset_key, user_id):
        """
        Internal; deletes a single asset's metadata.

        Arguments:
            asset_key (AssetKey): key containing original asset filename

        Returns:
            Number of asset metadata entries deleted (0 or 1)
        """
        def _internal_method(all_asset_info, asset_idx):
            """
            Remove the item if it was found
            """
            if asset_idx is None:
                raise ItemNotFoundError(asset_key)

            all_asset_info.pop(asset_idx)
            return all_asset_info

        try:
            self._update_course_assets(user_id, asset_key, _internal_method)
            return 1
        except ItemNotFoundError:
            return 0

    def copy_all_asset_metadata(self, source_course_key, dest_course_key, user_id):
        """
        Copy all the course assets from source_course_key to dest_course_key.

        Arguments:
            source_course_key (CourseKey): identifier of course to copy from
            dest_course_key (CourseKey): identifier of course to copy to
        """
        source_structure = self._lookup_course(source_course_key).structure
        with self.bulk_operations(dest_course_key):
            original_structure = self._lookup_course(dest_course_key).structure
            index_entry = self._get_index_if_valid(dest_course_key)
            new_structure = self.version_structure(dest_course_key, original_structure, user_id)

            new_structure['assets'] = source_structure.get('assets', {})
            new_structure['thumbnails'] = source_structure.get('thumbnails', [])

            # update index if appropriate and structures
            self.update_structure(dest_course_key, new_structure)

            if index_entry is not None:
                # update the index entry if appropriate
                self._update_head(dest_course_key, index_entry, dest_course_key.branch, new_structure['_id'])

    def fix_not_found(self, course_locator, user_id):
        """
        Only intended for rather low level methods to use. Goes through the children attrs of
        each block removing any whose block_id is not a member of the course.

        :param course_locator: the course to clean
        """
        original_structure = self._lookup_course(course_locator).structure
        index_entry = self._get_index_if_valid(course_locator)
        new_structure = self.version_structure(course_locator, original_structure, user_id)
        for block in new_structure['blocks'].values():
            if 'children' in block.fields:
                block.fields['children'] = [
                    block_id for block_id in block.fields['children']
                    if block_id in new_structure['blocks']
                ]
        self.update_structure(course_locator, new_structure)
        if index_entry is not None:
            # update the index entry if appropriate
            self._update_head(course_locator, index_entry, course_locator.branch, new_structure['_id'])

    def convert_references_to_keys(self, course_key, xblock_class, jsonfields, blocks):  # lint-amnesty, pylint: disable=unused-argument
        """
        Convert the given serialized fields to the deserialized values by finding all references
        and converting them.
        :param jsonfields: the serialized copy of the xblock's fields
        """
        def robust_usage_key(block_key):
            """
            create a course_key relative usage key for the block_key. If the block_key is in blocks,
            use its correct category; otherwise, use 'unknown'.
            The purpose for this is that some operations add pointers as they build up the
            structure without worrying about order of creation. Because the category of the
            usage_key is for the most part inert, it's better to hack a value than to work
            out a dependency graph algorithm for those functions which may prereference blocks.
            """
            # if this was taken from cache, then its fields are already converted
            if isinstance(block_key, BlockUsageLocator):
                return block_key.map_into_course(course_key)
            elif not isinstance(block_key, BlockKey):
                block_key = BlockKey(*block_key)

            try:
                return course_key.make_usage_key(
                    block_key.type, block_key.id
                )
            except KeyError:
                return course_key.make_usage_key('unknown', block_key.id)

        xblock_class = self.mixologist.mix(xblock_class)
        # Make a shallow copy, so that we aren't manipulating a cached field dictionary
        output_fields = dict(jsonfields)
        for field_name, value in output_fields.items():
            if value:
                try:
                    field = xblock_class.fields.get(field_name)
                except AttributeError:
                    continue
                if isinstance(field, Reference):
                    output_fields[field_name] = robust_usage_key(value)
                elif isinstance(field, ReferenceList):
                    output_fields[field_name] = [robust_usage_key(ele) for ele in value]
                elif isinstance(field, ReferenceValueDict):
                    for key, subvalue in value.items():
                        value[key] = robust_usage_key(subvalue)
        return output_fields

    def _get_index_if_valid(self, course_key, force=False):
        """
        If the course_key identifies a course and points to its draft (or plausibly its draft),
        then return the index entry.

        raises VersionConflictError if not the right version

        :param course_key: a CourseLocator
        :param force: if false, raises VersionConflictError if the current head of the course != the one identified
        by course_key
        """
        if (course_key.org is None or
                get_library_or_course_attribute(course_key) is None or
                course_key.run is None or course_key.branch is None):
            return None
        else:
            index_entry = self.get_course_index(course_key)
            is_head = (
                course_key.version_guid is None or
                index_entry['versions'][course_key.branch] == course_key.version_guid
            )
            if is_head or force:
                return index_entry
            else:
                raise VersionConflictError(
                    course_key,
                    index_entry['versions'][course_key.branch]
                )

    def _find_local_root(self, element_to_find, possibility, tree):  # lint-amnesty, pylint: disable=missing-function-docstring
        if possibility not in tree:
            return False
        if element_to_find in tree[possibility]:
            return True
        for subtree in tree[possibility]:
            if self._find_local_root(element_to_find, subtree, tree):
                return True
        return False

    def _update_search_targets(self, index_entry, fields):
        """
        Update the index entry if any of the given fields are in SEARCH_TARGET_DICT. (doesn't save
        the changes, just changes them in the entry dict)
        :param index_entry:
        :param fields: a dictionary of fields and values usually only those explicitly set and already
            ready for persisting (e.g., references converted to block_ids)
        """
        for field_name, field_value in fields.items():
            if field_name in self.SEARCH_TARGET_DICT:
                index_entry.setdefault('search_targets', {})[field_name] = field_value

    def _update_head(self, course_key, index_entry, branch, new_id):
        """
        Update the active index for the given course's branch to point to new_id

        :param index_entry:
        :param course_locator:
        :param new_id:
        """
        if not isinstance(new_id, ObjectId):
            raise TypeError(f'new_id must be an ObjectId, but is {new_id!r}')
        index_entry['versions'][branch] = new_id
        self.update_course_index(course_key, index_entry)

    def partition_xblock_fields_by_scope(self, xblock):
        """
        Return a dictionary of scopes mapped to this xblock's explicitly set fields w/o any conversions
        """
        # explicitly_set_fields_by_scope converts to json; so, avoiding it
        # the existing partition_fields_by_scope works on a dict not an xblock
        result = defaultdict(dict)
        for field in xblock.fields.values():
            if field.is_set_on(xblock):
                result[field.scope][field.name] = field.read_from(xblock)
        return result

    def _serialize_fields(self, category, fields):
        """
        Convert any references to their serialized form. Handle some references already being unicoded
        because the client passed them that way and nothing above this layer did the necessary deserialization.

        Remove any fields which split or its kvs computes or adds but does not want persisted.

        :param fields: a dict of fields
        """
        assert isinstance(fields, dict)
        xblock_class = XBlock.load_class(category, self.default_class)
        xblock_class = self.mixologist.mix(xblock_class)

        def reference_block_id(reference):
            """
            Handle client possibly setting field to strings rather than keys to get the block_id
            """
            # perhaps replace by fixing the views or Field Reference*.from_json to return a Key
            if isinstance(reference, str):
                reference = BlockUsageLocator.from_string(reference)
            elif isinstance(reference, BlockKey):
                return reference
            return BlockKey.from_usage_key(reference)

        for field_name, value in fields.items():
            if value is not None:
                if isinstance(xblock_class.fields[field_name], Reference):
                    fields[field_name] = reference_block_id(value)
                elif isinstance(xblock_class.fields[field_name], ReferenceList):
                    fields[field_name] = [
                        reference_block_id(ele) for ele in value
                    ]
                elif isinstance(xblock_class.fields[field_name], ReferenceValueDict):
                    for key, subvalue in value.items():
                        value[key] = reference_block_id(subvalue)
                # should this recurse down dicts and lists just in case they contain datetime?
                elif not isinstance(value, datetime.datetime):  # don't convert datetimes!
                    fields[field_name] = xblock_class.fields[field_name].to_json(value)
        return fields

    def _new_structure(self, user_id, root_block_key, block_fields=None, definition_id=None):
        """
        Internal function: create a structure element with no previous version. Must provide the root id
        but not necessarily the info needed to create it (for the use case of publishing). If providing
        root_category, must also provide block_fields and definition_id
        """
        new_id = ObjectId()
        if root_block_key is not None:
            if block_fields is None:
                block_fields = {}
            blocks = {
                root_block_key: self._new_block(
                    user_id, root_block_key.type, block_fields, definition_id, new_id
                )
            }
        else:
            blocks = {}
        return {
            '_id': new_id,
            'root': root_block_key,
            'previous_version': None,
            'original_version': new_id,
            'edited_by': user_id,
            'edited_on': datetime.datetime.now(ZoneInfo("UTC")),
            'blocks': blocks,
            'schema_version': self.SCHEMA_VERSION,
        }

    def _get_parents_from_structure(self, block_key, structure):
        """
        Given a structure, find block_key's parent in that structure. Note returns
        the encoded format for parent
        """
        return [
            parent_block_key
            for parent_block_key, value in structure['blocks'].items()
            if block_key in value.fields.get('children', [])
        ]

    def _sync_children(self, source_parent, destination_parent, new_child):
        """
        Reorder destination's children to the same as source's and remove any no longer in source.
        Return the removed ones as orphans (a set).
        """
        destination_reordered = []
        destination_children = set(destination_parent.fields['children'])
        source_children = source_parent.fields['children']
        orphans = destination_children - set(source_children)
        for child in source_children:
            if child == new_child or child in destination_children:
                destination_reordered.append(child)
        destination_parent.fields['children'] = destination_reordered
        return orphans

    def _copy_subdag(self, user_id, destination_version, block_key, source_blocks, destination_blocks, blacklist):
        """
        Update destination_blocks for the sub-dag rooted at block_key to be like the one in
        source_blocks excluding blacklist.

        Return any newly discovered orphans (as a set)
        """
        orphans = set()
        destination_block = destination_blocks.get(block_key)
        new_block = source_blocks[block_key]
        if destination_block:
            # reorder children to correspond to whatever order holds for source.
            # remove any which source no longer claims (put into orphans)
            # add any which are being copied
            source_children = new_block.fields.get('children', [])
            existing_children = destination_block.fields.get('children', [])
            destination_reordered = SparseList()
            for child in existing_children:
                try:
                    index = source_children.index(child)
                    destination_reordered[index] = child
                except ValueError:
                    orphans.add(BlockKey(*child))
            if blacklist != EXCLUDE_ALL:
                for index, child in enumerate(source_children):
                    if child not in blacklist:
                        destination_reordered[index] = child
            # the history of the published leaps between publications and only points to
            # previously published versions.
            previous_version = destination_block.edit_info.update_version
            destination_block = copy.deepcopy(new_block)
            destination_block.fields['children'] = destination_reordered.compact_list()
            destination_block.edit_info.previous_version = previous_version
            destination_block.edit_info.update_version = destination_version
            destination_block.edit_info.edited_by = user_id
            destination_block.edit_info.edited_on = datetime.datetime.now(ZoneInfo("UTC"))
        else:
            destination_block = self._new_block(
                user_id, new_block.block_type,
                self._filter_blacklist(copy.copy(new_block.fields), blacklist),
                new_block.definition,
                destination_version,
                raw=True,
                asides=new_block.asides,
                block_defaults=new_block.defaults
            )
            # Extend the block's new edit_info with any extra edit_info fields from the source (e.g. original_usage):
            for key, val in new_block.edit_info.to_storable().items():
                if getattr(destination_block.edit_info, key) is None:
                    setattr(destination_block.edit_info, key, val)

        # If the block we are copying from was itself a copy, then just
        # reference the original source, rather than the copy.
        destination_block.edit_info.source_version = (
            new_block.edit_info.source_version or new_block.edit_info.update_version
        )

        if blacklist != EXCLUDE_ALL:
            for child in destination_block.fields.get('children', []):
                if child not in blacklist:
                    orphans.update(
                        self._copy_subdag(
                            user_id, destination_version, BlockKey(*child), source_blocks, destination_blocks, blacklist
                        )
                    )
        destination_blocks[block_key] = destination_block
        return orphans

    def _filter_blacklist(self, fields, blacklist):
        """
        Filter out blacklist from the children field in fields. Will construct a new list for children;
        so, no need to worry about copying the children field, but it will modify fiels.
        """
        if blacklist == EXCLUDE_ALL:
            fields['children'] = []
        else:
            fields['children'] = [child for child in fields.get('children', []) if BlockKey(*child) not in blacklist]
        return fields

    def _delete_if_true_orphan(self, orphan, structure):
        """
        Delete the orphan and any of its descendants which no longer have parents.
        """
        if len(self._get_parents_from_structure(orphan, structure)) == 0:
            orphan_data = structure['blocks'].pop(orphan)
            for child in orphan_data.fields.get('children', []):
                self._delete_if_true_orphan(BlockKey(*child), structure)

    def _new_block(self, user_id, category, block_fields, definition_id, new_id, raw=False,
                   asides=None, block_defaults=None):
        """
        Create the core document structure for a block.

        :param block_fields: the settings and children scoped fields as a dict or son
        :param definition_id: the pointer to the content scoped fields
        :param new_id: the structure's version id
        :param raw: true if this block already has all references serialized
        :param asides: dict information related to the connected xblock asides
        """
        if not raw:
            block_fields = self._serialize_fields(category, block_fields)
        if not asides:
            asides = {}
        document = {
            'block_type': category,
            'definition': definition_id,
            'fields': block_fields,
            'asides': asides,
            'edit_info': {
                'edited_on': datetime.datetime.now(ZoneInfo("UTC")),
                'edited_by': user_id,
                'previous_version': None,
                'update_version': new_id
            }
        }
        if block_defaults:
            document['defaults'] = block_defaults
        return BlockData(**document)

    def _get_block_from_structure(self, structure, block_key):
        """
        Encodes the block key before retrieving it from the structure to ensure it can
        be a json dict key.
        """
        return structure['blocks'].get(block_key)

    def _get_asides_to_update_from_structure(self, structure, block_key, asides):
        """
        Get list of aside fields that should be updated/inserted
        """
        block = self._get_block_from_structure(structure, block_key)

        if asides:
            updated = False

            tmp_new_asides_data = {}
            for asd in asides:
                aside_type = asd['aside_type']
                tmp_new_asides_data[aside_type] = asd

            result_list = []
            for i, aside in enumerate(block.asides):  # lint-amnesty, pylint: disable=unused-variable
                if aside['aside_type'] in tmp_new_asides_data:
                    result_list.append(tmp_new_asides_data.pop(aside['aside_type']))
                    updated = True
                else:
                    result_list.append(aside)

            if tmp_new_asides_data:
                for _, asd in tmp_new_asides_data.items():
                    result_list.append(asd)
                    updated = True

            return result_list, updated
        else:
            return block.asides, False

    def _update_block_in_structure(self, structure, block_key, content):
        """
        Encodes the block key before accessing it in the structure to ensure it can
        be a json dict key.
        """
        structure['blocks'][block_key] = content

    def find_courses_by_search_target(self, field_name, field_value):
        """
        Find all the courses which cached that they have the given field with the given value.

        Returns: list of branch-agnostic course_keys
        """
        entries = self.find_matching_course_indexes(
            search_targets={field_name: field_value}
        )
        return [
            CourseLocator(entry['org'], entry['course'], entry['run'])  # Branch agnostic
            for entry in entries
        ]

    def get_courses_for_wiki(self, wiki_slug, **kwargs):
        """
        Return the list of courses which use this wiki_slug
        :param wiki_slug: the course wiki root slug
        :return: list of course keys
        """
        return self.find_courses_by_search_target('wiki_slug', wiki_slug)

    def heartbeat(self):
        """
        Check that the db is reachable.
        """
        return {ModuleStoreEnum.Type.split: self.db_connection.heartbeat()}

    def create_runtime(self, course_entry, lazy):
        """
        Create the proper runtime for this course
        """
        # A single SplitMongoModuleStore may create many SplitModuleStoreRuntimes,
        # each of which will later modify its internal dict of services on a per-item and often per-user basis.
        # Therefore, it's critical that we make a new copy of our baseline services dict here,
        # so that each runtime is free to add and replace its services without impacting other runtimes.
        services = self.services.copy()
        # Only the CourseBlock can have user partitions. Therefore, creating the PartitionService with the library key
        # instead of the course key does not work. The XBlock validation in Studio fails with the following message:
        # "This component's access settings refer to deleted or invalid group configurations.".
        if not isinstance(course_entry.course_key, LibraryLocator):
            services["partitions"] = PartitionService(course_entry.course_key)

        return SplitModuleStoreRuntime(
            modulestore=self,
            course_entry=course_entry,
            module_data={},
            lazy=lazy,
            default_class=self.default_class,
            error_tracker=self.error_tracker,
            render_template=self.render_template,
            mixins=self.xblock_mixins,
            select=self.xblock_select,
            disabled_xblock_types=self.disabled_xblock_types,
            services=services,
        )

    def ensure_indexes(self):
        """
        Ensure that all appropriate indexes are created that are needed by this modulestore, or raise
        an exception if unable to.

        This method is intended for use by tests and administrative commands, and not
        to be run during server startup.
        """
        self.db_connection.ensure_indexes()


class SparseList(list):
    """
    Enable inserting items into a list in arbitrary order and then retrieving them.
    """
    # taken from http://stackoverflow.com/questions/1857780/sparse-assignment-list-in-python

    def __setitem__(self, index, value):
        """
        Add value to the list ensuring the list is long enough to accommodate it at the given index
        """
        missing = index - len(self) + 1
        if missing > 0:
            self.extend([None] * missing)
        list.__setitem__(self, index, value)

    def compact_list(self):
        """
        Return as a regular lists w/ all Nones removed
        """
        return [ele for ele in self if ele is not None]
