"""
API for reading information about previous migrations
"""
from __future__ import annotations

import typing as t
from uuid import UUID
from django.conf import settings

from opaque_keys.edx.keys import UsageKey
from opaque_keys.edx.locator import (
    LibraryLocatorV2, LibraryUsageLocatorV2, LibraryContainerLocator
)
from openedx_content.api import get_draft_version, get_all_drafts
from openedx_content.models_api import (
    PublishableEntityVersion, PublishableEntity, DraftChangeLogRecord
)
from xblock.plugin import PluginMissingError

from openedx.core.djangoapps.content_libraries.api import (
    library_component_usage_key, library_container_locator,
    validate_can_add_block_to_library, BlockLimitReachedError,
    IncompatibleTypesError, LibraryBlockAlreadyExists,
    ContentLibrary
)
from openedx.core.djangoapps.content.search.api import (
    fetch_block_types,
    get_all_blocks_from_context,
)

from ..data import (
    SourceContextKey, ModulestoreMigration, ModulestoreBlockMigrationResult,
    ModulestoreBlockMigrationSuccess, ModulestoreBlockMigrationFailure
)
from .. import models


__all__ = (
    'get_forwarding',
    'is_forwarded',
    'get_forwarding_for_blocks',
    'get_migrations',
    'get_migration_blocks',
    'preview_migration',
)


def get_forwarding_for_blocks(source_keys: t.Iterable[UsageKey]) -> dict[UsageKey, ModulestoreBlockMigrationSuccess]:
    """
    Authoritatively determine how some Modulestore blocks have been migrated to Learning Core.

    Returns a mapping from source usage keys to block migration data objects. Each block migration object
    holds the target usage key and title. If a source key is missing from the mapping, then it has not
    been authoritatively migrated.
    """
    sources = models.ModulestoreBlockSource.objects.filter(
        key__in=[str(sk) for sk in source_keys]
    ).select_related(
        "forwarded__target__learning_package",
        # For building component key
        "forwarded__target__component__component_type",
        # For building container key
        "forwarded__target__container__section",
        "forwarded__target__container__subsection",
        "forwarded__target__container__unit",
        # For determining title and version
        "forwarded__change_log_record__new_version",
    )
    result = {}
    for source in sources:
        if source.forwarded and source.forwarded.target:
            result[source.key] = _block_migration_success(
                source_key=source.key,
                target=source.forwarded.target,
                change_log_record=source.forwarded.change_log_record,
            )
    return result


def is_forwarded(source_key: SourceContextKey) -> bool:
    """
    Has this course or legacy library been authoratively migrated to Learning Core,
    such that references to the source course/library should be forwarded to the target library?
    """
    return get_forwarding(source_key) is not None


def get_forwarding(source_key: SourceContextKey) -> ModulestoreMigration | None:
    """
    Authoritatively determine how some Modulestore course or legacy library has been migrated to Learning Core.

    If no such successful migration exists, returns None.

    Note: This function may return None for a course or legacy lib that *has* been migrated 1+ times.
          This just means that those migrations were non-forwarding. In user parlance, that is,
          they have been "imported" but not truly "migrated".
    """
    try:
        source = models.ModulestoreSource.objects.select_related(
            # The following are used in _migration:
            "forwarded__source",
            "forwarded__target",
            "forwarded__task_status",
            "forwarded__target_collection",
        ).get(
            key=str(source_key)
        )
    except models.ModulestoreSource.DoesNotExist:
        return None
    if not source.forwarded:
        return None
    if source.forwarded.is_failed:
        return None
    return _migration(source.forwarded)


