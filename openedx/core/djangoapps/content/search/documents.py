"""
Utilities related to indexing content for search
"""
from __future__ import annotations

import logging
from hashlib import blake2b

from django.core.exceptions import ObjectDoesNotExist
from django.utils.text import slugify
from opaque_keys.edx.keys import ContainerKey, LearningContextKey, UsageKey, OpaqueKey
from opaque_keys.edx.locator import LibraryCollectionLocator, LibraryContainerLocator
from openedx_content import api as content_api
from openedx_content.models_api import Collection
from rest_framework.exceptions import NotFound

from openedx.core.djangoapps.content.search.models import SearchAccess
from openedx.core.djangoapps.content.search.plain_text_math import process_mathjax
from openedx.core.djangoapps.content_libraries import api as lib_api
from openedx.core.djangoapps.content_tagging import api as tagging_api
from openedx.core.djangoapps.xblock import api as xblock_api
from openedx.core.djangoapps.xblock.data import LatestVersion

log = logging.getLogger(__name__)


class Fields:
    """
    Fields that exist on the documents in our search index
    """
    # Meilisearch primary key. String.
    id = "id"
    usage_key = "usage_key"
    type = "type"  # DocType.course_block or DocType.library_block (see below)
    # The block_id part of the usage key for course or library blocks.
    # If it's a collection, the collection.key is stored here.
    # Sometimes human-readable, sometimes a random hex ID
    # Is only unique within the given context_key.
    block_id = "block_id"
    display_name = "display_name"
    description = "description"
    modified = "modified"
    created = "created"
    last_published = "last_published"
    block_type = "block_type"
    problem_types = "problem_types"
    context_key = "context_key"
    org = "org"
    access_id = "access_id"  # .models.SearchAccess.id
    # breadcrumbs: an array of {"display_name": "..."} entries. First one is the name of the course/library itself.
    # After that is the name of any parent Section/Subsection/Unit/etc and its usage_key.
    # It's a list of dictionaries because for now we just include the name of each but in future we may add their IDs.
    # Example: [{"display_name": "My course"}, {"display_name": "Section1", "usage_key": "..."}]}
    breadcrumbs = "breadcrumbs"
    # tags (dictionary)
    # See https://blog.meilisearch.com/nested-hierarchical-facets-guide/
    # and https://www.algolia.com/doc/api-reference/widgets/hierarchical-menu/js/
    # For details on the format of the hierarchical tag data.
    # We currently have a hard-coded limit of 4 levels of tags in the search index (level0..level3).
    tags = "tags"
    tags_taxonomy = "taxonomy"  # subfield of tags, i.e. tags.taxonomy
    tags_level0 = "level0"  # subfield of tags, i.e. tags.level0
    tags_level1 = "level1"
    tags_level2 = "level2"
    tags_level3 = "level3"
    # Collections (dictionary) that this object belongs to.
    # Similarly to tags above, we collect the collection.titles and collection.keys into hierarchical facets.
    collections = "collections"
    collections_display_name = "display_name"
    collections_key = "key"
    # Containers (dictionaries) that this object belongs to.
    units = "units"
    subsections = "subsections"
    sections = "sections"
    containers_display_name = "display_name"
    containers_key = "key"

    sections_display_name = "display_name"
    sections_key = "key"

    # The "content" field is a dictionary of arbitrary data, depending on the block_type.
    # It comes from each XBlock's index_dictionary() method (if present) plus some processing.
    # Text (html) blocks have an "html_content" key in here, capa has "capa_content" and "problem_types", and so on.
    # Containers store their list of child usage keys here.
    content = "content"

    # Collections use this field to communicate how many entities/components they contain.
    # Structural XBlocks may use this one day to indicate how many child blocks they ocntain.
    num_children = "num_children"

    # Publish status can be on of:
    #   "published",
    #   "modified" (for blocks that were published but have been modified since),
    #   "never" (for never-published blocks).
    publish_status = "publish_status"

    # Published data (dictionary) of this object
    published = "published"
    published_display_name = "display_name"
    published_description = "description"
    published_content = "content"
    published_num_children = "num_children"

    # List of children keys
    child_usage_keys = "child_usage_keys"
    # List of children display names
    child_display_names = "child_display_names"

    # Note: new fields or values can be added at any time, but if they need to be indexed for filtering or keyword
    # search, the index configuration will need to be changed, which is only done as part of the 'reindex_studio'
    # command (changing those settings on an large active index is not recommended).


class DocType:
    """
    Values for the 'type' field on each doc in the search index
    """
    course_block = "course_block"
    library_block = "library_block"
    library_container = "library_container"
    collection = "collection"


