"""Service layer for the v2 container endpoint."""

import logging
from urllib.parse import quote_plus

from django.urls import reverse
from rest_framework.exceptions import NotFound

from cms.djangoapps.contentstore.helpers import (
    get_parent_xblock,
    xblock_studio_url,
    xblock_type_display_name,
)
from cms.djangoapps.contentstore.utils import (
    _get_component_templates_for_xblock,
    get_library_content_picker_url,
    get_lms_link_for_item,
    get_sequence_usage_keys,
    get_sibling_urls,
    reverse_course_url,
)
from cms.djangoapps.contentstore.views.component import LIBRARY_BLOCK_TYPES, get_component_templates, get_unit_tags
from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import (
    add_container_page_publishing_info,
    create_xblock_info,
)
from openedx.core.djangoapps.content_tagging.toggles import is_tagging_feature_disabled
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)

CONTAINER_NOT_FOUND_MESSAGE = "No container in this course has this key."
FULL_VIEW_NOT_AVAILABLE_MESSAGE = "The full view is available only for a unit or a container inside a unit."


class _Placement:
    """
    Where a container sits: the chain of blocks from it up to the course, and
    the unit and subsection it belongs to when it is a unit or inside one.
    """

    def __init__(self, chain, unit_index):
        self.chain = chain
        self.unit = chain[unit_index] if unit_index is not None else None
        self.subsection = chain[unit_index + 1] if unit_index is not None else None

    @property
    def container(self):
        return self.chain[0]

    @property
    def is_unit(self):
        return self.unit is self.container


def _place(usage_key, block):
    """
    Return where ``block`` sits, or raise ``NotFound`` when it is not a container of the course.

    A container is a block other than the course that can hold children and
    that the course reaches through its children: a section, a subsection, a
    unit (a vertical placed directly in a subsection), or a container placed
    in any of them. The course itself, components, and blocks the course does
    not reach are not containers.
    """
    if not block.has_children or block.category == "course":
        log.info("Container %s requested, which is a %s.", usage_key, block.category)
        raise NotFound(CONTAINER_NOT_FOUND_MESSAGE)
    chain = [block]
    parent = get_parent_xblock(block)
    while parent is not None:
        chain.append(parent)
        parent = get_parent_xblock(parent)
    if chain[-1].category != "course":
        log.info("Container %s requested, which the course does not reach.", usage_key)
        raise NotFound(CONTAINER_NOT_FOUND_MESSAGE)
    unit_index = next(
        (
            index for index, ancestor in enumerate(chain[:-1])
            if ancestor.category == "vertical" and chain[index + 1].category == "sequential"
        ),
        None,
    )
    return _Placement(chain, unit_index)


def _load(store, usage_key):
    """Return the block ``usage_key`` names and where it sits, or raise ``NotFound``."""
    try:
        block = store.get_item(usage_key, depth=1)
    except ItemNotFoundError as error:
        log.info("Container %s requested, which does not exist: %s", usage_key, error)
        raise NotFound(CONTAINER_NOT_FOUND_MESSAGE) from error
    return block, _place(usage_key, block)


def _position(placement):
    """
    The 1-based position of the container's unit among its subsection's
    children, as the Studio unit page counts it: children that cannot be
    loaded are not counted.
    """
    index = 1
    for child in placement.subsection.get_children():
        if child.location == placement.unit.location:
            break
        index += 1
    return index


