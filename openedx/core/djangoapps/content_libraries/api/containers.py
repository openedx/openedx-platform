"""
API for containers (Sections, Subsections, Units) in Content Libraries
"""

from __future__ import annotations

import logging
import operator
import typing
from datetime import datetime, timezone
from functools import cache
from uuid import UUID, uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import F
from django.utils.text import slugify
from opaque_keys.edx.locator import LibraryContainerLocator, LibraryLocatorV2, LibraryUsageLocatorV2
from openedx_content import api as content_api
from openedx_content.models_api import Container, PublishLogRecord
from openedx_events.content_authoring.data import LibraryContainerData
from openedx_events.content_authoring.signals import LIBRARY_CONTAINER_DELETED

from ..models import ContentLibrary
from .block_metadata import (
    LibraryHistoryContributor,
    LibraryHistoryEntry,
    LibraryPublishHistoryGroup,
    LibraryXBlockMetadata,
    direct_published_entity_from_record,
    get_entity_item_type,
    make_contributor,
    resolve_change_action,
)
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
    "get_library_container_draft_history",
    "get_library_container_publish_history",
    "get_library_container_publish_history_entries",
    "get_library_container_creation_entry",
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


def get_library_container_draft_history(
    container_key: LibraryContainerLocator,
    request=None,
) -> list[LibraryHistoryEntry]:
    """
    [ 🛑 UNSTABLE ] Return the combined draft history for a container and all of its descendant
    components, sorted from most-recent to oldest.

    Each entry describes a single change log record: who made the change, when,
    what the title was at that point.
    """
    container = get_container_from_key(container_key)
    # Collect entity IDs for all components nested inside this container.
    component_entity_ids = content_api.get_descendant_component_entity_ids(container)

    @cache
    def _contributor(user):
        return make_contributor(user, request)

    results: list[LibraryHistoryEntry] = []
    # Process the container itself first, then each descendant component.
    for item_id in [container.pk] + component_entity_ids:
        for record in content_api.get_entity_draft_history(item_id).select_related(
            "entity__component__component_type",
            "entity__container__container_type",
            "draft_change_log__changed_by__profile",
        ):
            # Use the new version when available; fall back to the old version
            # (e.g. for delete records where new_version is None).
            version = record.new_version if record.new_version is not None else record.old_version
            # old_version is None only for the very first publish (entity had no prior published version)
            old_version_num = record.old_version.version_num if record.old_version else 0
            # new_version is None for soft-delete publishes (container deleted without a new draft version)
            new_version_num = record.new_version.version_num if record.new_version else None
            item_type = get_entity_item_type(record.entity)
            results.append(LibraryHistoryEntry(
                contributor=_contributor(record.draft_change_log.changed_by),
                changed_at=record.draft_change_log.changed_at,
                title=version.title if version is not None else "",
                item_type=item_type,
                action=resolve_change_action(record.old_version, record.new_version),
                old_version=old_version_num,
                new_version=new_version_num,
            ))

    # Return all entries sorted newest-first across the container and its children.
    results.sort(
        key=operator.attrgetter('changed_at', 'title'),
        reverse=True,
    )
    return results