class PublishStatus:
    """
    Values for the 'publish_status' field on each doc in the search index
    """
    never = "never"
    published = "published"
    modified = "modified"


def meili_id_from_opaque_key(key: OpaqueKey) -> str:
    """
    Meilisearch requires each document to have a primary key that's either an
    integer or a string composed of alphanumeric characters (a-z A-Z 0-9),
    hyphens (-) and underscores (_). Since our opaque keys don't meet this
    requirement, we transform them to a similar slug ID string that does.

    In the future, with Learning Core's data models in place for courseware,
    we could use PublishableEntity's primary key / UUID instead.
    """
    # The slugified key _may_ not be unique so we append a hashed string to make it unique:
    key_str = str(key)
    key_bin = key_str.encode()

    suffix = blake2b(key_bin, digest_size=4, usedforsecurity=False).hexdigest()

    return f"{slugify(key_str)}-{suffix}"


def _meili_access_id_from_context_key(context_key: LearningContextKey) -> int:
    """
    Retrieve the numeric access id for the given course/library context.
    """
    access, _ = SearchAccess.objects.get_or_create(context_key=context_key)
    return access.id


def searchable_doc_for_key(key: OpaqueKey) -> dict:
    """
    Generates a base document identified by its opaque key.
    """
    return {
        Fields.id: meili_id_from_opaque_key(key),
    }


def _get_content_from_block(block) -> dict:
    """
    Get the content from block
    """
    try:
        content_data = block.index_dictionary()
        # Will be something like:
        # {
        #     'content': {'display_name': '...', 'capa_content': '...'},
        #     'content_type': 'CAPA',
        #     'problem_types': ['multiplechoiceresponse']
        # }
        # Which we need to flatten:
        if "content_type" in content_data:
            del content_data["content_type"]  # Redundant with our standard Fields.block_type field.
        if "content" in content_data and isinstance(content_data["content"], dict):
            content = content_data["content"]
            if "display_name" in content:
                del content["display_name"]
            del content_data["content"]
            content_data.update(content)

        return content_data

    except Exception as err:  # pylint: disable=broad-except
        log.exception(f"Failed to process index_dictionary for {block.usage_key}: {err}")


def _get_description_from_block_content(block_type, block_content) -> str:
    """
    Generate description from block content
    """
    result = None

    if block_type == 'html' and 'html_content' in block_content:
        result = block_content['html_content']
    elif block_type == 'problem' and 'capa_content' in block_content:
        result = block_content['capa_content']

    return result


def _fields_from_block(block) -> dict:
    """
    Given an XBlock instance, call its index_dictionary() method to load any
    data that it wants included in the search index. Format into a flat dict.

    Note: the format of index_dictionary() depends on the block type. The base
    class implementation returns only:
        {"content": {"display_name": "..."}, "content_type": "..."}
    """
    block_type = block.scope_ids.block_type
    block_data = {
        Fields.usage_key: str(block.usage_key),
        Fields.block_id: str(block.usage_key.block_id),
        Fields.display_name: xblock_api.get_block_display_name(block),
        Fields.block_type: block_type,
        # This is called context_key so it's the same for courses and libraries
        Fields.context_key: str(block.usage_key.context_key),  # same as lib_key
        Fields.org: str(block.usage_key.context_key.org),
        Fields.access_id: _meili_access_id_from_context_key(block.usage_key.context_key),
        Fields.breadcrumbs: [],
    }
    if hasattr(block, "edited_on"):
        block_data[Fields.modified] = block.edited_on.timestamp()
    # Get the breadcrumbs (course, section, subsection, etc.):
    if block.usage_key.context_key.is_course:  # Getting parent is not yet implemented in Learning Core (for libraries).
        cur_block = block
        while cur_block.parent:
            if not cur_block.has_cached_parent:
                # This is not a big deal, but if you're updating many blocks in the same course at once,
                # this would be very inefficient. Better to recurse the tree top-down with the parent blocks loaded.
                log.warning(f"Updating Studio search index for XBlock {block.usage_key} but ancestors weren't cached.")
            cur_block = cur_block.get_parent()
            parent_data = {
                "display_name": xblock_api.get_block_display_name(cur_block),
            }
            if cur_block.scope_ids.block_type != "course":
                parent_data["usage_key"] = str(cur_block.usage_key)
            block_data[Fields.breadcrumbs].insert(
                0,
                parent_data,
            )
    try:
        content_data = _get_content_from_block(block)
        block_data[Fields.content] = content_data

        # Generate description from the content
        description = _get_description_from_block_content(block_type, content_data)
        if description:
            block_data[Fields.description] = process_mathjax(description)

    except Exception as err:  # pylint: disable=broad-except
        log.exception(f"Failed to process index_dictionary for {block.usage_key}: {err}")
    return block_data


