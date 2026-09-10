"""
Defines asynchronous celery task for content indexing

Every task here talks to Meilisearch, so every task inherits whatever latency the
search backend is having. Three of the knobs below bound that:

``max_retries`` bounds the number of attempts. It is the only one of the three that
also applies on the inline path: the signal handlers in ``handlers.py`` run several
of these with ``.apply()`` so that the index is updated before the Authoring MFE
refetches, which puts the whole attempt sequence inside a Studio HTTP request. Celery
runs an eager retry immediately, so the backoff below does not slow that path down;
only the attempt count limits it.

``retry_backoff`` / ``retry_backoff_max`` / ``retry_jitter`` bound how hard a queued
retry storm hits a struggling Meilisearch. Celery's default is a flat 180s, which is
both too long for the request-path tasks and unjittered for the rest.

``soft_time_limit`` / ``time_limit`` bound a single attempt. These are enforced by the
worker's prefork pool and are therefore ignored under ``.apply()``; they exist so a
task that blocks on Meilisearch cannot occupy a worker slot indefinitely.
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

# Tasks the handlers run inline with .apply(), so their attempts land inside a Studio
# HTTP request. Each is a single-document write; two extra attempts is the useful
# amount before the caller is better off failing fast.
_REQUEST_PATH_TASK = {
    "base": LoggedTask,
    "autoretry_for": (MeilisearchError, ConnectionError),
    "max_retries": 2,
    "retry_backoff": 1,
    "retry_backoff_max": 8,
    "retry_jitter": True,
    "soft_time_limit": 10,
    "time_limit": 15,
}

# Tasks only ever reached through .delay(). Nothing is waiting on them, so they get
# more attempts and a longer per-attempt budget.
_BACKGROUND_TASK = {
    "base": LoggedTask,
    "autoretry_for": (MeilisearchError, ConnectionError),
    "max_retries": 3,
    "retry_backoff": True,
    "retry_backoff_max": 60,
    "retry_jitter": True,
    "soft_time_limit": 60,
    "time_limit": 90,
}

# Whole-context walks: these load every block in a course or library from the
# modulestore before writing, which legitimately takes minutes on a large context.
_BULK_TASK = {
    "base": LoggedTask,
    "autoretry_for": (MeilisearchError, ConnectionError),
    "max_retries": 2,
    "retry_backoff": True,
    "retry_backoff_max": 300,
    "retry_jitter": True,
    "soft_time_limit": 30 * 60,
    "time_limit": 31 * 60,
}


@shared_task(**_BACKGROUND_TASK)
@set_code_owner_attribute
def upsert_xblock_index_doc(usage_key_str: str, recursive: bool) -> None:
    """
    Celery task to update the content index document for an XBlock
    """
    usage_key = UsageKey.from_string(usage_key_str)

    log.info("Updating content index document for XBlock with id: %s", usage_key)

    api.upsert_xblock_index_doc(usage_key, recursive)


@shared_task(**_BULK_TASK)
@set_code_owner_attribute
def upsert_course_blocks_docs(course_key_str: str) -> None:
    """
    Celery task to update the content index document for all XBlocks in a course.
    """
    course_key = CourseKey.from_string(course_key_str)

    log.info("Updating content index documents for XBlocks in course with id: %s", course_key)

    api.index_course(course_key)


@shared_task(**_BACKGROUND_TASK)
@set_code_owner_attribute
def delete_xblock_index_doc(usage_key_str: str) -> None:
    """
    Celery task to delete the content index document for an XBlock
    """
    usage_key = UsageKey.from_string(usage_key_str)

    log.info("Updating content index document for XBlock with id: %s", usage_key)

    # Delete children index data for course blocks.
    api.delete_index_doc(usage_key, delete_children=True)


@shared_task(**_REQUEST_PATH_TASK)
@set_code_owner_attribute
def upsert_library_block_index_doc(usage_key_str: str) -> None:
    """
    Celery task to update the content index document for a library block
    """
    usage_key = LibraryUsageLocatorV2.from_string(usage_key_str)

    log.info("Updating content index document for library block with id: %s", usage_key)

    api.upsert_library_block_index_doc(usage_key)


@shared_task(**_REQUEST_PATH_TASK)
@set_code_owner_attribute
def delete_library_block_index_doc(usage_key_str: str) -> None:
    """
    Celery task to delete the content index document for a library block
    """
    usage_key = LibraryUsageLocatorV2.from_string(usage_key_str)

    log.info("Deleting content index document for library block with id: %s", usage_key)

    api.delete_index_doc(usage_key)


@shared_task(**_BULK_TASK)
@set_code_owner_attribute
def update_content_library_index_docs(library_key_str: str, full_index: bool = False) -> None:
    """
    Celery task to update the content index documents for all library blocks in a library
    """
    library_key = LibraryLocatorV2.from_string(library_key_str)

    log.info("Updating content index documents for library with id: %s", library_key)

    # If full_index is True, also update collections and containers data
    api.upsert_content_library_index_docs(library_key, full_index=full_index)


@shared_task(**_REQUEST_PATH_TASK)
@set_code_owner_attribute
def update_library_collection_index_doc(collection_key_str: str) -> None:
    """
    Celery task to update the content index document for a library collection
    """
    collection_key = LibraryCollectionLocator.from_string(collection_key_str)
    library_key = collection_key.lib_key

    log.info("Updating content index documents for collection %s in library%s", collection_key, library_key)

    api.upsert_library_collection_index_doc(collection_key)


@shared_task(**_BACKGROUND_TASK)
@set_code_owner_attribute
def update_library_components_collections(collection_key_str: str) -> None:
    """
    Celery task to update the "collections" field for components in the given content library collection.
    """
    collection_key = LibraryCollectionLocator.from_string(collection_key_str)
    library_key = collection_key.lib_key

    log.info("Updating document.collections for library %s collection %s components", library_key, collection_key)

    api.update_library_components_collections(collection_key)


@shared_task(**_BACKGROUND_TASK)
@set_code_owner_attribute
def update_library_containers_collections(collection_key_str: str) -> None:
    """
    Celery task to update the "collections" field for containers in the given content library collection.
    """
    collection_key = LibraryCollectionLocator.from_string(collection_key_str)
    library_key = collection_key.lib_key

    log.info("Updating document.collections for library %s collection %s containers", library_key, collection_key)

    api.update_library_containers_collections(collection_key)


@shared_task(**_REQUEST_PATH_TASK)
@set_code_owner_attribute
def update_library_container_index_doc(container_key_str: str) -> None:
    """
    Celery task to update the content index document for a library container
    """
    container_key = LibraryContainerLocator.from_string(container_key_str)
    library_key = container_key.lib_key

    log.info("Updating content index documents for container %s in library%s", container_key, library_key)

    api.upsert_library_container_index_doc(container_key)


@shared_task(**_REQUEST_PATH_TASK)
@set_code_owner_attribute
def delete_library_container_index_doc(container_key_str: str) -> None:
    """
    Celery task to delete the content index document for a library block
    """
    container_key = LibraryContainerLocator.from_string(container_key_str)

    log.info("Deleting content index document for library block with id: %s", container_key)

    api.delete_index_doc(container_key)


@shared_task(**_BACKGROUND_TASK)
@set_code_owner_attribute
def delete_course_index_docs(course_key_str: str) -> None:
    """
    Celery task to delete the content index documents for a Course
    """
    course_key = CourseKey.from_string(course_key_str)

    log.info("Deleting all index documents related to course_key: %s", course_key)

    # Delete children index data for course blocks.
    api.delete_docs_with_context_key(course_key)


# No time limit: a full rebuild walks every course and library and legitimately runs
# for hours. It is bounded by the index rebuild lock instead, and by resuming from
# IncrementalIndexCompleted rather than starting over.
@shared_task(
    base=LoggedTask,
    autoretry_for=(MeilisearchError, ConnectionError),
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
@set_code_owner_attribute
def rebuild_index_incremental() -> None:
    """
    Celery task to incrementally populate the Studio Meilisearch index.

    Uses IncrementalIndexCompleted to track progress and resume from where
    it left off if interrupted. Safe to call multiple times — already-indexed
    contexts are skipped.

    If a rebuild is already in progress (lock held), the task exits gracefully.
    """
    log.info("Starting incremental Studio search index population...")

    try:
        api.rebuild_index(status_cb=log.info, incremental=True)
    except RuntimeError as exc:
        # rebuild_index -> _using_temp_index or lock contention
        if "already in progress" in str(exc).lower():
            log.warning(
                "Studio index population skipped: a rebuild is already in progress. Will retry later if re-enqueued."
            )
            return
        raise

    log.info("Incremental Studio search index population complete.")
