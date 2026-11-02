"""
Defines asynchronous celery task for content indexing
"""

from __future__ import annotations

import logging

from celery import shared_task
from celery_utils.logged_task import LoggedTask
from edx_django_utils.monitoring import set_code_owner_attribute
from meilisearch.errors import MeilisearchError
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locator import (
    LibraryCollectionLocator,
    LibraryContainerLocator,
    LibraryLocatorV2,
    LibraryUsageLocatorV2,
)

from . import api

log = logging.getLogger(__name__)


@shared_task(base=LoggedTask, autoretry_for=(MeilisearchError, ConnectionError))
@set_code_owner_attribute
def upsert_xblock_index_doc(usage_key_str: str, recursive: bool) -> None:
    """
    Celery task to update the content index document for an XBlock
    """
    usage_key = UsageKey.from_string(usage_key_str)

    log.info("Updating content index document for XBlock with id: %s", usage_key)

    api.upsert_xblock_index_doc(usage_key, recursive)


@shared_task(base=LoggedTask, autoretry_for=(MeilisearchError, ConnectionError))
@set_code_owner_attribute
def upsert_course_blocks_docs(course_key_str: str) -> None:
    """
    Celery task to update the content index document for all XBlocks in a course.
    """
    course_key = CourseKey.from_string(course_key_str)

    log.info("Updating content index documents for XBlocks in course with id: %s", course_key)

    api.index_course(course_key)


@shared_task(base=LoggedTask, autoretry_for=(MeilisearchError, ConnectionError))
@set_code_owner_attribute
def delete_xblock_index_doc(usage_key_str: str) -> None:
    """
    Celery task to delete the content index document for an XBlock
    """
    usage_key = UsageKey.from_string(usage_key_str)

    log.info("Updating content index document for XBlock with id: %s", usage_key)

    # Delete children index data for course blocks.
    api.delete_index_doc(usage_key, delete_children=True)


@shared_task(base=LoggedTask, autoretry_for=(MeilisearchError, ConnectionError))
@set_code_owner_attribute
def upsert_library_block_index_doc(usage_key_str: str) -> None:
    """
    Celery task to update the content index document for a library block
    """
    usage_key = LibraryUsageLocatorV2.from_string(usage_key_str)

    log.info("Updating content index document for library block with id: %s", usage_key)

    api.upsert_library_block_index_doc(usage_key)


@shared_task(base=LoggedTask, autoretry_for=(MeilisearchError, ConnectionError))
@set_code_owner_attribute
def delete_library_block_index_doc(usage_key_str: str) -> None:
    """
    Celery task to delete the content index document for a library block
    """
    usage_key = LibraryUsageLocatorV2.from_string(usage_key_str)

    log.info("Deleting content index document for library block with id: %s", usage_key)

    api.delete_index_doc(usage_key)


@shared_task(base=LoggedTask, autoretry_for=(MeilisearchError, ConnectionError))
@set_code_owner_attribute
def update_content_library_index_docs(library_key_str: str, full_index: bool = False) -> None:
    """
    Celery task to update the content index documents for all library blocks in a library
    """
    library_key = LibraryLocatorV2.from_string(library_key_str)

    log.info("Updating content index documents for library with id: %s", library_key)

    # If full_index is True, also update collections and containers data
    api.upsert_content_library_index_docs(library_key, full_index=full_index)


@shared_task(base=LoggedTask, autoretry_for=(MeilisearchError, ConnectionError))
@set_code_owner_attribute
def update_library_collection_index_doc(collection_key_str: str) -> None:
    """
    Celery task to update the content index document for a library collection
    """
    collection_key = LibraryCollectionLocator.from_string(collection_key_str)
    library_key = collection_key.lib_key

    log.info("Updating content index documents for collection %s in library%s", collection_key, library_key)

    api.upsert_library_collection_index_doc(collection_key)


@shared_task(base=LoggedTask, autoretry_for=(MeilisearchError, ConnectionError))
@set_code_owner_attribute
def update_library_components_collections(collection_key_str: str) -> None:
    """
    Celery task to update the "collections" field for components in the given content library collection.
    """
    collection_key = LibraryCollectionLocator.from_string(collection_key_str)
    library_key = collection_key.lib_key

    log.info("Updating document.collections for library %s collection %s components", library_key, collection_key)

    api.update_library_components_collections(collection_key)


@shared_task(base=LoggedTask, autoretry_for=(MeilisearchError, ConnectionError))
@set_code_owner_attribute
def update_library_containers_collections(collection_key_str: str) -> None:
    """
    Celery task to update the "collections" field for containers in the given content library collection.
    """
    collection_key = LibraryCollectionLocator.from_string(collection_key_str)
    library_key = collection_key.lib_key

    log.info("Updating document.collections for library %s collection %s containers", library_key, collection_key)

    api.update_library_containers_collections(collection_key)


@shared_task(base=LoggedTask, autoretry_for=(MeilisearchError, ConnectionError))
@set_code_owner_attribute
def update_library_container_index_doc(container_key_str: str) -> None:
    """
    Celery task to update the content index document for a library container
    """
    container_key = LibraryContainerLocator.from_string(container_key_str)
    library_key = container_key.lib_key

    log.info("Updating content index documents for container %s in library%s", container_key, library_key)

    api.upsert_library_container_index_doc(container_key)


@shared_task(base=LoggedTask, autoretry_for=(MeilisearchError, ConnectionError))
@set_code_owner_attribute
def delete_library_container_index_doc(container_key_str: str) -> None:
    """
    Celery task to delete the content index document for a library block
    """
    container_key = LibraryContainerLocator.from_string(container_key_str)

    log.info("Deleting content index document for library block with id: %s", container_key)

    api.delete_index_doc(container_key)


@shared_task(base=LoggedTask, autoretry_for=(MeilisearchError, ConnectionError))
@set_code_owner_attribute
def delete_course_index_docs(course_key_str: str) -> None:
    """
    Celery task to delete the content index documents for a Course
    """
    course_key = CourseKey.from_string(course_key_str)

    log.info("Deleting all index documents related to course_key: %s", course_key)

    # Delete children index data for course blocks.
    api.delete_docs_with_context_key(course_key)
