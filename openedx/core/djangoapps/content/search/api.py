"""
Content index and search API using Meilisearch
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Callable, Generator, cast  # noqa: UP035

from attrs import define
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.paginator import Paginator
from meilisearch import Client as MeilisearchClient
from meilisearch.errors import MeilisearchApiError, MeilisearchError
from meilisearch.models.task import TaskInfo
from opaque_keys import OpaqueKey
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locator import LibraryCollectionLocator, LibraryContainerLocator, LibraryLocatorV2
from openedx_content import api as content_api
from openedx_content import models_api as content_models
from rest_framework.request import Request

from common.djangoapps.student.role_helpers import get_course_roles
from common.djangoapps.student.roles import GlobalStaff
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.search.index_config import (
    INDEX_DISTINCT_ATTRIBUTE,
    INDEX_FILTERABLE_ATTRIBUTES,
    INDEX_PRIMARY_KEY,
    INDEX_RANKING_RULES,
    INDEX_SEARCHABLE_ATTRIBUTES,
    INDEX_SORTABLE_ATTRIBUTES,
)
from openedx.core.djangoapps.content.search.models import IncrementalIndexCompleted, get_access_ids_for_request
from openedx.core.djangoapps.content_libraries import api as lib_api
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

from .documents import (
    Fields,
    meili_id_from_opaque_key,
    searchable_doc_collections,
    searchable_doc_containers,
    searchable_doc_for_collection,
    searchable_doc_for_container,
    searchable_doc_for_course_block,
    searchable_doc_for_key,
    searchable_doc_for_library_block,
    searchable_doc_tags,
)

log = logging.getLogger(__name__)

User = get_user_model()

STUDIO_INDEX_SUFFIX = "studio_content"

Filter = str | list[str | list[str]]

if hasattr(settings, "MEILISEARCH_INDEX_PREFIX"):
    STUDIO_INDEX_NAME = settings.MEILISEARCH_INDEX_PREFIX + STUDIO_INDEX_SUFFIX
else:
    STUDIO_INDEX_NAME = STUDIO_INDEX_SUFFIX


_MEILI_CLIENT = None
_MEILI_API_KEY_UID = None

LOCK_EXPIRE = 24 * 60 * 60  # Lock expires in 24 hours

MAX_ACCESS_IDS_IN_FILTER = 1_000
MAX_ORGS_IN_FILTER = 1_000

EXCLUDED_XBLOCK_TYPES = ["course", "course_info"]


@contextmanager
def _index_rebuild_lock() -> Generator[str, None, None]:
    """
    Lock to prevent that more than one rebuild is running at the same time
    """
    lock_id = f"lock-meilisearch-index-{STUDIO_INDEX_NAME}"
    new_index_name = STUDIO_INDEX_NAME + "_new"

    status = cache.add(lock_id, new_index_name, LOCK_EXPIRE)

    if not status:
        # Lock already acquired
        raise RuntimeError("Rebuild already in progress")

    # Lock acquired
    try:
        yield new_index_name
    finally:
        # Release the lock
        cache.delete(lock_id)


def _get_running_rebuild_index_name() -> str | None:
    lock_id = f"lock-meilisearch-index-{STUDIO_INDEX_NAME}"

    return cache.get(lock_id)


def _get_meilisearch_client():
    """
    Get the Meiliesearch client
    """
    global _MEILI_CLIENT  # pylint: disable=global-statement

    # Connect to Meilisearch
    if not is_meilisearch_enabled():
        raise RuntimeError("MEILISEARCH_ENABLED is not set - search functionality disabled.")

    if _MEILI_CLIENT is not None:
        return _MEILI_CLIENT

    _MEILI_CLIENT = MeilisearchClient(settings.MEILISEARCH_URL, settings.MEILISEARCH_API_KEY)
    try:
        _MEILI_CLIENT.health()
    except MeilisearchError as err:
        _MEILI_CLIENT = None
        raise ConnectionError("Unable to connect to Meilisearch") from err
    return _MEILI_CLIENT


def clear_meilisearch_client():
    global _MEILI_CLIENT  # pylint: disable=global-statement

    _MEILI_CLIENT = None


def _get_meili_api_key_uid():
    """
    Helper method to get the UID of the API key we're using for Meilisearch
    """
    global _MEILI_API_KEY_UID  # pylint: disable=global-statement
    if _MEILI_API_KEY_UID is None:
        _MEILI_API_KEY_UID = _get_meilisearch_client().get_key(settings.MEILISEARCH_API_KEY).uid
    return _MEILI_API_KEY_UID


def _wait_for_meili_task(info: TaskInfo) -> None:
    """
    Simple helper method to wait for a Meilisearch task to complete
    This method will block until the task is completed, so it should only be used in celery tasks
    or management commands.
    """
    client = _get_meilisearch_client()
    current_status = client.get_task(info.task_uid)
    while current_status.status in ("enqueued", "processing"):
        time.sleep(0.5)
        current_status = client.get_task(info.task_uid)
    if current_status.status != "succeeded":
        try:
            err_reason = current_status.error["message"]
        except (TypeError, KeyError):
            err_reason = "Unknown error"
        raise MeilisearchError(err_reason)


def _wait_for_meili_tasks(info_list: list[TaskInfo]) -> None:
    """
    Simple helper method to wait for multiple Meilisearch tasks to complete
    """
    while info_list:
        info = info_list.pop()
        _wait_for_meili_task(info)


def _index_exists(index_name: str) -> bool:
    """
    Check if an index exists
    """
    client = _get_meilisearch_client()
    try:
        client.get_index(index_name)
    except MeilisearchError as err:
        if err.code == "index_not_found":
            return False
        else:
            raise err
    return True


@contextmanager
def _using_temp_index(status_cb: Callable[[str], None] | None = None) -> Generator[str, None, None]:
    """
    Create a new temporary Meilisearch index, populate it, then swap it to
    become the active index.

    Args:
        status_cb (Callable): A callback function to report status messages
    """
    if status_cb is None:
        status_cb = log.info

    client = _get_meilisearch_client()
    status_cb("Checking index...")
    with _index_rebuild_lock() as temp_index_name:
        if _index_exists(temp_index_name):
            status_cb("Temporary index already exists. Deleting it...")
            _wait_for_meili_task(client.delete_index(temp_index_name))

        status_cb("Creating new index...")
        _wait_for_meili_task(client.create_index(temp_index_name, {"primaryKey": INDEX_PRIMARY_KEY}))
        new_index_created = client.get_index(temp_index_name).created_at

        yield temp_index_name

        if not _index_exists(STUDIO_INDEX_NAME):
            # We have to create the "target" index before we can successfully swap the new one into it:
            status_cb("Preparing to swap into index (first time)...")
            _wait_for_meili_task(client.create_index(STUDIO_INDEX_NAME))
        status_cb("Swapping index...")
        client.swap_indexes([{"indexes": [temp_index_name, STUDIO_INDEX_NAME]}])
        # If we're using an API key that's restricted to certain index prefix(es), we won't be able to get the status
        # of this request unfortunately. https://github.com/meilisearch/meilisearch/issues/4103
        while True:
            time.sleep(1)
            if client.get_index(STUDIO_INDEX_NAME).created_at != new_index_created:
                status_cb("Waiting for swap completion...")
            else:
                break
        status_cb("Deleting old index...")
        _wait_for_meili_task(client.delete_index(temp_index_name))


def _index_is_empty(index_name: str) -> bool:
    """
    Check if an index is empty

    Args:
        index_name (str): The name of the index to check
    """
    client = _get_meilisearch_client()
    index = client.get_index(index_name)
    return index.get_stats().number_of_documents == 0


def _apply_index_settings(
    index_name: str,
    wait: bool,
    status_cb: Callable[[str], None] | None = None,
) -> None:
    """
    Apply the standard Meilisearch settings to an index.

    When wait=False, settings are sent in fire-and-forget mode. This is appropriate
    for empty temporary indexes that will immediately be populated on the same Meilisearch
    task queue.

    When wait=True, each settings task is synchronously waited on before returning.
    This is appropriate when reconciling a live index and we need confirmation that the
    settings have been applied before returning.

    Args:
        index_name: The name of the index to configure.
        wait: Whether to wait for each Meilisearch settings task to complete.
        status_cb: Optional callback for status messages when wait=True.
    """
    if status_cb is None:
        status_cb = log.info

    client = _get_meilisearch_client()
    index = client.index(index_name)

    settings_updates = (
        ("distinct attribute", index.update_distinct_attribute, INDEX_DISTINCT_ATTRIBUTE),
        ("filterable attributes", index.update_filterable_attributes, INDEX_FILTERABLE_ATTRIBUTES),
        ("searchable attributes", index.update_searchable_attributes, INDEX_SEARCHABLE_ATTRIBUTES),
        ("sortable attributes", index.update_sortable_attributes, INDEX_SORTABLE_ATTRIBUTES),
        ("ranking rules", index.update_ranking_rules, INDEX_RANKING_RULES),
    )

    for label, update_method, value in settings_updates:
        status_cb(f"Applying {label} to '{index_name}'...")
        if wait:
            _wait_for_meili_task(update_method(value))
        else:
            update_method(value)

    status_cb(f"All settings applied to '{index_name}'.")


def _recurse_children(block, fn, status_cb: Callable[[str], None] | None = None) -> None:
    """
    Recurse the children of an XBlock and call the given function for each

    The main purpose of this is just to wrap the loading of each child in
    try...except. Otherwise block.get_children() would do what we need.
    """
    if block.has_children:
        for child_id in block.children:
            try:
                child = block.get_child(child_id)
                if child is None:
                    # XBlocks with XModuleMixin will return None from get_child() instead of raising an exception :/
                    raise ItemNotFoundError(f"block.get_child() from {block.usage_key} failed to load child {child_id}")
            except Exception as err:  # pylint: disable=broad-except
                log.exception(err)
                if status_cb is not None:
                    status_cb(f"Unable to load block {child_id}")
            else:
                fn(child)


def _update_index_docs(docs) -> None:
    """
    Helper function that updates the documents in the search index

    If there is a rebuild in progress, the document will also be added to the new index.
    """
    if not docs:
        return

    client = _get_meilisearch_client()
    current_rebuild_index_name = _get_running_rebuild_index_name()

    tasks = []
    if current_rebuild_index_name:
        # If there is a rebuild in progress, the document will also be added to the new index.
        tasks.append(client.index(current_rebuild_index_name).update_documents(docs))
    tasks.append(client.index(STUDIO_INDEX_NAME).update_documents(docs))

    _wait_for_meili_tasks(tasks)


def only_if_meilisearch_enabled(f):
    """
    Only call `f` if meilisearch is enabled
    """

    @wraps(f)
    def wrapper(*args, **kwargs):
        """Wraps the decorated function."""
        if is_meilisearch_enabled():
            return f(*args, **kwargs)

    return wrapper


def is_meilisearch_enabled() -> bool:
    """
    Returns whether Meilisearch is enabled
    """
    if hasattr(settings, "MEILISEARCH_ENABLED"):
        return settings.MEILISEARCH_ENABLED

    return False


def reset_index(status_cb: Callable[[str], None] | None = None) -> None:
    """
    Reset the Meilisearch index, deleting all documents and reconfiguring it
    """
    if status_cb is None:
        status_cb = log.info

    status_cb("Creating new empty index...")
    with _using_temp_index(status_cb) as temp_index_name:
        _apply_index_settings(temp_index_name, wait=False)
        status_cb("Index recreated!")
    status_cb("Index reset complete.")


@define
class IndexDrift:
    """
    Represents the drift state of a Meilisearch index compared to the expected configuration.
    """

    exists: bool
    is_empty: bool | None = None  # None if index doesn't exist
    primary_key_correct: bool | None = None  # None if index doesn't exist
    distinct_attribute_match: bool | None = None
    filterable_attributes_match: bool | None = None
    searchable_attributes_match: bool | None = None
    sortable_attributes_match: bool | None = None
    ranking_rules_match: bool | None = None

    @property
    def is_settings_drifted(self) -> bool:
        """True if any of the 5 settings fields is False (not None, but explicitly False)."""
        return any(
            setting_fields is False
            for setting_fields in (
                self.distinct_attribute_match,
                self.filterable_attributes_match,
                self.searchable_attributes_match,
                self.sortable_attributes_match,
                self.ranking_rules_match,
            )
        )


def _detect_index_drift(index_name: str) -> IndexDrift:
    """
    Inspect the current state of a Meilisearch index and return a structured drift report.

    It provides per-setting match status plus primary key and emptiness information.

    Args:
        index_name (str): The name of the index to inspect.

    Returns:
        IndexDrift: Structured drift report.
    """
    if not _index_exists(index_name):
        return IndexDrift(exists=False)

    client = _get_meilisearch_client()
    index = client.get_index(index_name)

    # Check primary key
    primary_key_correct = index.primary_key == INDEX_PRIMARY_KEY

    # Check emptiness
    is_empty = index.get_stats().number_of_documents == 0

    # Check settings
    index_settings = index.get_settings()

    def _compare_setting(key, expected):
        """Compare a single setting value against the expected value."""
        actual = index_settings.get(key, [] if isinstance(expected, list) else None)
        if isinstance(expected, list):
            # For ranking rules, order matters; for other lists, it doesn't
            if key == "rankingRules":
                return list(actual) == list(expected)
            return set(actual) == set(expected)
        return actual == expected

    return IndexDrift(
        exists=True,
        is_empty=is_empty,
        primary_key_correct=primary_key_correct,
        distinct_attribute_match=_compare_setting("distinctAttribute", INDEX_DISTINCT_ATTRIBUTE),
        filterable_attributes_match=_compare_setting("filterableAttributes", INDEX_FILTERABLE_ATTRIBUTES),
        searchable_attributes_match=_compare_setting("searchableAttributes", INDEX_SEARCHABLE_ATTRIBUTES),
        sortable_attributes_match=_compare_setting("sortableAttributes", INDEX_SORTABLE_ATTRIBUTES),
        ranking_rules_match=_compare_setting("rankingRules", INDEX_RANKING_RULES),
    )


def reconcile_index(
    status_cb: Callable[[str], None] | None = None, warn_cb: Callable[[str], None] | None = None
) -> None:  # noqa: E501
    """
    Reconcile the Meilisearch index state.

    Inspects the current Studio Meilisearch index and takes appropriate action based on its state:
    - Creates the index if missing.
    - Reconfigures if empty and drifted.
    - Applies updated settings if populated and drifted.
    - Recreates the index if primary key is mismatched (even if populated — data loss is unavoidable).
    - No-ops if everything is correctly configured.

    This is the primary reconciliation entry point, called from post_migrate and init_index().
    """
    if status_cb is None:
        status_cb = log.info
    if warn_cb is None:
        warn_cb = log.warning

    drift = _detect_index_drift(STUDIO_INDEX_NAME)

    # CASE: Index missing
    if not drift.exists:
        status_cb("Studio search index not found. Creating and configuring...")
        reset_index(status_cb)
        status_cb("Index created. Run './manage.py cms reindex_studio' to populate.")
        return

    # CASE: Primary key mismatch (must recreate regardless of population state)
    if not drift.primary_key_correct:
        if drift.is_empty:
            warn_cb("Primary key mismatch on empty index. Recreating...")
        else:
            warn_cb(
                f"PRIMARY KEY MISMATCH on populated index '{STUDIO_INDEX_NAME}'. "
                "Index must be recreated (data loss is unavoidable for primary key changes)."
            )
            warn_cb("Dropping and recreating index. Repopulate with: './manage.py cms reindex_studio'")
        reset_index(status_cb)
        warn_cb("Index recreated empty. Run './manage.py cms reindex_studio' to repopulate.")
        return

    # CASE: Index empty
    if drift.is_empty:
        if drift.is_settings_drifted:
            status_cb("Empty index has drifted settings. Reconfiguring...")
            _apply_index_settings(STUDIO_INDEX_NAME, wait=True, status_cb=status_cb)
            status_cb("Reconfigured. Run './manage.py cms reindex_studio' to populate.")
        else:
            status_cb(
                "Index exists and is correctly configured but empty. Run './manage.py cms reindex_studio' to populate."
            )
        return

    # CASE: Index populated, attribute drifted i.e settings mismatched
    if drift.is_settings_drifted:
        warn_cb(f"Settings drift detected on populated index '{STUDIO_INDEX_NAME}'. Applying updated settings...")
        # Log per-setting mismatch details
        for field_name, match in (
            ("distinctAttribute", drift.distinct_attribute_match),
            ("filterableAttributes", drift.filterable_attributes_match),
            ("searchableAttributes", drift.searchable_attributes_match),
            ("sortableAttributes", drift.sortable_attributes_match),
            ("rankingRules", drift.ranking_rules_match),
        ):
            if match is False:
                warn_cb(f"  - {field_name}: DRIFTED")

        _apply_index_settings(STUDIO_INDEX_NAME, wait=True, status_cb=status_cb)
        warn_cb(
            "Settings applied. Meilisearch will re-index documents in the background. "
            "Consider running './manage.py cms reindex_studio' for a full rebuild "
            "if search quality is affected."
        )
    else:
        status_cb("Index is populated and correctly configured. No action needed.")


def init_index(status_cb: Callable[[str], None] | None = None, warn_cb: Callable[[str], None] | None = None) -> None:
    """
    This method is depricated as of Verawood and would be removed in the future release.

    Initialize the Meilisearch index, creating it and configuring it if it doesn't exist.

    This is a compatibility wrapper around reconcile_index().
    """
    log.warning("init_index is deprecated as of Verawood and will be removed in the future release.")
    reconcile_index(status_cb=status_cb, warn_cb=warn_cb)


def index_course(course_key: CourseKey, index_name: str | None = None) -> list:
    """
    Rebuilds the index for a given course.
    """
    store = modulestore()
    client = _get_meilisearch_client()
    docs = []
    if index_name is None:
        index_name = STUDIO_INDEX_NAME
    # Pre-fetch the course with all of its children:
    course = store.get_course(course_key, depth=None)

    def add_with_children(block):
        """Recursively index the given XBlock/component"""
        doc = searchable_doc_for_course_block(block)
        doc.update(searchable_doc_tags(block.usage_key))
        docs.append(doc)  # pylint: disable=cell-var-from-loop
        _recurse_children(block, add_with_children)  # pylint: disable=cell-var-from-loop

    # Index course children
    _recurse_children(course, add_with_children)

    if docs:
        # Add all the docs in this course at once (usually faster than adding one at a time):
        _wait_for_meili_task(client.index(index_name).add_documents(docs))
    return docs


def rebuild_index(  # pylint: disable=too-many-statements
    status_cb: Callable[[str], None] | None = None, incremental=False
) -> None:  # lint-amnesty
    """
    Rebuild the Meilisearch index from scratch
    """
    if status_cb is None:
        status_cb = log.info

    client = _get_meilisearch_client()

    # Get the lists of libraries
    status_cb("Counting libraries...")
    keys_indexed = []
    if incremental:
        keys_indexed = list(IncrementalIndexCompleted.objects.values_list("context_key", flat=True))
    lib_keys = [
        lib.library_key
        for lib in lib_api.ContentLibrary.objects.select_related("org").only("org", "slug").order_by("-id")
        if lib.library_key not in keys_indexed
    ]
    num_libraries = len(lib_keys)

    # Get the list of courses
    status_cb("Counting courses...")
    num_courses = CourseOverview.objects.count()

    # Some counters so we can track our progress as indexing progresses:
    num_libs_skipped = len(keys_indexed)
    num_contexts = num_courses + num_libraries + num_libs_skipped
    num_contexts_done = 0 + num_libs_skipped  # How many courses/libraries we've indexed
    num_blocks_done = 0  # How many individual components/XBlocks we've indexed

    status_cb(f"Found {num_courses} courses, {num_libraries} libraries.")
    with _using_temp_index(status_cb) if not incremental else nullcontext(STUDIO_INDEX_NAME) as index_name:
        ############## Configure the index ##############

        # The index settings are best changed on an empty index.
        # Changing them on a populated index will "re-index all documents in the index", which can take some time
        # and use more RAM. Instead, we configure an empty index then populate it one course/library at a time.
        if not incremental:
            _apply_index_settings(index_name, wait=False)

        ############## Libraries ##############
        status_cb("Indexing libraries...")

        def index_library(lib_key: LibraryLocatorV2) -> list:
            docs = []
            for component in lib_api.get_library_components(lib_key):
                try:
                    metadata = lib_api.LibraryXBlockMetadata.from_component(lib_key, component)
                    doc = {}
                    doc.update(searchable_doc_for_library_block(metadata))
                    doc.update(searchable_doc_tags(metadata.usage_key))
                    doc.update(searchable_doc_collections(metadata.usage_key))
                    doc.update(searchable_doc_containers(metadata.usage_key, "units"))
                    docs.append(doc)
                except Exception as err:  # pylint: disable=broad-except
                    status_cb(f"Error indexing library component {component}: {err}")
            if docs:
                try:
                    # Add all the docs in this library at once (usually faster than adding one at a time):
                    _wait_for_meili_task(client.index(index_name).add_documents(docs))
                except (TypeError, KeyError, MeilisearchError) as err:
                    status_cb(f"Error indexing library {lib_key}: {err}")
            return docs

        ############## Collections ##############
        def index_collection_batch(batch, num_done, library_key) -> int:
            docs = []
            for collection in batch:
                try:
                    collection_key = lib_api.library_collection_locator(library_key, collection.collection_code)
                    doc = searchable_doc_for_collection(collection_key, collection=collection)
                    doc.update(searchable_doc_tags(collection_key))
                    docs.append(doc)
                except Exception as err:  # pylint: disable=broad-except
                    status_cb(f"Error indexing collection {collection}: {err}")
                num_done += 1

            if docs:
                try:
                    # Add docs in batch of 100 at once (usually faster than adding one at a time):
                    _wait_for_meili_task(client.index(index_name).add_documents(docs))
                except (TypeError, KeyError, MeilisearchError) as err:
                    status_cb(f"Error indexing collection batch {p}: {err}")
            return num_done

        ############## Containers ##############
        def index_container_batch(batch, num_done, library_key) -> int:
            docs = []
            for container in batch:
                try:
                    container_key = lib_api.library_container_locator(
                        library_key,
                        container,
                    )
                    doc = searchable_doc_for_container(container_key)
                    doc.update(searchable_doc_tags(container_key))
                    doc.update(searchable_doc_collections(container_key))
                    container_type_code = container_key.container_type
                    match container_type_code:
                        case content_models.Unit.type_code:
                            doc.update(searchable_doc_containers(container_key, "subsections"))
                        case content_models.Subsection.type_code:
                            doc.update(searchable_doc_containers(container_key, "sections"))
                    docs.append(doc)
                except Exception as err:  # pylint: disable=broad-except
                    status_cb(f"Error indexing container {container.entity_ref}: {err}")
                num_done += 1

            if docs:
                try:
                    # Add docs in batch of 100 at once (usually faster than adding one at a time):
                    _wait_for_meili_task(client.index(index_name).add_documents(docs))
                except (TypeError, KeyError, MeilisearchError) as err:
                    status_cb(f"Error indexing container batch {p}: {err}")
            return num_done

        for lib_key in lib_keys:
            status_cb(f"{num_contexts_done + 1}/{num_contexts}. Now indexing blocks in library {lib_key}")
            lib_docs = index_library(lib_key)
            num_blocks_done += len(lib_docs)

            # To reduce memory usage on large instances, split up the Collections into pages of 100 collections:
            library = lib_api.get_library(lib_key)
            collections = content_api.get_collections(library.learning_package_id, enabled=True)
            num_collections = collections.count()
            num_collections_done = 0
            status_cb(f"{num_collections_done}/{num_collections}. Now indexing collections in library {lib_key}")
            paginator = Paginator(collections, 100)
            for p in paginator.page_range:
                num_collections_done = index_collection_batch(
                    paginator.page(p).object_list,
                    num_collections_done,
                    lib_key,
                )
            if incremental:
                IncrementalIndexCompleted.objects.get_or_create(context_key=lib_key)
            status_cb(f"{num_collections_done}/{num_collections} collections indexed for library {lib_key}")

            # Similarly, batch process Containers (units, sections, etc) in pages of 100
            containers = content_api.get_containers(library.learning_package_id)
            num_containers = containers.count()
            num_containers_done = 0
            status_cb(f"{num_containers_done}/{num_containers}. Now indexing containers in library {lib_key}")
            paginator = Paginator(containers, 100)
            for p in paginator.page_range:
                num_containers_done = index_container_batch(
                    paginator.page(p).object_list,
                    num_containers_done,
                    lib_key,
                )
                status_cb(f"{num_containers_done}/{num_containers} containers indexed for library {lib_key}")
            if incremental:
                IncrementalIndexCompleted.objects.get_or_create(context_key=lib_key)

            num_contexts_done += 1

        ############## Courses ##############
        status_cb("Indexing courses...")
        # To reduce memory usage on large instances, split up the CourseOverviews into pages of 1,000 courses:

        paginator = Paginator(CourseOverview.objects.only("id", "display_name"), 1000)
        for p in paginator.page_range:
            for course in paginator.page(p).object_list:
                status_cb(
                    f"{num_contexts_done + 1}/{num_contexts}. Now indexing course {course.display_name} ({course.id})"
                )
                if course.id in keys_indexed:
                    num_contexts_done += 1
                    continue
                course_docs = index_course(course.id, index_name)
                if incremental:
                    IncrementalIndexCompleted.objects.get_or_create(context_key=course.id)
                num_contexts_done += 1
                num_blocks_done += len(course_docs)

    IncrementalIndexCompleted.objects.all().delete()
    status_cb(f"Done! {num_blocks_done} blocks indexed across {num_contexts_done} courses, collections and libraries.")


def upsert_xblock_index_doc(usage_key: UsageKey, recursive: bool = True) -> None:
    """
    Creates or updates the document for the given XBlock in the search index


    Args:
        usage_key (UsageKey): The usage key of the XBlock to index
        recursive (bool): If True, also index all children of the XBlock
    """
    xblock = modulestore().get_item(usage_key)
    xblock_type = xblock.scope_ids.block_type

    if xblock_type in EXCLUDED_XBLOCK_TYPES:
        return

    docs = []

    def add_with_children(block):
        """Recursively index the given XBlock/component"""
        doc = searchable_doc_for_course_block(block)
        docs.append(doc)
        if recursive:
            _recurse_children(block, add_with_children)

    add_with_children(xblock)

    _update_index_docs(docs)


def delete_index_doc(key: OpaqueKey, *, delete_children: bool = False) -> None:
    """
    Deletes the document for the given XBlock from the search index

    Args:
        key (OpaqueKey): The opaque key of the XBlock/Container to be removed from the index
    """
    doc = searchable_doc_for_key(key)
    _delete_index_doc(doc[Fields.id])
    if delete_children:
        _delete_documents(f'{Fields.breadcrumbs}.{Fields.usage_key} = "{key}"')


def delete_docs_with_context_key(key: OpaqueKey) -> None:
    """
    Delete all docs for given context key
    """
    _delete_documents(f'{Fields.context_key} = "{key}"')


def _delete_documents(filter_query: str) -> None:
    """
    Deletes all documents from the search index that match the given filter

    Args:
        filter (str): The query to use when filtering documents
    """
    if not filter_query:
        return

    client = _get_meilisearch_client()
    current_rebuild_index_name = _get_running_rebuild_index_name()

    tasks = []
    if current_rebuild_index_name:
        # If there is a rebuild in progress, the document will also be removed from the new index.
        tasks.append(client.index(current_rebuild_index_name).delete_documents(filter=filter_query))
    tasks.append(client.index(STUDIO_INDEX_NAME).delete_documents(filter=filter_query))

    _wait_for_meili_tasks(tasks)


def _delete_index_doc(doc_id) -> None:
    """
    Helper function that deletes the document with the given ID from the search index

    If there is a rebuild in progress, the document will also be removed from the new index.
    """
    if not doc_id:
        return

    client = _get_meilisearch_client()
    current_rebuild_index_name = _get_running_rebuild_index_name()

    tasks = []
    if current_rebuild_index_name:
        # If there is a rebuild in progress, the document will also be removed from the new index.
        tasks.append(client.index(current_rebuild_index_name).delete_document(doc_id))

    tasks.append(client.index(STUDIO_INDEX_NAME).delete_document(doc_id))

    _wait_for_meili_tasks(tasks)


def upsert_library_block_index_doc(usage_key: UsageKey) -> None:
    """
    Creates or updates the document for the given Library Block in the search index
    """

    library_block = lib_api.get_component_from_usage_key(usage_key)
    library_block_metadata = lib_api.LibraryXBlockMetadata.from_component(usage_key.context_key, library_block)

    docs = [searchable_doc_for_library_block(library_block_metadata)]

    _update_index_docs(docs)


def _get_document_from_index(document_id: str) -> dict:
    """
    Returns the Document identified by the given ID, from the given index.

    Returns None if the document or index do not exist.
    """
    client = _get_meilisearch_client()
    document = None
    index_name = STUDIO_INDEX_NAME
    try:
        index = client.get_index(index_name)
        document = index.get_document(document_id)
    except (MeilisearchError, MeilisearchApiError) as err:
        # The index or document doesn't exist
        log.warning(f"Unable to fetch document {document_id} from {index_name}: {err}")

    return document


def upsert_library_collection_index_doc(collection_key: LibraryCollectionLocator) -> None:
    """
    Creates, updates, or deletes the document for the given Library Collection in the search index.

    If the Collection is not found or disabled (i.e. soft-deleted), then delete it from the search index.
    """
    doc = searchable_doc_for_collection(collection_key)
    # Soft-deleted/disabled/hard-deleted collections are removed from the index:
    # (If the collection is soft-deleted, searchable_doc_for_collection() sets `_disabled: True`)
    # (If the collection is hard-deleted, searchable_doc_for_collection() leaves all fields other than ID empty)
    if doc.get("_disabled") or not doc.get(Fields.type):
        _delete_index_doc(doc[Fields.id])
        return

    # Normal case - update the collection doc.
    _update_index_docs([doc])

    # We do NOT update the individual entities (components/containers) in the collection here.
    # This event can be called if a single entity is added or removed from the collection (to update the "# of items in
    # collection" field (Fields.num_children), and we don't want to re-index all entities in that case).
    #
    # If the collection is renamed, the COLLECTION_CHANGED signal will be emitted, and content_libraries will handle it
    # and emit CONTENT_OBJECT_ASSOCIATIONS_CHANGED for every entity in the collection, which will update their
    # "collections" field in the search index.
    #
    # If the collection is enabled/disabled/deleted, the COLLECTION_CHANGED signal will include all entities in the
    # collection as added or removed, which the same libraries signal handler will convert to
    # CONTENT_OBJECT_ASSOCIATIONS_CHANGED events, which will update them.


def update_library_components_collections(
    collection_key: LibraryCollectionLocator,
    batch_size: int = 1000,
) -> None:
    """
    Updates the "collections" field for all components associated with a given Library Collection.

    Because there may be a lot of components, we send these updates to Meilisearch in batches.
    """
    library_key = collection_key.lib_key
    library = lib_api.get_library(library_key)
    components = content_api.get_collection_components(
        library.learning_package_id,
        collection_key.collection_id,
    )

    paginator = Paginator(components, batch_size)
    for page in paginator.page_range:
        docs = []

        for component in paginator.page(page).object_list:
            usage_key = lib_api.library_component_usage_key(
                library_key,
                component,
            )
            doc = searchable_doc_for_key(usage_key)
            doc.update(searchable_doc_collections(usage_key))
            docs.append(doc)

        log.info(
            f"Updating document.collections for library {library_key} components page {page} / {paginator.num_pages}"
        )
        _update_index_docs(docs)


def update_library_containers_collections(
    collection_key: LibraryCollectionLocator,
    batch_size: int = 1000,
) -> None:
    """
    Updates the "collections" field for all containers associated with a given Library Collection.

    Because there may be a lot of containers, we send these updates to Meilisearch in batches.
    """
    library_key = collection_key.lib_key
    library = lib_api.get_library(library_key)
    container_entities = (
        content_api.get_collection_entities(
            library.learning_package_id,
            collection_key.collection_id,
        )
        .exclude(container=None)
        .select_related("container")
    )

    paginator = Paginator(container_entities, batch_size)
    for page in paginator.page_range:
        docs = []

        for container_entity in paginator.page(page).object_list:
            container_key = lib_api.library_container_locator(
                library_key,
                container_entity.container,
            )
            doc = searchable_doc_for_key(container_key)
            doc.update(searchable_doc_collections(container_key))
            docs.append(doc)

        log.info(
            f"Updating document.collections for library {library_key} containers page {page} / {paginator.num_pages}"
        )
        _update_index_docs(docs)


def upsert_library_container_index_doc(container_key: LibraryContainerLocator) -> None:
    """
    Creates, updates, or deletes the document for the given Library Container in the search index.

    TODO: add support for indexing a container's components, like upsert_library_collection_index_doc does.
    """
    doc = searchable_doc_for_container(container_key)

    # Soft-deleted/disabled containers are removed from the index
    # and their components updated.
    if doc.get("_disabled"):
        _delete_index_doc(doc[Fields.id])

    # Hard-deleted containers are also deleted from the index
    elif not doc.get(Fields.type):
        _delete_index_doc(doc[Fields.id])

    # Otherwise, upsert the container.
    else:
        _update_index_docs([doc])


def upsert_content_library_index_docs(library_key: LibraryLocatorV2, full_index: bool = False) -> None:
    """
    Creates or updates the documents for the given Content Library in the search index
    """
    docs = []
    for component in lib_api.get_library_components(library_key):
        metadata = lib_api.LibraryXBlockMetadata.from_component(library_key, component)
        doc = searchable_doc_for_library_block(metadata)
        docs.append(doc)

    if full_index:
        # For a full re-index, we also need to update collections, and containers data:
        for container in lib_api.get_library_containers(library_key):
            container_key = lib_api.library_container_locator(
                library_key,
                container,
            )
            doc = searchable_doc_for_container(container_key)
            docs.append(doc)

        for collection in lib_api.get_library_collections(library_key):
            collection_key = lib_api.library_collection_locator(library_key, collection.collection_code)
            doc = searchable_doc_for_collection(collection_key, collection=collection)
            docs.append(doc)

    _update_index_docs(docs)


def upsert_content_object_tags_index_doc(key: OpaqueKey):
    """
    Updates the tags data in document for the given Course/Library item
    """
    doc = {Fields.id: meili_id_from_opaque_key(key)}
    doc.update(searchable_doc_tags(key))
    _update_index_docs([doc])


def upsert_item_collections_index_docs(opaque_key: OpaqueKey):
    """
    Updates the collections data in documents for the given Course/Library block, or Container
    """
    doc = {Fields.id: meili_id_from_opaque_key(opaque_key)}
    doc.update(searchable_doc_collections(opaque_key))
    _update_index_docs([doc])


def upsert_item_containers_index_docs(opaque_key: OpaqueKey, container_type: str):
    """
    Updates the containers (units/subsections/sections) data in documents for the given Course/Library block
    """
    doc = {Fields.id: meili_id_from_opaque_key(opaque_key)}
    doc.update(searchable_doc_containers(opaque_key, container_type))
    _update_index_docs([doc])


def _get_user_orgs(request: Request) -> list[str]:
    """
    Get the org.short_names for the organizations that the requesting user has OrgStaffRole or OrgInstructorRole.

    Note: org-level roles have course_id=None to distinguish them from course-level roles.
    """
    course_roles = get_course_roles(request.user)
    return list(
        set(role.org for role in course_roles if role.course_id is None and role.role in ["staff", "instructor"])
    )


def _get_meili_access_filter(request: Request) -> dict:
    """
    Return meilisearch filter based on the requesting user's permissions.
    """
    # Global staff can see anything, so no filters required.
    if GlobalStaff().has_user(request.user):
        return {}

    # Everyone else is limited to their org staff roles...
    user_orgs = _get_user_orgs(request)[:MAX_ORGS_IN_FILTER]

    # ...or the N most recent courses and libraries they can access.
    access_ids = get_access_ids_for_request(request, omit_orgs=user_orgs)[:MAX_ACCESS_IDS_IN_FILTER]
    return {
        "filter": f"org IN {user_orgs} OR access_id IN {access_ids}",
    }


def generate_user_token_for_studio_search(request):
    """
    Returns a Meilisearch API key that only allows the user to search content that they have permission to view
    """
    expires_at = datetime.now(tz=timezone.utc) + timedelta(days=7)  # noqa: UP017

    search_rules = {
        STUDIO_INDEX_NAME: _get_meili_access_filter(request),
    }
    # Note: the following is just generating a JWT. It doesn't actually make an API call to Meilisearch.
    restricted_api_key = _get_meilisearch_client().generate_tenant_token(
        api_key_uid=_get_meili_api_key_uid(),
        search_rules=search_rules,
        expires_at=expires_at,
    )

    return {
        "url": settings.MEILISEARCH_PUBLIC_URL,
        "index_name": STUDIO_INDEX_NAME,
        "api_key": restricted_api_key,
    }


def force_array(extra_filter: Filter | None = None) -> list[str]:
    """
    Convert a filter value into a list of strings.

    Strings are wrapped in a list, lists are returned as-is (cast to `list[str]`),
    and None results in an empty list.
    """
    if isinstance(extra_filter, str):
        return [extra_filter]
    if isinstance(extra_filter, list):
        return cast(list[str], extra_filter)
    return []


def fetch_block_types(extra_filter: Filter | None = None):
    """
    Fetch the block types facet distribution for the search results.

    This data may not always be 100% accurate / up to date because it's based
    on the search index, so this should only be used for analysis/estimation
    purposes.

    Params:
    - extra_filter: Filters the query. Example: ['context_key = "course-v1:SampleTaxonomyOrg1+CC22+CC22"']

    Return example:
    {
        ...
        'estimatedTotalHits': 5,
        'facetDistribution': {
            'block_type': {
                'html': 2,
                'problem': 1,
                'video': 2,
            }
        },
    }
    """
    extra_filter_formatted = force_array(extra_filter)

    client = _get_meilisearch_client()
    index = client.get_index(STUDIO_INDEX_NAME)

    response = index.search(
        "",
        {
            "facets": ["block_type"],
            "filter": extra_filter_formatted,
            "limit": 0,
        },
    )

    return response


def get_all_blocks_from_context(
    context_key: str,
    extra_attributes_to_retrieve: list[str] | None = None,
) -> Iterator[dict]:
    """
    Lazily yields all blocks for a given context key using Meilisearch pagination.
    Meilisearch works with limits of 1000 maximum; ensuring we obtain all blocks
    requires making several queries.

    This data may not always be 100% accurate / up to date because it's based
    on the search index, so this should only be used for analysis/estimation
    purposes.
    """
    limit = 1000
    offset = 0

    client = _get_meilisearch_client()
    index = client.get_index(STUDIO_INDEX_NAME)

    while True:
        response = index.search(
            "",
            {
                "filter": [f'context_key = "{context_key}"'],
                "limit": limit,
                "offset": offset,
                "attributesToRetrieve": ["usage_key"] + (extra_attributes_to_retrieve or []),
            },
        )

        yield from response["hits"]

        if response["estimatedTotalHits"] <= offset + limit:
            break

        offset += limit
