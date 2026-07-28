"""
This module contains helper functions to build a object tree with object tags.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator, Union  # noqa: UP035

from attrs import define
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locator import LibraryLocatorV2
from xblock.core import XBlock

import openedx.core.djangoapps.content_libraries.api as library_api
from xmodule.modulestore.django import modulestore

from ..types import TagValuesByObjectIdDict, TagValuesByTaxonomyIdDict


@define
class TaggedContent:
    """
    A tagged content, with its tags and children.
    """
    display_name: str
    block_id: str
    category: str
    object_tags: TagValuesByTaxonomyIdDict
    children: list[TaggedContent] | None


def iterate_with_level(
    tagged_content: TaggedContent, level: int = 0
) -> Iterator[tuple[TaggedContent, int]]:
    """
    Iterator that yields the tagged content and the level of the block
    """
    yield tagged_content, level
    if tagged_content.children:
        for child in tagged_content.children:
            yield from iterate_with_level(child, level + 1)


def _get_course_tagged_object_and_children(
    course_key: CourseKey, object_tag_cache: TagValuesByObjectIdDict
) -> tuple[TaggedContent, list[XBlock]]:
    """
    Returns a TaggedContent with course metadata with its tags, and its children.
    """
    store = modulestore()

    course = store.get_course(course_key)
    if course is None:
        raise ValueError(f"Course not found: {course_key}")

    course_id = str(course_key)

    tagged_course = TaggedContent(
        display_name=course.display_name_with_default,
        block_id=course_id,
        category=course.category,
        object_tags=object_tag_cache.get(course_id, {}),
        children=None,
    )

    return tagged_course, course.children if course.has_children else []


def _get_library_tagged_object_and_children(
    library_key: LibraryLocatorV2, object_tag_cache: TagValuesByObjectIdDict
) -> tuple[TaggedContent, list[library_api.LibraryXBlockMetadata]]:
    """
    Returns a TaggedContent with library metadata with its tags, and its children.
    """
    library = library_api.get_library(library_key)
    if library is None:
        raise ValueError(f"Library not found: {library_key}")

    library_id = str(library_key)

    tagged_library = TaggedContent(
        display_name=library.title,
        block_id=library_id,
        category='library',
        object_tags=object_tag_cache.get(library_id, {}),
        children=None,
    )

    library_components = library_api.get_library_components(library_key)
    children = [
        library_api.LibraryXBlockMetadata.from_component(library_key, component)
        for component in library_components
    ]

    return tagged_library, children


def _get_xblock_tagged_object_and_children(
    usage_key: UsageKey, object_tag_cache: TagValuesByObjectIdDict
) -> tuple[TaggedContent, list[XBlock]]:
    """
    Returns a TaggedContent with xblock metadata with its tags, and its children.
    """
    store = modulestore()
    block = store.get_item(usage_key)
    block_id = str(usage_key)
    tagged_block = TaggedContent(
        display_name=block.display_name_with_default,
        block_id=block_id,
        category=block.category,
        object_tags=object_tag_cache.get(block_id, {}),
        children=None,
    )

    return tagged_block, block.children if block.has_children else []


def _get_library_block_tagged_object(
    library_block: library_api.LibraryXBlockMetadata, object_tag_cache: TagValuesByObjectIdDict
) -> tuple[TaggedContent, None]:
    """
    Returns a TaggedContent with library content block metadata and its tags,
    and 'None' as children.
    """
    block_id = str(library_block.usage_key)
    tagged_library_block = TaggedContent(
        display_name=library_block.display_name,
        block_id=block_id,
        category=library_block.usage_key.block_type,
        object_tags=object_tag_cache.get(block_id, {}),
        children=None,
    )

    return tagged_library_block, None


def build_object_tree_with_objecttags(
    content_key: LibraryLocatorV2 | CourseKey,
    object_tag_cache: TagValuesByObjectIdDict,
) -> TaggedContent:
    """
    Returns the object with the tags associated with it.
    """
    get_tagged_children: Union[  # noqa: UP007
        # _get_course_tagged_object_and_children type
        Callable[[library_api.LibraryXBlockMetadata, dict[str, dict[int, list[Any]]]], tuple[TaggedContent, None]],
        # _get_library_block_tagged_object type
        Callable[[UsageKey, dict[str, dict[int, list[Any]]]], tuple[TaggedContent, list[Any]]]
    ]
    if isinstance(content_key, CourseKey):
        tagged_content, children = _get_course_tagged_object_and_children(
            content_key, object_tag_cache
        )
        get_tagged_children = _get_xblock_tagged_object_and_children
    elif isinstance(content_key, LibraryLocatorV2):
        tagged_content, children = _get_library_tagged_object_and_children(
            content_key, object_tag_cache
        )
        get_tagged_children = _get_library_block_tagged_object
    else:
        raise ValueError(f"Invalid content_key: {type(content_key)} -> {content_key}")

    blocks: list[tuple[TaggedContent, list | None]] = [(tagged_content, children)]

    while blocks:
        tagged_block, block_children = blocks.pop()
        tagged_block.children = []

        if not block_children:
            continue

        for child in block_children:
            tagged_child, child_children = get_tagged_children(child, object_tag_cache)
            tagged_block.children.append(tagged_child)
            blocks.append((tagged_child, child_children))

    return tagged_content
