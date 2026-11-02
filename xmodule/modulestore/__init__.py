"""
This module provides an abstraction for working with XModuleDescriptors
that are stored in a database an accessible using their Location as an identifier
"""


import datetime
import logging
import re
import threading
from abc import ABCMeta, abstractmethod
from collections import defaultdict
from contextlib import contextmanager
from operator import itemgetter
from django.db import transaction

from opaque_keys.edx.keys import AssetKey, CourseKey
from opaque_keys.edx.locations import Location  # For import backwards compatibility
from pytz import UTC
from sortedcontainers import SortedKeyList
from xblock.core import XBlock
from xblock.plugin import default_select
from xblock.runtime import Mixologist

# The below import is not used within this module, but ir is still needed becuase
# other modules are imorting EdxJSONEncoder from here
from openedx.core.lib.json_utils import EdxJSONEncoder
from xmodule.assetstore import AssetMetadata
from xmodule.errortracker import make_error_tracker
from xmodule.util.misc import get_library_or_course_attribute

from .exceptions import InsufficientSpecificationError, InvalidLocationError

log = logging.getLogger('edx.modulestore')

LIBRARY_ROOT = 'library.xml'
COURSE_ROOT = 'course.xml'

# List of names of computed fields on xmodules that are of type usage keys.
# This list can be used to determine which fields need to be stripped of
# extraneous usage key data when entering/exiting modulestores.
XMODULE_FIELDS_WITH_USAGE_KEYS = ['location', 'parent']


class ModuleStoreEnum:
    """
    A class to encapsulate common constants that are used with the various modulestores.
    """

    class Type:
        """
        The various types of modulestores provided
        """
        split = 'split'
        mongo = 'mongo'

    class RevisionOption:
        """
        Revision constants to use for Module Store operations
        Note: These values are passed into store APIs and only used at run time
        """
        # both DRAFT and PUBLISHED versions are queried, with preference to DRAFT versions
        draft_preferred = 'rev-opt-draft-preferred'

        # only DRAFT versions are queried and no PUBLISHED versions
        draft_only = 'rev-opt-draft-only'

        # # only PUBLISHED versions are queried and no DRAFT versions
        published_only = 'rev-opt-published-only'

        # all revisions are queried
        all = 'rev-opt-all'

    class Branch:
        """
        Branch constants to use for stores, such as Mongo, that have only 2 branches: DRAFT and PUBLISHED
        Note: These values are taken from server configuration settings, so should not be changed without alerting DevOps  # lint-amnesty, pylint: disable=line-too-long
        """
        draft_preferred = 'draft-preferred'
        published_only = 'published-only'

    class BranchName:
        """
        Branch constants to use for stores, such as Split, that have named branches
        """
        draft = 'draft-branch'
        published = 'published-branch'
        library = 'library'

    class UserID:
        """
        Values for user ID defaults
        """
        # Note: we use negative values here to (try to) not collide
        # with user identifiers provided by actual user services.

        # user ID to use for all management commands
        mgmt_command = -1

        # user ID to use for primitive commands
        primitive_command = -2

        # user ID to use for tests that do not have a django user available
        test = -3

        # user ID for automatic update by the system
        system = -4

    class SortOrder:
        """
        Values for sorting asset metadata.
        """
        ascending = 1
        descending = 2


class BulkOpsRecord:
    """
    For handling nesting of bulk operations
    """
    def __init__(self):
        self._active_count = 0
        self.has_publish_item = False
        self.has_library_updated_item = False
        self._commit_callbacks = []

    @property
    def active(self):
        """
        Return whether this bulk write is active.
        """
        return self._active_count > 0

    def nest(self):
        """
        Record another level of nesting of this bulk write operation
        """
        self._active_count += 1

    def unnest(self):
        """
        Record the completion of a level of nesting of the bulk write operation
        """
        self._active_count -= 1

    @property
    def is_root(self):
        """
        Return whether the bulk write is at the root (first) level of nesting
        """
        return self._active_count == 1

    def defer_until_commit(self, fn):
        """
        Run some code when the changes from this bulk op are committed to the DB
        """
        self._commit_callbacks.append(fn)

    def call_commit_callbacks(self):
        """
        When the changes have been committed to the DB, call this to run any queued callbacks
        """
        for fn in self._commit_callbacks:
            fn()
        self._commit_callbacks.clear()


class ActiveBulkThread(threading.local):
    """
    Add the expected vars to the thread.
    """
    def __init__(self, bulk_ops_record_type, **kwargs):
        super().__init__(**kwargs)
        self.records = defaultdict(bulk_ops_record_type)


