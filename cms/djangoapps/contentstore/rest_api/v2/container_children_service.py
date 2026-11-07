"""Service layer for the v2 container children endpoint."""

import logging

from rest_framework.exceptions import NotFound

from cms.djangoapps.contentstore.helpers import xblock_studio_url, xblock_type_display_name
from cms.djangoapps.contentstore.utils import (
    get_user_partition_info,
    get_visibility_partition_info,
    get_xblock_render_error,
    get_xblock_validation_messages,
)
from cms.lib.xblock.upstream_sync import UpstreamLink
from openedx.core.djangoapps.content_tagging.toggles import is_tagging_feature_disabled
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)

CONTAINER_NOT_FOUND_MESSAGE = "No container in this course has this key."
RENDER_ERROR_MESSAGE = "This block could not be rendered. The cause is in the server log."


def _load_container(store, usage_key):
    """
    Return the container ``usage_key`` names, with its children loaded, or raise ``NotFound``.

    A container is a block that can hold children and is not the course
    itself. The course's own children are served by the course outline.
    """
    try:
        block = store.get_item(usage_key, depth=1)
    except ItemNotFoundError as error:
        log.info("Children of %s requested, which does not exist: %s", usage_key, error)
        raise NotFound(CONTAINER_NOT_FOUND_MESSAGE) from error
    if not block.has_children or block.category == "course":
        log.info("Children of %s requested, which is a %s.", usage_key, block.category)
        raise NotFound(CONTAINER_NOT_FOUND_MESSAGE)
    return block


def _summary(child):
    """The short description of a child."""
    return {
        "id": str(child.location),
        "category": child.category,
        "display_name": child.display_name_with_default,
        "display_type": xblock_type_display_name(child),
        "has_children": child.has_children,
    }


def _actions(container, child, can_manage_tags):
    """What the Studio unit page lets an author do with ``child``."""
    is_course = child.scope_ids.usage_id.context_key.is_course
    # A child that holds children and has no page of its own is edited inline,
    # where copying, moving and the other whole-block actions do not apply.
    show_inline = child.has_children and not xblock_studio_url(child, parent_xblock=container)
    show_other_action = not show_inline and is_course
    return {
        "can_copy": show_other_action,
        "can_duplicate": show_other_action,
        "can_move": show_other_action,
        "can_manage_access": show_other_action,
        "can_delete": is_course,
        "can_manage_tags": can_manage_tags,
    }


def _render_error(request, child):
    """
    The message to show for a child that fails to render, or an empty string.

    The cause comes from the block's own code and may expose internals, so it
    is logged with the child's key rather than returned.
    """
    cause = get_xblock_render_error(request, child)
    if not cause:
        return ""
    log.warning("Rendering %s for the children list failed: %s", child.location, cause)
    return RENDER_ERROR_MESSAGE


def _detail(request, container, child, course, can_manage_tags):
    """Everything the Studio unit page shows about ``child``."""
    upstream_link = UpstreamLink.try_get_for_block(child, log_error=False)
    return {
        "name": child.display_name_with_default,
        "block_id": str(child.location),
        "block_type": child.location.block_type,
        "user_partition_info": get_visibility_partition_info(child, course=course),
        "user_partitions": get_user_partition_info(child, course=course),
        # A block with no upstream is by far the commonest case, so it is
        # null rather than a record of empty fields.
        "upstream_link": upstream_link.to_json(include_child_info=True) if upstream_link.upstream_ref else None,
        "actions": _actions(container, child, can_manage_tags),
        "validation_messages": get_xblock_validation_messages(child),
        "render_error": _render_error(request, child),
    }


def list_container_children(request, usage_key, full, paginate):
    """
    Return the children of the container ``usage_key`` names, in the order the container stores them.

    ``paginate(children, build_page)`` receives every loadable child and a
    function that describes one page of them; it returns the response. The
    children that make up the answer are the ones that can be loaded, so a
    dangling reference in the container is left out, and the fields that are
    costly to work out (partitions, upstream link, validation, rendering) are
    worked out for the requested page only. ``full`` selects them.

    Raises ``NotFound`` when no block has the key or the block is not a
    container. Nothing is written.
    """
    store = modulestore()
    with store.bulk_operations(usage_key.course_key):
        container = _load_container(store, usage_key)
        children = container.get_children()
        if not full:
            return paginate(children, lambda page: [_summary(child) for child in page])
        # Loaded once and shared by every child, as the partitions are
        # defined on the course.
        course = store.get_course(usage_key.course_key)
        can_manage_tags = not is_tagging_feature_disabled()
        return paginate(
            children,
            lambda page: [_detail(request, container, child, course, can_manage_tags) for child in page],
        )
