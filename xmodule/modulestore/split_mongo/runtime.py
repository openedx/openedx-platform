# lint-amnesty, pylint: disable=missing-module-docstring

import logging
import sys
import weakref

from crum import get_current_user
from fs.osfs import OSFS
from lazy import lazy
from opaque_keys.edx.locator import BlockUsageLocator, DefinitionLocator, LocalId
from xblock.fields import ScopeIds
from xblock.runtime import KeyValueStore, KvsFieldData

from xmodule.error_block import ErrorBlock
from xmodule.errortracker import exc_info_to_str
from xmodule.library_tools import LegacyLibraryToolsService
from xmodule.modulestore import BlockData
from xmodule.modulestore.edit_info import EditInfoRuntimeMixin
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.modulestore.inheritance import InheritanceMixin, inheriting_field_data
from xmodule.modulestore.split_mongo import BlockKey, CourseEnvelope
from xmodule.modulestore.split_mongo.definition_lazy_loader import DefinitionLazyLoader
from xmodule.modulestore.split_mongo.id_manager import SplitMongoIdManager
from xmodule.modulestore.split_mongo.split_mongo_kvs import SplitMongoKVS
from xmodule.util.misc import get_library_or_course_attribute
from xmodule.x_module import XModuleMixin, ModuleStoreRuntime

log = logging.getLogger(__name__)