class BulkOperationsMixin:
    """
    This implements the :meth:`bulk_operations` modulestore semantics which handles nested invocations

    In particular, it implements :meth:`_begin_bulk_operation` and
    :meth:`_end_bulk_operation` to provide the external interface

    Internally, this mixin records the set of all active bulk operations (keyed on the active course),
    and only writes those values when :meth:`_end_bulk_operation` is called.
    If a bulk write operation isn't active, then the changes are immediately written to the underlying
    mongo_connection.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._active_bulk_ops = ActiveBulkThread(self._bulk_ops_record_type)
        self.signal_handler = None

    @contextmanager
    def bulk_operations(self, course_id, emit_signals=True, ignore_case=False):
        """
        A context manager for notifying the store of bulk operations. This affects only the current thread.

        In the case of Mongo, it temporarily disables refreshing the metadata inheritance tree
        until the bulk operation is completed.
        """
        try:
            self._begin_bulk_operation(course_id, ignore_case)
            yield
        finally:
            self._end_bulk_operation(course_id, emit_signals, ignore_case)

    # the relevant type of bulk_ops_record for the mixin (overriding classes should override
    # this variable)
    _bulk_ops_record_type = BulkOpsRecord

    def _get_bulk_ops_record(self, course_key, ignore_case=False):
        """
        Return the :class:`.BulkOpsRecord` for this course.
        """
        if course_key is None:
            return self._bulk_ops_record_type()

        # Retrieve the bulk record based on matching org/course/run (possibly ignoring case)
        if ignore_case:
            for key, record in self._active_bulk_ops.records.items():
                # Shortcut: check basic equivalence for cases where org/course/run might be None.
                key_library = get_library_or_course_attribute(key)
                course_library = get_library_or_course_attribute(course_key)
                if (key == course_key) or (  # lint-amnesty, pylint: disable=too-many-boolean-expressions
                        (key.org and key.org.lower() == course_key.org.lower()) and
                        (key_library and key_library.lower() == course_library.lower()) and
                        (key.run and key.run.lower() == course_key.run.lower())
                ):
                    return record

        return self._active_bulk_ops.records[course_key.for_branch(None)]

    @property
    def _active_records(self):
        """
        Yield all active (CourseLocator, BulkOpsRecord) tuples.
        """
        for course_key, record in self._active_bulk_ops.records.items():
            if record.active:
                yield (course_key, record)

    def _clear_bulk_ops_record(self, course_key):
        """
        Clear the record for this course
        """
        if course_key.for_branch(None) in self._active_bulk_ops.records:
            del self._active_bulk_ops.records[course_key.for_branch(None)]

    def _start_outermost_bulk_operation(self, bulk_ops_record, course_key, ignore_case=False):
        """
        The outermost nested bulk_operation call: do the actual begin of the bulk operation.

        Implementing classes must override this method; otherwise, the bulk operations are a noop
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    def _begin_bulk_operation(self, course_key, ignore_case=False):
        """
        Begin a bulk operation on course_key.
        """
        bulk_ops_record = self._get_bulk_ops_record(course_key, ignore_case)

        # Increment the number of active bulk operations (bulk operations
        # on the same course can be nested)
        bulk_ops_record.nest()

        # If this is the highest level bulk operation, then initialize it
        if bulk_ops_record.is_root:
            self._start_outermost_bulk_operation(bulk_ops_record, course_key, ignore_case)

    def _end_outermost_bulk_operation(self, bulk_ops_record, structure_key):
        """
        The outermost nested bulk_operation call: do the actual end of the bulk operation.

        Implementing classes must override this method; otherwise, the bulk operations are a noop
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    def _end_bulk_operation(self, structure_key, emit_signals=True, ignore_case=False):
        """
        End the active bulk operation on structure_key (course or library key).
        """
        # If no bulk op is active, return
        bulk_ops_record = self._get_bulk_ops_record(structure_key, ignore_case)
        if not bulk_ops_record.active:
            return

        # Send the pre-publish signal within the context of the bulk operation.
        # Writes performed by signal handlers will be persisted when the bulk
        # operation ends.
        if emit_signals and bulk_ops_record.is_root:
            self.send_pre_publish_signal(bulk_ops_record, structure_key)

        bulk_ops_record.unnest()

        # If this wasn't the outermost context, then don't close out the
        # bulk operation.
        if bulk_ops_record.active:
            return

        dirty = self._end_outermost_bulk_operation(bulk_ops_record, structure_key)  # lint-amnesty, pylint: disable=assignment-from-no-return

        # The bulk op has ended. However, the signal tasks below still need to use the
        # built-up bulk op information (if the signals trigger tasks in the same thread).
        # So re-nest until the signals are sent.
        bulk_ops_record.nest()

        if dirty:
            # Call any "on commit" callback, regardless of if this was "published" or is still draft:
            bulk_ops_record.call_commit_callbacks()
            # Call any "on publish" handlers - emit_signals is usually false for draft-only changes:
            if emit_signals:
                self.send_bulk_published_signal(bulk_ops_record, structure_key)
                self.send_bulk_library_updated_signal(bulk_ops_record, structure_key)

        # Signals are sent. Now unnest and clear the bulk op for good.
        bulk_ops_record.unnest()

        self._clear_bulk_ops_record(structure_key)

    def on_commit_changes_to(self, course_key, fn):
        """
        Call some callback when the currently active bulk operation has saved
        """
        # If we're in a MySQL transaction, so the new version will only be committed to the
        # SplitModulestoreCourseIndex table after the MySQL transaction is closed.
        def wrapped_fn():
            transaction.on_commit(fn)
        # Check if a bulk op is active. If so, defer fn(); otherwise call it immediately.
        # Note: calling _get_bulk_ops_record() here and then checking .active can have side-effects in some cases
        # because it creates an entry in the defaultdict if none exists, so we check if the record is active using
        # the same code as _clear_bulk_ops_record(), which doesn't modify the defaultdict.
        # so we check it this way:
        if course_key and course_key.for_branch(None) in self._active_bulk_ops.records:
            bulk_ops_record = self._active_bulk_ops.records[course_key.for_branch(None)]
            bulk_ops_record.defer_until_commit(wrapped_fn)
        else:
            wrapped_fn()  # There is no active bulk operation - call wrapped_fn() now.

    def _is_in_bulk_operation(self, course_key, ignore_case=False):
        """
        Return whether a bulk operation is active on `course_key`.
        """
        return self._get_bulk_ops_record(course_key, ignore_case).active

    def send_pre_publish_signal(self, bulk_ops_record, course_id):
        """
        Send a signal just before items are published in the course.
        """
        signal_handler = getattr(self, "signal_handler", None)
        if signal_handler and bulk_ops_record.has_publish_item:
            signal_handler.send("pre_publish", course_key=course_id)

    def send_bulk_published_signal(self, bulk_ops_record, course_id):
        """
        Sends out the signal that items have been published from within this course.
        """
        if self.signal_handler and bulk_ops_record.has_publish_item:
            # We remove the branch, because publishing always means copying from draft to published
            self.signal_handler.send("course_published", course_key=course_id.for_branch(None))
            bulk_ops_record.has_publish_item = False

    def send_bulk_library_updated_signal(self, bulk_ops_record, library_id):
        """
        Sends out the signal that library have been updated.
        """
        if self.signal_handler and bulk_ops_record.has_library_updated_item:
            self.signal_handler.send("library_updated", library_key=library_id)
            bulk_ops_record.has_library_updated_item = False


class EditInfo:
    """
    Encapsulates the editing info of a block.
    """
    def __init__(self, **kwargs):
        self.from_storable(kwargs)

        # For details, see runtime.py get_subtree_edited_by/on.
        self._subtree_edited_on = kwargs.get('_subtree_edited_on', None)
        self._subtree_edited_by = kwargs.get('_subtree_edited_by', None)

    def to_storable(self):
        """
        Serialize to a Mongo-storable format.
        """
        return {
            'previous_version': self.previous_version,
            'update_version': self.update_version,
            'source_version': self.source_version,
            'edited_on': self.edited_on,
            'edited_by': self.edited_by,
            'original_usage': self.original_usage,
            'original_usage_version': self.original_usage_version,
        }

    def from_storable(self, edit_info):
        """
        De-serialize from Mongo-storable format to an object.
        """
        # Guid for the structure which previously changed this XBlock.
        # (Will be the previous value of 'update_version'.)
        self.previous_version = edit_info.get('previous_version', None)

        # Guid for the structure where this XBlock got its current field values.
        # May point to a structure not in this structure's history (e.g., to a draft
        # branch from which this version was published).
        self.update_version = edit_info.get('update_version', None)

        self.source_version = edit_info.get('source_version', None)

        # Datetime when this XBlock's fields last changed.
        self.edited_on = edit_info.get('edited_on', None)
        # User ID which changed this XBlock last.
        self.edited_by = edit_info.get('edited_by', None)

        # If this block has been copied from a library using copy_from_template,
        # these fields point to the original block in the library, for analytics.
        self.original_usage = edit_info.get('original_usage', None)
        self.original_usage_version = edit_info.get('original_usage_version', None)

    def __repr__(self):
        return ("{classname}(previous_version={self.previous_version}, "
                "update_version={self.update_version}, "
                "source_version={source_version}, "
                "edited_on={self.edited_on}, "
                "edited_by={self.edited_by}, "
                "original_usage={self.original_usage}, "
                "original_usage_version={self.original_usage_version}, "
                "_subtree_edited_on={self._subtree_edited_on}, "
                "_subtree_edited_by={self._subtree_edited_by})").format(
            self=self,
            classname=self.__class__.__name__,
            source_version="UNSET" if self.source_version is None else self.source_version,
        )

    def __eq__(self, edit_info):
        """
        Two EditInfo instances are equal iff their storable representations
        are equal.
        """
        return self.to_storable() == edit_info.to_storable()

    def __neq__(self, edit_info):
        """
        Two EditInfo instances are not equal if they're not equal.
        """
        return not self == edit_info


class BlockData:
    """
    Wrap the block data in an object instead of using a straight Python dictionary.
    Allows the storing of meta-information about a structure that doesn't persist along with
    the structure itself.
    """
    def __init__(self, **kwargs):
        # Has the definition been loaded?
        self.definition_loaded = False
        self.from_storable(kwargs)

    def to_storable(self):
        """
        Serialize to a Mongo-storable format.
        """
        return {
            'fields': self.fields,
            'block_type': self.block_type,
            'definition': self.definition,
            'defaults': self.defaults,
            'asides': self.get_asides(),
            'edit_info': self.edit_info.to_storable(),
        }

    def from_storable(self, block_data):
        """
        De-serialize from Mongo-storable format to an object.
        """
        # Contains the Scope.settings and 'children' field values.
        # 'children' are stored as a list of (block_type, block_id) pairs.
        self.fields = block_data.get('fields', {})

        # XBlock type ID.
        self.block_type = block_data.get('block_type', None)

        # DB id of the record containing the content of this XBlock.
        self.definition = block_data.get('definition', None)

        # Scope.settings default values copied from a template block (used e.g. when
        # blocks are copied from a library to a course)
        self.defaults = block_data.get('defaults', {})

        # Additional field data that stored in connected XBlockAsides
        self.asides = block_data.get('asides', {})

        # EditInfo object containing all versioning/editing data.
        self.edit_info = EditInfo(**block_data.get('edit_info', {}))

    def get_asides(self):
        """
        For the situations if block_data has no asides attribute
        (in case it was taken from memcache)
        """
        if not hasattr(self, 'asides'):
            self.asides = {}   # pylint: disable=attribute-defined-outside-init
        return self.asides

    def __repr__(self):
        return ("{classname}(fields={self.fields}, "
                "block_type={self.block_type}, "
                "definition={self.definition}, "
                "definition_loaded={self.definition_loaded}, "
                "defaults={self.defaults}, "
                "asides={asides}, "
                "edit_info={self.edit_info})").format(
            self=self,
            classname=self.__class__.__name__,
            asides=self.get_asides()
        )

    def __eq__(self, block_data):
        """
        Two BlockData objects are equal iff all their attributes are equal.
        """
        attrs = ['fields', 'block_type', 'definition', 'defaults', 'asides', 'edit_info']
        return all(getattr(self, attr, None) == getattr(block_data, attr, None) for attr in attrs)

    def __neq__(self, block_data):
        """
        Just define this as not self.__eq__(block_data)
        """
        return not self == block_data


class IncorrectlySortedList(Exception):
    """
    Thrown when calling find() on a SortedAssetList not sorted by filename.
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