def _published_data_from_block(block_published) -> dict:
    """
    Given an library block get the published data.
    """
    result = {
        Fields.published: {
            Fields.published_display_name: xblock_api.get_block_display_name(block_published),
        }
    }

    try:
        content_data = _get_content_from_block(block_published)

        description = _get_description_from_block_content(
            block_published.scope_ids.block_type,
            content_data,
        )

        if description:
            result[Fields.published][Fields.published_description] = description
    except Exception as err:  # pylint: disable=broad-except
        log.exception(f"Failed to process index_dictionary for {block_published.usage_key}: {err}")

    return result


def searchable_doc_for_library_block(xblock_metadata: lib_api.LibraryXBlockMetadata) -> dict:
    """
    Generate a dictionary document suitable for ingestion into a search engine
    like Meilisearch or Elasticsearch, so that the given library block can be
    found using faceted search.

    Datetime fields (created, modified, last_published) are serialized to POSIX timestamps so that they can be used to
    sort the search results.
    """
    library_name = lib_api.get_library(xblock_metadata.usage_key.context_key).title
    block = xblock_api.load_block(xblock_metadata.usage_key, user=None)

    publish_status = PublishStatus.published
    try:
        block_published = xblock_api.load_block(xblock_metadata.usage_key, user=None, version=LatestVersion.PUBLISHED)
        if xblock_metadata.last_published and xblock_metadata.last_published < xblock_metadata.modified:
            publish_status = PublishStatus.modified
    except NotFound:
        # Never published
        block_published = None
        publish_status = PublishStatus.never

    doc = searchable_doc_for_key(xblock_metadata.usage_key)
    doc.update({
        Fields.type: DocType.library_block,
        Fields.breadcrumbs: [],
        Fields.created: xblock_metadata.created.timestamp(),
        Fields.modified: xblock_metadata.modified.timestamp(),
        Fields.last_published: xblock_metadata.last_published.timestamp() if xblock_metadata.last_published else None,
        Fields.publish_status: publish_status,
    })

    doc.update(_fields_from_block(block))

    if block_published:
        doc.update(_published_data_from_block(block_published))

    # Add the breadcrumbs. In v2 libraries, the library itself is not a "parent" of the XBlocks so we add it here:
    doc[Fields.breadcrumbs] = [{"display_name": library_name}]

    return doc


def searchable_doc_for_course_block(block) -> dict:
    """
    Generate a dictionary document suitable for ingestion into a search engine
    like Meilisearch or Elasticsearch, so that the given course block can be
    found using faceted search.
    """
    doc = searchable_doc_for_key(block.usage_key)
    doc.update({
        Fields.type: DocType.course_block,
    })

    doc.update(_fields_from_block(block))

    return doc


def searchable_doc_tags(object_id: OpaqueKey) -> dict:
    """
    Given an XBlock, course, library, etc., get the tag data for its index doc.

    See the comments above on "Field.tags" for an explanation of the format.

    e.g. for something tagged "Difficulty: Hard" and "Location: Vancouver" this
    would return:
        {
            "tags": {
                "taxonomy": ["Location", "Difficulty"],
                "level0": ["Location > North America", "Difficulty > Hard"],
                "level1": ["Location > North America > Canada"],
                "level2": ["Location > North America > Canada > Vancouver"],
            }
        }

    Note: despite what you might expect, because this is only used for the
    filtering/refinement UI, it's fine if this is a one-way transformation.
    It's not necessary to be able to re-construct the exact tag IDs nor taxonomy
    IDs from this data that's stored in the search index. It's just a bunch of
    strings in a particular format that the frontend knows how to render to
    support hierarchical refinement by tag.
    """
    # Note that we could improve performance for indexing many components from the same library/course,
    # if we used get_all_object_tags() to load all the tags for the library in a single query rather than loading the
    # tags for each component separately.
    all_tags = tagging_api.get_object_tags(str(object_id)).all()
    if not all_tags:
        # Clear out tags in the index when unselecting all tags for the block, otherwise
        # it would remain the last value if a cleared Fields.tags field is not included
        return {Fields.tags: {}}
    result = {
        Fields.tags_taxonomy: [],
        Fields.tags_level0: [],
        # ... other levels added as needed
    }
    for obj_tag in all_tags:
        # Add the taxonomy name:
        if obj_tag.taxonomy.name not in result[Fields.tags_taxonomy]:
            result[Fields.tags_taxonomy].append(obj_tag.taxonomy.name)
        # Taxonomy name plus each level of tags, in a list: # e.g. ["Location", "North America", "Canada", "Vancouver"]
        parts = [obj_tag.taxonomy.name] + obj_tag.get_lineage()
        parts = [part.replace(" > ", " _ ") for part in parts]  # Escape our separator.
        # Now we build each level (tags.level0, tags.level1, etc.) as applicable.
        # We have a hard-coded limit of 4 levels of tags for now (see Fields.tags above).
        # A tag like "Difficulty: Hard" will only result in one level (tags.level0)
        # But a tag like "Location: North America > Canada > Vancouver" would result in three levels (tags.level0:
        #   "North America", tags.level1: "North America > Canada", tags.level2: "North America > Canada > Vancouver")
        # See the comments above on "Field.tags" for an explanation of why we use this format (basically it's the format
        # required by the Instantsearch frontend).
        for level in range(4):
            # We use '>' as a separator because it's the default for the Instantsearch frontend library, and our
            # preferred separator (\t) used in the database is ignored by Meilisearch since it's whitespace.
            new_value = " > ".join(parts[0:level + 2])
            if f"level{level}" not in result:
                result[f"level{level}"] = [new_value]
            elif new_value not in result[f"level{level}"]:
                result[f"level{level}"].append(new_value)
            if len(parts) == level + 2:
                break  # We have all the levels for this tag now (e.g. parts=["Difficulty", "Hard"] -> need level0 only)

    return {Fields.tags: result}