def get_library_container_publish_history(
    container_key: LibraryContainerLocator,
    request=None,
) -> list[LibraryPublishHistoryGroup]:
    """
    [ 🛑 UNSTABLE ] Return the publish history of a container as a list of groups.

    Pre-Verawood records (direct=None): one group per entity × publish event
    (same PublishLog may produce multiple groups — one per entity in scope).

    Post-Verawood records (direct!=None): one group per unique PublishLog that
    touched the container or any descendant. Contributors are accumulated across
    all entities in that PublishLog within scope. direct_published_entities lists
    the entities the user directly clicked "Publish" on.

    Groups are ordered most-recent-first. Returns [] if nothing has been published.
    """
    container = get_container_from_key(container_key)
    component_entity_ids = content_api.get_descendant_component_entity_ids(container)
    all_entity_ids = [container.pk] + component_entity_ids

    # Collect all records grouped by publish_log_uuid.
    publish_log_groups: dict[UUID, list[tuple[int, PublishLogRecord]]] = {}
    for entity_id in all_entity_ids:
        for pub_record in content_api.get_entity_publish_history(entity_id).select_related(
            "entity__component__component_type",
            "entity__container__container_type",
            "new_version",
            "old_version",
        ):
            uuid: UUID = pub_record.publish_log.uuid
            publish_log_groups.setdefault(uuid, []).append((entity_id, pub_record))

    groups = []
    for uuid, entity_records in publish_log_groups.items():
        # Era is uniform across all records in one PublishLog.
        _, first_record = entity_records[0]
        user_publish_intent_was_recorded = first_record.direct is not None

        if user_publish_intent_was_recorded:
            # ONE merged group for this entire PublishLog.
            groups.append(
                _build_post_verawood_container_group(
                    uuid, entity_records, container_key, request
                )
            )
        else:
            # Pre-Verawood: one group per entity-record pair (separated).
            for entity_id, pub_record in entity_records:
                groups.append(
                    _build_pre_verawood_container_group(
                        pub_record, entity_id, container_key, request
                    )
                )
    groups.sort(key=operator.attrgetter('published_at', 'publish_log_uuid'), reverse=True)
    return groups


def _build_post_verawood_container_group(
    uuid: UUID,
    entity_records: list[tuple[int, PublishLogRecord]],
    container_key: LibraryContainerLocator,
    request,
) -> LibraryPublishHistoryGroup:
    """
    Build one merged LibraryPublishHistoryGroup for a Post-Verawood PublishLog.

    Queries the full PublishLog for direct=True records (covers both in-scope
    and out-of-scope cases, e.g. a shared component published from a sibling).
    Contributors are accumulated across all in-scope entity records.
    """
    publish_log = entity_records[0][1].publish_log
    direct_records = (
        publish_log.records
        .filter(direct=True)
        .select_related(
            'entity__component__component_type',
            'entity__container__container_type',
            'new_version',
            'old_version',
        )
    )
    direct_published_entities = [
        direct_published_entity_from_record(r, container_key.lib_key)
        for r in direct_records
    ]

    seen_usernames: set[str] = set()
    all_contributors = []
    for entity_id, pub_record in entity_records:
        old_version_num = pub_record.old_version.version_num if pub_record.old_version else 0
        new_version_num = pub_record.new_version.version_num if pub_record.new_version else None
        contributing_users = content_api.get_entity_version_contributors(
            entity_id,
            old_version_num=old_version_num,
            new_version_num=new_version_num,
        ).select_related('profile')
        for user in contributing_users:
            contributor = LibraryHistoryContributor.from_user(user, request)
            if contributor.username not in seen_usernames:
                seen_usernames.add(contributor.username)
                all_contributors.append(contributor)

    return LibraryPublishHistoryGroup(
        publish_log_uuid=uuid,
        published_by=publish_log.published_by,
        published_at=publish_log.published_at,
        contributors=all_contributors,
        direct_published_entities=direct_published_entities,
        scope_entity_key=None,
    )


def _build_pre_verawood_container_group(
    pub_record: PublishLogRecord,
    entity_id: int,
    container_key: LibraryContainerLocator,
    request,
) -> LibraryPublishHistoryGroup:
    """
    Build one LibraryPublishHistoryGroup for a Pre-Verawood record.

    One group per entity × publish event (separated). entity_key is approximated:
    str(container_key) for the container itself, str(usage_key) for a component.
    """
    old_version_num = pub_record.old_version.version_num if pub_record.old_version else 0
    new_version_num = pub_record.new_version.version_num if pub_record.new_version else None
    contributing_users = content_api.get_entity_version_contributors(
        entity_id,
        old_version_num=old_version_num,
        new_version_num=new_version_num,
    ).select_related('profile')
    contributors = [
        LibraryHistoryContributor.from_user(user, request)
        for user in contributing_users
    ]

    entity = direct_published_entity_from_record(pub_record, container_key.lib_key)
    return LibraryPublishHistoryGroup(
        publish_log_uuid=pub_record.publish_log.uuid,
        published_by=pub_record.publish_log.published_by,
        published_at=pub_record.publish_log.published_at,
        contributors=contributors,
        # Pre-Verawood: single approximated entry built from the record itself.
        direct_published_entities=[entity],
        scope_entity_key=entity.entity_key,
    )


