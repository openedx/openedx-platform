"""
MixedModuleStore allows for aggregation between multiple modulestores.

In this way, courses can be served up via either SplitMongoModuleStore or MongoModuleStore.

"""
import functools
import itertools
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import LibraryLocator
from openedx_events.content_authoring.data import CourseData, XBlockData
from openedx_events.content_authoring.signals import (
    COURSE_CREATED,
    XBLOCK_CREATED,
    XBLOCK_DELETED,
    XBLOCK_PUBLISHED,
    XBLOCK_UPDATED,
)

from xmodule.assetstore import AssetMetadata

from . import XMODULE_FIELDS_WITH_USAGE_KEYS, ModuleStoreWriteBase
from .draft_and_published import ModuleStoreDraftAndPublished
from .exceptions import DuplicateCourseError, ItemNotFoundError

log = logging.getLogger(__name__)


def strip_key(func):
    """
    A decorator for stripping version and branch information from return values that are, or contain, UsageKeys or
    CourseKeys.
    Additionally, the decorated function is called with an optional 'field_decorator' parameter that can be used
    to strip any location(-containing) fields, which are not directly returned by the function.

    The behavior can be controlled by passing 'remove_version' and 'remove_branch' booleans to the decorated
    function's kwargs.
    """
    @functools.wraps(func)
    def inner(*args, **kwargs):
        """
        Supported kwargs:
            remove_version - If True, calls 'version_agnostic' on all return values, including those in lists and dicts.
            remove_branch - If True, calls 'for_branch(None)' on all return values, including those in lists and dicts.
            Note: The 'field_decorator' parameter passed to the decorated function is a function that honors the
            values of these kwargs.
        """

        # remove version and branch, by default
        rem_vers = kwargs.pop('remove_version', True)
        rem_branch = kwargs.pop('remove_branch', True)

        # helper function for stripping individual values
        def strip_key_func(val):
            """
            Strips the version and branch information according to the settings of rem_vers and rem_branch.
            Recursively calls this function if the given value has a 'location' attribute.
            """
            retval = val
            if rem_vers and hasattr(retval, 'version_agnostic'):
                retval = retval.version_agnostic()
            if rem_branch and hasattr(retval, 'for_branch'):
                retval = retval.for_branch(None)
            for field_name in XMODULE_FIELDS_WITH_USAGE_KEYS:
                if hasattr(retval, field_name):
                    setattr(retval, field_name, strip_key_func(getattr(retval, field_name)))
            return retval

        # function for stripping both, collection of, and individual, values
        def strip_key_collection(field_value):
            """
            Calls strip_key_func for each element in the given value.
            """
            if rem_vers or rem_branch:
                if isinstance(field_value, list):
                    field_value = [strip_key_func(fv) for fv in field_value]
                elif isinstance(field_value, dict):
                    for key, val in field_value.items():
                        field_value[key] = strip_key_func(val)
                else:
                    field_value = strip_key_func(field_value)
            return field_value

        # call the decorated function
        retval = func(field_decorator=strip_key_collection, *args, **kwargs)  # noqa: B026

        # strip the return value
        return strip_key_collection(retval)

    return inner


