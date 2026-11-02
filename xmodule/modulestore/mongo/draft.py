"""
A ModuleStore that knows about a special version DRAFT. Blocks
marked as DRAFT are read in preference to blocks without the DRAFT
version by this ModuleStore (so, access to i4x://org/course/cat/name
returns the i4x://org/course/cat/name@draft object if that exists,
and otherwise returns i4x://org/course/cat/name).
"""


import logging

from opaque_keys.edx.locator import BlockUsageLocator

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.draft_and_published import DIRECT_ONLY_CATEGORIES, UnsupportedRevisionError
from xmodule.modulestore.exceptions import (
    InvalidBranchSetting,
    ItemNotFoundError
)
from xmodule.modulestore.mongo.base import (
    SORT_REVISION_FAVOR_DRAFT,
    MongoModuleStore,
    MongoRevisionKey,
    as_draft,
    as_published
)

log = logging.getLogger(__name__)


def wrap_draft(item):
    """
    Cleans the item's location and sets the `is_draft` attribute if needed.

    Sets `item.is_draft` to `True` if the item is DRAFT, and `False` otherwise.
    Sets the item's location to the non-draft location in either case.
    """
    item.is_draft = (item.location.branch == MongoRevisionKey.draft)
    item.location = item.location.replace(revision=MongoRevisionKey.published)
    return item


