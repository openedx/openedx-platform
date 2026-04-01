"""
Signal/event handlers for content search
"""

import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from opaque_keys.edx.locator import LibraryCollectionLocator, LibraryContainerLocator
from openedx_events.content_authoring.data import (
    ContentLibraryData,
    ContentObjectChangedData,
    CourseData,
    LibraryBlockData,
    LibraryCollectionData,
    LibraryContainerData,
    XBlockData,
)
from openedx_events.content_authoring.signals import (
    CONTENT_LIBRARY_CREATED,
    CONTENT_LIBRARY_DELETED,
    CONTENT_LIBRARY_UPDATED,
    CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
    COURSE_IMPORT_COMPLETED,
    COURSE_RERUN_COMPLETED,
    LIBRARY_BLOCK_CREATED,
    LIBRARY_BLOCK_DELETED,
    LIBRARY_BLOCK_PUBLISHED,
    LIBRARY_BLOCK_UPDATED,
    LIBRARY_COLLECTION_CREATED,
    LIBRARY_COLLECTION_DELETED,
    LIBRARY_COLLECTION_UPDATED,
    LIBRARY_CONTAINER_CREATED,
    LIBRARY_CONTAINER_DELETED,
    LIBRARY_CONTAINER_PUBLISHED,
    LIBRARY_CONTAINER_UPDATED,
    XBLOCK_CREATED,
    XBLOCK_DELETED,
    XBLOCK_UPDATED,
)

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.search.models import SearchAccess
from openedx.core.djangoapps.content_libraries import api as lib_api
from xmodule.modulestore.django import SignalHandler

from .api import (
    only_if_meilisearch_enabled,
    upsert_content_object_tags_index_doc,
    upsert_item_collections_index_docs,
    upsert_item_containers_index_docs,
)
from .tasks import (
    delete_course_index_docs,
    delete_library_block_index_doc,
    delete_library_container_index_doc,
    delete_xblock_index_doc,
    update_content_library_index_docs,
    update_library_collection_index_doc,
    update_library_container_index_doc,
    upsert_course_blocks_docs,
    upsert_library_block_index_doc,
    upsert_xblock_index_doc,
)

log = logging.getLogger(__name__)