def searchable_doc_collections(object_id: OpaqueKey) -> dict:
    """
    Given an XBlock, course, library, etc., get the collections for its index doc.

    e.g. for something in Collections "COL_A" and "COL_B", this would return:
        {
            "collections":  {
                "display_name": ["Collection A", "Collection B"],
                "key": ["COL_A", "COL_B"],
            }
        }

    If the object is in no collections, returns:
        {
            "collections":  {
                "display_name": [],
                "key": [],
            },
        }

    """
    result = {
        Fields.collections: {
            Fields.collections_display_name: [],
            Fields.collections_key: [],
        }
    }

    # Gather the collections associated with this object
    collections = None
    try:
        if isinstance(object_id, UsageKey):
            component = lib_api.get_component_from_usage_key(object_id)
            collections = content_api.get_entity_collections(
                component.learning_package_id,
                component.key,
            ).values('key', 'title')
        elif isinstance(object_id, LibraryContainerLocator):
            container = lib_api.get_container(object_id, include_collections=True)
            collections = container.collections
        else:
            log.warning(f"Unexpected key type for {object_id}")

    except ObjectDoesNotExist:
        log.warning(f"No library item found for {object_id}")

    if not collections:
        return result

    for collection in collections:
        result[Fields.collections][Fields.collections_display_name].append(collection["title"])
        result[Fields.collections][Fields.collections_key].append(collection["key"])

    return result


def searchable_doc_containers(object_id: OpaqueKey, container_type: str) -> dict:
    """
    Given an XBlock, course, library, etc., get the containers that it is part of for its index doc.

    e.g. for something in Units "UNIT_A" and "UNIT_B", this would return:
        {
            "units":  {
                "display_name": ["Unit A", "Unit B"],
                "key": ["UNIT_A", "UNIT_B"],
            }
        }

    If the object is in no containers, returns:
        {
            "sections":  {
                "display_name": [],
                "key": [],
            },
        }
    """
    container_field = getattr(Fields, container_type)
    result = {
        container_field: {
            Fields.containers_display_name: [],
            Fields.containers_key: [],
        }
    }

    # Gather the units associated with this object
    containers = None
    try:
        if isinstance(object_id, OpaqueKey):
            containers = lib_api.get_containers_contains_item(object_id)
        else:
            log.warning(f"Unexpected key type for {object_id}")

    except ObjectDoesNotExist:
        log.warning(f"No library item found for {object_id}")

    if not containers:
        return result

    for container in containers:
        result[container_field][Fields.containers_display_name].append(container.display_name)
        result[container_field][Fields.containers_key].append(str(container.container_key))

    return result


