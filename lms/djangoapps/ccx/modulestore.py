"""A modulestore wrapper

It will 'unwrap' ccx keys on the way in and re-wrap them on the way out

In practical terms this means that when an object is retrieved from modulestore
using a CCXLocator or CCXBlockUsageLocator as the key, the equivalent
CourseLocator or BlockUsageLocator will actually be used. And all objects
returned from the modulestore will have their keys updated to be the CCX
version that was passed in.
"""


from contextlib import contextmanager
from functools import partial

from ccx_keys.locator import CCXBlockUsageLocator, CCXLocator
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator

from xmodule.modulestore import XMODULE_FIELDS_WITH_USAGE_KEYS


def strip_ccx(val):
    """remove any reference to a CCX from the incoming value

    return a tuple of the stripped value and the id of the ccx
    """
    retval = val
    ccx_id = None
    if isinstance(retval, CCXLocator):
        ccx_id = retval.ccx
        retval = retval.to_course_locator()
    elif isinstance(retval, CCXBlockUsageLocator):
        ccx_id = retval.course_key.ccx
        retval = retval.to_block_locator()
    else:
        for field_name in XMODULE_FIELDS_WITH_USAGE_KEYS:
            if hasattr(retval, field_name):
                stripped_field_value, ccx_id = strip_ccx(getattr(retval, field_name))
                setattr(retval, field_name, stripped_field_value)
    return retval, ccx_id


def restore_ccx(val, ccx_id):
    """restore references to a CCX to the incoming value

    returns the value converted to a CCX-aware state, using the provided ccx_id
    """
    if isinstance(val, CourseLocator):
        return CCXLocator.from_course_locator(val, ccx_id)
    elif isinstance(val, BlockUsageLocator):
        ccx_key = restore_ccx(val.course_key, ccx_id)
        val = CCXBlockUsageLocator(ccx_key, val.block_type, val.block_id)
    for field_name in XMODULE_FIELDS_WITH_USAGE_KEYS:
        if hasattr(val, field_name):
            setattr(val, field_name, restore_ccx(getattr(val, field_name), ccx_id))
    if hasattr(val, 'children'):
        val.children = restore_ccx_collection(val.children, ccx_id)
    return val


def restore_ccx_collection(field_value, ccx_id=None):
    """restore references to a CCX to collections of incoming values

    returns the original collection with all values converted to a ccx-aware
    state, using the provided ccx_id
    """
    if ccx_id is None:
        return field_value
    if isinstance(field_value, list):
        field_value = [restore_ccx(fv, ccx_id) for fv in field_value]
    elif isinstance(field_value, dict):
        for key, val in field_value.items():
            field_value[key] = restore_ccx(val, ccx_id)
    else:
        field_value = restore_ccx(field_value, ccx_id)
    return field_value


@contextmanager
def remove_ccx(to_strip):
    """A context manager for wrapping modulestore api methods.

    yields a stripped value and a function suitable for restoring it
    """
    stripped, ccx = strip_ccx(to_strip)
    yield stripped, partial(restore_ccx_collection, ccx_id=ccx)