def get_migrations(
    source_key: SourceContextKey | None = None,
    *,
    target_key: LibraryLocatorV2 | None = None,
    target_collection_slug: str | None = None,
    task_uuid: UUID | None = None,
    is_failed: bool | None = None,
) -> t.Generator[ModulestoreMigration]:
    """
    Given some criteria, get all modulestore->LearningCore migrations.

    Returns an iterable, ordered from NEWEST to OLDEST.

    Please note: If you provide no filters, this will return an iterable across the whole
                 ModulestoreMigration table. Please paginate thoughtfully if you do that.
    """
    migrations = models.ModulestoreMigration.objects.all().select_related(
        "source",
        "target",
        "target_collection",
        "task_status",
    )
    if source_key:
        migrations = migrations.filter(source__key=source_key)
    if target_key:
        migrations = migrations.filter(target__key=str(target_key))
    if target_collection_slug:
        migrations = migrations.filter(target_collection__key=target_collection_slug)
    if task_uuid:
        migrations = migrations.filter(task_status__uuid=task_uuid)
    if is_failed is not None:
        migrations = migrations.filter(is_failed=is_failed)
    return (
        _migration(migration)
        for migration in migrations.order_by("-id")  # primary key is a proxy for newness
    )


def get_migration_blocks(migration_pk: int) -> dict[UsageKey, ModulestoreBlockMigrationResult]:
    """
    Get details about the migrations of each individual block within a course/lib migration.
    """
    return {
        block_migration.source.key: _block_migration_result(block_migration)
        for block_migration in models.ModulestoreBlockMigration.objects.filter(
            overall_migration_id=migration_pk
        ).select_related(
            "source",
            "target__learning_package",
            # For building component key
            "target__component__component_type",
            # For building container key.
            # (Hard-coding these exact 3 container types here is not a good pattern, but it's what is needed
            #  here in order to avoid additional SELECTs while determining the container type).
            "target__container__section",
            "target__container__subsection",
            "target__container__unit",
            # For determining title and version
            "change_log_record__new_version",
        )
    }


def _migration(m: models.ModulestoreMigration) -> ModulestoreMigration:
    """
    Build a migration dataclass from the database row
    """
    return ModulestoreMigration(
        pk=m.id,
        source_key=m.source.key,
        target_key=LibraryLocatorV2.from_string(m.target.key),
        target_title=m.target.title,
        target_collection_slug=(m.target_collection.key if m.target_collection else None),
        target_collection_title=(m.target_collection.title if m.target_collection else None),
        is_failed=m.is_failed,
        task_uuid=m.task_status.uuid,
    )


def _block_migration_result(m: models.ModulestoreBlockMigration) -> ModulestoreBlockMigrationResult:
    """
    Build an instance of the migration result (successs/failure) dataclass from a database row
    """
    if m.target:
        return _block_migration_success(
            source_key=m.source.key,
            target=m.target,
            change_log_record=m.change_log_record,
        )
    return ModulestoreBlockMigrationFailure(
        source_key=m.source.key,
        unsupported_reason=(m.unsupported_reason or ""),
    )


def _block_migration_success(
    source_key: UsageKey,
    target: PublishableEntity,
    change_log_record: DraftChangeLogRecord | None,
) -> ModulestoreBlockMigrationSuccess:
    """
    Build an instance of the migration success dataclass
    """
    target_library_key = LibraryLocatorV2.from_string(target.learning_package.key)
    target_key: LibraryUsageLocatorV2 | LibraryContainerLocator
    if hasattr(target, "component"):
        target_key = library_component_usage_key(target_library_key, target.component)
    elif hasattr(target, "container"):
        target_key = library_container_locator(target_library_key, target.container)
    else:
        raise ValueError(f"Entity is neither a container nor component: {target}")
    # We expect that any successful BlockMigration (that is, one where `target is not None`)
    # will also have a `change_log_record` with a non-None `new_version`. However, the data model
    # does not guarantee that `change_log_record` nor `change_log_record.new_version` are non-
    # None. So, just in case some bug in the modulestore_migrator or some manual modification of
    # the database leads us to a situation where `target` is set but `change_log_record.new_version`
    # is not, we have fallback behavior:
    # * For target_title, use the latest draft's title, which is good enough, because the
    #   title is just there to help users.
    # * For target_version_num, just use None, because we don't want downstream code to make decisions
    #   about syncing, etc based on incorrect version info.
    target_version: PublishableEntityVersion | None = (
        change_log_record.new_version if change_log_record else None
    )
    if target_version:
        target_title = target_version.title
        target_version_num = target_version.version_num
    else:
        latest_draft = get_draft_version(target)
        target_title = latest_draft.title if latest_draft else ""
        target_version_num = None
    return ModulestoreBlockMigrationSuccess(
        source_key=source_key,
        target_entity_pk=target.id,
        target_key=target_key,
        target_title=target_title,
        target_version_num=target_version_num,
    )