class DraftModuleStore(MongoModuleStore):
    """
    This mixin modifies a modulestore to give it draft semantics.
    Edits made to units are stored to locations that have the revision DRAFT.
    Reads are first read with revision DRAFT, and then fall back
    to the baseline revision only if DRAFT doesn't exist.

    This module store also includes functionality to promote DRAFT blocks (and their children)
    to published blocks.
    """
    def get_item(self, usage_key, revision=None, using_descriptor_system=None, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Returns an XModuleDescriptor instance for the item at usage_key.

        Args:
            usage_key: A :class:`.UsageKey` instance

            depth (int): An argument that some module stores may use to prefetch
                descendants of the queried blocks for more efficient results later
                in the request. The depth is counted in the number of calls to
                get_children() to cache.  None indicates to cache all descendants.

            revision:
                ModuleStoreEnum.RevisionOption.published_only - returns only the published item.
                ModuleStoreEnum.RevisionOption.draft_only - returns only the draft item.
                None - uses the branch setting as follows:
                    if branch setting is ModuleStoreEnum.Branch.published_only, returns only the published item.
                    if branch setting is ModuleStoreEnum.Branch.draft_preferred, returns either draft or published item,
                        preferring draft.

                Note: If the item is in DIRECT_ONLY_CATEGORIES, then returns only the PUBLISHED
                version regardless of the revision.

            using_descriptor_system (ModuleStoreRuntime): The existing runtime
                to add data to, and to load the XBlocks from.

        Raises:
            xmodule.modulestore.exceptions.InsufficientSpecificationError
            if any segment of the usage_key is None except revision

            xmodule.modulestore.exceptions.ItemNotFoundError if no object
            is found at that usage_key
        """
        def get_published():
            return wrap_draft(super(DraftModuleStore, self).get_item(  # lint-amnesty, pylint: disable=super-with-arguments
                usage_key, using_descriptor_system=using_descriptor_system,
                for_parent=kwargs.get('for_parent'),
            ))

        def get_draft():
            return wrap_draft(super(DraftModuleStore, self).get_item(  # lint-amnesty, pylint: disable=super-with-arguments
                as_draft(usage_key), using_descriptor_system=using_descriptor_system,
                for_parent=kwargs.get('for_parent')
            ))

        # return the published version if ModuleStoreEnum.RevisionOption.published_only is requested
        if revision == ModuleStoreEnum.RevisionOption.published_only:
            return get_published()

        # if the item is direct-only, there can only be a published version
        elif usage_key.block_type in DIRECT_ONLY_CATEGORIES:
            return get_published()

        # return the draft version (without any fallback to PUBLISHED) if DRAFT-ONLY is requested
        elif revision == ModuleStoreEnum.RevisionOption.draft_only:
            return get_draft()

        elif self.get_branch_setting() == ModuleStoreEnum.Branch.published_only:
            return get_published()

        elif revision is None:
            # could use a single query wildcarding revision and sorting by revision. would need to
            # use prefix form of to_deprecated_son
            try:
                # first check for a draft version
                return get_draft()
            except ItemNotFoundError:
                # otherwise, fall back to the published version
                return get_published()

        else:
            raise UnsupportedRevisionError()

    def has_item(self, usage_key, revision=None):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Returns True if location exists in this ModuleStore.

        Args:
            revision:
                ModuleStoreEnum.RevisionOption.published_only - checks for the published item only
                ModuleStoreEnum.RevisionOption.draft_only - checks for the draft item only
                None - uses the branch setting, as follows:
                    if branch setting is ModuleStoreEnum.Branch.published_only, checks for the published item only
                    if branch setting is ModuleStoreEnum.Branch.draft_preferred, checks whether draft or published item exists  # lint-amnesty, pylint: disable=line-too-long
        """
        def has_published():
            return super(DraftModuleStore, self).has_item(usage_key)  # lint-amnesty, pylint: disable=super-with-arguments

        def has_draft():
            return super(DraftModuleStore, self).has_item(as_draft(usage_key))  # lint-amnesty, pylint: disable=super-with-arguments

        if revision == ModuleStoreEnum.RevisionOption.draft_only:
            return has_draft()
        elif (
                revision == ModuleStoreEnum.RevisionOption.published_only or
                self.get_branch_setting() == ModuleStoreEnum.Branch.published_only
        ):
            return has_published()
        elif revision is None:
            key = usage_key.to_deprecated_son(prefix='_id.')
            del key['_id.revision']
            return self.collection.count_documents(key) > 0
        else:
            raise UnsupportedRevisionError()

    def delete_course(self, course_key, user_id):  # lint-amnesty, pylint: disable=arguments-differ
        """
        :param course_key: which course to delete
        :param user_id: id of the user deleting the course
        """
        # Note: does not need to inform the bulk mechanism since after the course is deleted,
        # it can't calculate inheritance anyway. Nothing is there to be dirty.
        # delete the assets
        super().delete_course(course_key, user_id)  # lint-amnesty, pylint: disable=super-with-arguments

        # delete all of the db records for the course
        course_query = self._course_key_to_son(course_key)
        self.collection.delete_many(course_query)
        self.delete_all_asset_metadata(course_key, user_id)

        self._emit_course_deleted_signal(course_key)

    def clone_course(self, source_course_id, dest_course_id, user_id, fields=None, **kwargs):
        """
        Only called if cloning within this store or if env doesn't set up mixed.
        * copy the courseware
        """
        raise NotImplementedError

    def _get_raw_parent_locations(self, location, key_revision):
        """
        Get the parents but don't unset the revision in their locations.

        Intended for internal use but not restricted.

        Args:
            location (UsageKey): assumes the location's revision is None; so, uses revision keyword solely
            key_revision:
                MongoRevisionKey.draft - return only the draft parent
                MongoRevisionKey.published - return only the published parent
                ModuleStoreEnum.RevisionOption.all - return both draft and published parents
        """
        _verify_revision_is_published(location)

        # create a query to find all items in the course that have the given location listed as a child
        query = self._course_key_to_son(location.course_key)
        query['definition.children'] = str(location)

        # find all the items that satisfy the query
        parents = self.collection.find(query, {'_id': True}, sort=[SORT_REVISION_FAVOR_DRAFT])

        # return only the parent(s) that satisfy the request
        return [
            BlockUsageLocator._from_deprecated_son(parent['_id'], location.course_key.run)  # lint-amnesty, pylint: disable=protected-access
            for parent in parents
            if (
                # return all versions of the parent if revision is ModuleStoreEnum.RevisionOption.all
                key_revision == ModuleStoreEnum.RevisionOption.all or
                # return this parent if it's direct-only, regardless of which revision is requested
                parent['_id']['category'] in DIRECT_ONLY_CATEGORIES or
                # return this parent only if its revision matches the requested one
                parent['_id']['revision'] == key_revision
            )
        ]

    def get_parent_location(self, location, revision=None, **kwargs):
        '''
        Returns the given location's parent location in this course.

        Returns: version agnostic locations (revision always None) as per the rest of mongo.

        Args:
            revision:
                None - uses the branch setting for the revision
                ModuleStoreEnum.RevisionOption.published_only
                    - return only the PUBLISHED parent if it exists, else returns None
                ModuleStoreEnum.RevisionOption.draft_preferred
                    - return either the DRAFT or PUBLISHED parent, preferring DRAFT, if parent(s) exists,
                        else returns None

                    If the draft has a different parent than the published, it returns only
                    the draft's parent. Because parents don't record their children's revisions, this
                    is actually a potentially fragile deduction based on parent type. If the parent type
                    is not DIRECT_ONLY, then the parent revision must be DRAFT.
                    Only xml_exporter currently uses this argument. Others should avoid it.
        '''
        if revision is None:
            revision = ModuleStoreEnum.RevisionOption.published_only \
                if self.get_branch_setting() == ModuleStoreEnum.Branch.published_only \
                else ModuleStoreEnum.RevisionOption.draft_preferred
        return super().get_parent_location(location, revision, **kwargs)  # lint-amnesty, pylint: disable=super-with-arguments

    def create_xblock(self, runtime, course_key, block_type, block_id=None, fields=None, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Create the new xblock but don't save it. Returns the new block with a draft locator if
        the category allows drafts. If the category does not allow drafts, just creates a published block.

        :param location: a Location--must have a category
        :param definition_data: can be empty. The initial definition_data for the kvs
        :param metadata: can be empty, the initial metadata for the kvs
        :param runtime: if you already have an xmodule from the course, the xmodule.runtime value
        :param fields: a dictionary of field names and values for the new xmodule
        """
        new_block = super().create_xblock(  # lint-amnesty, pylint: disable=super-with-arguments
            runtime, course_key, block_type, block_id, fields, **kwargs
        )
        new_block.location = self.for_branch_setting(new_block.location)
        return wrap_draft(new_block)

    def get_items(self, course_key, revision=None, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Performance Note: This is generally a costly operation, but useful for wildcard searches.

        Returns:
            list of XModuleDescriptor instances for the matching items within the course with
            the given course_key

        NOTE: don't use this to look for courses as the course_key is required. Use get_courses instead.

        Args:
            course_key (CourseKey): the course identifier
            revision:
                ModuleStoreEnum.RevisionOption.published_only - returns only Published items
                ModuleStoreEnum.RevisionOption.draft_only - returns only Draft items
                None - uses the branch setting, as follows:
                    if the branch setting is ModuleStoreEnum.Branch.published_only,
                        returns only Published items
                    if the branch setting is ModuleStoreEnum.Branch.draft_preferred,
                        returns either Draft or Published, preferring Draft items.
        """
        def base_get_items(key_revision):
            return super(DraftModuleStore, self).get_items(course_key, key_revision=key_revision, **kwargs)  # lint-amnesty, pylint: disable=super-with-arguments

        def draft_items():
            return [wrap_draft(item) for item in base_get_items(MongoRevisionKey.draft)]

        def published_items(draft_items):
            # filters out items that are not already in draft_items
            draft_items_locations = {item.location for item in draft_items}
            return [
                item for item in
                base_get_items(MongoRevisionKey.published)
                if item.location not in draft_items_locations
            ]

        if revision == ModuleStoreEnum.RevisionOption.draft_only:
            return draft_items()
        elif revision == ModuleStoreEnum.RevisionOption.published_only \
                or self.get_branch_setting() == ModuleStoreEnum.Branch.published_only:
            return published_items([])
        elif revision is None:
            draft_items = draft_items()
            return draft_items + published_items(draft_items)
        else:
            raise UnsupportedRevisionError()

    def _breadth_first(self, function, root_usages):
        """
        Get the root_usage from the db and do a depth first scan. Call the function on each. The
        function should return a list of SON for any next tier items to process and should
        add the SON for any items to delete to the to_be_deleted array.

        At the end, it mass deletes the to_be_deleted items and refreshes the cached metadata inheritance
        tree.

        :param function: a function taking (item, to_be_deleted) and returning [SON] for next_tier invocation
        :param root_usages: the usage keys for the root items (ensure they have the right revision set)
        """
        if len(root_usages) == 0:
            return
        to_be_deleted = []

        def _internal(tier):
            next_tier = []
            tier_items = self.collection.find({'_id': {'$in': tier}})
            for current_entry in tier_items:
                next_tier.extend(function(current_entry, to_be_deleted))

            if len(next_tier) > 0:
                _internal(next_tier)

        _internal([root_usage.to_deprecated_son() for root_usage in root_usages])
        if len(to_be_deleted) > 0:
            bulk_record = self._get_bulk_ops_record(root_usages[0].course_key)
            bulk_record.dirty = True
            self.collection.delete_many({'_id': {'$in': to_be_deleted}})

    def update_parent_if_moved(self, original_parent_location, published_version, delete_draft_only, user_id):
        """
        Update parent of an item if it has moved.

        Arguments:
            original_parent_location (BlockUsageLocator)  : Original parent block locator.
            published_version (dict)   : Published version of the block.
            delete_draft_only (function)    : A callback function to delete draft children if it was moved.
            user_id (int)   : User id
        """
        raise NotImplementedError

    def has_published_version(self, xblock):
        """
        Returns True if this xblock has an existing published version regardless of whether the
        published version is up to date.
        """
        if getattr(xblock, 'is_draft', False):
            published_xblock_location = as_published(xblock.location)
            try:
                xblock.runtime.lookup_item(published_xblock_location)
            except ItemNotFoundError:
                return False
        return True

    def _verify_branch_setting(self, expected_branch_setting):
        """
        Raises an exception if the current branch setting does not match the expected branch setting.
        """
        actual_branch_setting = self.get_branch_setting()
        if actual_branch_setting != expected_branch_setting:
            raise InvalidBranchSetting(
                expected_setting=expected_branch_setting,
                actual_setting=actual_branch_setting
            )


def _verify_revision_is_published(location):
    """
    Asserts that the revision set on the given location is MongoRevisionKey.published
    """
    assert location.branch == MongoRevisionKey.published
