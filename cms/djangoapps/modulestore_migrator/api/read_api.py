"""
API for reading information about previous migrations
"""
from __future__ import annotations

import typing as t
from uuid import UUID

from opaque_keys.edx.keys import UsageKey
from opaque_keys.edx.locator import (
    LibraryLocatorV2, LibraryUsageLocatorV2, LibraryContainerLocator
)
from openedx_learning.api.authoring import get_draft_version
from openedx_learning.api.authoring_models import (
    PublishableEntityVersion, PublishableEntity, DraftChangeLogRecord
)

from openedx.core.djangoapps.content_libraries.api import (
    library_component_usage_key, library_container_locator
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
        migrations = migrations.filter(task_status__uuid=str(task_uuid))
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
