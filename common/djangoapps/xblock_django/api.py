"""
API methods related to xblock state.
"""


from common.djangoapps.xblock_django.models import XBlockConfiguration, XBlockStudioConfiguration
from openedx.core.lib.cache_utils import CacheInvalidationManager

cacher = CacheInvalidationManager(model=XBlockConfiguration)
studio_config_cacher = CacheInvalidationManager(model=XBlockStudioConfiguration)


@cacher
def deprecated_xblocks():
    """
    Return the QuerySet of deprecated XBlock types. Note that this method is independent of
    `XBlockStudioConfigurationFlag` and `XBlockStudioConfiguration`.
    """
    return XBlockConfiguration.objects.current_set().filter(deprecated=True)


@cacher
def disabled_xblocks():
    """
    Return the QuerySet of disabled XBlock types (which should not render in the LMS).
    Note that this method is independent of `XBlockStudioConfigurationFlag` and `XBlockStudioConfiguration`.
    """
    return XBlockConfiguration.objects.current_set().filter(enabled=False)


@studio_config_cacher
def default_advanced_xblocks():
    """
    Return the QuerySet of XBlock types which operators have marked as advanced by default, meaning that
    Studio offers them in the Advanced component list of every course, without course teams having to add
    them to the course's Advanced Module List.

    Note that this method is independent of `XBlockStudioConfigurationFlag`: the flag governs whether support
    levels are enforced, not whether an operator has opted an XBlock into every course. It does not take into
    account fully disabled xblocks (as returned by `disabled_xblocks`) or deprecated xblocks (as returned by
    `deprecated_xblocks`); callers are expected to filter those out, as `get_component_templates` does.
    """
    return XBlockStudioConfiguration.objects.current_set().filter(enabled=True, advanced_by_default=True)


def authorable_xblocks(allow_unsupported=False, name=None):
    """
    This method returns the QuerySet of XBlocks that can be created in Studio (by default, only fully supported
    and provisionally supported XBlocks), as stored in `XBlockStudioConfiguration`.
    Note that this method does NOT check the value `XBlockStudioConfigurationFlag`, nor does it take into account
    fully disabled xblocks (as returned by `disabled_xblocks`) or deprecated xblocks
    (as returned by `deprecated_xblocks`).

    Arguments:
        allow_unsupported (bool): If `True`, enabled but unsupported XBlocks will also be returned.
            Note that unsupported XBlocks are not recommended for use in courses due to non-compliance
            with one or more of the base requirements, such as testing, accessibility, internationalization,
            and documentation. Default value is `False`.
        name (str): If provided, filters the returned XBlocks to those with the provided name. This is
            useful for XBlocks with lots of template types.
    Returns:
        QuerySet: Returns authorable XBlocks, taking into account `support_level`, `enabled` and `name`
        (if specified) as specified by `XBlockStudioConfiguration`. Does not take into account whether or not
        `XBlockStudioConfigurationFlag` is enabled.
    """
    blocks = XBlockStudioConfiguration.objects.current_set().filter(enabled=True)
    if not allow_unsupported:
        blocks = blocks.exclude(support_level=XBlockStudioConfiguration.UNSUPPORTED)

    if name:
        blocks = blocks.filter(name=name)

    return blocks