def prepare_asides(func):
    """
    A decorator to handle optional asides param
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        """
        Supported kwargs:
            asides - list with connected asides data for the passed block
        """
        if 'asides' in kwargs:
            kwargs['asides'] = prepare_asides_to_store(kwargs['asides'])
        return func(*args, **kwargs)
    return wrapper


def prepare_asides_to_store(asides_source):
    """
    Convert Asides Xblocks objects to the list of dicts (to store this information in MongoDB)
    """
    asides = None
    if asides_source:
        asides = []
        for asd in asides_source:
            aside_fields = {}
            for asd_field_key, asd_field_val in asd.fields.items():
                aside_fields[asd_field_key] = asd_field_val.read_from(asd)
            asides.append({
                'aside_type': asd.scope_ids.block_type,
                'fields': aside_fields
            })
    return asides


class MixedModuleStore(ModuleStoreDraftAndPublished, ModuleStoreWriteBase):
    """
    ModuleStore knows how to route requests to the right persistence ms
    """

    def __init__(
            self,
            contentstore,
            mappings,
            stores,
            i18n_service=None,
            fs_service=None,
            user_service=None,
            create_modulestore_instance=None,
            signal_handler=None,
            **kwargs
    ):
        """
        Initialize a MixedModuleStore. Here we look into our passed in kwargs which should be a
        collection of other modulestore configuration information
        """
        super().__init__(contentstore, **kwargs)

        if create_modulestore_instance is None:
            raise ValueError('MixedModuleStore constructor must be passed a create_modulestore_instance function')

        self.modulestores = []
        self.mappings = {}

        for course_id, store_name in mappings.items():
            try:
                self.mappings[CourseKey.from_string(course_id)] = store_name
            except InvalidKeyError:
                log.exception("Invalid MixedModuleStore configuration. Unable to parse course_id %r", course_id)
                continue

        for store_settings in stores:
            key = store_settings['NAME']
            store = create_modulestore_instance(
                store_settings['ENGINE'],
                self.contentstore,
                store_settings.get('DOC_STORE_CONFIG', {}),
                store_settings.get('OPTIONS', {}),
                i18n_service=i18n_service,
                fs_service=fs_service,
                user_service=user_service,
                signal_handler=signal_handler,
            )
            # replace all named pointers to the store into actual pointers
            for course_key, store_name in self.mappings.items():
                if store_name == key:
                    self.mappings[course_key] = store
            self.modulestores.append(store)

    def _clean_locator_for_mapping(self, locator):
        """
        In order for mapping to work, the locator must be minimal--no version, no branch--
        as we never store one version or one branch in one ms and another in another ms.

        :param locator: the CourseKey
        """
        if hasattr(locator, 'version_agnostic'):
            locator = locator.version_agnostic()
        if hasattr(locator, 'branch'):
            locator = locator.replace(branch=None)
        return locator

    def _get_modulestore_for_courselike(self, locator=None):
        """
        For a given locator, look in the mapping table and see if it has been pinned
        to a particular modulestore

        If locator is None, returns the first (ordered) store as the default
        """
        if locator is not None:
            locator = self._clean_locator_for_mapping(locator)
            mapping = self.mappings.get(locator, None)
            if mapping is not None:
                return mapping
            else:
                if isinstance(locator, LibraryLocator):
                    has_locator = lambda store: hasattr(store, 'has_library') and store.has_library(locator)
                else:
                    has_locator = lambda store: store.has_course(locator)
                for store in self.modulestores:
                    if has_locator(store):
                        self.mappings[locator] = store
                        return store

        # return the default store
        return self.default_modulestore

    def _get_modulestore_by_type(self, modulestore_type):
        """
        This method should only really be used by tests and migration scripts when necessary.
        Returns the module store as requested by type.  The type can be a value from ModuleStoreEnum.Type.
        """
        for store in self.modulestores:
            if store.get_modulestore_type() == modulestore_type:
                return store
        return None

    def fill_in_run(self, course_key):
        """
        Some course_keys are used without runs. This function calls the corresponding
        fill_in_run function on the appropriate modulestore.
        """
        store = self._get_modulestore_for_courselike(course_key)
        if not hasattr(store, 'fill_in_run'):
            return course_key
        return store.fill_in_run(course_key)

    def has_item(self, usage_key, **kwargs):
        """
        Does the course include the xblock who's id is reference?
        """
        store = self._get_modulestore_for_courselike(usage_key.course_key)
        return store.has_item(usage_key, **kwargs)

    @strip_key
    def get_item(self, usage_key, depth=0, **kwargs):  # pylint: disable=arguments-differ
        """
        see parent doc
        """
        store = self._get_modulestore_for_courselike(usage_key.course_key)
        return store.get_item(usage_key, depth, **kwargs)

    @strip_key
    def get_items(self, course_key, **kwargs):  # pylint: disable=arguments-differ
        """
        Returns:
            list of XModuleDescriptor instances for the matching items within the course with
            the given course_key

        NOTE: don't use this to look for courses
        as the course_key is required. Use get_courses.

        Args:
            course_key (CourseKey): the course identifier
            kwargs:
                settings (dict): fields to look for which have settings scope. Follows same syntax
                    and rules as kwargs below
                content (dict): fields to look for which have content scope. Follows same syntax and
                    rules as kwargs below.
                qualifiers (dict): what to look for within the course.
                    Common qualifiers are ``category`` or any field name. if the target field is a list,
                    then it searches for the given value in the list not list equivalence.
                    Substring matching pass a regex object.
                    For some modulestores, ``name`` is another commonly provided key (Location based stores)
                    For some modulestores,
                    you can search by ``edited_by``, ``edited_on`` providing either a datetime for == (probably
                    useless) or a function accepting one arg to do inequality
        """
        if not isinstance(course_key, CourseKey):
            raise Exception("Must pass in a course_key when calling get_items()")

        store = self._get_modulestore_for_courselike(course_key)
        return store.get_items(course_key, **kwargs)

    @strip_key
    def get_course_summaries(self, **kwargs):
        """
        Returns a list containing the course information in CourseSummary objects.
        Information contains `location`, `display_name`, `locator` of the courses in this modulestore.
        """
        course_summaries = {}
        for store in self.modulestores:
            for course_summary in store.get_course_summaries(**kwargs):
                course_id = self._clean_locator_for_mapping(locator=course_summary.id)

                # Check if course is indeed unique. Save it in result if unique
                if course_id in course_summaries:
                    log.warning(
                        "Modulestore %s have duplicate courses %s; skipping from result.", store, course_id
                    )
                else:
                    course_summaries[course_id] = course_summary
        return list(course_summaries.values())

    @strip_key
    def get_courses(self, **kwargs):
        '''
        Returns a list containing the top level XModuleDescriptors of the courses in this modulestore.
        '''
        courses = {}
        for store in self.modulestores:
            # filter out ones which were fetched from earlier stores but locations may not be ==
            for course in store.get_courses(**kwargs):
                course_id = self._clean_locator_for_mapping(course.id)
                if course_id not in courses:
                    # course is indeed unique. save it in result
                    courses[course_id] = course
        return list(courses.values())

    def get_library_keys(self):
        """
        Returns a list of all unique content library keys in the mixed
        modulestore.

        Returns: list[LibraryLocator]
        """
        all_library_keys = set()
        for store in self.modulestores:
            if not hasattr(store, 'get_library_keys'):
                continue
            all_library_keys |= {
                self._clean_locator_for_mapping(library_key)
                for library_key in store.get_library_keys()
            }
        return list(all_library_keys)

    @strip_key
    def get_library_summaries(self, **kwargs):
        """
        Returns a list of LegacyLibrarySummary objects.
        Information contains `location`, `display_name`, `locator` of the libraries in this modulestore.
        """
        library_summaries = {}
        for store in self.modulestores:
            if not hasattr(store, 'get_libraries'):
                continue
            # fetch library summaries and filter out any duplicated entry across/within stores
            for library_summary in store.get_library_summaries(**kwargs):
                library_id = self._clean_locator_for_mapping(library_summary.location)
                if library_id not in library_summaries:
                    library_summaries[library_id] = library_summary
        return list(library_summaries.values())

    @strip_key
    def get_libraries(self, **kwargs):
        """
        Returns a list containing the top level XBlock of the libraries (LibraryRoot) in this modulestore.
        """
        libraries = {}
        for store in self.modulestores:
            if not hasattr(store, 'get_libraries'):
                continue
            # filter out ones which were fetched from earlier stores but locations may not be ==
            for library in store.get_libraries(**kwargs):
                library_id = self._clean_locator_for_mapping(library.location)
                if library_id not in libraries:
                    # library is indeed unique. save it in result
                    libraries[library_id] = library
        return list(libraries.values())

    def make_course_key(self, org, course, run):
        """
        Return a valid :class:`~opaque_keys.edx.keys.CourseKey` for this modulestore
        that matches the supplied `org`, `course`, and `run`.

        This key may represent a course that doesn't exist in this modulestore.
        """
        # If there is a mapping that match this org/course/run, use that
        for course_id, store in self.mappings.items():
            candidate_key = store.make_course_key(org, course, run)
            if candidate_key == course_id:
                return candidate_key

        # Otherwise, return the key created by the default store
        return self.default_modulestore.make_course_key(org, course, run)

    def make_course_usage_key(self, course_key):
        """
        Return a valid :class:`~opaque_keys.edx.keys.UsageKey` for the modulestore
        that matches the supplied course_key.
        """
        assert isinstance(course_key, CourseKey)
        store = self._get_modulestore_for_courselike(course_key)
        return store.make_course_usage_key(course_key)

    @strip_key
    def get_course(self, course_key, depth=0, **kwargs):  # pylint: disable=arguments-differ
        """
        returns the course block associated with the course_id. If no such course exists,
        it returns None

        :param course_key: must be a CourseKey
        """
        assert isinstance(course_key, CourseKey)
        store = self._get_modulestore_for_courselike(course_key)
        try:
            return store.get_course(course_key, depth=depth, **kwargs)
        except ItemNotFoundError:
            return None

    @strip_key
    def get_library(self, library_key, depth=0, **kwargs):
        """
        returns the library block associated with the given key. If no such library exists,
        it returns None

        :param library_key: must be a LibraryLocator
        """
        try:
            store = self._verify_modulestore_support(library_key, 'get_library')
            return store.get_library(library_key, depth=depth, **kwargs)
        except NotImplementedError:
            log.exception("Modulestore configured for %s does not have get_library method", library_key)
            return None
        except ItemNotFoundError:
            return None

    @strip_key
    def has_course(self, course_id, ignore_case=False, **kwargs):
        """
        returns the course_id of the course if it was found, else None
        Note: we return the course_id instead of a boolean here since the found course may have
           a different id than the given course_id when ignore_case is True.

        Args:
        * course_id (CourseKey)
        * ignore_case (bool): If True, do a case insensitive search. If
            False, do a case sensitive search
        """
        assert isinstance(course_id, CourseKey)
        store = self._get_modulestore_for_courselike(course_id)
        return store.has_course(course_id, ignore_case, **kwargs)

    def delete_course(self, course_key, user_id):  # pylint: disable=arguments-differ
        """
        See xmodule.modulestore.__init__.ModuleStoreWrite.delete_course
        """
        assert isinstance(course_key, CourseKey)
        store = self._get_modulestore_for_courselike(course_key)
        return store.delete_course(course_key, user_id)

    def save_asset_metadata(self, asset_metadata, user_id, import_only=False):
        """
        Saves the asset metadata for a particular course's asset.

        Args:
        asset_metadata (AssetMetadata): data about the course asset data
        user_id (int|long): user ID saving the asset metadata
        import_only (bool): True if importing without editing, False if editing

        Returns:
            True if info save was successful, else False
        """
        store = self._get_modulestore_for_courselike(asset_metadata.asset_id.course_key)
        return store.save_asset_metadata(asset_metadata, user_id, import_only)

    def save_asset_metadata_list(self, asset_metadata_list, user_id, import_only=False):
        """
        Saves the asset metadata for each asset in a list of asset metadata.
        Optimizes the saving of many assets.

        Args:
        asset_metadata_list (list(AssetMetadata)): list of data about several course assets
        user_id (int|long): user ID saving the asset metadata
        import_only (bool): True if importing without editing, False if editing

        Returns:
            True if info save was successful, else False
        """
        if len(asset_metadata_list) == 0:
            return True
        store = self._get_modulestore_for_courselike(asset_metadata_list[0].asset_id.course_key)
        return store.save_asset_metadata_list(asset_metadata_list, user_id, import_only)

    @strip_key
    def find_asset_metadata(self, asset_key, **kwargs):
        """
        Find the metadata for a particular course asset.

        Args:
            asset_key (AssetKey): locator containing original asset filename

        Returns:
            asset metadata (AssetMetadata) -or- None if not found
        """
        store = self._get_modulestore_for_courselike(asset_key.course_key)
        return store.find_asset_metadata(asset_key, **kwargs)

    @strip_key
    def get_all_asset_metadata(self, course_key, asset_type, start=0, maxresults=-1, sort=None, **kwargs):
        """
        Returns a list of static assets for a course.
        By default all assets are returned, but start and maxresults can be provided to limit the query.

        Args:
            course_key (CourseKey): course identifier
            asset_type (str): type of asset, such as 'asset', 'video', etc. If None, return assets of all types.
            start (int): optional - start at this asset number
            maxresults (int): optional - return at most this many, -1 means no limit
            sort (array): optional - None means no sort
                (sort_by (str), sort_order (str))
                sort_by - one of 'uploadDate' or 'displayname'
                sort_order - one of 'ascending' or 'descending'

        Returns:
            List of AssetMetadata objects.
        """
        store = self._get_modulestore_for_courselike(course_key)
        return store.get_all_asset_metadata(course_key, asset_type, start, maxresults, sort, **kwargs)

    def delete_asset_metadata(self, asset_key, user_id):
        """
        Deletes a single asset's metadata.

        Arguments:
            asset_id (AssetKey): locator containing original asset filename
            user_id (int_long): user deleting the metadata

        Returns:
            Number of asset metadata entries deleted (0 or 1)
        """
        store = self._get_modulestore_for_courselike(asset_key.course_key)
        return store.delete_asset_metadata(asset_key, user_id)

    def copy_all_asset_metadata(self, source_course_key, dest_course_key, user_id):
        """
        Copy all the course assets from source_course_key to dest_course_key.

        Arguments:
            source_course_key (CourseKey): identifier of course to copy from
            dest_course_key (CourseKey): identifier of course to copy to
            user_id (int|long): user copying the asset metadata
        """
        source_store = self._get_modulestore_for_courselike(source_course_key)
        dest_store = self._get_modulestore_for_courselike(dest_course_key)
        if source_store != dest_store:
            with self.bulk_operations(dest_course_key):
                # Get all the asset metadata in the source course.
                all_assets = source_store.get_all_asset_metadata(source_course_key, 'asset')
                # Store it all in the dest course.
                for asset in all_assets:
                    new_asset_key = dest_course_key.make_asset_key('asset', asset.asset_id.path)
                    copied_asset = AssetMetadata(new_asset_key)
                    copied_asset.from_storable(asset.to_storable())
                    dest_store.save_asset_metadata(copied_asset, user_id)
        else:
            # Courses in the same modulestore can be handled by the modulestore itself.
            source_store.copy_all_asset_metadata(source_course_key, dest_course_key, user_id)

    def set_asset_metadata_attr(self, asset_key, attr, value, user_id):
        """
        Add/set the given attr on the asset at the given location. Value can be any type which pymongo accepts.

        Arguments:
            asset_key (AssetKey): asset identifier
            attr (str): which attribute to set
            value: the value to set it to (any type pymongo accepts such as datetime, number, string)
            user_id: (int|long): user setting the attribute

        Raises:
            NotFoundError if no such item exists
            AttributeError is attr is one of the build in attrs.
        """
        store = self._get_modulestore_for_courselike(asset_key.course_key)
        return store.set_asset_metadata_attrs(asset_key, {attr: value}, user_id)

    def set_asset_metadata_attrs(self, asset_key, attr_dict, user_id):  # pylint: disable=arguments-differ
        """
        Add/set the given dict of attrs on the asset at the given location. Value can be any type which pymongo accepts.

        Arguments:
            asset_key (AssetKey): asset identifier
            attr_dict (dict): attribute/value pairs to set
            user_id: (int|long): user setting the attributes

        Raises:
            NotFoundError if no such item exists
            AttributeError is attr is one of the build in attrs.
        """
        store = self._get_modulestore_for_courselike(asset_key.course_key)
        return store.set_asset_metadata_attrs(asset_key, attr_dict, user_id)

    @strip_key
    def get_parent_location(self, location, **kwargs):  # pylint: disable=arguments-differ
        """
        returns the parent locations for a given location
        """
        store = self._get_modulestore_for_courselike(location.course_key)
        return store.get_parent_location(location, **kwargs)

    def get_block_original_usage(self, usage_key):
        """
        If a block was inherited into another structure using copy_from_template,
        this will return the original block usage locator from which the
        copy was inherited.
        """
        try:
            store = self._verify_modulestore_support(usage_key.course_key, 'get_block_original_usage')
            return store.get_block_original_usage(usage_key)
        except NotImplementedError:
            return None, None

    def get_modulestore_type(self, course_id):
        """
        Returns a type which identifies which modulestore is servicing the given course_id.
        The return can be one of:
        "xml" (for XML based courses),
        "mongo" for old-style MongoDB backed courses,
        "split" for new-style split MongoDB backed courses.
        """
        return self._get_modulestore_for_courselike(course_id).get_modulestore_type()

    @strip_key
    def get_orphans(self, course_key, **kwargs):
        """
        Get all of the xblocks in the given course which have no parents and are not of types which are
        usually orphaned. NOTE: may include xblocks which still have references via xblocks which don't
        use children to point to their dependents.
        """
        store = self._get_modulestore_for_courselike(course_key)
        return store.get_orphans(course_key, **kwargs)

    def get_errored_courses(self):
        """
        Return a dictionary of course_dir -> [(msg, exception_str)], for each
        course_dir where course loading failed.
        """
        errs = {}
        for store in self.modulestores:
            errs.update(store.get_errored_courses())
        return errs

    @strip_key
    def create_course(self, org, course, run, user_id, **kwargs):  # pylint: disable=arguments-differ
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
        # first make sure an existing course doesn't already exist in the mapping
        course_key = self.make_course_key(org, course, run)

        log.info('Creating course run %s...', course_key)
        if course_key in self.mappings and self.mappings[course_key].has_course(course_key):
            log.error('Cannot create course run %s. It already exists!', course_key)
            raise DuplicateCourseError(course_key, course_key)

        # create the course
        store = self._verify_modulestore_support(None, 'create_course')
        course = store.create_course(org, course, run, user_id, **kwargs)
        log.info('Course run %s created successfully!', course_key)

        # add new course to the mapping
        self.mappings[course_key] = store

        # .. event_implemented_name: COURSE_CREATED
        # .. event_type: org.openedx.content_authoring.course.created.v1
        COURSE_CREATED.send_event(
            time=datetime.now(timezone.utc),  # noqa: UP017
            course=CourseData(
                course_key=course_key,
            )
        )

        return course

    @strip_key
    def create_library(self, org, library, user_id, fields, **kwargs):
        """
        Creates and returns a new library.

        Args:
            org (str): the organization that owns the course
            library (str): the code/number/name of the library
            user_id: id of the user creating the course
            fields (dict): Fields to set on the course at initialization - e.g. display_name
            kwargs: Any optional arguments understood by a subset of modulestores to customize instantiation

        Returns: a LibraryRoot
        """
        # first make sure an existing course/lib doesn't already exist in the mapping
        lib_key = LibraryLocator(org=org, library=library)
        if lib_key in self.mappings:
            raise DuplicateCourseError(lib_key, lib_key)

        # create the library
        store = self._verify_modulestore_support(None, 'create_library')
        library = store.create_library(org, library, user_id, fields, **kwargs)

        # add new library to the mapping
        self.mappings[lib_key] = store

        return library

    @strip_key
    def clone_course(self, source_course_id, dest_course_id, user_id, fields=None, **kwargs):
        """
        See the superclass for the general documentation.

        If cloning w/in a store, delegates to that store's clone_course which, in order to be self-
        sufficient, should handle the asset copying (call the same method as this one does)
        If cloning between stores,
            * copy the assets
            * migrate the courseware
        """

        source_modulestore = self._get_modulestore_for_courselike(source_course_id)
        # for a temporary period of time, we may want to hardcode dest_modulestore as split if there's a split
        # to have only course re-runs go to split. This code, however, uses the config'd priority
        dest_modulestore = self._get_modulestore_for_courselike(dest_course_id)
        if source_modulestore == dest_modulestore:
            return source_modulestore.clone_course(source_course_id, dest_course_id, user_id, fields, **kwargs)

        raise NotImplementedError("No code for cloning from {} to {}".format(  # noqa: UP032
            source_modulestore, dest_modulestore
        ))

    @strip_key
    @prepare_asides
    def create_item(self, user_id, course_key, block_type, block_id=None, fields=None, **kwargs):
        """
        Creates and saves a new item in a course.

        Returns the newly created item.

        Args:
            user_id: ID of the user creating and saving the xmodule
            course_key: A :class:`~opaque_keys.edx.CourseKey` identifying which course to create
                this item in
            block_type: The typo of block to create
            block_id: a unique identifier for the new item. If not supplied,
                a new identifier will be generated
            fields (dict): A dictionary specifying initial values for some or all fields
                in the newly created block
        """
        modulestore = self._verify_modulestore_support(course_key, 'create_item')
        xblock = modulestore.create_item(user_id, course_key, block_type, block_id=block_id, fields=fields, **kwargs)

        def send_created_event():
            # .. event_implemented_name: XBLOCK_CREATED
            # .. event_type: org.openedx.content_authoring.xblock.created.v1
            XBLOCK_CREATED.send_event(
                time=datetime.now(timezone.utc),  # noqa: UP017
                xblock_info=XBlockData(
                    usage_key=xblock.location.for_branch(None),
                    block_type=block_type,
                    version=xblock.location
                )
            )

        modulestore.on_commit_changes_to(course_key, send_created_event)
        return xblock

    @strip_key
    @prepare_asides
    def create_child(self, user_id, parent_usage_key, block_type, block_id=None, fields=None, **kwargs):
        """
        Creates and saves a new xblock that is a child of the specified block

        Returns the newly created item.

        Args:
            user_id: ID of the user creating and saving the xmodule
            parent_usage_key: a :class:`~opaque_key.edx.UsageKey` identifying the
                block that this item should be parented under
            block_type: The typo of block to create
            block_id: a unique identifier for the new item. If not supplied,
                a new identifier will be generated
            fields (dict): A dictionary specifying initial values for some or all fields
                in the newly created block
        """
        course_key = parent_usage_key.course_key
        store = self._verify_modulestore_support(course_key, 'create_child')
        xblock = store.create_child(
            user_id, parent_usage_key, block_type, block_id=block_id, fields=fields, **kwargs
        )

        def send_created_event():
            # .. event_implemented_name: XBLOCK_CREATED
            # .. event_type: org.openedx.content_authoring.xblock.created.v1
            XBLOCK_CREATED.send_event(
                time=datetime.now(timezone.utc),  # noqa: UP017
                xblock_info=XBlockData(
                    usage_key=xblock.location.for_branch(None),
                    block_type=block_type,
                    version=xblock.location
                )
            )

        store.on_commit_changes_to(course_key, send_created_event)
        return xblock

    @strip_key
    @prepare_asides
    def import_xblock(self, user_id, course_key, block_type, block_id, fields=None, runtime=None, **kwargs):
        """
        See :py:meth `ModuleStoreDraftAndPublished.import_xblock`

        Defer to the course's modulestore if it supports this method
        """
        store = self._verify_modulestore_support(course_key, 'import_xblock')
        return store.import_xblock(user_id, course_key, block_type, block_id, fields, runtime, **kwargs)

    @strip_key
    def copy_from_template(self, source_keys, dest_key, user_id, **kwargs):  # pylint: disable=unused-argument
        """
        See :py:meth `SplitMongoModuleStore.copy_from_template`
        """
        store = self._verify_modulestore_support(dest_key.course_key, 'copy_from_template')
        return store.copy_from_template(source_keys, dest_key, user_id)

    @strip_key
    @prepare_asides
    def update_item(self, xblock, user_id, allow_not_found=False, **kwargs):  # pylint: disable=arguments-differ
        """
        Update the xblock persisted to be the same as the given for all types of fields
        (content, children, and metadata) attribute the change to the given user.
        """
        course_key = xblock.location.course_key
        store = self._verify_modulestore_support(course_key, 'update_item')
        xblock = store.update_item(xblock, user_id, allow_not_found, **kwargs)

        def send_updated_event():
            # .. event_implemented_name: XBLOCK_UPDATED
            # .. event_type: org.openedx.content_authoring.xblock.updated.v1
            XBLOCK_UPDATED.send_event(
                time=datetime.now(timezone.utc),  # noqa: UP017
                xblock_info=XBlockData(
                    usage_key=xblock.location.for_branch(None),
                    block_type=xblock.location.block_type,
                    version=xblock.location
                )
            )

        store.on_commit_changes_to(course_key, send_updated_event)
        return xblock

    @strip_key
    def delete_item(self, location, user_id, **kwargs):  # pylint: disable=arguments-differ
        """
        Delete the given item from persistence. kwargs allow modulestore specific parameters.
        """
        course_key = location.course_key
        store = self._verify_modulestore_support(course_key, 'delete_item')
        item = store.delete_item(location, user_id=user_id, **kwargs)

        def send_deleted_event():
            # .. event_implemented_name: XBLOCK_DELETED
            # .. event_type: org.openedx.content_authoring.xblock.deleted.v1
            XBLOCK_DELETED.send_event(
                time=datetime.now(timezone.utc),  # noqa: UP017
                xblock_info=XBlockData(
                    usage_key=location,
                    block_type=location.block_type,
                )
            )

        store.on_commit_changes_to(course_key, send_deleted_event)
        return item

    def revert_to_published(self, location, user_id):
        """
        Reverts an item to its last published version (recursively traversing all of its descendants).
        If no published version exists, an InvalidVersionError is thrown.

        If a published version exists but there is no draft version of this item or any of its descendants, this
        method is a no-op.

        :raises InvalidVersionError: if no published version exists for the location specified
        """
        store = self._verify_modulestore_support(location.course_key, 'revert_to_published')
        return store.revert_to_published(location, user_id)

    def reset_course_to_version(self, course_key, version_guid, user_id):
        """
        Resets the content of a course at `course_key` to a version specified by `version_guid`.

        :raises NotImplementedError: if not supported by store.
        """
        store = self._verify_modulestore_support(course_key, 'reset_course_to_version')
        return store.reset_course_to_version(
            course_key=course_key,
            version_guid=version_guid,
            user_id=user_id,
        )

    def close_all_connections(self):
        """
        Close all db connections
        """
        for modulestore in self.modulestores:
            modulestore.close_connections()

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
        for modulestore in self.modulestores:
            # drop database if the store supports it (read-only stores do not)
            if hasattr(modulestore, '_drop_database'):
                modulestore._drop_database(database, collections, connections)  # pylint: disable=protected-access

    @strip_key
    def create_xblock(self, runtime, course_key, block_type, block_id=None, fields=None, **kwargs):
        """
        Create the new xblock but don't save it. Returns the new block.

        Args:
            runtime: :py:class `xblock.runtime` from another xblock in the same course. Providing this
                significantly speeds up processing (inheritance and subsequent persistence)
            course_key: :py:class `opaque_keys.CourseKey`
            block_type: :py:class `string`: the string identifying the xblock type
            block_id: the string uniquely identifying the block within the given course
            fields: :py:class `dict` field_name, value pairs for initializing the xblock fields. Values
                should be the pythonic types not the json serialized ones.
        """
        store = self._verify_modulestore_support(course_key, 'create_xblock')
        return store.create_xblock(runtime, course_key, block_type, block_id, fields or {}, **kwargs)

    @strip_key
    def get_courses_for_wiki(self, wiki_slug, **kwargs):
        """
        Return the list of courses which use this wiki_slug
        :param wiki_slug: the course wiki root slug
        :return: list of course keys
        """
        courses = []
        for modulestore in self.modulestores:
            courses.extend(modulestore.get_courses_for_wiki(wiki_slug, **kwargs))
        return courses

    def heartbeat(self):
        """
        Delegate to each modulestore and package the results for the caller.
        """
        # could be done in parallel threads if needed
        return dict(
            itertools.chain.from_iterable(
                store.heartbeat().items()
                for store in self.modulestores
            )
        )

    def has_published_version(self, xblock):
        """
        Returns whether this xblock is draft, public, or private.

        Returns:
            PublishState.draft - content is in the process of being edited, but still has a previous
                version deployed to LMS
            PublishState.public - content is locked and deployed to LMS
            PublishState.private - content is editable and not deployed to LMS
        """
        course_id = xblock.scope_ids.usage_id.course_key
        store = self._get_modulestore_for_courselike(course_id)
        return store.has_published_version(xblock)

    @strip_key
    def publish(self, location, user_id, **kwargs):
        """
        Save a current draft to the underlying modulestore
        Returns the newly published item.
        """
        store = self._verify_modulestore_support(location.course_key, 'publish')
        item = store.publish(location, user_id, **kwargs)
        # .. event_implemented_name: XBLOCK_PUBLISHED
        # .. event_type: org.openedx.content_authoring.xblock.published.v1
        XBLOCK_PUBLISHED.send_event(
            time=datetime.now(timezone.utc),  # noqa: UP017
            xblock_info=XBlockData(
                usage_key=location,
                block_type=location.block_type,
            )
        )
        return item

    @strip_key
    def unpublish(self, location, user_id, **kwargs):
        """
        Save a current draft to the underlying modulestore
        Returns the newly unpublished item.
        """
        store = self._verify_modulestore_support(location.course_key, 'unpublish')
        return store.unpublish(location, user_id, **kwargs)

    def convert_to_draft(self, location, user_id):
        """
        Create a copy of the source and mark its revision as draft.
        Note: This method is to support the Mongo Modulestore and may be deprecated.

        :param location: the location of the source (its revision must be None)
        """
        store = self._verify_modulestore_support(location.course_key, 'convert_to_draft')
        return store.convert_to_draft(location, user_id)

    def has_changes(self, xblock):
        """
        Checks if the given block has unpublished changes
        :param xblock: the block to check
        :return: True if the draft and published versions differ
        """
        store = self._verify_modulestore_support(xblock.location.course_key, 'has_changes')
        return store.has_changes(xblock)

    def check_supports(self, course_key, method):
        """
        Verifies that the modulestore for a particular course supports a feature.
        Returns True/false based on this.
        """
        try:
            self._verify_modulestore_support(course_key, method)
            return True
        except NotImplementedError:
            return False

    def _verify_modulestore_support(self, course_key, method):
        """
        Finds and returns the store that contains the course for the given location, and verifying
        that the store supports the given method.

        Raises NotImplementedError if the found store does not support the given method.
        """
        store = self._get_modulestore_for_courselike(course_key)
        if hasattr(store, method):
            return store
        else:
            raise NotImplementedError(f"Cannot call {method} on store {store}")

    @property
    def default_modulestore(self):
        """
        Return the default modulestore
        """
        thread_local_default_store = getattr(self.thread_cache, 'default_store', None)
        if thread_local_default_store:
            # return the thread-local cache, if found
            return thread_local_default_store
        else:
            # else return the default store
            return self.modulestores[0]

    @contextmanager
    def default_store(self, store_type):
        """
        A context manager for temporarily changing the default store in the Mixed modulestore to the given store type
        """
        # find the store corresponding to the given type
        store = next((store for store in self.modulestores if store.get_modulestore_type() == store_type), None)
        if not store:
            raise Exception(f"Cannot find store of type {store_type}")

        prev_thread_local_store = getattr(self.thread_cache, 'default_store', None)
        try:
            self.thread_cache.default_store = store
            yield
        finally:
            self.thread_cache.default_store = prev_thread_local_store

    @contextmanager
    def branch_setting(self, branch_setting, course_id=None):
        """
        A context manager for temporarily setting the branch value for the given course' store
        to the given branch_setting.  If course_id is None, the default store is used.
        """
        store = self._verify_modulestore_support(course_id, 'branch_setting')
        previous_thread_branch_setting = getattr(self.thread_cache, 'branch_setting', None)
        try:
            self.thread_cache.branch_setting = branch_setting
            with store.branch_setting(branch_setting, course_id):
                yield
        finally:
            self.thread_cache.branch_setting = previous_thread_branch_setting

    @contextmanager
    def bulk_operations(self, course_id, emit_signals=True, ignore_case=False):
        """
        A context manager for notifying the store of bulk operations.
        If course_id is None, the default store is used.
        """
        store = self._get_modulestore_for_courselike(course_id)
        with store.bulk_operations(course_id, emit_signals, ignore_case):
            yield

    def ensure_indexes(self):
        """
        Ensure that all appropriate indexes are created that are needed by this modulestore, or raise
        an exception if unable to.

        This method is intended for use by tests and administrative commands, and not
        to be run during server startup.
        """
        for store in self.modulestores:
            store.ensure_indexes()
