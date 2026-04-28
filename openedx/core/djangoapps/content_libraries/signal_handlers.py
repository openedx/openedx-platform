"""
Content library signal handlers.
"""

import logging

from attrs import asdict
from django.dispatch import receiver
from openedx_content import api as content_api
from openedx_content.api import signals as content_signals
from openedx_events.content_authoring.data import LibraryCollectionData
from openedx_events.content_authoring.signals import (
    LIBRARY_COLLECTION_CREATED,
    LIBRARY_COLLECTION_DELETED,
    LIBRARY_COLLECTION_UPDATED,
)

from . import tasks
from .api import library_collection_locator
from .models import ContentLibrary

log = logging.getLogger(__name__)


@receiver(content_signals.ENTITIES_DRAFT_CHANGED)
def entities_updated(
    learning_package: content_signals.LearningPackageEventData,
    change_log: content_signals.DraftChangeLogEventData,
    **kwargs,
) -> None:
    """
    Entities (containers/components) have been changed - handle that as needed.

    We receive this low-level event from `openedx_content`, and check if it
    happened in a library. If so, we emit more detailed library-specific events.

    This event change log includes entities that were directly edited as well as
    their dependencies which may be only indirectly affected.

    💾 This event is only received after the transaction has committed.
    ⏳ This event is emitted synchronously and this handler is called
       synchronously. If multiple entities were changed, we need to dispatch an
       asynchronous handler to deal with them to avoid slowdowns. If only one
       entity is changed, we want to deal with that synchronously so that we
       can show the user correct data when the current requests completes.
    """
    try:
        ContentLibrary.objects.get(learning_package_id=learning_package.id)
    except ContentLibrary.DoesNotExist:
        return  # We don't care about non-library events.

    # The list of entities changed, both directly changed and indirectly affected (e.g. ancestor containers)
    change_list = [asdict(change) for change in change_log.changes]
    tasks.dispatch_and_wait(
        tasks.send_change_events_for_modified_entities,
        learning_package_id=learning_package.id,
        change_list=change_list,
        # If there are only a few entities changed, we'll call the handler synchronously so that everything is up to
        # date when the requests finishes. (This is important for the Authoring MFE which uses the search index for its
        # main UI listing components, containers, and collections in the library.) If many entities have changed, we'll
        # handle it asynchronously but _try_ waiting a bit in case it finishes quickly.
        wait_for_full_completion=len(change_list) < 5,
    )


@receiver(content_signals.ENTITIES_PUBLISHED)
def entities_published(
    learning_package: content_signals.LearningPackageEventData,
    change_log: content_signals.PublishLogEventData,
    **kwargs,
) -> None:
    """
    Entities (containers/components) have been published - handle that as needed.

    We receive this low-level event from `openedx_content`, and check if it
    happened in a library. If so, we emit more detailed library-specific events.

    This event change log includes entities that were directly published as well
    as other things that are affected as publish "side effects".

    💾 This event is only received after the transaction has committed.
    ⏳ This event is emitted synchronously and this handler is called
       synchronously. If multiple entities were published, we need to dispatch
       an asynchronous handler to deal with them to avoid slowdowns. If only one
       entity was published, we want to deal with that synchronously so that we
       can show the user correct data when the current requests completes.
    """
    try:
        library = ContentLibrary.objects.get(learning_package_id=learning_package.id)
    except ContentLibrary.DoesNotExist:
        return  # We don't care about non-library events.

    tasks.dispatch_and_wait(
        tasks.send_events_after_publish,
        publish_log_id=change_log.publish_log_id,
        library_key_str=str(library.library_key),
        # If there are only a few entities published, we'll call the handler synchronously so that everything is up to
        # date when the requests finishes:
        wait_for_full_completion=len(change_log.changes) < 5,
    )


@receiver(content_signals.COLLECTION_CHANGED)
def collection_updated(
    learning_package: content_signals.LearningPackageEventData,
    change: content_signals.CollectionChangeData,
    **kwargs,
) -> None:
    """
    A Collection has been updated - handle that as needed.

    We receive this low-level event from `openedx_content`, and check if it
    happened in a library. If so, we emit more detailed library-specific events.

    ⏳ This event is emitted synchronously and this handler is called
       synchronously. If multiple entities were changed, we need to dispatch an
       asynchronous handler to deal with them to avoid slowdowns.
    """
    try:
        library = ContentLibrary.objects.get(learning_package_id=learning_package.id)
    except ContentLibrary.DoesNotExist:
        return  # We don't care about non-library events.

    collection_key = library_collection_locator(library_key=library.library_key, collection_key=change.collection_code)
    entities_changed = change.entities_added + change.entities_removed

    if change.created:  # This is a newly-created collection, or was "un-deleted":
        # .. event_implemented_name: LIBRARY_COLLECTION_CREATED
        # .. event_type: org.openedx.content_authoring.content_library.collection.created.v1
        LIBRARY_COLLECTION_CREATED.send_event(library_collection=LibraryCollectionData(collection_key=collection_key))
        # As an example of what this event triggers,  Collections are listed in the Meilisearch index as items in the
        # library. So the handler will add this Collection as an entry in the Meilisearch index.
    elif change.metadata_modified or entities_changed:
        # The collection was renamed or its items were changed.
        # This event is ambiguous but because the search index of the collection itself may have something like
        # "contains 15 items", we _do_ need to emit it even when only the items have changed and not the metadata.
        # .. event_implemented_name: LIBRARY_COLLECTION_UPDATED
        # .. event_type: org.openedx.content_authoring.content_library.collection.updated.v1
        LIBRARY_COLLECTION_UPDATED.send_event(library_collection=LibraryCollectionData(collection_key=collection_key))
    elif change.deleted:
        # .. event_implemented_name: LIBRARY_COLLECTION_DELETED
        # .. event_type: org.openedx.content_authoring.content_library.collection.deleted.v1
        LIBRARY_COLLECTION_DELETED.send_event(library_collection=LibraryCollectionData(collection_key=collection_key))

    if change.metadata_modified:
        # If the collection was renamed, then in addition to LIBRARY_COLLECTION_UPDATED we need to send out a
        # CONTENT_OBJECT_ASSOCIATIONS_CHANGED notice to update the "collections=..." field in the search index on all
        # entities that are in the collection.
        current_collection_entities = content_api.get_collection_entities(learning_package.id, change.collection_code)
        # We also need to process any entities that happened to be removed as part of this same event (if any)
        entities_changed = change.entities_removed + list(current_collection_entities.values_list("id", flat=True))

    # Update any entities that were added/removed to this collection.
    # If the collection was re-enabled (un-deleted), this will already include all entities in the collection.
    # If the collection was renamed, we just now added all entities in the collection to this list of changed entities.
    if entities_changed:
        tasks.dispatch_and_wait(
            tasks.send_collections_changed_events,
            publishable_entity_ids=sorted(entities_changed),  # sorted() is mostly for test purposes
            learning_package_id=learning_package.id,
            library_key_str=str(library.library_key),
            # If there's only one changed entity, emit the event synchronously:
            wait_for_full_completion=len(entities_changed) == 1,
        )
