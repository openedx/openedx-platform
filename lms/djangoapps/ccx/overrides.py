"""
API related to providing field overrides for individual students.  This is used
by the individual custom courses feature.
"""


import json
import logging

from ccx_keys.locator import CCXBlockUsageLocator, CCXLocator
from django.db import transaction
from opaque_keys.edx.keys import CourseKey, UsageKey

from lms.djangoapps.ccx.models import CcxFieldOverride, CustomCourseForEdX
from lms.djangoapps.courseware.field_overrides import FieldOverrideProvider
from openedx.core.lib.cache_utils import get_cache

log = logging.getLogger(__name__)


class CustomCoursesForEdxOverrideProvider(FieldOverrideProvider):
    """
    A concrete implementation of
    :class:`~courseware.field_overrides.FieldOverrideProvider` which allows for
    overrides to be made on a per user basis.
    """
    def get(self, block, name, default):
        """
        Just call the get_override_for_ccx method if there is a ccx
        """
        # The incoming block might be a CourseKey instance of some type, a
        # UsageKey instance of some type, or it might be something that has a
        # location attribute.  That location attribute will be a UsageKey
        ccx = course_key = None
        identifier = getattr(block, 'id', None)
        if isinstance(identifier, CourseKey):
            course_key = block.id
        elif isinstance(identifier, UsageKey):
            course_key = block.id.course_key
        elif hasattr(block, 'location'):
            course_key = block.location.course_key
        else:
            msg = "Unable to get course id when calculating ccx overide for block type %r"
            log.error(msg, type(block))
        if course_key is not None:
            ccx = get_current_ccx(course_key)
        if ccx:
            return get_override_for_ccx(ccx, block, name, default)
        return default

    @classmethod
    def enabled_for(cls, block):  # pylint: disable=arguments-differ
        """
        CCX field overrides are enabled for CCX blocks.
        """
        return getattr(block.location, 'ccx', None) or getattr(block, 'enable_ccx', False)


def get_current_ccx(course_key):
    """
    Return the ccx that is active for this course.

    course_key is expected to be an instance of an opaque CourseKey, a
    ValueError is raised if this expectation is not met.
    """
    if not isinstance(course_key, CourseKey):
        raise ValueError("get_current_ccx requires a CourseKey instance")

    if not isinstance(course_key, CCXLocator):
        return None

    ccx_cache = get_cache('ccx')
    if course_key not in ccx_cache:
        ccx_cache[course_key] = CustomCourseForEdX.objects.get(pk=course_key.ccx)

    return ccx_cache[course_key]


def get_override_for_ccx(ccx, block, name, default=None):
    """
    Gets the value of the overridden field for the `ccx`.  `block` and `name`
    specify the block and the name of the field.  If the field is not
    overridden for the given ccx, returns `default`.
    """
    overrides = _get_overrides_for_ccx(ccx)

    clean_ccx_key = _clean_ccx_key(block.location)

    block_overrides = overrides.get(clean_ccx_key, {})

    # Hardcode the course_edit_method to be None instead of 'Studio', so,
    # the LMS never tries to link back to Studio. CCX courses
    # can't be edited in Studio.
    block_overrides['course_edit_method'] = None

    if name in block_overrides:
        try:
            return block.fields[name].from_json(block_overrides[name])
        except KeyError:
            return block_overrides[name]
    else:
        return default


def _clean_ccx_key(block_location):
    """
    Converts the given BlockUsageKey from a CCX key to the
    corresponding key for its parent course, while handling the case
    where no conversion is needed.  Also strips any version and
    branch information from the key.
    Returns the cleaned key.
    """
    if isinstance(block_location, CCXBlockUsageLocator):
        clean_key = block_location.to_block_locator()
    else:
        clean_key = block_location
    return clean_key.version_agnostic().for_branch(None)


def _get_overrides_for_ccx(ccx):
    """
    Returns a dictionary mapping field name to overriden value for any
    overrides set on this block for this CCX.
    """
    overrides_cache = get_cache('ccx-overrides')

    if ccx not in overrides_cache:
        overrides = {}
        query = CcxFieldOverride.objects.filter(
            ccx=ccx,
        )

        for override in query:
            block_overrides = overrides.setdefault(override.location, {})
            block_overrides[override.field] = json.loads(override.value)
            block_overrides[override.field + "_id"] = override.id
            block_overrides[override.field + "_instance"] = override

        overrides_cache[ccx] = overrides

    return overrides_cache[ccx]


@transaction.atomic
def override_field_for_ccx(ccx, block, name, value):
    """
    Overrides a field for the `ccx`.  `block` and `name` specify the block
    and the name of the field on that block to override.  `value` is the
    value to set for the given field.
    """
    field = block.fields[name]
    value_json = field.to_json(value)
    serialized_value = json.dumps(value_json)
    override_has_changes = False
    clean_ccx_key = _clean_ccx_key(block.location)

    override = get_override_for_ccx(ccx, block, name + "_instance")
    if override:
        override_has_changes = serialized_value != override.value

    if not override:
        override, created = CcxFieldOverride.objects.get_or_create(
            ccx=ccx,
            location=block.location,
            field=name,
            defaults={'value': serialized_value},
        )
        if created:
            _get_overrides_for_ccx(ccx).setdefault(clean_ccx_key, {})[name + "_id"] = override.id
        else:
            override_has_changes = serialized_value != override.value

    if override_has_changes:
        override.value = serialized_value
        override.save()

    _get_overrides_for_ccx(ccx).setdefault(clean_ccx_key, {})[name] = value_json
    _get_overrides_for_ccx(ccx).setdefault(clean_ccx_key, {})[name + "_instance"] = override


def clear_override_for_ccx(ccx, block, name):
    """
    Clears a previously set field override for the `ccx`.  `block` and `name`
    specify the block and the name of the field on that block to clear.
    This function is idempotent--if no override is set, nothing action is
    performed.
    """
    try:
        CcxFieldOverride.objects.get(
            ccx=ccx,
            location=block.location,
            field=name).delete()

        clear_ccx_field_info_from_ccx_map(ccx, block, name)

    except CcxFieldOverride.DoesNotExist:
        pass


def clear_ccx_field_info_from_ccx_map(ccx, block, name):
    """
    Remove field information from ccx overrides mapping dictionary
    """
    try:
        clean_ccx_key = _clean_ccx_key(block.location)
        ccx_override_map = _get_overrides_for_ccx(ccx).setdefault(clean_ccx_key, {})
        ccx_override_map.pop(name)
        ccx_override_map.pop(name + "_id")
        ccx_override_map.pop(name + "_instance")
    except KeyError:
        pass


def bulk_delete_ccx_override_fields(ccx, ids):
    """
    Bulk delete for CcxFieldOverride model
    """
    ids = [ccx_id for ccx_id in ids if ccx_id]
    ids = list(set(ids))
    if ids:
        CcxFieldOverride.objects.filter(ccx=ccx, id__in=ids).delete()
