"""
This module provides an abstraction for Module Stores that support Draft and Published branches.
"""


import logging  # noqa: I001
import threading
from abc import ABCMeta, abstractmethod
from contextlib import contextmanager

from . import BulkOperationsMixin, ModuleStoreEnum
from .exceptions import ItemNotFoundError

# Things w/ these categories should never be marked as version=DRAFT
DIRECT_ONLY_CATEGORIES = ['course', 'chapter', 'sequential', 'about', 'static_tab', 'course_info']

log = logging.getLogger(__name__)


class BranchSettingMixin:
    """
    A mixin to manage a module store's branch setting.
    The order of override is (from higher precedence to lower):
       1. thread-specific setting temporarily set using the branch_setting contextmanager
       2. the return value of the branch_setting_func passed into this mixin's init method
       3. the default branch setting being ModuleStoreEnum.Branch.published_only
    """

    def __init__(self, *args, **kwargs):
        """
        :param branch_setting_func: a function that returns the default branch setting for this object.
            If not specified, ModuleStoreEnum.Branch.published_only is used as the default setting.
        """
        self.default_branch_setting_func = kwargs.pop(
            'branch_setting_func',
            lambda: ModuleStoreEnum.Branch.published_only
        )
        super().__init__(*args, **kwargs)

        # cache the branch setting on a local thread to support a multi-threaded environment
        self.thread_cache = threading.local()

    @contextmanager
    def branch_setting(self, branch_setting, course_id=None):  # pylint: disable=unused-argument
        """
        A context manager for temporarily setting a store's branch value on the current thread.
        """
        previous_thread_branch_setting = getattr(self.thread_cache, 'branch_setting', None)
        try:
            self.thread_cache.branch_setting = branch_setting
            yield
        finally:
            self.thread_cache.branch_setting = previous_thread_branch_setting

    def get_branch_setting(self, course_id=None):  # pylint: disable=unused-argument
        """
        Returns the current branch_setting on the store.

        Returns the thread-local setting, if set.
        Otherwise, returns the default value of the setting function set during the store's initialization.
        """
        # first check the thread-local cache
        thread_local_branch_setting = getattr(self.thread_cache, 'branch_setting', None)
        if thread_local_branch_setting:
            return thread_local_branch_setting
        else:
            # return the default value
            return self.default_branch_setting_func()


class ModuleStoreDraftAndPublished(BranchSettingMixin, BulkOperationsMixin, metaclass=ABCMeta):
    """
    A mixin for a read-write database backend that supports two branches, Draft and Published, with
    options to prefer Draft and fallback to Published.
    """

    @abstractmethod
    def delete_item(self, location, user_id, revision=None, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def get_parent_location(self, location, revision=None, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def has_changes(self, xblock):
        raise NotImplementedError

    @abstractmethod
    def publish(self, location, user_id):
        raise NotImplementedError

    @abstractmethod
    def unpublish(self, location, user_id):
        """
        Turn the published version into a draft, removing the published version.

        Raises: InvalidVersionError if called on a DIRECT_ONLY_CATEGORY
        """
        raise NotImplementedError

    @abstractmethod
    def revert_to_published(self, location, user_id):
        raise NotImplementedError

    @abstractmethod
    def has_published_version(self, xblock):
        raise NotImplementedError

    @abstractmethod
    def convert_to_draft(self, location, user_id):
        raise NotImplementedError

    @abstractmethod
    def import_xblock(self, user_id, course_key, block_type, block_id, fields=None, runtime=None, **kwargs):
        """
        Import the given xblock into the current branch setting: import completely overwrites any
        existing block of the same id.

        In ModuleStoreDraftAndPublished, importing a published block ensures that access from the draft
        will get a block (either the one imported or a preexisting one). See xml_importer
        """
        raise NotImplementedError

    def _flag_publish_event(self, course_key):
        """
        Wrapper around calls to fire the course_published signal
        Unless we're nested in an active bulk operation, this simply fires the signal
        otherwise a publish will be signalled at the end of the bulk operation

        Arguments:
            course_key - course_key to which the signal applies
        """
        if self.signal_handler:
            bulk_record = self._get_bulk_ops_record(course_key) if isinstance(self, BulkOperationsMixin) else None
            if bulk_record and bulk_record.active:
                bulk_record.has_publish_item = True
            else:
                # We remove the branch, because publishing always means copying from draft to published
                self.signal_handler.send("course_published", course_key=course_key.for_branch(None))

    def update_item_parent(self, item_location, new_parent_location, old_parent_location, user_id, insert_at=None):
        """
        Updates item's parent and removes it's reference from old parent.

        Arguments:
            item_location (BlockUsageLocator)    : Locator of item.
            new_parent_location (BlockUsageLocator)  : New parent block locator.
            old_parent_location (BlockUsageLocator)  : Old parent block locator.
            user_id (int)   : User id.
            insert_at (int) : Insert item at the particular index in new parent.

        Returns:
           BlockUsageLocator or None: Source item location if updated, otherwise None.
        """
        try:
            source_item = self.get_item(item_location)  # pylint: disable=no-member
            old_parent_item = self.get_item(old_parent_location)    # pylint: disable=no-member
            new_parent_item = self.get_item(new_parent_location)    # pylint: disable=no-member
        except ItemNotFoundError as exception:
            log.error('Unable to find the item : %s', str(exception))
            return

        # Remove item from the list of children of old parent.
        if source_item.location in old_parent_item.children:
            old_parent_item.children.remove(source_item.location)
            self.update_item(old_parent_item, user_id)  # pylint: disable=no-member
            log.info(
                '%s removed from %s children',
                str(source_item.location),
                str(old_parent_item.location)
            )

        # Add item to new parent at particular location.
        if source_item.location not in new_parent_item.children:
            if insert_at is not None:
                new_parent_item.children.insert(insert_at, source_item.location)
            else:
                new_parent_item.children.append(source_item.location)
            self.update_item(new_parent_item, user_id)  # pylint: disable=no-member
            log.info(
                '%s added to %s children',
                str(source_item.location),
                str(new_parent_item.location)
            )

        # Update parent attribute of the item block
        source_item.parent = new_parent_location
        self.update_item(source_item, user_id)  # pylint: disable=no-member
        log.info(
            '%s parent updated to %s',
            str(source_item.location),
            str(new_parent_item.location)
        )
        return source_item.location


class UnsupportedRevisionError(ValueError):
    """
    This error is raised if a method is called with an unsupported revision parameter.
    """
    def __init__(self, allowed_revisions=None):
        if not allowed_revisions:
            allowed_revisions = [
                None,
                ModuleStoreEnum.RevisionOption.published_only,
                ModuleStoreEnum.RevisionOption.draft_only
            ]
        super().__init__(f'revision not one of {allowed_revisions}')