class CCXModulestoreWrapper:
    """This class wraps a modulestore

    The purpose is to remove ccx-specific identifiers during lookup and restore
    it after retrieval so that data can be stored local to a course, but
    referenced in app context as ccx-specific
    """

    def __init__(self, modulestore):
        """wrap the provided modulestore"""
        self.__dict__['_modulestore'] = modulestore

    def __getattr__(self, name):
        """look up missing attributes on the wrapped modulestore"""
        return getattr(self._modulestore, name)

    def __setattr__(self, name, value):
        """set attributes only on the wrapped modulestore"""
        setattr(self._modulestore, name, value)

    def __delattr__(self, name):
        """delete attributes only on the wrapped modulestore"""
        delattr(self._modulestore, name)

    def _clean_locator_for_mapping(self, locator):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(locator) as (locator, restore):  # pylint: disable=redefined-argument-from-local
            # pylint: disable=protected-access
            return restore(
                self._modulestore._clean_locator_for_mapping(locator)
            )

    def _get_modulestore_for_courselike(self, locator=None):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        if locator is not None:
            locator, _ = strip_ccx(locator)
        # pylint: disable=protected-access
        return self._modulestore._get_modulestore_for_courselike(locator)

    def fill_in_run(self, course_key):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(course_key) as (course_key, restore):  # pylint: disable=redefined-argument-from-local
            return restore(self._modulestore.fill_in_run(course_key))

    def has_item(self, usage_key, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        usage_key, _ = strip_ccx(usage_key)
        return self._modulestore.has_item(usage_key, **kwargs)

    def get_item(self, usage_key, depth=0, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(usage_key) as (usage_key, restore):  # pylint: disable=redefined-argument-from-local
            return restore(
                self._modulestore.get_item(usage_key, depth, **kwargs)
            )

    def get_items(self, course_key, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(course_key) as (course_key, restore):  # pylint: disable=redefined-argument-from-local
            return restore(self._modulestore.get_items(course_key, **kwargs))

    def get_course(self, course_key, depth=0, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(course_key) as (course_key, restore):  # pylint: disable=redefined-argument-from-local
            return restore(self._modulestore.get_course(
                course_key, depth=depth, **kwargs
            ))

    def has_course(self, course_id, ignore_case=False, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(course_id) as (course_id, restore):  # pylint: disable=redefined-argument-from-local
            return restore(self._modulestore.has_course(
                course_id, ignore_case=ignore_case, **kwargs
            ))

    def delete_course(self, course_key, user_id):
        """
        See xmodule.modulestore.__init__.ModuleStoreWrite.delete_course
        """
        course_key, _ = strip_ccx(course_key)
        return self._modulestore.delete_course(course_key, user_id)

    def get_parent_location(self, location, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(location) as (location, restore):  # pylint: disable=redefined-argument-from-local
            return restore(
                self._modulestore.get_parent_location(location, **kwargs)
            )

    def get_block_original_usage(self, usage_key):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(usage_key) as (usage_key, restore):  # pylint: disable=redefined-argument-from-local
            orig_key, version = self._modulestore.get_block_original_usage(usage_key)
            return restore(orig_key), version

    def get_modulestore_type(self, course_id):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(course_id) as (course_id, restore):  # pylint: disable=redefined-argument-from-local
            return restore(self._modulestore.get_modulestore_type(course_id))

    def get_orphans(self, course_key, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(course_key) as (course_key, restore):  # pylint: disable=redefined-argument-from-local
            return restore(self._modulestore.get_orphans(course_key, **kwargs))

    def clone_course(self, source_course_id, dest_course_id, user_id, fields=None, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(source_course_id) as (source_course_id, _):  # pylint: disable=redefined-argument-from-local
            with remove_ccx(dest_course_id) as (dest_course_id, dest_restore):  # pylint: disable=redefined-argument-from-local
                return dest_restore(self._modulestore.clone_course(
                    source_course_id, dest_course_id, user_id, fields=fields, **kwargs
                ))

    def create_item(self, user_id, course_key, block_type, block_id=None, fields=None, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(course_key) as (course_key, restore):  # pylint: disable=redefined-argument-from-local
            return restore(self._modulestore.create_item(
                user_id, course_key, block_type, block_id=block_id, fields=fields, **kwargs
            ))

    def create_child(self, user_id, parent_usage_key, block_type, block_id=None, fields=None, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(parent_usage_key) as (parent_usage_key, restore):  # pylint: disable=redefined-argument-from-local
            return restore(self._modulestore.create_child(
                user_id, parent_usage_key, block_type, block_id=block_id, fields=fields, **kwargs
            ))

    def import_xblock(self, user_id, course_key, block_type, block_id, fields=None, runtime=None, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(course_key) as (course_key, restore):  # pylint: disable=redefined-argument-from-local
            return restore(self._modulestore.import_xblock(
                user_id, course_key, block_type, block_id, fields=fields, runtime=runtime, **kwargs
            ))

    def copy_from_template(self, source_keys, dest_key, user_id, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(dest_key) as (dest_key, restore):  # pylint: disable=redefined-argument-from-local
            return restore(self._modulestore.copy_from_template(
                source_keys, dest_key, user_id, **kwargs
            ))

    def update_item(self, xblock, user_id, allow_not_found=False, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(xblock) as (xblock, restore):  # pylint: disable=redefined-argument-from-local
            return restore(self._modulestore.update_item(
                xblock, user_id, allow_not_found=allow_not_found, **kwargs
            ))

    def delete_item(self, location, user_id, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(location) as (location, restore):  # pylint: disable=redefined-argument-from-local
            return restore(
                self._modulestore.delete_item(location, user_id, **kwargs)
            )

    def revert_to_published(self, location, user_id):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(location) as (location, restore):  # pylint: disable=redefined-argument-from-local
            return restore(
                self._modulestore.revert_to_published(location, user_id)
            )

    def create_xblock(self, runtime, course_key, block_type, block_id=None, fields=None, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(course_key) as (course_key, restore):  # pylint: disable=redefined-argument-from-local
            return restore(self._modulestore.create_xblock(
                runtime, course_key, block_type, block_id=block_id, fields=fields, **kwargs
            ))

    def has_published_version(self, xblock):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(xblock) as (xblock, restore):  # pylint: disable=redefined-argument-from-local
            return restore(self._modulestore.has_published_version(xblock))

    def publish(self, location, user_id, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(location) as (location, restore):  # pylint: disable=redefined-argument-from-local
            return restore(
                self._modulestore.publish(location, user_id, **kwargs)
            )

    def unpublish(self, location, user_id, **kwargs):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(location) as (location, restore):  # pylint: disable=redefined-argument-from-local
            return restore(
                self._modulestore.unpublish(location, user_id, **kwargs)
            )

    def convert_to_draft(self, location, user_id):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(location) as (location, restore):  # pylint: disable=redefined-argument-from-local
            return restore(
                self._modulestore.convert_to_draft(location, user_id)
            )

    def has_changes(self, xblock):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        with remove_ccx(xblock) as (xblock, restore):  # pylint: disable=redefined-argument-from-local
            return restore(self._modulestore.has_changes(xblock))

    def check_supports(self, course_key, method):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        course_key, _ = strip_ccx(course_key)
        return self._modulestore.check_supports(course_key, method)

    @contextmanager
    def branch_setting(self, branch_setting, course_id=None):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        course_id, _ = strip_ccx(course_id)
        with self._modulestore.branch_setting(branch_setting, course_id):
            yield

    @contextmanager
    def bulk_operations(self, course_id, emit_signals=True, ignore_case=False):
        """See the docs for xmodule.modulestore.mixed.MixedModuleStore"""
        course_id, _ = strip_ccx(course_id)
        with self._modulestore.bulk_operations(course_id, emit_signals=emit_signals, ignore_case=ignore_case):
            yield
