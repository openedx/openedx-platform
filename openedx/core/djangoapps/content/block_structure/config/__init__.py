"""
This module contains various configuration settings via
waffle switches for the Block Structure framework.
"""
from edx_django_utils.cache import RequestCache  # noqa: F401
from edx_toggles.toggles import WaffleSwitch  # noqa: F401

from openedx.core.lib.cache_utils import request_cached

from .models import BlockStructureConfiguration


@request_cached()
def num_versions_to_keep():
    """
    Returns and caches the current setting for num_versions_to_keep.
    """
    return BlockStructureConfiguration.current().num_versions_to_keep


@request_cached()
def cache_timeout_in_seconds():
    """
    Returns and caches the current setting for cache_timeout_in_seconds.
    """
    return BlockStructureConfiguration.current().cache_timeout_in_seconds
