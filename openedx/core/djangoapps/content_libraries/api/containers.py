"""
API for containers (Sections, Subsections, Units) in Content Libraries
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from uuid import uuid4
import typing

from django.utils.text import slugify
from opaque_keys.edx.locator import LibraryContainerLocator, LibraryLocatorV2, LibraryUsageLocatorV2
from openedx_events.content_authoring.data import (
    ContentObjectChangedData,
    LibraryCollectionData,
    LibraryContainerData,
)
from openedx_events.content_authoring.signals import (
    CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
    LIBRARY_COLLECTION_UPDATED,
    LIBRARY_CONTAINER_CREATED,
    LIBRARY_CONTAINER_DELETED,
    LIBRARY_CONTAINER_UPDATED,
)
from openedx_content import api as content_api
from openedx_content.models_api import Container, ContainerVersion, Component
from openedx.core.djangoapps.content_libraries.api.collections import library_collection_locator

from openedx.core.djangoapps.xblock.api import get_component_from_usage_key

from .. import tasks
from ..models import ContentLibrary
from .block_metadata import LibraryXBlockMetadata
from .container_metadata import (
    ContainerHierarchy,
    ContainerMetadata,
    ContainerType,
    library_container_locator,
    get_container_from_key,
)
from .serializers import ContainerSerializer


if typing.TYPE_CHECKING:
    from openedx.core.djangoapps.content_staging.api import UserClipboardData


# 🛑 UNSTABLE: All APIs related to containers are unstable until we've figured
#              out our approach to dynamic content (randomized, A/B tests, etc.)
__all__ = [
    "get_container",
    "create_container",
    "get_container_children",
    "get_container_children_count",
    "update_container",
    "delete_container",
    "restore_container",
    "update_container_children",
    "get_containers_contains_item",
    "publish_container_changes",
    "get_library_object_hierarchy",
    "copy_container",
    "library_container_locator",
]

log = logging.getLogger(__name__)


def get_container(
    container_key: LibraryContainerLocator,
    *,
    include_collections=False,
) -> ContainerMetadata:
    """
    [ 🛑 UNSTABLE ] Get a container (a Section, Subsection, or Unit).
    """
    container = get_container_from_key(container_key)
    if include_collections:
        associated_collections = content_api.get_entity_collections(
            container.publishable_entity.learning_package_id,
            container_key.container_id,
        ).values('key', 'title')
    else:
        associated_collections = None
    container_meta = ContainerMetadata.from_container(
        container_key.lib_key,
        container,
        associated_collections=associated_collections,
    )
    assert container_meta.container_type.value == container_key.container_type
    return container_meta


def create_container(
    library_key: LibraryLocatorV2,
    container_type: ContainerType,
    slug: str | None,
    title: str,
    user_id: int | None,
    created: datetime | None = None,
) -> ContainerMetadata:
    """
    [ 🛑 UNSTABLE ] Create a container (a Section, Subsection, or Unit) in the specified content library.

    It will initially be empty.
    """
    assert isinstance(library_key, LibraryLocatorV2)
    content_library = ContentLibrary.objects.get_by_key(library_key)
    assert content_library.learning_package_id  # Should never happen but we made this a nullable field so need to check
    if slug is None:
        # Automatically generate a slug. Append a random suffix so it should be unique.
        slug = slugify(title, allow_unicode=True) + '-' + uuid4().hex[-6:]
    # Make sure the slug is valid by first creating a key for the new container:
    container_key = LibraryContainerLocator(
        library_key,
        container_type=container_type.value,
        container_id=slug,
    )

    if not created:
        created = datetime.now(tz=timezone.utc)

    container: Container
    _initial_version: ContainerVersion

    # Then try creating the actual container:
    match container_type:
        case ContainerType.Unit:
            container, _initial_version = content_api.create_unit_and_version(
                content_library.learning_package_id,
                key=slug,
                title=title,
                created=created,
                created_by=user_id,
            )
        case ContainerType.Subsection:
            container, _initial_version = content_api.create_subsection_and_version(
                content_library.learning_package_id,
                key=slug,
                title=title,
                created=created,
                created_by=user_id,
            )
        case ContainerType.Section:
            container, _initial_version = content_api.create_section_and_version(
                content_library.learning_package_id,
                key=slug,
                title=title,
                created=created,
                created_by=user_id,
            )
        case _:
            raise NotImplementedError(f"Library does not support {container_type} yet")

    # .. event_implemented_name: LIBRARY_CONTAINER_CREATED
    # .. event_type: org.openedx.content_authoring.content_library.container.created.v1
    LIBRARY_CONTAINER_CREATED.send_event(
        library_container=LibraryContainerData(
            container_key=container_key,
        )
    )

    return ContainerMetadata.from_container(library_key, container)


def update_container(
    container_key: LibraryContainerLocator,
    display_name: str,
    user_id: int | None,
) -> ContainerMetadata:
    """
    [ 🛑 UNSTABLE ] Update a container (a Section, Subsection, or Unit) title.
    """
    container = get_container_from_key(container_key)
    library_key = container_key.lib_key
    created = datetime.now(tz=timezone.utc)

    container_type = ContainerType(container_key.container_type)

    version: ContainerVersion
    affected_containers: list[ContainerMetadata] = []
    # Get children containers or components to update their index data
    children = get_container_children(
        container_key,
        published=False,
    )
    child_key_name = 'container_key'

    match container_type:
        case ContainerType.Unit:
            version = content_api.create_next_unit_version(
                container.unit,
                title=display_name,
                created=created,
                created_by=user_id,
            )
            affected_containers = get_containers_contains_item(container_key)
            # Components have usage_key instead of container_key
            child_key_name = 'usage_key'
        case ContainerType.Subsection:
            version = content_api.create_next_subsection_version(
                container.subsection,
                title=display_name,
                created=created,
                created_by=user_id,
            )
            affected_containers = get_containers_contains_item(container_key)
        case ContainerType.Section:
            version = content_api.create_next_section_version(
                container.section,
                title=display_name,
                created=created,
                created_by=user_id,
            )

            # The `affected_containers` are not obtained, because the sections are
            # not contained in any container.
        case _:
            raise NotImplementedError(f"Library does not support {container_type} yet")

    # Send event related to the updated container
    # .. event_implemented_name: LIBRARY_CONTAINER_UPDATED
    # .. event_type: org.openedx.content_authoring.content_library.container.updated.v1
    LIBRARY_CONTAINER_UPDATED.send_event(
        library_container=LibraryContainerData(
            container_key=container_key,
        )
    )

    # Send events related to the containers that contains the updated container.
    # This is to update the children display names used in the section/subsection previews.
    for affected_container in affected_containers:
        # .. event_implemented_name: LIBRARY_CONTAINER_UPDATED
        # .. event_type: org.openedx.content_authoring.content_library.container.updated.v1
        LIBRARY_CONTAINER_UPDATED.send_event(
            library_container=LibraryContainerData(
                container_key=affected_container.container_key,
            )
        )
    # Update children components and containers index data, for example,
    # All subsections under a section have section key in index that needs to be updated.
    # So if parent section name has been changed, it needs to be reflected in sections key of children
    for child in children:
        # .. event_implemented_name: CONTENT_OBJECT_ASSOCIATIONS_CHANGED
        # .. event_type: org.openedx.content_authoring.content.object.associations.changed.v1
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
            content_object=ContentObjectChangedData(
                object_id=str(getattr(child, child_key_name)),
                changes=[container_key.container_type + "s"],
            ),
        )

    return ContainerMetadata.from_container(library_key, version.container)


def delete_container(
    container_key: LibraryContainerLocator,
) -> None:
    """
    [ 🛑 UNSTABLE ] Delete a container (a Section, Subsection, or Unit) (soft delete).

    No-op if container doesn't exist or has already been soft-deleted.
    """
    library_key = container_key.lib_key
    container = get_container_from_key(container_key)

    # Fetch related collections and containers before soft-delete
    affected_collections = content_api.get_entity_collections(
        container.publishable_entity.learning_package_id,
        container.key,
    )
    affected_containers = get_containers_contains_item(container_key)
    # Get children containers or components to update their index data
    children = get_container_children(
        container_key,
        published=False,
    )
    content_api.soft_delete_draft(container.pk)

    # .. event_implemented_name: LIBRARY_CONTAINER_DELETED
    # .. event_type: org.openedx.content_authoring.content_library.container.deleted.v1
    LIBRARY_CONTAINER_DELETED.send_event(
        library_container=LibraryContainerData(
            container_key=container_key,
        )
    )

    # For each collection, trigger LIBRARY_COLLECTION_UPDATED signal and set background=True to trigger
    # collection indexing asynchronously.
    #
    # To delete the container on collections
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
    # Send events related to the containers that contains the updated container.
    # This is to update the children display names used in the section/subsection previews.
    for affected_container in affected_containers:
        # .. event_implemented_name: LIBRARY_CONTAINER_UPDATED
        # .. event_type: org.openedx.content_authoring.content_library.container.updated.v1
        LIBRARY_CONTAINER_UPDATED.send_event(
            library_container=LibraryContainerData(
                container_key=affected_container.container_key,
            )
        )
    container_type = ContainerType(container_key.container_type)
    key_name = 'container_key'
    if container_type == ContainerType.Unit:
        # Components have usage_key instead of container_key
        key_name = 'usage_key'
    # Update children components and containers index data, for example,
    # All subsections under a section have section key in index that needs to be updated.
    # So if parent section is deleted, it needs to be removed from sections key of children
    for child in children:
        # .. event_implemented_name: CONTENT_OBJECT_ASSOCIATIONS_CHANGED
        # .. event_type: org.openedx.content_authoring.content.object.associations.changed.v1
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
            content_object=ContentObjectChangedData(
                object_id=str(getattr(child, key_name)),
                changes=[container_key.container_type + "s"],
            ),
        )


def restore_container(container_key: LibraryContainerLocator) -> None:
    """
    [ 🛑 UNSTABLE ] Restore the specified library container.
    """
    library_key = container_key.lib_key
    container = get_container_from_key(container_key, include_deleted=True)

    affected_collections = content_api.get_entity_collections(
        container.publishable_entity.learning_package_id,
        container.key,
    )

    content_api.set_draft_version(container.pk, container.versioning.latest.pk)
    # Fetch related containers after restore
    affected_containers = get_containers_contains_item(container_key)
    # Get children containers or components to update their index data
    children = get_container_children(
        container_key,
        published=False,
    )

    # .. event_implemented_name: LIBRARY_CONTAINER_CREATED
    # .. event_type: org.openedx.content_authoring.content_library.container.created.v1
    LIBRARY_CONTAINER_CREATED.send_event(
        library_container=LibraryContainerData(
            container_key=container_key,
        )
    )

    content_changes = ["collections", "tags"]
    if affected_containers and len(affected_containers) > 0:
        # Update parent key data in index. Eg. `sections` key in index for subsection
        content_changes.append(str(affected_containers[0].container_type.value) + "s")
    # Add tags, collections and parent data back to index
    # .. event_implemented_name: CONTENT_OBJECT_ASSOCIATIONS_CHANGED
    # .. event_type: org.openedx.content_authoring.content.object.associations.changed.v1
    CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
        content_object=ContentObjectChangedData(
            object_id=str(container_key),
            changes=content_changes,
        ),
    )

    # For each collection, trigger LIBRARY_COLLECTION_UPDATED signal and set background=True to trigger
    # collection indexing asynchronously.
    #
    # To restore the container on collections
    for collection in affected_collections:
        # .. event_implemented_name: LIBRARY_COLLECTION_UPDATED
        # .. event_type: org.openedx.content_authoring.content_library.collection.updated.v1
        LIBRARY_COLLECTION_UPDATED.send_event(
            library_collection=LibraryCollectionData(
                collection_key=library_collection_locator(
                    library_key=library_key,
                    collection_key=collection.key,
                ),
            )
        )
    # Send events related to the containers that contains the updated container.
    # This is to update the children display names used in the section/subsection previews.
    for affected_container in affected_containers:
        # .. event_implemented_name: LIBRARY_CONTAINER_UPDATED
        # .. event_type: org.openedx.content_authoring.content_library.container.updated.v1
        LIBRARY_CONTAINER_UPDATED.send_event(
            library_container=LibraryContainerData(
                container_key=affected_container.container_key,
            )
        )
    container_type = ContainerType(container_key.container_type)
    key_name = 'container_key'
    if container_type == ContainerType.Unit:
        key_name = 'usage_key'
    # Update children components and containers index data, for example,
    # All subsections under a section have section key in index that needs to be updated.
    # Should restore removed parent section in sections key of children subsections
    for child in children:
        # .. event_implemented_name: CONTENT_OBJECT_ASSOCIATIONS_CHANGED
        # .. event_type: org.openedx.content_authoring.content.object.associations.changed.v1
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
            content_object=ContentObjectChangedData(
                object_id=str(getattr(child, key_name)),
                changes=[container_key.container_type + "s"],
            ),
        )


def get_container_children(
    container_key: LibraryContainerLocator,
    *,
    published=False,
) -> list[LibraryXBlockMetadata | ContainerMetadata]:
    """
    [ 🛑 UNSTABLE ] Get the entities contained in the given container
    (e.g. the components/xblocks in a unit, units in a subsection, subsections in a section)
    """
    container = get_container_from_key(container_key)
    container_type = ContainerType(container_key.container_type)

    match container_type:
        case ContainerType.Unit:
            child_components = content_api.get_components_in_unit(container.unit, published=published)
            return [LibraryXBlockMetadata.from_component(
                container_key.lib_key,
                entry.component
            ) for entry in child_components]
        case ContainerType.Subsection:
            child_units = content_api.get_units_in_subsection(container.subsection, published=published)
            return [ContainerMetadata.from_container(
                container_key.lib_key,
                entry.unit
            ) for entry in child_units]
        case ContainerType.Section:
            child_subsections = content_api.get_subsections_in_section(container.section, published=published)
            return [ContainerMetadata.from_container(
                container_key.lib_key,
                entry.subsection,
            ) for entry in child_subsections]
        case _:
            child_entities = content_api.get_entities_in_container(container, published=published)
            return [ContainerMetadata.from_container(
                container_key.lib_key,
                entry.entity
            ) for entry in child_entities]


def get_container_children_count(
    container_key: LibraryContainerLocator,
    published=False,
) -> int:
    """
    [ 🛑 UNSTABLE ] Get the count of entities contained in the given container (e.g. the components/xblocks in a unit)
    """
    container = get_container_from_key(container_key)
    return content_api.get_container_children_count(container, published=published)


def update_container_children(
    container_key: LibraryContainerLocator,
    children_ids: list[LibraryUsageLocatorV2] | list[LibraryContainerLocator],
    user_id: int | None,
    entities_action: content_api.ChildrenEntitiesAction = content_api.ChildrenEntitiesAction.REPLACE,
):
    """
    [ 🛑 UNSTABLE ] Adds children components or containers to given container.
    """
    library_key = container_key.lib_key
    container_type = ContainerType(container_key.container_type)
    container = get_container_from_key(container_key)
    created = datetime.now(tz=timezone.utc)
    new_version: ContainerVersion
    match container_type:
        case ContainerType.Unit:
            components = [get_component_from_usage_key(key) for key in children_ids]  # type: ignore[arg-type]
            new_version = content_api.create_next_unit_version(
                container.unit,
                components=components,  # type: ignore[arg-type]
                created=created,
                created_by=user_id,
                entities_action=entities_action,
            )

            for key in children_ids:
                # .. event_implemented_name: CONTENT_OBJECT_ASSOCIATIONS_CHANGED
                # .. event_type: org.openedx.content_authoring.content.object.associations.changed.v1
                CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
                    content_object=ContentObjectChangedData(
                        object_id=str(key),
                        changes=["units"],
                    ),
                )
        case ContainerType.Subsection:
            units = [get_container_from_key(key).unit for key in children_ids]  # type: ignore[arg-type]
            new_version = content_api.create_next_subsection_version(
                container.subsection,
                units=units,  # type: ignore[arg-type]
                created=created,
                created_by=user_id,
                entities_action=entities_action,
            )

            for key in children_ids:
                # .. event_implemented_name: CONTENT_OBJECT_ASSOCIATIONS_CHANGED
                # .. event_type: org.openedx.content_authoring.content.object.associations.changed.v1
                CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
                    content_object=ContentObjectChangedData(
                        object_id=str(key),
                        changes=["subsections"],
                    ),
                )
        case ContainerType.Section:
            subsections = [get_container_from_key(key).subsection for key in children_ids]  # type: ignore[arg-type]
            new_version = content_api.create_next_section_version(
                container.section,
                subsections=subsections,  # type: ignore[arg-type]
                created=created,
                created_by=user_id,
                entities_action=entities_action,
            )

            for key in children_ids:
                # .. event_implemented_name: CONTENT_OBJECT_ASSOCIATIONS_CHANGED
                # .. event_type: org.openedx.content_authoring.content.object.associations.changed.v1
                CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
                    content_object=ContentObjectChangedData(
                        object_id=str(key),
                        changes=["sections"],
                    ),
                )
        case _:
            raise ValueError(f"Invalid container type: {container_type}")

    # .. event_implemented_name: LIBRARY_CONTAINER_UPDATED
    # .. event_type: org.openedx.content_authoring.content_library.container.updated.v1
    LIBRARY_CONTAINER_UPDATED.send_event(
        library_container=LibraryContainerData(
            container_key=container_key,
        )
    )

    return ContainerMetadata.from_container(library_key, new_version.container)


def get_containers_contains_item(
    key: LibraryUsageLocatorV2 | LibraryContainerLocator
) -> list[ContainerMetadata]:
    """
    [ 🛑 UNSTABLE ] Get containers that contains the item, that can be a component or another container.
    """
    item: Component | Container

    if isinstance(key, LibraryUsageLocatorV2):
        item = get_component_from_usage_key(key)

    elif isinstance(key, LibraryContainerLocator):
        item = get_container_from_key(key)

    containers = content_api.get_containers_with_entity(
        item.publishable_entity.pk,
    )
    return [
        ContainerMetadata.from_container(key.lib_key, container)
        for container in containers
    ]


def publish_container_changes(
    container_key: LibraryContainerLocator,
    user_id: int | None,
    call_post_publish_events_sync=False,
) -> None:
    """
    [ 🛑 UNSTABLE ] Publish all unpublished changes in a container and all its child
    containers/blocks.
    """
    container = get_container_from_key(container_key)
    library_key = container_key.lib_key
    content_library = ContentLibrary.objects.get_by_key(library_key)  # type: ignore[attr-defined]
    learning_package = content_library.learning_package
    assert learning_package
    # The core publishing API is based on draft objects, so find the draft that corresponds to this container:
    drafts_to_publish = content_api.get_all_drafts(learning_package.id).filter(entity__pk=container.pk)
    # Publish the container, which will also auto-publish any unpublished child components:
    publish_log = content_api.publish_from_drafts(
        learning_package.id,
        draft_qset=drafts_to_publish,
        published_by=user_id,
    )
    # Update the search index (and anything else) for the affected container + blocks
    # This is mostly synchronous but may complete some work asynchronously if there are a lot of changes.
    if call_post_publish_events_sync:
        tasks.send_events_after_publish(publish_log.pk, str(library_key))
    else:
        tasks.wait_for_post_publish_events(publish_log, library_key)


def copy_container(container_key: LibraryContainerLocator, user_id: int) -> UserClipboardData:
    """
    [ 🛑 UNSTABLE ] Copy a container (a Section, Subsection, or Unit) to the content staging.
    """
    container_metadata = get_container(container_key)
    container_serializer = ContainerSerializer(container_metadata)
    block_type = ContainerType(container_key.container_type).olx_tag

    from openedx.core.djangoapps.content_staging import api as content_staging_api

    return content_staging_api.save_content_to_user_clipboard(
        user_id=user_id,
        block_type=block_type,
        olx=container_serializer.olx_str,
        display_name=container_metadata.display_name,
        suggested_url_name=str(container_key),
        tags=container_serializer.tags,
        copied_from=container_key,
        version_num=container_metadata.published_version_num,
        static_files=container_serializer.static_files,
    )


def get_library_object_hierarchy(
    object_key: LibraryUsageLocatorV2 | LibraryContainerLocator,
) -> ContainerHierarchy:
    """
    [ 🛑 UNSTABLE ] Returns the full ancestry and descendents of the library object with the given object_key.

    TODO: We intend to replace this implementation with a more efficient one that makes fewer
    database queries in the future. More details being discussed in
    https://github.com/openedx/edx-platform/pull/36813#issuecomment-3136631767
    """
    return ContainerHierarchy.create_from_library_object_key(object_key)