def get_library_container_publish_history_entries(
    scope_entity_key: LibraryContainerLocator,
    publish_log_uuid: UUID,
    request=None,
) -> list[LibraryHistoryEntry]:
    """
    [ 🛑 UNSTABLE ] Return the individual draft change entries for all entities
    in scope that participated in a specific publish event.

    scope_entity_key identifies the container being viewed — it defines which
    entities' entries to return (the container + its descendants). This may differ
    from the direct_published_entities in the publish group (e.g. a parent Section
    was directly published, but the scope here is a child Unit).

    Post-Verawood (direct!=None): returns entries for all entities in scope that
    participated in the PublishLog.

    Pre-Verawood (direct=None): returns entries only for the container itself
    (old behavior — one group per entity, scope == directly published entity).

    Returns [] if no entities in scope participated in this publish event.
    """
    container = get_container_from_key(scope_entity_key)
    component_entity_ids = content_api.get_descendant_component_entity_ids(container)
    scope_entity_ids = {container.pk} | set(component_entity_ids)

    publish_log_records = PublishLogRecord.objects.filter(publish_log__uuid=publish_log_uuid)
    is_post_verawood = publish_log_records.filter(direct__isnull=False).exists()

    if is_post_verawood:
        # Return entries for all entities in scope that participated in this PublishLog.
        relevant_entity_ids = (
            set(publish_log_records.values_list('entity_id', flat=True)) & scope_entity_ids
        )
    else:
        # Pre-Verawood: scope_entity_key is the directly published entity.
        # Return entries only for the container itself (old behavior).
        relevant_entity_ids = {container.pk} & set(
            publish_log_records.values_list('entity_id', flat=True)
        )

    if not relevant_entity_ids:
        return []

    @cache
    def _contributor(user):
        return make_contributor(user, request)

    entries = []
    for entity_id in relevant_entity_ids:
        try:
            records = (
                content_api.get_entity_publish_history_entries(entity_id, str(publish_log_uuid))
                .select_related(
                    'entity__component__component_type',
                    'entity__container__container_type',
                    'draft_change_log__changed_by__profile',
                )
            )
        except ObjectDoesNotExist:
            continue

        for record in records:
            version = record.new_version if record.new_version is not None else record.old_version
            # old_version is None only for the very first publish (entity had no prior published version)
            old_version_num = record.old_version.version_num if record.old_version else 0
            # new_version is None for soft-delete publishes (component deleted without a new draft version)
            new_version_num = record.new_version.version_num if record.new_version else None
            item_type = get_entity_item_type(record.entity)
            entries.append(LibraryHistoryEntry(
                contributor=_contributor(record.draft_change_log.changed_by),
                changed_at=record.draft_change_log.changed_at,
                title=version.title if version is not None else "",
                item_type=item_type,
                action=resolve_change_action(record.old_version, record.new_version),
                old_version=old_version_num,
                new_version=new_version_num,
            ))

    # Return entries sorted newest-first; use title as tiebreaker for determinism.
    entries.sort(key=operator.attrgetter('changed_at', 'title'), reverse=True)
    return entries


def get_library_container_creation_entry(
    container_key: LibraryContainerLocator,
    request=None,
) -> LibraryHistoryEntry | None:
    """
    [ 🛑 UNSTABLE ] Return the creation entry for a library container.

    This is a single LibraryHistoryEntry representing the moment the container
    was first created. Returns None if the container has no
    versions yet.
    """
    container = get_container_from_key(container_key)
    # TODO: replace with container.versioning.earliest once VersioningHelper exposes that helper.
    first_version = (
        container.publishable_entity.versions
        .order_by('version_num')
        .select_related("created_by__profile")
        .first()
    )
    if first_version is None:
        return None

    user = first_version.created_by
    return LibraryHistoryEntry(
        contributor=make_contributor(user, request),
        changed_at=first_version.created,
        title=first_version.title,
        item_type=container.container_type.type_code,
        action="created",
        old_version=0,
        new_version=first_version.version_num,
    )