# Using post_delete here because there is no COURSE_DELETED event defined.
@receiver(post_delete, sender=CourseOverview)
def delete_course_search_access(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """Deletes the SearchAccess instance for deleted CourseOverview"""
    SearchAccess.objects.filter(context_key=instance.id).delete()


@receiver(CONTENT_LIBRARY_DELETED)
def delete_library_search_access(content_library: ContentLibraryData, **kwargs):
    """Deletes the SearchAccess instance for deleted content libraries"""
    SearchAccess.objects.filter(context_key=content_library.library_key).delete()


@receiver(XBLOCK_CREATED)
@only_if_meilisearch_enabled
def xblock_created_handler(**kwargs) -> None:
    """
    Create the index for the XBlock
    """
    xblock_info = kwargs.get("xblock_info", None)
    if not xblock_info or not isinstance(xblock_info, XBlockData):  # pragma: no cover
        log.error("Received null or incorrect data for event")
        return

    upsert_xblock_index_doc.delay(
        str(xblock_info.usage_key),
        recursive=False,
    )


@receiver(XBLOCK_UPDATED)
@only_if_meilisearch_enabled
def xblock_updated_handler(**kwargs) -> None:
    """
    Update the index for the XBlock and its children
    """
    xblock_info = kwargs.get("xblock_info", None)
    if not xblock_info or not isinstance(xblock_info, XBlockData):  # pragma: no cover
        log.error("Received null or incorrect data for event")
        return

    upsert_xblock_index_doc.delay(
        str(xblock_info.usage_key),
        recursive=True,  # Update all children because the breadcrumb may have changed
    )


@receiver(XBLOCK_DELETED)
@only_if_meilisearch_enabled
def xblock_deleted_handler(**kwargs) -> None:
    """
    Delete the index for the XBlock
    """
    xblock_info = kwargs.get("xblock_info", None)
    if not xblock_info or not isinstance(xblock_info, XBlockData):  # pragma: no cover
        log.error("Received null or incorrect data for event")
        return

    delete_xblock_index_doc.delay(str(xblock_info.usage_key))


@receiver(LIBRARY_BLOCK_CREATED)
@receiver(LIBRARY_BLOCK_UPDATED)
@only_if_meilisearch_enabled
def library_block_updated_handler(**kwargs) -> None:
    """
    Create or update the index for the content library block
    """
    library_block_data = kwargs.get("library_block", None)
    if not library_block_data or not isinstance(library_block_data, LibraryBlockData):  # pragma: no cover
        log.error("Received null or incorrect data for event")
        return

    # Update content library index synchronously to make sure that search index is updated before
    # the frontend invalidates/refetches results. This is only a single document update so is very fast.
    upsert_library_block_index_doc.apply(args=[str(library_block_data.usage_key)])


@receiver(LIBRARY_BLOCK_PUBLISHED)
@only_if_meilisearch_enabled
def library_block_published_handler(**kwargs) -> None:
    """
    Update the index for the content library block when its published version
    has changed.
    """
    library_block_data = kwargs.get("library_block", None)
    if not library_block_data or not isinstance(library_block_data, LibraryBlockData):  # pragma: no cover
        log.error("Received null or incorrect data for event")
        return

    # The PUBLISHED event is sent for any change to the published version including deletes, so check if it exists:
    try:
        lib_api.get_library_block(library_block_data.usage_key)
    except lib_api.ContentLibraryBlockNotFound:
        log.info(f"Observed published deletion of library block {str(library_block_data.usage_key)}.")
        # The document should already have been deleted from the search index
        # via the DELETED handler, so there's nothing to do now.
        return

    # Update content library index synchronously to make sure that search index is updated before
    # the frontend invalidates/refetches results. This is only a single document update so is very fast.
    upsert_library_block_index_doc.apply(args=[str(library_block_data.usage_key)])


@receiver(LIBRARY_BLOCK_DELETED)
@only_if_meilisearch_enabled
def library_block_deleted(**kwargs) -> None:
    """
    Delete the index for the content library block
    """
    library_block_data = kwargs.get("library_block", None)
    if not library_block_data or not isinstance(library_block_data, LibraryBlockData):  # pragma: no cover
        log.error("Received null or incorrect data for event")
        return

    # Update content library index synchronously to make sure that search index is updated before
    # the frontend invalidates/refetches results. This is only a single document update so is very fast.
    delete_library_block_index_doc.apply(args=[str(library_block_data.usage_key)])


@receiver(CONTENT_LIBRARY_CREATED)
@only_if_meilisearch_enabled
def content_library_created_handler(**kwargs) -> None:
    """
    Create the index and SearchAccess for the content library
    """
    content_library_data = kwargs.get("content_library", None)
    if not content_library_data or not isinstance(content_library_data, ContentLibraryData):  # pragma: no cover
        log.error("Received null or incorrect data for event")
        return
    library_key = content_library_data.library_key

    # Create SearchAccess record immediately so course creators can search this library
    # right after creation. Without this, the JWT token won't include the new library's
    # access_id until it's added by the document indexing process or the page is refreshed.
    SearchAccess.objects.get_or_create(context_key=library_key)
    update_content_library_index_docs.apply(args=[str(library_key), True])


@receiver(CONTENT_LIBRARY_UPDATED)
@only_if_meilisearch_enabled
def content_library_updated_handler(**kwargs) -> None:
    """
    Update the index for the content library
    """
    content_library_data = kwargs.get("content_library", None)
    if not content_library_data or not isinstance(content_library_data, ContentLibraryData):  # pragma: no cover
        log.error("Received null or incorrect data for event")
        return
    library_key = content_library_data.library_key

    # For now we assume the library has been renamed. Few other things will trigger this event.

    # Update ALL items in the library, because their breadcrumbs will be outdated.
    # TODO: just patch the "breadcrumbs" field? It's the same on every one.
    # TODO: check if the library display_name has actually changed before updating all items?
    update_content_library_index_docs.apply(args=[str(library_key)])


@receiver(LIBRARY_COLLECTION_CREATED)
@receiver(LIBRARY_COLLECTION_DELETED)
@receiver(LIBRARY_COLLECTION_UPDATED)
@only_if_meilisearch_enabled
def library_collection_updated_handler(**kwargs) -> None:
    """
    Create or update the index for the content library collection
    """
    library_collection = kwargs.get("library_collection", None)
    if not library_collection or not isinstance(library_collection, LibraryCollectionData):  # pragma: no cover
        log.error("Received null or incorrect data for event")
        return

    if library_collection.background:
        update_library_collection_index_doc.delay(
            str(library_collection.collection_key),
        )
    else:
        # Update collection index synchronously to make sure that search index is updated before
        # the frontend invalidates/refetches index.
        # See content_library_updated_handler for more details.
        update_library_collection_index_doc.apply(args=[
            str(library_collection.collection_key),
        ])


@receiver(CONTENT_OBJECT_ASSOCIATIONS_CHANGED)
@only_if_meilisearch_enabled
def content_object_associations_changed_handler(**kwargs) -> None:
    """
    Update the collections/tags data in the index for the Content Object
    """
    content_object = kwargs.get("content_object", None)
    if not content_object or not isinstance(content_object, ContentObjectChangedData):
        log.error("Received null or incorrect data for event")
        return

    try:
        # Check if valid course or library block
        opaque_key = UsageKey.from_string(str(content_object.object_id))
    except InvalidKeyError:
        try:
            # Check if valid library collection
            opaque_key = LibraryCollectionLocator.from_string(str(content_object.object_id))
        except InvalidKeyError:
            try:
                # Check if valid library container
                opaque_key = LibraryContainerLocator.from_string(str(content_object.object_id))
            except InvalidKeyError:
                # Invalid content object id
                log.error("Received invalid content object id")
                return

    # This event's changes may contain both "tags" and "collections", but this will happen rarely, if ever.
    # So we allow a potential double "upsert" here.
    if not content_object.changes or "tags" in content_object.changes:
        upsert_content_object_tags_index_doc(opaque_key)
    if not content_object.changes or "collections" in content_object.changes:
        upsert_item_collections_index_docs(opaque_key)
    if not content_object.changes or "units" in content_object.changes:
        upsert_item_containers_index_docs(opaque_key, "units")
    if not content_object.changes or "sections" in content_object.changes:
        upsert_item_containers_index_docs(opaque_key, "sections")
    if not content_object.changes or "subsections" in content_object.changes:
        upsert_item_containers_index_docs(opaque_key, "subsections")


@receiver(LIBRARY_CONTAINER_CREATED)
@receiver(LIBRARY_CONTAINER_UPDATED)
@only_if_meilisearch_enabled
def library_container_updated_handler(**kwargs) -> None:
    """
    Create or update the index for the content library container
    """
    library_container = kwargs.get("library_container", None)
    if not library_container or not isinstance(library_container, LibraryContainerData):  # pragma: no cover
        log.error("Received null or incorrect data for event")
        return

    update_library_container_index_doc.apply(args=[
        str(library_container.container_key),
    ])


@receiver(LIBRARY_CONTAINER_PUBLISHED)
@only_if_meilisearch_enabled
def library_container_published_handler(**kwargs) -> None:
    """
    Update the index for the content library container when its published
    version has changed.
    """
    library_container = kwargs.get("library_container", None)
    if not library_container or not isinstance(library_container, LibraryContainerData):  # pragma: no cover
        log.error("Received null or incorrect data for event")
        return
    # The PUBLISHED event is sent for any change to the published version including deletes, so check if it exists:
    try:
        lib_api.get_container(library_container.container_key)
    except lib_api.ContentLibraryContainerNotFound:
        log.info(f"Observed published deletion of container {str(library_container.container_key)}.")
        # The document should already have been deleted from the search index
        # via the DELETED handler, so there's nothing to do now.
        return

    update_library_container_index_doc.apply(args=[
        str(library_container.container_key),
    ])


@receiver(LIBRARY_CONTAINER_DELETED)
@only_if_meilisearch_enabled
def library_container_deleted(**kwargs) -> None:
    """
    Delete the index for the content library container
    """
    library_container = kwargs.get("library_container", None)
    if not library_container or not isinstance(library_container, LibraryContainerData):  # pragma: no cover
        log.error("Received null or incorrect data for event")
        return

    # Update content library index synchronously to make sure that search index is updated before
    # the frontend invalidates/refetches results. This is only a single document update so is very fast.
    delete_library_container_index_doc.apply(args=[str(library_container.container_key)])
    # TODO: post-Teak, move all the celery tasks directly inline into this handlers? Because now the
    # events are emitted in an [async] worker, so it doesn't matter if the handlers are synchronous.
    # See https://github.com/openedx/edx-platform/pull/36640 discussion.


@receiver([COURSE_IMPORT_COMPLETED, COURSE_RERUN_COMPLETED])
def handle_reindex_on_signal(**kwargs):
    """
    Automatically update Meiliesearch index for course in database on new import or rerun.
    """
    course_data = kwargs.get("course", None)
    if not course_data or not isinstance(course_data, CourseData):
        log.error("Received null or incorrect data for event")
        return

    upsert_course_blocks_docs.delay(str(course_data.course_key))


@receiver(SignalHandler.course_deleted)
def listen_for_course_delete(sender, course_key, **kwargs):  # pylint: disable=unused-argument
    """
    Catches the signal that a course has been deleted
    and removes its entry from the Course About Search index.
    """
    delete_course_index_docs.delay(str(course_key))
