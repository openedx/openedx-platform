"""
Python API for library collections
==================================
"""
from django.db import IntegrityError
from opaque_keys import OpaqueKey
from opaque_keys.edx.keys import BlockTypeKey, UsageKeyV2
from opaque_keys.edx.locator import LibraryCollectionLocator, LibraryContainerLocator, LibraryLocatorV2
from openedx_events.content_authoring.data import LibraryCollectionData
from openedx_events.content_authoring.signals import LIBRARY_COLLECTION_UPDATED
from openedx_content import api as content_api
from openedx_content.models_api import Collection, Component, PublishableEntity

from ..models import ContentLibrary
from .exceptions import (
    ContentLibraryBlockNotFound,
    ContentLibraryCollectionNotFound,
    ContentLibraryContainerNotFound,
    LibraryCollectionAlreadyExists,
)

# The public API is only the following symbols:
__all__ = [
    "create_library_collection",
    "update_library_collection",
    "update_library_collection_items",
    "set_library_item_collections",
    "library_collection_locator",
    "get_library_collection_from_locator",
]


def create_library_collection(
    library_key: LibraryLocatorV2,
    collection_key: str,
    title: str,
    *,
    description: str = "",
    created_by: int | None = None,
    # As an optimization, callers may pass in a pre-fetched ContentLibrary instance
    content_library: ContentLibrary | None = None,
) -> Collection:
    """
    Creates a Collection in the given ContentLibrary.

    If you've already fetched a ContentLibrary for the given library_key, pass it in here to avoid refetching.
    """
    if not content_library:
        content_library = ContentLibrary.objects.get_by_key(library_key)  # type: ignore[attr-defined]
    assert content_library
    assert content_library.learning_package_id
    assert content_library.library_key == library_key

    try:
        collection = content_api.create_collection(
            learning_package_id=content_library.learning_package_id,
            key=collection_key,
            title=title,
            description=description,
            created_by=created_by,
        )
    except IntegrityError as err:
        raise LibraryCollectionAlreadyExists from err

    return collection


def update_library_collection(
    library_key: LibraryLocatorV2,
    collection_key: str,
    *,
    title: str | None = None,
    description: str | None = None,
    # As an optimization, callers may pass in a pre-fetched ContentLibrary instance
    content_library: ContentLibrary | None = None,
) -> Collection:
    """
    Updates a Collection in the given ContentLibrary.
    """
    if not content_library:
        content_library = ContentLibrary.objects.get_by_key(library_key)  # type: ignore[attr-defined]
    assert content_library
    assert content_library.learning_package_id
    assert content_library.library_key == library_key

    try:
        collection = content_api.update_collection(
            learning_package_id=content_library.learning_package_id,
            key=collection_key,
            title=title,
            description=description,
        )
    except Collection.DoesNotExist as exc:
        raise ContentLibraryCollectionNotFound from exc

    return collection