class SortedAssetList(SortedKeyList):  # lint-amnesty, pylint: disable=abstract-method
    """
    List of assets that is sorted based on an asset attribute.
    """
    def __init__(self, **kwargs):
        self.filename_sort = False
        key_func = kwargs.get('key', None)
        if key_func is None:
            kwargs['key'] = itemgetter('filename')
            self.filename_sort = True
        super().__init__(**kwargs)

    def find(self, asset_id):
        """
        Find the index of a particular asset in the list. This method is only functional for lists
        sorted by filename. If the list is sorted on any other key, find() raises a
        Returns: Index of asset, if found. None if not found.
        """
        # Don't attempt to find an asset by filename in a list that's not sorted by filename.
        if not self.filename_sort:
            raise IncorrectlySortedList()
        # See if this asset already exists by checking the external_filename.
        # Studio doesn't currently support using multiple course assets with the same filename.
        # So use the filename as the unique identifier.
        idx = None
        idx_left = self.bisect_left({'filename': asset_id.path})
        idx_right = self.bisect_right({'filename': asset_id.path})
        if idx_left != idx_right:
            # Asset was found in the list.
            idx = idx_left
        return idx

    def insert_or_update(self, asset_md):
        """
        Insert asset metadata if asset is not present. Update asset metadata if asset is already present.
        """
        metadata_to_insert = asset_md.to_storable()
        asset_idx = self.find(asset_md.asset_id)
        if asset_idx is not None:
            # Delete existing metadata.
            del self[asset_idx]
        # Add new metadata sorted into the list.
        self.add(metadata_to_insert)