def get_container(usage_key):
    """
    Return the container ``usage_key`` names, with the blocks above it.

    Raises ``NotFound`` when no block has the key or the block is not a
    container of the course. ``unit_id`` and ``position`` are None above unit
    level. Nothing is written.

    The database and Mongo reads depend on how deeply the container is
    nested, not on the size of the course: the structure is read once and the
    walk up to the course works on it. That walk, and loading the unit's
    siblings to count its position, still take time in proportion to the
    structure.
    """
    store = modulestore()
    with store.bulk_operations(usage_key.course_key):
        block, placement = _load(store, usage_key)
        unit = placement.unit
        return {
            "id": str(block.location),
            "category": block.category,
            "display_name": block.display_name_with_default,
            "display_type": xblock_type_display_name(block),
            "course_key": str(block.location.course_key),
            "is_unit": placement.is_unit,
            "unit_id": str(unit.location) if unit is not None else None,
            "position": _position(placement) if unit is not None else None,
            "ancestors": [
                {
                    "id": str(ancestor.location),
                    "category": ancestor.category,
                    "display_name": ancestor.display_name_with_default,
                }
                for ancestor in reversed(placement.chain[1:-1])
            ],
        }


def _ancestor_xblocks(placement):
    """
    Each ancestor of the container below the topmost block, outermost first,
    with every child of that ancestor's parent.
    """
    chain = placement.chain
    entries = [
        {
            "title": chain[index].display_name_with_default,
            "is_last": index == 1,
            "children": [
                {
                    "url": xblock_studio_url(child),
                    "display_name": child.display_name_with_default,
                    "usage_key": str(child.location),
                }
                for child in chain[index + 1].get_children()
            ],
        }
        for index in range(1, len(chain) - 1)
    ]
    entries.reverse()
    return entries


def get_container_page(request, usage_key):
    """
    Return everything the Studio unit page shows for the container ``usage_key`` names.

    Raises ``NotFound`` as ``get_container`` does, and also for a container
    that is neither a unit nor inside one, such as a section or subsection.
    The requesting user's clipboard is not part of the result; it is read on
    its own. Nothing is written.
    """
    store = modulestore()
    course_key = usage_key.course_key
    with store.bulk_operations(course_key):
        block, placement = _load(store, usage_key)
        if placement.unit is None:
            log.info("Unit page data requested for %s, which is not a unit or inside one.", usage_key)
            raise NotFound(FULL_VIEW_NOT_AVAILABLE_MESSAGE)
        course = store.get_course(course_key)
        is_unit_page = placement.is_unit

        prev_url, next_url = get_sibling_urls(placement.subsection, placement.unit.location)
        show_unit_tags = not is_tagging_feature_disabled()
        unit_tags = get_unit_tags(usage_key) if show_unit_tags and is_unit_page else None
        xblock_info = create_xblock_info(block, include_ancestor_info=is_unit_page, tags=unit_tags)
        if is_unit_page:
            add_container_page_publishing_info(block, xblock_info)
        library_block_types = [block_type["component"] for block_type in LIBRARY_BLOCK_TYPES]
        is_library_block = block.location.block_type in library_block_types

        return {
            "language_code": request.LANGUAGE_CODE,
            "xblock": {
                "display_name": block.display_name_with_default,
                "display_type": xblock_type_display_name(block),
                "category": block.category,
            },
            "is_unit_page": is_unit_page,
            "is_collapsible": is_library_block,
            "position": _position(placement),
            # Quoted because the unit page places them in a query string.
            "prev_url": quote_plus(prev_url) if prev_url else None,
            "next_url": quote_plus(next_url) if next_url else None,
            "new_unit_category": "vertical",
            "outline_url": f"{reverse_course_url('course_handler', course.id)}?format=concise",
            "ancestor_xblocks": _ancestor_xblocks(placement),
            "component_templates": _get_component_templates_for_xblock(get_component_templates(course), block),
            "xblock_info": xblock_info,
            "draft_preview_link": get_lms_link_for_item(block.location, preview=True),
            "published_preview_link": get_lms_link_for_item(block.location),
            "show_unit_tags": show_unit_tags,
            "is_fullwidth_content": is_library_block,
            "assets_url": reverse("assets_handler", kwargs={"course_key_string": course.id}),
            "unit_block_id": placement.unit.location.block_id,
            "subsection_location": str(placement.subsection.location),
            "course_sequence_ids": get_sequence_usage_keys(course),
            "library_content_picker_url": get_library_content_picker_url(course.id),
        }