def update_library_collection_items(
    library_key: LibraryLocatorV2,
    collection_key: str,
    *,
    opaque_keys: list[OpaqueKey],
    created_by: int | None = None,
    remove=False,
    # As an optimization, callers may pass in a pre-fetched ContentLibrary instance
    content_library: ContentLibrary | None = None,
) -> Collection:
    """
    Associates the Collection with items (XBlocks, Containers) for the given OpaqueKeys.

    By default the items are added to the Collection.
    If remove=True, the items are removed from the Collection.

    If you've already fetched the ContentLibrary, pass it in to avoid refetching.

    Raises:
    * ContentLibraryCollectionNotFound if no Collection with the given pk is found in the given library.
    * ContentLibraryBlockNotFound if any of the given opaque_keys don't match Components in the given library.
    * ContentLibraryContainerNotFound if any of the given opaque_keys don't match Containers in the given library.

    Returns the updated Collection.
    """
    if not content_library:
        content_library = ContentLibrary.objects.get_by_key(library_key)  # type: ignore[attr-defined]
    assert content_library
    assert content_library.learning_package_id
    assert content_library.library_key == library_key

    # Fetch the Component.key values for the provided UsageKeys.
    item_keys = []
    for opaque_key in opaque_keys:
        if isinstance(opaque_key, LibraryContainerLocator):
            try:
                container = content_api.get_container_by_key(
                    content_library.learning_package_id,
                    key=opaque_key.container_id,
                )
            except Collection.DoesNotExist as exc:
                raise ContentLibraryContainerNotFound(opaque_key) from exc

            item_keys.append(container.key)
        elif isinstance(opaque_key, UsageKeyV2):
            # Parse the block_family from the key to use as namespace.
            block_type = BlockTypeKey.from_string(str(opaque_key))
            try:
                component = content_api.get_component_by_key(
                    content_library.learning_package_id,
                    namespace=block_type.block_family,
                    type_name=opaque_key.block_type,
                    local_key=opaque_key.block_id,
                )
            except Component.DoesNotExist as exc:
                raise ContentLibraryBlockNotFound(opaque_key) from exc

            item_keys.append(component.key)
        else:
            # This should never happen, but just in case.
            raise ValueError(f"Invalid opaque_key: {opaque_key}")

    entities_qset = PublishableEntity.objects.filter(
        key__in=item_keys,
    )

    if remove:
        collection = content_api.remove_from_collection(
            content_library.learning_package_id,
            collection_key,
            entities_qset,
        )
    else:
        collection = content_api.add_to_collection(
            content_library.learning_package_id,
            collection_key,
            entities_qset,
            created_by=created_by,
        )

    return collection


def set_library_item_collections(
    library_key: LibraryLocatorV2,
    entity_key: str,
    *,
    collection_keys: list[str],
    created_by: int | None = None,
    # As an optimization, callers may pass in a pre-fetched ContentLibrary instance
    content_library: ContentLibrary | None = None,
) -> PublishableEntity:
    """
    It Associates the publishable_entity with collections for the given collection keys.

    Only collections in queryset are associated with publishable_entity, all previous publishable_entity-collections
    associations are removed.

    If you've already fetched the ContentLibrary, pass it in to avoid refetching.

    Raises:
    * ContentLibraryCollectionNotFound if any of the given collection_keys don't match Collections in the given library.

    Returns the updated PublishableEntity.
    """
    if not content_library:
        content_library = ContentLibrary.objects.get_by_key(library_key)  # type: ignore[attr-defined]
    assert content_library
    assert content_library.learning_package_id
    assert content_library.library_key == library_key

    publishable_entity = content_api.get_publishable_entity_by_key(
        content_library.learning_package_id,
        key=entity_key,
    )

    # Note: Component.key matches its PublishableEntity.key
    collection_qs = content_api.get_collections(content_library.learning_package_id).filter(
        key__in=collection_keys
    )

    affected_collections = content_api.set_collections(
        publishable_entity,
        collection_qs,
        created_by=created_by,
    )

    # For each collection, trigger LIBRARY_COLLECTION_UPDATED signal and set background=True to trigger
    # collection indexing asynchronously.
    for collection in affected_collections:
        # .. event_implemented_name: LIBRARY_COLLECTION_UPDATED
        # .. event_type: org.openedx.content_authoring.content_library.collection.updated.v1
        LIBRARY_COLLECTION_UPDATED.send_event(
            library_collection=LibraryCollectionData(
                collection_key=library_collection_locator(
                    library_key=library_key,
                    collection_key=collection.key,
                ),
                background=True,
            )
        )

    return publishable_entity


def library_collection_locator(
    library_key: LibraryLocatorV2,
    collection_key: str,
) -> LibraryCollectionLocator:
    """
    Returns the LibraryCollectionLocator associated to a collection
    """

    return LibraryCollectionLocator(library_key, collection_key)


def get_library_collection_from_locator(
    collection_locator: LibraryCollectionLocator,
) -> Collection:
    """
    Return a Collection using the LibraryCollectionLocator
    """
    library_key = collection_locator.lib_key
    collection_key = collection_locator.collection_id
    content_library = ContentLibrary.objects.get_by_key(library_key)  # type: ignore[attr-defined]
    assert content_library.learning_package_id is not None  # shouldn't happen but it's technically possible.
    try:
        return content_api.get_collection(
            content_library.learning_package_id,
            collection_key,
        )
    except Collection.DoesNotExist as exc:
        raise ContentLibraryCollectionNotFound from exc