def preview_migration(source_key: SourceContextKey, target_key: LibraryLocatorV2):
    """
    Returns a summary preview of the migration given a source key and a target key
    on this form:

    ```
        {
            "state": "partial",
            "unsupported_blocks": 4,
            "unsupported_percentage": 25,
            "blocks_limit": 1000,
            "total_blocks": 20,
            "total_components": 10,
            "sections": 2,
            "subsections": 3,
            "units": 5,
        }
    ```

    List of states:
    - 'success': The migration can be carried out in its entirety
    - 'partial': The migration will be partial, because there are unsupported blocks.
    - 'block_limit_reached': The migration cannot be performed because the block limit per library has been reached.

    This runs Meilisiearch queries to speed up the response, as it's a summary/analysis.
    The decision has been made not to run a "migration" for each analysis to obtain this summary.

    TODO: For now, the repeat_handling_strategy is not taken into account. This can be taken into
    account for a more advanced summary.
    """
    # Get all containers and components from the source key
    blocks = get_all_blocks_from_context(str(source_key), ["block_type", "block_id"])

    unsupported_blocks = []
    total_blocks = 0
    total_components = 0
    sections = 0
    subsections = 0
    units = 0
    blocks_limit = settings.MAX_BLOCKS_PER_CONTENT_LIBRARY

    # Builds the summary: counts every container and verify if each component can be added to the library
    for block in blocks:
        block_type = block["block_type"]
        block_id = block["block_id"]
        total_blocks += 1
        if block_type not in ['chapter', 'sequential', 'vertical']:
            total_components += 1
            try:
                validate_can_add_block_to_library(
                    target_key,
                    block_type,
                    block_id,
                )
            except BlockLimitReachedError:
                return {
                    "state": "block_limit_reached",
                    "unsupported_blocks": 0,
                    "unsupported_percentage": 0,
                    "blocks_limit": blocks_limit,
                    "total_blocks": 0,
                    "total_components": 0,
                    "sections": 0,
                    "subsections": 0,
                    "units": 0,
                }
            except (IncompatibleTypesError, PluginMissingError):
                unsupported_blocks.append(block["usage_key"])
            except LibraryBlockAlreadyExists:
                # Skip this validation, The block may be repeated in the library, but that's not a bad thing.
                pass
        elif block_type == "chapter":
            sections += 1
        elif block_type == "sequential":
            subsections += 1
        elif block_type == "vertical":
            units += 1

    # Gets the count of children of unsupported blocks
    quoted_keys = ','.join(f'"{key}"' for key in unsupported_blocks)
    unsupportedBlocksChildren = fetch_block_types(
        [
            f'context_key = "{source_key}"',
            f'breadcrumbs.usage_key IN [{quoted_keys}]'
        ],
    )
    # Final unsupported blocks count
    # The unsupported children are subtracted from the totals since they have already been counted in the first query.
    unsupported_blocks_count = len(unsupported_blocks)
    total_blocks -= unsupportedBlocksChildren["estimatedTotalHits"]
    total_components -= unsupportedBlocksChildren["estimatedTotalHits"]
    unsupported_percentage = (unsupported_blocks_count / total_blocks) * 100

    state = "success"
    if unsupported_blocks_count:
        state = "partial"

    # Checks if this migration reaches the block limit
    content_library = ContentLibrary.objects.get_by_key(target_key)
    assert content_library.learning_package_id is not None
    target_item_counts = get_all_drafts(content_library.learning_package_id).count()
    if (target_item_counts + total_blocks - unsupported_blocks_count) > blocks_limit:
        state = "block_limit_reached"

    return {
        "state": state,
        "unsupported_blocks": unsupported_blocks_count,
        "unsupported_percentage": unsupported_percentage,
        "blocks_limit": blocks_limit,
        "total_blocks": total_blocks,
        "total_components": total_components,
        "sections": sections,
        "subsections": subsections,
        "units": units,
    }
