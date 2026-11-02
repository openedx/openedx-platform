"""
general helper functions for xblocks
"""

from opaque_keys.edx.keys import UsageKey
from xblock.core import XBlock
from xmodule.modulestore.django import modulestore
from xmodule.util.keys import BlockKey
from openedx.core.djangoapps.content_tagging.api import get_object_tag_counts


def usage_key_with_run(usage_key_string: str) -> UsageKey:
    """
    Converts usage_key_string to a UsageKey, adding a course run if necessary
    """
    usage_key = UsageKey.from_string(usage_key_string)
    usage_key = usage_key.replace(course_key=modulestore().fill_in_run(usage_key.course_key))
    return usage_key


def get_block_key_string(usage_key: UsageKey) -> str:
    """
    Extract block key from UsageKey in string format: `html:my-id`.
    """
    return str(BlockKey.from_usage_key(usage_key))


def get_tags_count(xblock: XBlock, include_children=False) -> dict[str, int]:
    """
    Returns a map with tag count of the `xblock`

    Use `include_children` to include each children on the query.
    """
    query_list = [str(xblock.location)]

    if include_children:
        children = xblock.get_children()
        child_usage_keys = [str(child.location) for child in children]
        query_list.extend(child_usage_keys)

    tags_count_query = ",".join(query_list)

    return get_object_tag_counts(tags_count_query, count_implicit=True)