class SplitModuleStoreRuntime(ModuleStoreRuntime, EditInfoRuntimeMixin):  # pylint: disable=abstract-method
    """
    A system that has a cache of a course version's json that it will use to load blocks
    from, with a backup of calling to the underlying modulestore for more data.

    Computes the settings (nee 'metadata') inheritance upon creation.
    """
    def __init__(self, modulestore, course_entry, default_class, module_data, lazy, **kwargs):  # lint-amnesty, pylint: disable=redefined-outer-name
        """
        Computes the settings inheritance and sets up the cache.

        modulestore: the module store that can be used to retrieve additional blocks

        course_entry: the originally fetched enveloped course_structure w/ branch and course id info.
        Callers to _load_item provide an override but that function ignores the provided structure and
        only looks at the branch and course id

        module_data: a dict mapping Location -> json that was cached from the
            underlying modulestore
        """
        # needed by capa_problem (as runtime.resources_fs via this.resources_fs)
        course_library = get_library_or_course_attribute(course_entry.course_key)
        if course_library:
            root = modulestore.fs_root / course_entry.course_key.org / course_library / course_entry.course_key.run  # lint-amnesty, pylint: disable=line-too-long
        else:
            root = modulestore.fs_root / str(course_entry.structure['_id'])
        root.makedirs_p()  # create directory if it doesn't exist

        id_manager = SplitMongoIdManager(self)
        kwargs.setdefault('id_reader', id_manager)
        kwargs.setdefault('id_generator', id_manager)

        super().__init__(
            load_item=self._load_item,
            resources_fs=OSFS(root),
            **kwargs
        )
        self.modulestore = modulestore
        self.course_entry = course_entry
        # set course_id attribute to avoid problems with subsystems that expect
        # it here. (grading, for example)
        self.course_id = course_entry.course_key
        self.lazy = lazy
        self.module_data = module_data
        self.default_class = default_class
        self.local_modules = {}

        user = get_current_user()
        user_id = user.id if user else None
        self._services['library_tools'] = LegacyLibraryToolsService(modulestore, user_id=user_id)

        # Cache of block field datas, keyed by the XBlock instance (since the ScopeId changes!)
        self.block_field_datas = weakref.WeakKeyDictionary()

    @lazy
    def _parent_map(self):  # lint-amnesty, pylint: disable=missing-function-docstring
        parent_map = {}
        for block_key, block in self.course_entry.structure['blocks'].items():
            for child in block.fields.get('children', []):
                parent_map[child] = block_key
        return parent_map

    def _load_item(self, usage_key, course_entry_override=None, **kwargs):
        """
        Instantiate the xblock fetching it either from the cache or from the structure

        :param course_entry_override: the course_info with the course_key to use (defaults to cached)
        """
        # usage_key is either a UsageKey or just the block_key. if a usage_key,
        if isinstance(usage_key, BlockUsageLocator):

            # trust the passed in key to know the caller's expectations of which fields are filled in.
            # particularly useful for strip_keys so may go away when we're version aware
            course_key = usage_key.course_key

            if isinstance(usage_key.block_id, LocalId):
                try:
                    return self.local_modules[usage_key]
                except KeyError:
                    raise ItemNotFoundError  # lint-amnesty, pylint: disable=raise-missing-from
            else:
                block_key = BlockKey.from_usage_key(usage_key)
                version_guid = self.course_entry.course_key.version_guid
        else:
            block_key = usage_key

            course_info = course_entry_override or self.course_entry
            course_key = course_info.course_key
            version_guid = course_key.version_guid

        # look in cache
        cached_block = self.modulestore.get_cached_block(course_key, version_guid, block_key)
        if cached_block:
            return cached_block

        block_data = self.get_module_data(block_key, course_key)

        class_ = self.load_block_type(block_data.block_type)
        block = self.xblock_from_json(class_, course_key, block_key, block_data, course_entry_override, **kwargs)

        # TODO Once TNL-5092 is implemented, we can expose the course version
        # information within the key identifier of the block.  Until then, set
        # the course_version as a field on the returned block so higher layers
        # can use it when needed.
        block.course_version = version_guid

        self.modulestore.cache_block(course_key, version_guid, block_key, block)
        return block

    def get_module_data(self, block_key, course_key):
        """
        Get block from module_data adding it to module_data if it's not already there but is in the structure

        Raises:
            ItemNotFoundError if block is not in the structure
        """
        json_data = self.module_data.get(block_key)
        if json_data is None:
            # deeper than initial descendant fetch or doesn't exist
            self.modulestore.cache_items(self, [block_key], course_key, lazy=self.lazy)
            json_data = self.module_data.get(block_key)
            if json_data is None:
                raise ItemNotFoundError(block_key)

        return json_data

    # xblock's runtime does not always pass enough contextual information to figure out
    # which named container (course x branch) or which parent is requesting an item. Because split allows
    # a many:1 mapping from named containers to structures and because item's identities encode
    # context as well as unique identity, this function must sometimes infer whether the access is
    # within an unspecified named container. In most cases, course_entry_override will give the
    # explicit context; however, runtime.get_block(), e.g., does not. HOWEVER, there are simple heuristics
    # which will work 99.999% of the time: a runtime is thread & even context specific. The likelihood that
    # the thread is working with more than one named container pointing to the same specific structure is
    # low; thus, the course_entry is most likely correct. If the thread is looking at > 1 named container
    # pointing to the same structure, the access is likely to be chunky enough that the last known container
    # is the intended one when not given a course_entry_override; thus, the caching of the last branch/course id.
    def xblock_from_json(self, class_, course_key, block_key, block_data, course_entry_override=None, **kwargs):
        """
        Instantiate an XBlock of the specified class, using the provided data.
        """
        if course_entry_override is None:
            course_entry_override = self.course_entry
        else:
            # most recent retrieval is most likely the right one for next caller (see comment above fn)
            self.course_entry = CourseEnvelope(course_entry_override.course_key, self.course_entry.structure)

        # If no usage id is provided, generate an in-memory id
        if block_key is None:
            block_key = BlockKey(block_data.block_type, LocalId())

        # Construct the Block Usage Locator:
        block_locator = course_key.make_usage_key(block_type=block_key.type, block_id=block_key.id)

        # If no definition id is provided, generate an in-memory id
        definition_id = block_data.definition or LocalId()

        try:
            block = self.construct_xblock_from_class(
                class_,
                ScopeIds(None, block_key.type, definition_id, block_locator),
                for_parent=kwargs.get('for_parent'),
                # Pass this tuple on for use by _init_field_data_for_block() when field data is initialized.
                cds_init_args=(block_data, definition_id, kwargs.get("field_decorator")),
                # Passing field_data here is deprecated, so we don't. Get it via block.service(block, "field-data").
                # https://github.com/openedx/XBlock/blob/e89cbc5/xblock/mixins.py#L200-L207
            )
        except Exception:  # pylint: disable=broad-except
            log.warning("Failed to load descriptor", exc_info=True)
            return ErrorBlock.from_json(
                block_data,
                self,
                course_entry_override.course_key.make_usage_key(
                    block_type='error',
                    block_id=block_key.id
                ),
                error_msg=exc_info_to_str(sys.exc_info())
            )

        edit_info = block_data.edit_info
        block._edited_by = edit_info.edited_by  # pylint: disable=protected-access
        block._edited_on = edit_info.edited_on  # pylint: disable=protected-access
        block.previous_version = edit_info.previous_version
        block.update_version = edit_info.update_version
        block.source_version = edit_info.source_version
        block.definition_locator = DefinitionLocator(block_key.type, definition_id)

        # If this is an in-memory block, store it in this system
        if isinstance(block_locator.block_id, LocalId):
            self.local_modules[block_locator] = block

        return block

    def service(self, block, service_name):
        """
        Return a service, or None.
        Services are objects implementing arbitrary other interfaces.
        """
        # Implement field data service:
        if service_name in ('field-data', 'field-data-unbound'):
            if hasattr(block, "_bound_field_data") and service_name != "field-data-unbound":
                # Return the user-specific wrapped field data that gets set onto the block during XModule.bind_for_user
                # This complexity is due to XModule heritage. If we didn't load the block as one step, then sometimes
                # "rebind" it to be user-specific as a later step, we could load the field data and overrides at once.
                return block._bound_field_data  # pylint: disable=protected-access
            if block not in self.block_field_datas:
                try:
                    self._init_field_data_for_block(block)
                except:
                    # Don't try again pointlessly every time another field is accessed
                    self.block_field_datas[block] = None
                    raise
            return self.block_field_datas[block]
        return super().service(block, service_name)

    def _init_field_data_for_block(self, block):
        """
        Initialize the field-data service for the given block.
        """
        block_key = BlockKey.from_usage_key(block.scope_ids.usage_id)
        course_key = block.scope_ids.usage_id.context_key
        try:
            (block_data, definition_id, field_decorator) = block.get_cds_init_args()
        except KeyError:
            # This block was not instantiate via xblock_from_json()/modulestore but rather via the "direct" XBlock API
            # such as using construct_xblock_from_class(). It probably doesn't yet exist in modulestore.
            block_data = BlockData()
            definition_id = LocalId()
            field_decorator = None
        class_ = self.load_block_type(block.scope_ids.block_type)
        convert_fields = lambda field: self.modulestore.convert_references_to_keys(
            course_key, class_, field, self.course_entry.structure['blocks'],
        )

        converted_fields = convert_fields(block_data.fields)
        converted_defaults = convert_fields(block_data.defaults)
        if block_key in self._parent_map:
            parent_key = self._parent_map[block_key]
            parent = course_key.make_usage_key(parent_key.type, parent_key.id)
        else:
            parent = None

        aside_fields = None

        # for the situation if block_data has no asides attribute
        # (in case it was taken from memcache)
        try:
            if block_data.asides:
                aside_fields = {block_key.type: {}}
                for aside in block_data.asides:
                    aside_fields[block_key.type].update(aside['fields'])
        except AttributeError:
            pass

        if not isinstance(definition_id, LocalId) and not block_data.definition_loaded:
            definition_loader = DefinitionLazyLoader(
                self.modulestore,
                course_key,
                block_key.type,
                definition_id,
                convert_fields,
            )
        else:
            definition_loader = None

        kvs = SplitMongoKVS(
            definition_loader,
            converted_fields,
            converted_defaults,
            parent=parent,
            aside_fields=aside_fields,
            field_decorator=field_decorator,
        )

        if InheritanceMixin in self.modulestore.xblock_mixins:
            field_data = inheriting_field_data(kvs)
        else:
            field_data = KvsFieldData(kvs)

        # Save the field_data now, because some of the wrappers will try to read fields from the block while they
        # determine if they want to wrap the field_data or not.
        self.block_field_datas[block] = field_data
        # Now add in any wrappers that want to be added:
        for wrapper in self.modulestore.xblock_field_data_wrappers:
            self.block_field_datas[block] = wrapper(block, self.block_field_datas[block])

        # Note: user-specific wrappers like LmsFieldData or OverrideFieldData do not get set here. They get set later
        # during bind_for_student(), which wraps the field-data and sets block._bound_field_data. Calling
        # runtime.service(block, "field-data") or block._field_data (deprecated) will load block._bound_field_data if
        # the block has been bound to a user, otherwise it returns the wrapped field data we just created above.

    def get_edited_by(self, xblock):
        """
        See :meth: cms.lib.xblock.runtime.EditInfoRuntimeMixin.get_edited_by
        """
        return xblock._edited_by  # lint-amnesty, pylint: disable=protected-access

    def get_edited_on(self, xblock):
        """
        See :class: cms.lib.xblock.runtime.EditInfoRuntimeMixin
        """
        return xblock._edited_on  # lint-amnesty, pylint: disable=protected-access

    def get_subtree_edited_by(self, xblock):
        """
        See :class: cms.lib.xblock.runtime.EditInfoRuntimeMixin
        """
        # pylint: disable=protected-access
        if not hasattr(xblock, '_subtree_edited_by'):
            block_data = self.module_data[BlockKey.from_usage_key(xblock.location)]
            if block_data.edit_info._subtree_edited_by is None:
                self._compute_subtree_edited_internal(
                    block_data, xblock.location.course_key
                )
            xblock._subtree_edited_by = block_data.edit_info._subtree_edited_by

        return xblock._subtree_edited_by

    def get_subtree_edited_on(self, xblock):
        """
        See :class: cms.lib.xblock.runtime.EditInfoRuntimeMixin
        """
        # pylint: disable=protected-access
        if not hasattr(xblock, '_subtree_edited_on'):
            block_data = self.module_data[BlockKey.from_usage_key(xblock.location)]
            if block_data.edit_info._subtree_edited_on is None:
                self._compute_subtree_edited_internal(
                    block_data, xblock.location.course_key
                )
            xblock._subtree_edited_on = block_data.edit_info._subtree_edited_on

        return xblock._subtree_edited_on

    def get_published_by(self, xblock):
        """
        See :class: cms.lib.xblock.runtime.EditInfoRuntimeMixin
        """
        if not hasattr(xblock, '_published_by'):
            self.modulestore.compute_published_info_internal(xblock)

        return getattr(xblock, '_published_by', None)

    def get_published_on(self, xblock):
        """
        See :class: cms.lib.xblock.runtime.EditInfoRuntimeMixin
        """
        if not hasattr(xblock, '_published_on'):
            self.modulestore.compute_published_info_internal(xblock)

        return getattr(xblock, '_published_on', None)

    def _compute_subtree_edited_internal(self, block_data, course_key):
        """
        Recurse the subtree finding the max edited_on date and its corresponding edited_by. Cache it.
        """
        # pylint: disable=protected-access
        max_date = block_data.edit_info.edited_on
        max_date_by = block_data.edit_info.edited_by

        for child in block_data.fields.get('children', []):
            child_data = self.get_module_data(BlockKey(*child), course_key)
            if block_data.edit_info._subtree_edited_on is None:
                self._compute_subtree_edited_internal(child_data, course_key)
            if child_data.edit_info._subtree_edited_on > max_date:
                max_date = child_data.edit_info._subtree_edited_on
                max_date_by = child_data.edit_info._subtree_edited_by

        block_data.edit_info._subtree_edited_on = max_date
        block_data.edit_info._subtree_edited_by = max_date_by

    def get_aside_of_type(self, block, aside_type):
        """
        See `runtime.Runtime.get_aside_of_type`

        This override adds the field data from the block to the aside
        """
        asides_cached = block.get_asides() if isinstance(block, XModuleMixin) else None
        if asides_cached:
            for aside in asides_cached:
                if aside.scope_ids.block_type == aside_type:
                    return aside

        new_aside = super().get_aside_of_type(block, aside_type)
        new_aside._field_data = block._field_data  # pylint: disable=protected-access

        for key, _ in new_aside.fields.items():
            if isinstance(key, KeyValueStore.Key) and block._field_data.has(new_aside, key):  # pylint: disable=protected-access
                try:
                    value = block._field_data.get(new_aside, key)  # pylint: disable=protected-access
                except KeyError:
                    pass
                else:
                    setattr(new_aside, key, value)

        block.add_aside(new_aside)
        return new_aside