class ModuleStoreAssetBase:
    """
    The methods for accessing assets and their metadata
    """
    def _find_course_asset(self, asset_key):
        """
        Returns same as _find_course_assets plus the index to the given asset or None. Does not convert
        to AssetMetadata; thus, is internal.

        Arguments:
            asset_key (AssetKey): what to look for

        Returns:
            Tuple of:
            - AssetMetadata[] for all assets of the given asset_key's type
            - the index of asset in list (None if asset does not exist)
        """
        course_assets = self._find_course_assets(asset_key.course_key)  # lint-amnesty, pylint: disable=no-member
        all_assets = SortedAssetList(iterable=course_assets.setdefault(asset_key.block_type, []))
        idx = all_assets.find(asset_key)

        return course_assets, idx

    def find_asset_metadata(self, asset_key, **kwargs):
        """
        Find the metadata for a particular course asset.

        Arguments:
            asset_key (AssetKey): key containing original asset filename

        Returns:
            asset metadata (AssetMetadata) -or- None if not found
        """
        course_assets, asset_idx = self._find_course_asset(asset_key)
        if asset_idx is None:
            return None

        mdata = AssetMetadata(asset_key, asset_key.path, **kwargs)
        all_assets = course_assets[asset_key.asset_type]
        mdata.from_storable(all_assets[asset_idx])
        return mdata

    def get_all_asset_metadata(self, course_key, asset_type, start=0, maxresults=-1, sort=None, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
        """
        Returns a list of asset metadata for all assets of the given asset_type in the course.

        Args:
            course_key (CourseKey): course identifier
            asset_type (str): the block_type of the assets to return. If None, return assets of all types.
            start (int): optional - start at this asset number. Zero-based!
            maxresults (int): optional - return at most this many, -1 means no limit
            sort (array): optional - None means no sort
                (sort_by (str), sort_order (str))
                sort_by - one of 'uploadDate' or 'displayname'
                sort_order - one of SortOrder.ascending or SortOrder.descending

        Returns:
            List of AssetMetadata objects.
        """
        course_assets = self._find_course_assets(course_key)  # lint-amnesty, pylint: disable=no-member

        # Determine the proper sort - with defaults of ('displayname', SortOrder.ascending).
        key_func = None
        sort_order = ModuleStoreEnum.SortOrder.ascending
        if sort:
            if sort[0] == 'uploadDate':
                key_func = lambda x: x['edit_info']['edited_on']
            if sort[1] == ModuleStoreEnum.SortOrder.descending:
                sort_order = ModuleStoreEnum.SortOrder.descending

        if asset_type is None:
            # Add assets of all types to the sorted list.
            all_assets = SortedAssetList(iterable=[], key=key_func)
            for asset_type, val in course_assets.items():  # lint-amnesty, pylint: disable=redefined-argument-from-local
                all_assets.update(val)
        else:
            # Add assets of a single type to the sorted list.
            all_assets = SortedAssetList(iterable=course_assets.get(asset_type, []), key=key_func)
        num_assets = len(all_assets)

        start_idx = start
        end_idx = min(num_assets, start + maxresults)
        if maxresults < 0:
            # No limit on the results.
            end_idx = num_assets

        step_incr = 1
        if sort_order == ModuleStoreEnum.SortOrder.descending:
            # Flip the indices and iterate backwards.
            step_incr = -1
            start_idx = (num_assets - 1) - start_idx
            end_idx = (num_assets - 1) - end_idx

        ret_assets = []
        for idx in range(start_idx, end_idx, step_incr):
            raw_asset = all_assets[idx]
            asset_key = course_key.make_asset_key(raw_asset['asset_type'], raw_asset['filename'])
            new_asset = AssetMetadata(asset_key)
            new_asset.from_storable(raw_asset)
            ret_assets.append(new_asset)
        return ret_assets

    # pylint: disable=unused-argument
    def check_supports(self, course_key, method):
        """
        Verifies that a modulestore supports a particular method.

        Some modulestores may differ based on the course_key, such
        as mixed (since it has to find the underlying modulestore),
        so it's required as part of the method signature.
        """
        return hasattr(self, method)


class ModuleStoreAssetWriteInterface(ModuleStoreAssetBase):
    """
    The write operations for assets and asset metadata
    """
    def _save_assets_by_type(self, course_key, asset_metadata_list, course_assets, user_id, import_only):
        """
        Common private method that saves/updates asset metadata items in the internal modulestore
        structure used to store asset metadata items.
        """
        # Lazily create a sorted list if not already created.
        assets_by_type = defaultdict(lambda: SortedAssetList(iterable=course_assets.get(asset_type, [])))

        for asset_md in asset_metadata_list:
            if asset_md.asset_id.course_key != course_key:
                # pylint: disable=logging-format-interpolation
                log.warning("Asset's course {} does not match other assets for course {} - not saved.".format(
                    asset_md.asset_id.course_key, course_key
                ))
                continue
            if not import_only:
                asset_md.update({'edited_by': user_id, 'edited_on': datetime.datetime.now(UTC)})
            asset_type = asset_md.asset_id.asset_type
            all_assets = assets_by_type[asset_type]
            all_assets.insert_or_update(asset_md)
        return assets_by_type

    def save_asset_metadata(self, asset_metadata, user_id, import_only):
        """
        Saves the asset metadata for a particular course's asset.

        Arguments:
            asset_metadata (AssetMetadata): data about the course asset data
            user_id (int): user ID saving the asset metadata
            import_only (bool): True if importing without editing, False if editing

        Returns:
            True if metadata save was successful, else False
        """
        raise NotImplementedError()

    def save_asset_metadata_list(self, asset_metadata_list, user_id, import_only):
        """
        Saves a list of asset metadata for a particular course's asset.

        Arguments:
            asset_metadata (AssetMetadata): data about the course asset data
            user_id (int): user ID saving the asset metadata
            import_only (bool): True if importing without editing, False if editing

        Returns:
            True if metadata save was successful, else False
        """
        raise NotImplementedError()

    def set_asset_metadata_attrs(self, asset_key, attrs, user_id):
        """
        Base method to over-ride in modulestore.
        """
        raise NotImplementedError()

    def delete_asset_metadata(self, asset_key, user_id):
        """
        Base method to over-ride in modulestore.
        """
        raise NotImplementedError()

    def set_asset_metadata_attr(self, asset_key, attr, value, user_id):
        """
        Add/set the given attr on the asset at the given location. Value can be any type which pymongo accepts.

        Arguments:
            asset_key (AssetKey): asset identifier
            attr (str): which attribute to set
            value: the value to set it to (any type pymongo accepts such as datetime, number, string)
            user_id (int): user ID saving the asset metadata

        Raises:
            ItemNotFoundError if no such item exists
            AttributeError is attr is one of the build in attrs.
        """
        return self.set_asset_metadata_attrs(asset_key, {attr: value}, user_id)

    def copy_all_asset_metadata(self, source_course_key, dest_course_key, user_id):
        """
        Copy all the course assets from source_course_key to dest_course_key.
        NOTE: unlike get_all_asset_metadata, this does not take an asset type because
        this function is intended for things like cloning or exporting courses not for
        clients to list assets.

        Arguments:
            source_course_key (CourseKey): identifier of course to copy from
            dest_course_key (CourseKey): identifier of course to copy to
            user_id (int): user ID copying the asset metadata
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass


class ModuleStoreRead(ModuleStoreAssetBase, metaclass=ABCMeta):
    """
    An abstract interface for a database backend that stores XModuleDescriptor
    instances and extends read-only functionality
    """

    @abstractmethod
    def has_item(self, usage_key):
        """
        Returns True if usage_key exists in this ModuleStore.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def get_item(self, usage_key, depth=0, using_descriptor_system=None, **kwargs):
        """
        Returns an XModuleDescriptor instance for the item at location.

        If any segment of the location is None except revision, raises
            xmodule.modulestore.exceptions.InsufficientSpecificationError

        If no object is found at that location, raises
            xmodule.modulestore.exceptions.ItemNotFoundError

        usage_key: A :class:`.UsageKey` subclass instance

        depth (int): An argument that some module stores may use to prefetch
            descendents of the queried blocks for more efficient results later
            in the request. The depth is counted in the number of calls to
            get_children() to cache. None indicates to cache all descendents
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def get_course_errors(self, course_key):
        """
        Return a list of (msg, exception-or-None) errors that the modulestore
        encountered when loading the course at course_id.

        Raises the same exceptions as get_item if the location isn't found or
        isn't fully specified.

        Args:
            course_key (:class:`.CourseKey`): The course to check for errors
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def get_items(self, course_id, qualifiers=None, **kwargs):
        """
        Returns a list of XModuleDescriptor instances for the items
        that match location. Any element of location that is None is treated
        as a wildcard that matches any value

        location: Something that can be passed to Location
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    def _block_matches(self, block, qualifiers):
        """
        Return True or False depending on whether the field value (block contents)
        matches the qualifiers as per get_items.
        NOTE: Method only finds directly set value matches - not inherited nor default value matches.
        For substring matching:
            pass a regex object.
        For arbitrary function comparison such as date time comparison:
            pass the function as in start=lambda x: x < datetime.datetime(2014, 1, 1, 0, tzinfo=pytz.UTC)

        Args:
            block (dict, XBlock, or BlockData): either the BlockData (transformed from the db) -or-
                a dict (from BlockData.fields or get_explicitly_set_fields_by_scope) -or-
                the xblock.fields() value -or-
                the XBlock from which to get the 'fields' value.
             qualifiers (dict): {field: value} search pairs.
        """
        if isinstance(block, XBlock):
            # If an XBlock is passed-in, just match its fields.
            xblock, fields = (block, block.fields)
        elif isinstance(block, BlockData):
            # BlockData is an object - compare its attributes in dict form.
            xblock, fields = (None, block.__dict__)
        else:
            xblock, fields = (None, block)

        def _is_set_on(key):
            """
            Is this key set in fields? (return tuple of boolean and value). A helper which can
            handle fields either being the json doc or xblock fields. Is inner function to restrict
            use and to access local vars.
            """
            if key not in fields:
                return False, None
            field = fields[key]
            if xblock is not None:
                return field.is_set_on(block), getattr(xblock, key)
            else:
                return True, field

        for key, criteria in qualifiers.items():
            is_set, value = _is_set_on(key)
            if isinstance(criteria, dict) and '$exists' in criteria and criteria['$exists'] == is_set:
                continue
            if not is_set:
                return False
            if not self._value_matches(value, criteria):
                return False
        return True

    def _value_matches(self, target, criteria):
        """
        helper for _block_matches: does the target (field value) match the criteria?

        If target is a list, do any of the list elements meet the criteria
        If the criteria is a regex, does the target match it?
        If the criteria is a function, does invoking it on the target yield something truthy?
        If criteria is a dict {($nin|$in): []}, then do (none|any) of the list elements meet the criteria
        Otherwise, is the target == criteria
        """
        if isinstance(target, list):
            return any(self._value_matches(ele, criteria) for ele in target)
        elif isinstance(criteria, re.Pattern):
            return criteria.search(target) is not None
        elif callable(criteria):
            return criteria(target)
        elif isinstance(criteria, dict) and '$in' in criteria:
            # note isn't handling any other things in the dict other than in
            return any(self._value_matches(target, test_val) for test_val in criteria['$in'])
        elif isinstance(criteria, dict) and '$nin' in criteria:
            # note isn't handling any other things in the dict other than nin
            return not any(self._value_matches(target, test_val) for test_val in criteria['$nin'])
        else:
            return criteria == target

    @abstractmethod
    def make_course_key(self, org, course, run):
        """
        Return a valid :class:`~opaque_keys.edx.keys.CourseKey` for this modulestore
        that matches the supplied `org`, `course`, and `run`.

        This key may represent a course that doesn't exist in this modulestore.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def make_course_usage_key(self, course_key):
        """
        Return a valid :class:`~opaque_keys.edx.keys.UsageKey` for this modulestore
        that matches the supplied course_key.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def get_courses(self, **kwargs):
        '''
        Returns a list containing the top level XModuleDescriptors of the courses
        in this modulestore. This method can take an optional argument 'org' which
        will efficiently apply a filter so that only the courses of the specified
        ORG in the CourseKey will be fetched.
        '''
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def get_course(self, course_id, depth=0, **kwargs):
        '''
        Look for a specific course by its id (:class:`CourseKey`).
        Returns the course block, or None if not found.
        '''
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def has_course(self, course_id, ignore_case=False, **kwargs):
        '''
        Look for a specific course id.  Returns whether it exists.
        Args:
            course_id (CourseKey):
            ignore_case (boolean): some modulestores are case-insensitive. Use this flag
                to search for whether a potentially conflicting course exists in that case.
        '''
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def get_parent_location(self, location, **kwargs):
        '''
        Find the location that is the parent of this location in this
        course.  Needed for path_to_location().
        '''
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def get_orphans(self, course_key, **kwargs):
        """
        Get all of the xblocks in the given course which have no parents and are not of types which are
        usually orphaned. NOTE: may include xblocks which still have references via xblocks which don't
        use children to point to their dependents.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def get_errored_courses(self):
        """
        Return a dictionary of course_dir -> [(msg, exception_str)], for each
        course_dir where course loading failed.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def get_modulestore_type(self, course_id):
        """
        Returns a type which identifies which modulestore is servicing the given
        course_id. The return can be either "xml" (for XML based courses) or "mongo" for MongoDB backed courses
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def get_courses_for_wiki(self, wiki_slug, **kwargs):
        """
        Return the list of courses which use this wiki_slug
        :param wiki_slug: the course wiki root slug
        :return: list of course keys
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def has_published_version(self, xblock):
        """
        Returns true if this xblock exists in the published course regardless of whether it's up to date
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def close_connections(self):
        """
        Closes any open connections to the underlying databases
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @contextmanager
    def bulk_operations(self, course_id, emit_signals=True, ignore_case=False):    # pylint: disable=unused-argument
        """
        A context manager for notifying the store of bulk operations. This affects only the current thread.
        """
        yield

    def ensure_indexes(self):
        """
        Ensure that all appropriate indexes are created that are needed by this modulestore, or raise
        an exception if unable to.

        This method is intended for use by tests and administrative commands, and not
        to be run during server startup.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass


class ModuleStoreWrite(ModuleStoreRead, ModuleStoreAssetWriteInterface, metaclass=ABCMeta):
    """
    An abstract interface for a database backend that stores XModuleDescriptor
    instances and extends both read and write functionality
    """

    @abstractmethod
    def update_item(self, xblock, user_id, allow_not_found=False, force=False, **kwargs):
        """
        Update the given xblock's persisted repr. Pass the user's unique id which the persistent store
        should save with the update if it has that ability.

        :param allow_not_found: whether this method should raise an exception if the given xblock
        has not been persisted before.
        :param force: fork the structure and don't update the course draftVersion if there's a version
        conflict (only applicable to version tracking and conflict detecting persistence stores)

        :raises VersionConflictError: if org, course, run, and version_guid given and the current
        version head != version_guid and force is not True. (only applicable to version tracking stores)
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def delete_item(self, location, user_id, **kwargs):
        """
        Delete an item and its subtree from persistence. Remove the item from any parents (Note, does not
        affect parents from other branches or logical branches; thus, in old mongo, deleting something
        whose parent cannot be draft, deletes it from both but deleting a component under a draft vertical
        only deletes it from the draft.

        Pass the user's unique id which the persistent store
        should save with the update if it has that ability.

        :param force: fork the structure and don't update the course draftVersion if there's a version
        conflict (only applicable to version tracking and conflict detecting persistence stores)

        :raises VersionConflictError: if org, course, run, and version_guid given and the current
        version head != version_guid and force is not True. (only applicable to version tracking stores)
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def create_course(self, org, course, run, user_id, fields=None, **kwargs):
        """
        Creates and returns the course.

        Args:
            org (str): the organization that owns the course
            course (str): the name of the course
            run (str): the name of the run
            user_id: id of the user creating the course
            fields (dict): Fields to set on the course at initialization
            kwargs: Any optional arguments understood by a subset of modulestores to customize instantiation

        Returns: a CourseBlock
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def create_item(self, user_id, course_key, block_type, block_id=None, fields=None, **kwargs):
        """
        Creates and saves a new item in a course.

        Returns the newly created item.

        Args:
            user_id: ID of the user creating and saving the xmodule
            course_key: A :class:`~opaque_keys.edx.CourseKey` identifying which course to create
                this item in
            block_type: The type of block to create
            block_id: a unique identifier for the new item. If not supplied,
                a new identifier will be generated
            fields (dict): A dictionary specifying initial values for some or all fields
                in the newly created block
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def clone_course(self, source_course_id, dest_course_id, user_id, fields=None):
        """
        Sets up source_course_id to point a course with the same content as the desct_course_id. This
        operation may be cheap or expensive. It may have to copy all assets and all xblock content or
        merely setup new pointers.

        Backward compatibility: this method used to require in some modulestores that dest_course_id
        pointed to an empty but already created course. Implementers should support this or should
        enable creating the course from scratch.

        Raises:
            ItemNotFoundError: if the source course doesn't exist (or any of its xblocks aren't found)
            DuplicateItemError: if the destination course already exists (with content in some cases)
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def delete_course(self, course_key, user_id, **kwargs):
        """
        Deletes the course. It may be a soft or hard delete. It may or may not remove the xblock definitions
        depending on the persistence layer and how tightly bound the xblocks are to the course.

        Args:
            course_key (CourseKey): which course to delete
            user_id: id of the user deleting the course
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
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
        pass  # lint-amnesty, pylint: disable=unnecessary-pass


# pylint: disable=abstract-method
class ModuleStoreReadBase(BulkOperationsMixin, ModuleStoreRead):
    """
    Implement interface functionality that can be shared.
    """
    def __init__(  # lint-amnesty, pylint: disable=unused-argument
        self,
        contentstore=None,
        doc_store_config=None,  # ignore if passed up
        metadata_inheritance_cache_subsystem=None, request_cache=None,
        xblock_mixins=(), xblock_select=None, xblock_field_data_wrappers=(), disabled_xblock_types=lambda: [],
        # temporary parms to enable backward compatibility. remove once all envs migrated
        db=None, collection=None, host=None, port=None, tz_aware=True, user=None, password=None,
        # allow lower level init args to pass harmlessly
        ** kwargs
    ):
        '''
        Set up the error-tracking logic.
        '''
        super().__init__(**kwargs)
        self._course_errors = defaultdict(make_error_tracker)  # location -> ErrorLog
        # TODO move the inheritance_cache_subsystem to classes which use it
        self.metadata_inheritance_cache_subsystem = metadata_inheritance_cache_subsystem
        self.request_cache = request_cache
        self.xblock_mixins = xblock_mixins
        self.xblock_select = xblock_select
        self.xblock_field_data_wrappers = xblock_field_data_wrappers
        self.disabled_xblock_types = disabled_xblock_types
        self.contentstore = contentstore

    def get_course_errors(self, course_key):
        """
        Return list of errors for this :class:`.CourseKey`, if any.  Raise the same
        errors as get_item if course_key isn't present.
        """
        # check that item is present and raise the promised exceptions if needed
        # TODO (vshnayder): post-launch, make errors properties of items
        # self.get_item(location)
        assert isinstance(course_key, CourseKey)
        return self._course_errors[course_key].errors

    def get_errored_courses(self):
        """
        Returns an empty dict.

        It is up to subclasses to extend this method if the concept
        of errored courses makes sense for their implementation.
        """
        return {}

    def get_course(self, course_id, depth=0, **kwargs):
        """
        See ModuleStoreRead.get_course

        Default impl--linear search through course list
        """
        assert isinstance(course_id, CourseKey)
        for course in self.get_courses(**kwargs):
            if course.id == course_id:
                return course
        return None

    def has_course(self, course_id, ignore_case=False, **kwargs):
        """
        Returns the course_id of the course if it was found, else None
        Args:
            course_id (CourseKey):
            ignore_case (boolean): some modulestores are case-insensitive. Use this flag
                to search for whether a potentially conflicting course exists in that case.
        """
        # linear search through list
        assert isinstance(course_id, CourseKey)
        if ignore_case:
            return next(
                (
                    c.id for c in self.get_courses()
                    if c.id.org.lower() == course_id.org.lower() and
                    c.id.course.lower() == course_id.course.lower() and
                    c.id.run.lower() == course_id.run.lower()
                ),
                None
            )
        else:
            return next(
                (c.id for c in self.get_courses() if c.id == course_id),
                None
            )

    def has_published_version(self, xblock):
        """
        Returns True since this is a read-only store.
        """
        return True

    def heartbeat(self):
        """
        Is this modulestore ready?
        """
        # default is to say yes by not raising an exception
        return {'default_impl': True}

    def close_connections(self):
        """
        Closes any open connections to the underlying databases
        """
        if self.contentstore:
            self.contentstore.close_connections()
        super().close_connections()

    @contextmanager
    def default_store(self, store_type):
        """
        A context manager for temporarily changing the default store
        """
        if self.get_modulestore_type(None) != store_type:
            raise ValueError(f"Cannot set default store to type {store_type}")
        yield


# pylint: disable=abstract-method
class ModuleStoreWriteBase(ModuleStoreReadBase, ModuleStoreWrite):
    '''
    Implement interface functionality that can be shared.
    '''
    def __init__(self, contentstore, **kwargs):
        super().__init__(contentstore=contentstore, **kwargs)
        self.mixologist = Mixologist(self.xblock_mixins)

    def partition_fields_by_scope(self, category, fields):
        """
        Return dictionary of {scope: {field1: val, ..}..} for the fields of this potential xblock

        :param category: the xblock category
        :param fields: the dictionary of {fieldname: value}
        """
        result = defaultdict(dict)
        if fields is None:
            return result
        classes = XBlock.load_class(category, default=self.default_class)
        cls = self.mixologist.mix(classes)
        for field_name, value in fields.items():
            field = getattr(cls, field_name)
            result[field.scope][field_name] = value
        return result

    def create_course(self, org, course, run, user_id, fields=None, runtime=None, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Creates any necessary other things for the course as a side effect and doesn't return
        anything useful. The real subclass should call this before it returns the course.
        """
        # clone a default 'about' overview block as well
        about_location = self.make_course_key(org, course, run).make_usage_key('about', 'overview')

        about_block = XBlock.load_class('about')
        overview_template = about_block.get_template('overview.yaml')
        self.create_item(
            user_id,
            about_location.course_key,
            about_location.block_type,
            block_id=about_location.block_id,
            definition_data={'data': overview_template.get('data')},
            metadata=overview_template.get('metadata'),
            runtime=runtime,
            continue_version=True,
        )

    def clone_course(self, source_course_id, dest_course_id, user_id, fields=None, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
        """
        This base method just copies the assets. The lower level impls must do the actual cloning of
        content.
        """
        with self.bulk_operations(dest_course_id):
            # copy the assets
            if self.contentstore:
                self.contentstore.copy_all_course_assets(source_course_id, dest_course_id)
            return dest_course_id

    def delete_course(self, course_key, user_id, **kwargs):
        """
        This base method just deletes the assets. The lower level impls must do the actual deleting of
        content.
        """
        # delete the assets
        if self.contentstore:
            self.contentstore.delete_all_course_assets(course_key)
        super().delete_course(course_key, user_id)

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
        if self.contentstore:
            self.contentstore._drop_database(database, collections, connections)  # pylint: disable=protected-access
        super()._drop_database(database, collections, connections)

    def create_child(self, user_id, parent_usage_key, block_type, block_id=None, fields=None, **kwargs):
        """
        Creates and saves a new xblock that as a child of the specified block

        Returns the newly created item.

        Args:
            user_id: ID of the user creating and saving the xmodule
            parent_usage_key: a :class:`~opaque_key.edx.UsageKey` identifing the
                block that this item should be parented under
            block_type: The type of block to create
            block_id: a unique identifier for the new item. If not supplied,
                a new identifier will be generated
            fields (dict): A dictionary specifying initial values for some or all fields
                in the newly created block
        """
        item = self.create_item(user_id, parent_usage_key.course_key, block_type, block_id=block_id, fields=fields, **kwargs)  # lint-amnesty, pylint: disable=line-too-long
        parent = self.get_item(parent_usage_key)
        parent.children.append(item.location)
        self.update_item(parent, user_id)

    def _flag_library_updated_event(self, library_key):
        """
        Wrapper around calls to fire the library_updated signal
        Unless we're nested in an active bulk operation, this simply fires the signal
        otherwise a publish will be signalled at the end of the bulk operation

        Arguments:
            library_key - library_key to which the signal applies
        """
        if self.signal_handler:
            bulk_record = self._get_bulk_ops_record(library_key) if isinstance(self, BulkOperationsMixin) else None
            if bulk_record and bulk_record.active:
                bulk_record.has_library_updated_item = True
            else:
                self.signal_handler.send("library_updated", library_key=library_key)

    def _emit_course_deleted_signal(self, course_key):
        """
        Helper method used to emit the course_deleted signal.
        """
        if self.signal_handler:
            self.signal_handler.send("course_deleted", course_key=course_key)

    def _emit_item_deleted_signal(self, usage_key, user_id):
        """
        Helper method used to emit the item_deleted signal.
        """
        if self.signal_handler:
            self.signal_handler.send("item_deleted", usage_key=usage_key, user_id=user_id)