def searchable_doc_for_collection(
    collection_key: LibraryCollectionLocator,
    *,
    # Optionally provide the collection if we've already fetched one
    collection: Collection | None = None,
) -> dict:
    """
    Generate a dictionary document suitable for ingestion into a search engine
    like Meilisearch or Elasticsearch, so that the given collection can be
    found using faceted search.

    If no collection is found for the given library_key + collection_key, the returned document will contain only basic
    information derived from the collection usage key, and no Fields.type value will be included in the returned dict.
    """
    doc = searchable_doc_for_key(collection_key)

    try:
        collection = collection or lib_api.get_library_collection_from_locator(collection_key)
    except lib_api.ContentLibraryCollectionNotFound:
        # Collection not found, so we can only return the base doc
        pass

    if collection:
        assert collection.key == collection_key.collection_id

        draft_num_children = content_api.filter_publishable_entities(
            collection.entities,
            has_draft=True,
        ).count()
        published_num_children = content_api.filter_publishable_entities(
            collection.entities,
            has_published=True,
        ).count()

        doc.update({
            Fields.context_key: str(collection_key.context_key),
            Fields.org: str(collection_key.org),
            Fields.usage_key: str(collection_key),
            Fields.block_id: collection.key,
            Fields.type: DocType.collection,
            Fields.display_name: collection.title,
            Fields.description: collection.description,
            Fields.created: collection.created.timestamp(),
            Fields.modified: collection.modified.timestamp(),
            Fields.num_children: draft_num_children,
            Fields.published: {
                Fields.published_num_children: published_num_children,
            },
            Fields.access_id: _meili_access_id_from_context_key(collection_key.context_key),
            Fields.breadcrumbs: [{"display_name": collection.learning_package.title}],
        })

        # Disabled collections should be removed from the search index,
        # so we mark them as _disabled
        if not collection.enabled:
            doc['_disabled'] = True

    return doc


def searchable_doc_for_container(
    container_key: ContainerKey,
) -> dict:
    """
    Generate a dictionary document suitable for ingestion into a search engine
    like Meilisearch or Elasticsearch, so that the given container can be
    found using faceted search.

    If no container is found for the given container key, the returned document
    will contain only basic information derived from the container key, and no
    Fields.type value will be included in the returned dict.
    """
    doc = {
        Fields.id: meili_id_from_opaque_key(container_key),
        Fields.context_key: str(container_key.context_key),
        Fields.org: str(container_key.org),
        # In the future, this may be either course_container or library_container
        Fields.type: DocType.library_container,
        # To check if it is "unit", "section", "subsection", etc..
        Fields.block_type: container_key.container_type,
        Fields.usage_key: str(container_key),  # Field name isn't exact but this is the closest match
        Fields.block_id: container_key.container_id,  # Field name isn't exact but this is the closest match
        Fields.access_id: _meili_access_id_from_context_key(container_key.context_key),
        Fields.publish_status: PublishStatus.never,
        Fields.last_published: None,
    }

    try:
        container = lib_api.get_container(container_key)
    except lib_api.ContentLibraryContainerNotFound:
        # Container not found, so we can only return the base doc
        log.error(f"Container {container_key} not found")
        return doc

    draft_children = lib_api.get_container_children(
        container_key,
        published=False,
    )
    publish_status = PublishStatus.published
    if container.last_published is None:
        publish_status = PublishStatus.never
    elif container.has_unpublished_changes:
        publish_status = PublishStatus.modified

    container_type = lib_api.ContainerType(container_key.container_type)

    def get_child_keys(children) -> list[str]:
        match container_type:
            case lib_api.ContainerType.Unit:
                return [
                    str(child.usage_key)
                    for child in children
                ]
            case lib_api.ContainerType.Subsection | lib_api.ContainerType.Section:
                return [
                    str(child.container_key)
                    for child in children
                ]

    def get_child_names(children) -> list[str]:
        return [child.display_name for child in children]

    doc.update({
        Fields.display_name: container.display_name,
        Fields.created: container.created.timestamp(),
        Fields.modified: container.modified.timestamp(),
        Fields.num_children: len(draft_children),
        Fields.content: {
            Fields.child_usage_keys: get_child_keys(draft_children),
            Fields.child_display_names: get_child_names(draft_children),
        },
        Fields.publish_status: publish_status,
        Fields.last_published: container.last_published.timestamp() if container.last_published else None,
    })
    library = lib_api.get_library(container_key.context_key)
    if library:
        doc[Fields.breadcrumbs] = [{"display_name": library.title}]

    if container.published_version_num is not None:
        published_children = lib_api.get_container_children(
            container_key,
            published=True,
        )
        doc[Fields.published] = {
            Fields.published_display_name: container.published_display_name,
            Fields.published_num_children: len(published_children),
            Fields.published_content: {
                Fields.child_usage_keys: get_child_keys(published_children),
                Fields.child_display_names: get_child_names(published_children),
            },
        }

    return doc
