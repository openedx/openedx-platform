"""
Content library signal handlers.
"""

import logging

from django.dispatch import receiver
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


@receiver(content_signals.LEARNING_PACKAGE_COLLECTION_CHANGED)
def collection_updated(
    learning_package: content_signals.LearningPackageEventData,
    change: content_signals.CollectionChangeData,
    **kwargs,
):
    """
    A Collection has been updated - handle that as needed.

    We receive this low-level event from `openedx_content`, and check if it
    happened in a library. If so, we emit more detailed library-specific events.

    ⏳ This event is emitted synchronously and this handler is called
       synchronously. If a lot of entities were changed, we need to dispatch an
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

    # Now, what about the actual entities (containers/components) in the collection?
    if entities_changed:
        if len(entities_changed) == 1:
            # If there's only one changed entity, emit the event synchronously:
            fn = tasks.send_collections_changed_events
        else:
            # If there are more than one changed entities, emit the events asynchronously:
            fn = tasks.send_collections_changed_events.delay
        fn(
            publishable_entity_ids=sorted(entities_changed),  # sorted() is mostly for test purposes
            learning_package_id=learning_package.id,
            library_key_str=str(library.library_key),
        )
