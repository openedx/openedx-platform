"""
API for containers (Sections, Subsections, Units) in Content Libraries
"""

from __future__ import annotations

import logging
import typing
from datetime import datetime, timezone
from uuid import uuid4

from django.db.models import F
from django.utils.text import slugify
from opaque_keys.edx.locator import LibraryContainerLocator, LibraryLocatorV2, LibraryUsageLocatorV2
from openedx_content import api as content_api
from openedx_content.models_api import Container
from openedx_events.content_authoring.data import LibraryContainerData
from openedx_events.content_authoring.signals import LIBRARY_CONTAINER_DELETED

from ..models import ContentLibrary
from .block_metadata import LibraryXBlockMetadata
from .container_metadata import (
    LIBRARY_ALLOWED_CONTAINER_TYPES,
    ContainerHierarchy,
    ContainerMetadata,
    get_container_from_key,
    get_entity_from_key,
    library_container_locator,
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
        # Temporarily alias collection_code to "key" so downstream consumers
        # (search indexer, REST API) keep the same field name.  We will update
        # downstream consumers later: https://github.com/openedx/openedx-platform/issues/38406
        associated_collections = content_api.get_entity_collections(
            container.publishable_entity.learning_package_id,
            container_key.container_id,
        ).values("title", key=F("collection_code"))
    else:
        associated_collections = None
    container_meta = ContainerMetadata.from_container(
        container_key.lib_key,
        container,
        associated_collections=associated_collections,
    )
    assert container_meta.container_type_code == container_key.container_type
    return container_meta


def create_container(
    library_key: LibraryLocatorV2,
    container_cls: content_api.ContainerSubclass,
    slug: str | None,
    title: str,
    user_id: int | None,
    created: datetime | None = None,
) -> ContainerMetadata:
    """
    [ 🛑 UNSTABLE ] Create a container (a Section, Subsection, or Unit) in the specified content library.

    It will initially be empty.
    """
    assert container_cls.type_code in LIBRARY_ALLOWED_CONTAINER_TYPES
    assert isinstance(library_key, LibraryLocatorV2)
    content_library = ContentLibrary.objects.get_by_key(library_key)
    assert content_library.learning_package_id  # Should never happen but we made this a nullable field so need to check
    if slug is None:
        # Automatically generate a slug. Append a random suffix so it should be unique.
        slug = slugify(title, allow_unicode=True) + "-" + uuid4().hex[-6:]
    # Make sure the slug is valid by first creating a key for the new container:
    _container_key = LibraryContainerLocator(
        library_key,
        container_type=container_cls.type_code,
        container_id=slug,
    )

    if not created:
        created = datetime.now(tz=timezone.utc)  # noqa: UP017

    # Then try creating the actual container:
    container, _initial_version = content_api.create_container_and_version(
        content_library.learning_package_id,
        container_code=slug,
        title=title,
        container_cls=container_cls,
        entities=[],
        created=created,
        created_by=user_id,
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
    created = datetime.now(tz=timezone.utc)  # noqa: UP017

    version = content_api.create_next_container_version(
        container,
        title=display_name,
        created=created,
        created_by=user_id,
    )

    return ContainerMetadata.from_container(library_key, version.container)


def delete_container(
    container_key: LibraryContainerLocator,
) -> None:
    """
    [ 🛑 UNSTABLE ] Delete a container (a Section, Subsection, or Unit) (soft delete).

    No-op if container doesn't exist or has already been soft-deleted.
    """
    try:
        container = get_container_from_key(container_key)
    except Container.DoesNotExist:
        # There may be cases where entries are created in the
        # search index, but the container is not created
        # (an intermediate error occurred).
        # In that case, we keep the index updated by removing the entry,
        # but still raise the error so the caller knows the container did not exist.
        # .. event_implemented_name: LIBRARY_CONTAINER_DELETED
        # .. event_type: org.openedx.content_authoring.content_library.container.deleted.v1
        LIBRARY_CONTAINER_DELETED.send_event(library_container=LibraryContainerData(container_key=container_key))
        raise

    content_api.soft_delete_draft(container.id)


def restore_container(container_key: LibraryContainerLocator) -> None:
    """
    [ 🛑 UNSTABLE ] Restore the specified library container.
    """
    container = get_container_from_key(container_key, include_deleted=True)
    content_api.set_draft_version(container.id, container.versioning.latest.pk)


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

    child_entities = content_api.get_entities_in_container(container, published=published)
    result: list[LibraryXBlockMetadata | ContainerMetadata] = []
    for entry in child_entities:
        if hasattr(entry.entity, "component"):  # the child is a Component
            result.append(LibraryXBlockMetadata.from_component(container_key.lib_key, entry.entity.component))
        else:
            assert isinstance(entry.entity.container, Container)
            result.append(ContainerMetadata.from_container(container_key.lib_key, entry.entity.container))
    return result


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
    children_keys: list[LibraryUsageLocatorV2] | list[LibraryContainerLocator],
    user_id: int | None,
    entities_action: content_api.ChildrenEntitiesAction = content_api.ChildrenEntitiesAction.REPLACE,
):
    """
    [ 🛑 UNSTABLE ] Adds children components or containers to given container.
    """
    container = get_container_from_key(container_key)
    created = datetime.now(tz=timezone.utc)  # noqa: UP017

    new_version = content_api.create_next_container_version(
        container,
        created=created,
        created_by=user_id,
        entities=[get_entity_from_key(key) for key in children_keys],
        entities_action=entities_action,
    )

    return ContainerMetadata.from_container(container_key.lib_key, new_version.container)


def get_containers_contains_item(key: LibraryUsageLocatorV2 | LibraryContainerLocator) -> list[ContainerMetadata]:
    """
    [ 🛑 UNSTABLE ] Get containers that contains the item, that can be a component or another container.
    """
    entity = get_entity_from_key(key)
    containers = content_api.get_containers_with_entity(entity.id).select_related("container_type")
    return [ContainerMetadata.from_container(key.lib_key, container) for container in containers]


def publish_container_changes(
    container_key: LibraryContainerLocator,
    user_id: int | None,
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
    drafts_to_publish = content_api.get_all_drafts(learning_package.id).filter(entity__pk=container.id)
    # Publish the container, which will also auto-publish any unpublished child components:
    content_api.publish_from_drafts(learning_package.id, draft_qset=drafts_to_publish, published_by=user_id)


def copy_container(container_key: LibraryContainerLocator, user_id: int) -> UserClipboardData:
    """
    [ 🛑 UNSTABLE ] Copy a container (a Section, Subsection, or Unit) to the content staging.
    """
    container_metadata = get_container(container_key)
    container_serializer = ContainerSerializer(container_metadata)
    block_type = content_api.get_container_subclass(container_key.container_type).olx_tag_name

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
