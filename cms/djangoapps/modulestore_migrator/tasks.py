"""
Tasks for the modulestore_migrator
"""
from __future__ import annotations

import mimetypes
import os
import typing as t
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from gettext import ngettext

from celery import shared_task
from celery.utils.log import get_task_logger
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from edx_django_utils.monitoring import set_code_owner_attribute_from_module
from lxml import etree
from lxml.etree import _ElementTree as XmlTree
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from opaque_keys.edx.locator import (
    BlockUsageLocator,
    CourseLocator,
    LibraryContainerLocator,
    LibraryLocator,
    LibraryLocatorV2,
    LibraryUsageLocatorV2,
)
from openedx_content import api as content_api
from openedx_content.models_api import (
    Collection,
    Component,
    ComponentType,
    LearningPackage,
    PublishableEntity,
    PublishableEntityVersion,
)
from user_tasks.tasks import UserTask, UserTaskStatus
from xblock.core import XBlock
from xblock.plugin import PluginMissingError

from common.djangoapps.split_modulestore_django.models import SplitModulestoreCourseIndex
from common.djangoapps.util.date_utils import DEFAULT_DATE_TIME_FORMAT, strftime_localized
from openedx.core.djangoapps.content_libraries import api as libraries_api
from openedx.core.djangoapps.content_libraries.api import ContainerType, get_library
from openedx.core.djangoapps.content_staging import api as staging_api
from xmodule.modulestore import exceptions as modulestore_exceptions
from xmodule.modulestore.django import modulestore

from . import models, data
from .constants import CONTENT_STAGING_PURPOSE_TEMPLATE
from .data import CompositionLevel, RepeatHandlingStrategy, SourceContextKey
from .api.read_api import get_migrations, get_migration_blocks

log = get_task_logger(__name__)


class MigrationStep(Enum):
    """
    Strings representation the state of an in-progress modulestore-to-learning-core import.

    We use these values to set UserTaskStatus.state.
    The other possible UserTaskStatus.state values are the built-in ones:
    UserTaskStatus.{PENDING,FAILED,CANCELED,SUCCEEDED}.
    """
    VALIDATING_INPUT = 'Validating migration parameters'
    CANCELLING_OLD = 'Cancelling any redundant migration tasks'
    LOADING = 'Loading legacy content from ModulesStore'
    STAGING = 'Staging legacy content for import'
    PARSING = 'Parsing staged OLX'
    IMPORTING_ASSETS = 'Importing staged files and resources'
    IMPORTING_STRUCTURE = 'Importing staged content structure'
    UNSTAGING = 'Cleaning staged content'
    MAPPING_OLD_TO_NEW = 'Saving map of legacy content to migrated content'
    FORWARDING = 'Forwarding legacy content to migrated content'
    POPULATING_COLLECTION = 'Assigning imported items to the specified collection'
    BULK_MIGRATION_PREFIX = 'Migrating legacy content'


class _BulkMigrationTask(UserTask):
    """
    Base class for bulk_migrate_from_modulestore
    """

    @staticmethod
    def calculate_total_steps(arguments_dict):
        """
        Get number of in-progress steps in importing process, as shown in the UI.

        There are steps that are general for all sources, but there are steps that are repeated in each source.
        All of this is taken into account to make the sum
        """
        sources_count = len(arguments_dict.get('sources_pks', 1))

        # STAGING, PARSING, IMPORTING_ASSETS, IMPORTING_STRUCTURE, MAPPING_OLD_TO_NEW, UNSTAGING
        steps_repeated_count = 6

        return (
            # All migration steps and subtract the BULK_MIGRATION_PREFIX
            len(list(MigrationStep)) - 1
            # We don't want to count these steps again, they will be counted in the operation below.
            - steps_repeated_count
            # Each source repeats all the `steps_repeated_count`
            + steps_repeated_count * sources_count
        )


@dataclass(frozen=True)
class _MigrationContext:
    """
    Context for the migration process.
    """
    # Fields that get mutated as we migrate blocks
    used_component_keys: set[LibraryUsageLocatorV2]
    used_container_slugs: set[str]

    # Fields that remain constant
    previous_block_migrations: dict[UsageKey, data.ModulestoreBlockMigrationResult]
    target_package_id: int
    target_library_key: LibraryLocatorV2
    source_context_key: SourceContextKey
    content_by_filename: dict[str, int]
    composition_level: CompositionLevel
    repeat_handling_strategy: RepeatHandlingStrategy
    preserve_url_slugs: bool
    created_by: int
    created_at: datetime

    @property
    def should_skip_strategy(self) -> bool:
        """
        Determines whether the repeat handling strategy should skip the entity.
        """
        return self.repeat_handling_strategy is RepeatHandlingStrategy.Skip

    @property
    def should_update_strategy(self) -> bool:
        """
        Determines whether the repeat handling strategy should update the entity.
        """
        return self.repeat_handling_strategy is RepeatHandlingStrategy.Update

    @property
    def should_fork_strategy(self) -> bool:
        """
        Determines whether the repeat handling strategy should fork the entity.
        """
        return self.repeat_handling_strategy is RepeatHandlingStrategy.Fork


@dataclass()
class _MigrationSourceData:
    """
    Data related to a ModulestoreSource
    """
    source: models.ModulestoreSource
    source_root_usage_key: BlockUsageLocator
    source_version: str | None
    migration: models.ModulestoreMigration
    previous_migration: data.ModulestoreMigration | None


def _validate_input(
    status: UserTaskStatus,
    source_pk: int,
    repeat_handling_strategy: str,
    preserve_url_slugs: bool,
    composition_level: str,
    target_library_key: LibraryLocatorV2,
    target_package: LearningPackage,
    target_collection: Collection | None,
) -> _MigrationSourceData | None:
    """
    Validates and build the source data related to `source_pk`
    """
    try:
        source = models.ModulestoreSource.objects.get(pk=source_pk)
    except (ObjectDoesNotExist) as exc:
        status.fail(str(exc))
        return None

    # The Model is used for Course and Legacy Library
    course_index = SplitModulestoreCourseIndex.objects.filter(course_id=source.key).first()
    if isinstance(source.key, CourseLocator):
        source_root_usage_key = source.key.make_usage_key('course', 'course')
        source_version = course_index.published_version if course_index else None
    elif isinstance(source.key, LibraryLocator):
        source_root_usage_key = source.key.make_usage_key('library', 'library')
        source_version = course_index.library_version if course_index else None
    else:
        status.fail(
            f"Not a valid source context key: {source.key}. "
            "Source key must reference a course or a legacy library."
        )
        return None

    # Find the latest successful migration that occurred, if any.
    # We're careful to do this before creating the new ModulestoreMigration object,
    # otherwise we would just end up grabbing that by one accident.
    # ( mypy gets confused by how use next(...) here )
    previous_migration = next(  # type: ignore[call-overload]
        get_migrations(
            source.key, target_key=target_library_key, is_failed=False
        ),
        None,  # default
    )
    migration = models.ModulestoreMigration.objects.create(
        source=source,
        source_version=source_version,
        composition_level=composition_level,
        repeat_handling_strategy=repeat_handling_strategy,
        preserve_url_slugs=preserve_url_slugs,
        target=target_package,
        target_collection=target_collection,
        task_status=status,
    )
    return _MigrationSourceData(
        source=source,
        source_root_usage_key=source_root_usage_key,
        source_version=source_version,
        migration=migration,
        previous_migration=previous_migration,
    )


def _cancel_old_tasks(
    source_list: list[models.ModulestoreSource],
    status: UserTaskStatus,
    target_package: LearningPackage,
    migration_ids_to_exclude: list[int],
) -> None:
    """
    Cancel all migration tasks related to the user and the source list
    """
    # In order to prevent a user from accidentally starting a bunch of identical import tasks...
    migrations_to_cancel = models.ModulestoreMigration.objects.filter(
        # get all Migration tasks by this user with the same sources and target
        task_status__user=status.user,
        source__in=source_list,
        target=target_package,
    ).select_related('task_status').exclude(
        # (excluding that aren't running)
        task_status__state__in=(UserTaskStatus.CANCELED, UserTaskStatus.FAILED, UserTaskStatus.SUCCEEDED)
    ).exclude(
        # (excluding these migrations themselves)
        id__in=migration_ids_to_exclude
    )
    # ... and cancel their tasks and clean away their staged content.
    for migration_to_cancel in migrations_to_cancel:
        if migration_to_cancel.task_status:
            migration_to_cancel.task_status.cancel()
        if migration_to_cancel.staged_content:
            migration_to_cancel.staged_content.delete()


def _load_xblock(
    status: UserTaskStatus,
    usage_key: UsageKey,
) -> XBlock | None:
    """
    Loads the Xblock for the given usage_key
    """
    try:
        xblock = modulestore().get_item(usage_key)
    except modulestore_exceptions.ItemNotFoundError as exc:
        status.fail(f"Failed to load source item '{usage_key}' from ModuleStore: {exc}")
        return None
    if not xblock:
        status.fail(f"Could not find source item '{usage_key}' in ModuleStore")
        return None
    return xblock


def _import_assets(migration: models.ModulestoreMigration) -> dict[str, int]:
    """
    Import the assets of the staged content to the migration target
    """
    if migration.staged_content is None:
        return {}

    content_by_filename: dict[str, int] = {}
    now = datetime.now(tz=timezone.utc)
    for staged_content_file_data in staging_api.get_staged_content_static_files(migration.staged_content.id):
        old_path = staged_content_file_data.filename
        file_data = staging_api.get_staged_content_static_file_data(migration.staged_content.id, old_path)
        if not file_data:
            log.error(
                f"Staged content {migration.staged_content.id} included referenced file {old_path}, "
                "but no file data was found."
            )
            continue
        filename = os.path.basename(old_path)
        media_type_str = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        media_type = content_api.get_or_create_media_type(media_type_str)
        content_by_filename[filename] = content_api.get_or_create_file_content(
            migration.target_id,
            media_type.id,
            data=file_data,
            created=now,
        ).id
    return content_by_filename


def _import_structure(
    source_data: _MigrationSourceData,
    target_library: libraries_api.ContentLibraryMetadata,
    content_by_filename: dict[str, int],
    root_node: XmlTree,
    status: UserTaskStatus,
) -> tuple[t.Any, _MigratedNode]:
    """
    Import the staged content structure into the target Learning Core library.

    Args:
        migration (ModulestoreMigration):
            The migration record representing the ongoing modulestore-to-learning-core migration.
        source_data (_MigrationSourceData):
            Data extracted from the legacy modulestore, including the source root usage key.
            Use `_validate_input()` to generate this data.
        target_library (libraries_api.ContentLibraryMetadata):
            The target library where the new Learning Core content will be created.
        content_by_filename (dict[str, int]):
            A mapping between OLX file names and their associated file IDs in the staging area.
            Use `_import_assets` to generate this content.
        root_node (XmlTree):
            The parsed XML tree representing the root of the staged OLX content.
        status (UserTaskStatus):
            The user task used to record progress and state updates throughout the import.

    Returns:
        tuple[Any, _MigratedNode]:
            A tuple containing:
                - The first element (`change_log`): the bulk draft change log generated by
                  `content_api.bulk_draft_changes_for`, containing all the imported changes.
                - The second element (`root_migrated_node`): a `_MigratedNode` object that
                  represents the mapping between the legacy root node and its newly created
                  Learning Core equivalent.
    """
    migration = source_data.migration
    migration_context = _MigrationContext(
        used_component_keys=set(
            LibraryUsageLocatorV2(target_library.key, block_type, block_id)  # type: ignore[abstract]
            for block_type, block_id
            in content_api.get_components(migration.target.pk).values_list(
                "component_type__name", "local_key"
            )
        ),
        used_container_slugs=set(
            content_api.get_containers(
                migration.target.pk
            ).values_list("publishable_entity__key", flat=True)
        ),
        previous_block_migrations=(
            get_migration_blocks(source_data.previous_migration.pk)
            if source_data.previous_migration
            else {}
        ),
        target_package_id=migration.target.pk,
        target_library_key=target_library.key,
        source_context_key=source_data.source_root_usage_key.course_key,
        content_by_filename=content_by_filename,
        composition_level=CompositionLevel(migration.composition_level),
        repeat_handling_strategy=RepeatHandlingStrategy(migration.repeat_handling_strategy),
        preserve_url_slugs=migration.preserve_url_slugs,
        created_by=status.user_id,
        created_at=datetime.now(timezone.utc),
    )
    with content_api.bulk_draft_changes_for(migration.target.id) as change_log:
        root_migrated_node = _migrate_node(
            context=migration_context,
            source_node=root_node,
        )
    change_log.save()
    return change_log, root_migrated_node


def _forward_content(source_data: _MigrationSourceData) -> None:
    """
    Forwarding legacy content to migrated content
    """
    block_migrations = models.ModulestoreBlockMigration.objects.filter(overall_migration=source_data.migration)
    block_sources_to_block_migrations = {
        block_migration.source: block_migration for block_migration in block_migrations
    }
    for block_source, block_migration in block_sources_to_block_migrations.items():
        block_source.forwarded = block_migration
        block_source.save()

    source_data.source.forwarded = source_data.migration
    source_data.source.save()


def _populate_collection(user_id: int, migration: models.ModulestoreMigration) -> None:
    """
    Assigning imported items to the specified collection in the migration
    """
    if migration.target_collection is None:
        return

    block_target_pks: list[int] = list(
        models.ModulestoreBlockMigration.objects.filter(
            overall_migration=migration
        ).values_list('target_id', flat=True)
    )
    if block_target_pks:
        content_api.add_to_collection(
            learning_package_id=migration.target.pk,
            key=migration.target_collection.key,
            entities_qset=PublishableEntity.objects.filter(id__in=block_target_pks),
            created_by=user_id,
        )
    else:
        log.warning("No target entities found to add to collection")


def _create_collection(
    library_key: LibraryLocatorV2,
    title: str,
    course_name: str | None = None,
) -> Collection:
    """
    Creates a collection in the given library

    If there's a collection with the same key, try again, adding the attempt number at the end.
    The same is true for the title.
    """
    key = slugify(title)
    collection: Collection | None = None
    attempt = 0
    created_at = strftime_localized(datetime.now(timezone.utc), DEFAULT_DATE_TIME_FORMAT)
    if course_name:
        description = f"{_('This collection contains content imported from the course')} {course_name} on: {created_at}"
    else:
        description = f"{_('This collection contains content migrated from a legacy library on')}: {created_at}"
    while not collection:
        modified_key = key if attempt == 0 else key + '-' + str(attempt)
        try:
            # Add transaction here to avoid TransactionManagementError on retry
            with transaction.atomic():
                collection = libraries_api.create_library_collection(
                    library_key=library_key,
                    collection_key=modified_key,
                    title=f"{title}{f'_{attempt}' if attempt > 0 else ''}",
                    description=description,
                )
        except libraries_api.LibraryCollectionAlreadyExists:
            attempt += 1
    return collection


def _set_migrations_to_fail(source_data_list: list[_MigrationSourceData]):
    """
    Set and save all migrations in `source_data_list` as failed
    """
    for source_data in source_data_list:
        source_data.migration.is_failed = True

    models.ModulestoreMigration.objects.bulk_update(
        [x.migration for x in source_data_list],
        ["is_failed"],
    )


@shared_task(base=_BulkMigrationTask, bind=True)
# Note: The decorator @set_code_owner_attribute cannot be used here because the UserTaskMixin
#   does stack inspection and can't handle additional decorators.
def bulk_migrate_from_modulestore(
    self: _BulkMigrationTask,
    *,
    user_id: int,
    sources_pks: list[int],
    target_library_key: str,
    target_collection_pks: list[int | None],
    create_collections: bool = False,
    repeat_handling_strategy: str,
    preserve_url_slugs: bool,
    composition_level: str,
    forward_source_to_target: bool | None,
) -> None:
    """
    Import multiple legacy courses or libraries into a single V2 library.

    The bulk migration maintains **one unified status record** that tracks progress across
    all included sources. This simplifies monitoring, since the client only needs to observe
    one task state.
    Each source item (course or library) still creates its own `ModulestoreMigration`
    database record, but all of them share the same parent task (`UserTaskStatus`).
    If any sub-migration fails (for example, due to invalid OLX or missing assets),
    the bulk migration **marks the entire task as failed** — there is no partial success.

    Args:
        self (_BulkMigrationTask):
            The Celery task instance that wraps the user task logic.
        user_id (int):
            The ID of the user initiating the migration.
        sources_pks (list[int]):
            Primary keys of the legacy modulestore sources to migrate.
        target_library_key (str):
            Key of the V2 library that will receive the imported content.
        target_collection_pks (list[int | None]):
            Optional list of target collection IDs corresponding to each source.
        create_collections (bool):
            Whether to automatically create new collections when none exist.
        repeat_handling_strategy (str):
            Strategy to handle repeated imports of the same content.
        preserve_url_slugs (bool):
            Whether to preserve existing XBlock URL slugs during import.
        composition_level (str):
            Composition level at which content should be imported (e.g. course, section).
        forward_source_to_target (bool | None)
            Whether to forward legacy content to its migrated equivalent after import.
            If unspecified (None), then forward legacy content for a source if and only
            if it's that source's first migration.

    See Also:
        - `migrate_from_modulestore`: Single-source migration equivalent.
        - API docs: `/api/cms/v1/migrations/bulk/` for REST behavior and responses.
    """
    # pylint: disable=too-many-statements
    # This is a large function, but breaking it up futher would probably not
    # make it any easier to understand.

    set_code_owner_attribute_from_module(__name__)
    status: UserTaskStatus = self.status

    # Validating input
    status.set_state(MigrationStep.VALIDATING_INPUT.value)
    target_collection_list: list[Collection | None] = []

    try:
        target_library_locator = LibraryLocatorV2.from_string(target_library_key)
        target_library = get_library(target_library_locator)
        if target_library.learning_package_id is None:
            raise ValueError("Target library has no associated learning package.")

        target_package = LearningPackage.objects.get(pk=target_library.learning_package_id)

        if target_collection_pks:
            for target_collection_pk in target_collection_pks:
                target_collection_list.append(
                    Collection.objects.get(pk=target_collection_pk) if target_collection_pk else None
                )
    except (ObjectDoesNotExist, InvalidKeyError, ValueError) as exc:
        status.fail(str(exc))
        return

    source_data_list: list[_MigrationSourceData] = []

    for i in range(len(sources_pks)):
        source_data = _validate_input(
            status,
            sources_pks[i],
            repeat_handling_strategy,
            preserve_url_slugs,
            composition_level,
            target_library_locator,
            target_package,
            target_collection_list[i] if target_collection_list else None,
        )
        if source_data is None:
            # Fail
            return
        source_data_list.append(source_data)

    status.increment_completed_steps()

    try:  # pylint: disable=too-many-nested-blocks
        # Cancelling old tasks
        status.set_state(MigrationStep.CANCELLING_OLD.value)
        _cancel_old_tasks(
            [x.source for x in source_data_list],
            status,
            target_package,
            [migration.id for migration in [x.migration for x in source_data_list]],
        )
        status.increment_completed_steps()

        # Loading legacy blocks
        status.set_state(MigrationStep.LOADING)
        legacy_root_list: list[XBlock] = []
        for source_data in source_data_list:
            legacy_root = _load_xblock(status, source_data.source_root_usage_key)
            if legacy_root is None:
                # Fail
                _set_migrations_to_fail(source_data_list)
                return
            legacy_root_list.append(legacy_root)
        status.increment_completed_steps()

        for i, source_pk in enumerate(sources_pks):
            source_data = source_data_list[i]
            try:
                with transaction.atomic():
                    # Start migration for `source_pk`
                    # Staging legacy blocks
                    status.set_state(
                        f"{MigrationStep.STAGING.BULK_MIGRATION_PREFIX} ({source_pk}): {MigrationStep.STAGING.value}"
                    )
                    staged_content = staging_api.stage_xblock_temporarily(
                        block=legacy_root_list[i],
                        user_id=status.user.pk,
                        purpose=CONTENT_STAGING_PURPOSE_TEMPLATE.format(source_key=source_pk),
                    )
                    source_data.migration.staged_content = staged_content
                    status.increment_completed_steps()

                    # Parsing OLX
                    status.set_state(
                        f"{MigrationStep.STAGING.BULK_MIGRATION_PREFIX} ({source_pk}): {MigrationStep.PARSING.value}"
                    )
                    parser = etree.XMLParser(strip_cdata=False)
                    root_node = etree.fromstring(staged_content.olx, parser=parser)
                    status.increment_completed_steps()

                    # Importing assets
                    status.set_state(
                        f"{MigrationStep.STAGING.BULK_MIGRATION_PREFIX} ({source_pk}): "
                        f"{MigrationStep.IMPORTING_ASSETS.value}"
                    )
                    content_by_filename = _import_assets(source_data.migration)
                    status.increment_completed_steps()

                    # Importing structure of the legacy block
                    status.set_state(
                        f"{MigrationStep.STAGING.BULK_MIGRATION_PREFIX} ({source_pk}): "
                        f"{MigrationStep.IMPORTING_STRUCTURE.value}"
                    )
                    change_log, root_migrated_node = _import_structure(
                        source_data=source_data,
                        target_library=target_library,
                        content_by_filename=content_by_filename,
                        root_node=root_node,
                        status=status,
                    )
                    source_data.migration.change_log = change_log
                    source_data.migration.save()  # @@TODO keep or nah?
                    status.increment_completed_steps()

                    status.set_state(
                        f"{MigrationStep.STAGING.BULK_MIGRATION_PREFIX} ({source_pk}): {MigrationStep.UNSTAGING.value}"
                    )
                    staged_content.delete()
                    status.increment_completed_steps()

                    _create_migration_artifacts_incrementally(
                        root_migrated_node=root_migrated_node,
                        source=source_data.source,
                        migration=source_data.migration,
                        status=status,
                        source_pk=source_pk,
                    )
                    status.increment_completed_steps()
            except Exception as _exc:  # pylint: disable=broad-exception-caught
                log.exception("Failed: {source_data.migration}")
                # Mark this library as failed, migration of other libraries can continue
                # If this case occurs and the migration ends without any further issues,
                # the bulk migration status is success,
                # TODO: add an intermediate status such as 'partially satisfactory'
                source_data.migration.is_failed = True

        # Forwarding legacy content to migrated content
        status.set_state(MigrationStep.FORWARDING.value)
        for source_data in source_data_list:
            if forward_source_to_target is False:
                continue  # Explicitly requested not to forward.
            if forward_source_to_target is None and source_data.source.forwarded:
                # Unspecified whether or not to forward.
                # So, forward iff there was no previous existing successful migration with forwarding.
                continue
            if source_data.migration.is_failed:
                # Don't forward failed migrations.
                continue
            _forward_content(source_data)
        status.increment_completed_steps()

        # Populating collections
        status.set_state(MigrationStep.POPULATING_COLLECTION.value)
        for i, source_data in enumerate(source_data_list):
            migration = source_data.migration
            if migration.is_failed:
                continue
            if migration.target_collection is None and not create_collections:
                continue
            if migration.target_collection is None:
                existing_collection_to_use: Collection | None = None
                # For Fork strategy: Create an new collection every time.
                # For Update and Skip strategies: Update an existing collection if possible.
                if migration.repeat_handling_strategy != RepeatHandlingStrategy.Fork.value:
                    if source_data.previous_migration:
                        if previous_collection_slug := source_data.previous_migration.target_collection_slug:
                            try:
                                existing_collection_to_use = content_api.get_collection(
                                    target_package.id, previous_collection_slug
                                )
                            except Collection.DoesNotExist:
                                # Collection no longer exists.
                                pass
                migration.target_collection = (
                    existing_collection_to_use or
                    _create_collection(
                        library_key=target_library_locator,
                        title=legacy_root_list[i].display_name,
                        course_name=legacy_root_list[i].display_name if source_data.source.key.is_course else None,
                    )
                )
            _populate_collection(user_id, migration)
        models.ModulestoreMigration.objects.bulk_update(
            [x.migration for x in source_data_list],
            ["target_collection", "is_failed"],
        )
        status.increment_completed_steps()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # If there is an exception in this block, all migrations fail.
        log.exception("Modulestore migrations failed")
        status.fail(str(exc))


SourceToTarget = tuple[UsageKey, PublishableEntityVersion | None, str | None]


@dataclass(frozen=True)
class _MigratedNode:
    """
    A node in the source tree, its target (if migrated), and any migrated children.

    Note that target_version can equal None even when there migrated children.
    This happens, particularly, if the node is above the requested composition level
    but has descendents which are at or below that level.
    """
    source_to_target: SourceToTarget | None
    children: list[_MigratedNode]

    def all_source_to_target_pairs(self) -> t.Iterable[SourceToTarget]:
        """
        Get all source_key->target_ver pairs via a pre-order traversal.
        """
        if self.source_to_target:
            yield self.source_to_target
        for child in self.children:
            yield from child.all_source_to_target_pairs()


def _migrate_node(
    *,
    context: _MigrationContext,
    source_node: XmlTree,
) -> _MigratedNode:
    """
    Migrate an OLX node (source_node) from a legacy course or library (context.source_context_key)
    to a learning package (context.target_library). If the node is a container, create it in the
    target if it is at or above the requested composition_level; otherwise, just import its contents.
    Recursively apply the same logic to all children.
    """
    # The OLX tag will map to one of the following...
    #   * A wiki tag                  --> Ignore
    #   * A recognized container type --> Migrate children, and import container if requested.
    #   * A legacy library root       --> Migrate children, but NOT the root itself.
    #   * A course root               --> Migrate children, but NOT the root itself (for Ulmo, at least. Future
    #                                     releases may support treating the Course as an importable container).
    #   * Something else              --> Try to import it as a component. If that fails, then it's either an un-
    #                                     supported component type, or it's an XBlock with dynamic children, which we
    #                                     do not support in libraries as of Ulmo.
    should_migrate_node: bool
    should_migrate_children: bool
    container_type: ContainerType | None  # if None, it's a Component
    if source_node.tag == "wiki":
        return _MigratedNode(None, [])
    try:
        container_type = ContainerType.from_source_olx_tag(source_node.tag)
    except ValueError:
        container_type = None
        if source_node.tag in {"course", "library"}:
            should_migrate_node = False
            should_migrate_children = True
        else:
            should_migrate_node = True
            should_migrate_children = False
    else:
        node_level = CompositionLevel(container_type.value)
        should_migrate_node = not node_level.is_higher_than(context.composition_level)
        should_migrate_children = True
    migrated_children: list[_MigratedNode] = []
    if should_migrate_children:
        migrated_children = [
            _migrate_node(
                context=context,
                source_node=source_node_child,
            )
            for source_node_child in source_node.getchildren()
        ]
    source_to_target: SourceToTarget | None = None
    if should_migrate_node:
        source_olx = etree.tostring(source_node).decode('utf-8')
        if source_block_id := source_node.get('url_name'):
            source_key: UsageKey = context.source_context_key.make_usage_key(source_node.tag, source_block_id)
            title = source_node.get('display_name', source_block_id)
            target_entity_version, reason = (
                _migrate_container(
                    context=context,
                    source_key=source_key,
                    container_type=container_type,
                    title=title,
                    children=[
                        migrated_child.source_to_target[1]
                        for migrated_child in migrated_children if
                        migrated_child.source_to_target and migrated_child.source_to_target[1]
                    ],
                )
                if container_type else
                _migrate_component(
                    context=context,
                    source_key=source_key,
                    olx=source_olx,
                    title=title,
                )
            )
            if container_type is None and target_entity_version is None and reason is not None:
                # Currently, components with children are not supported
                children_length = len(source_node.getchildren())
                if children_length:
                    reason += (
                        ngettext(
                            ' It has {count} children block.',
                            ' It has {count} children blocks.',
                            children_length,
                        )
                    ).format(count=children_length)
            source_to_target = (source_key, target_entity_version, reason)
        else:
            log.warning(
                f"Cannot migrate node from {context.source_context_key} to {context.target_library_key} "
                f"because it lacks an url_name and thus has no identity: {source_olx}"
            )
    return _MigratedNode(source_to_target=source_to_target, children=migrated_children)


def _migrate_container(
    *,
    context: _MigrationContext,
    source_key: UsageKey,
    container_type: ContainerType,
    title: str,
    children: list[PublishableEntityVersion],
) -> tuple[PublishableEntityVersion, str | None]:
    """
    Create, update, or replace a container in a library based on a source key and children.

    (We assume that the destination is a library rather than some other future kind of learning
    package, but let's keep than an internal assumption.)
    For now this returns None value for unsupported_reason as second value of tuple as we
    don't have any concrete condition where a container cannot be imported/migrated.
    """
    target_key = _get_distinct_target_container_key(
        context,
        source_key,
        container_type,
        title,
    )
    try:
        container = libraries_api.get_container(target_key)
        container_exists = True
    except libraries_api.ContentLibraryContainerNotFound:
        container_exists = False
        if PublishableEntity.objects.filter(
            learning_package_id=context.target_package_id,
            key=target_key.container_id,
        ).exists():
            libraries_api.restore_container(container_key=target_key)
            container = libraries_api.get_container(target_key)
        else:
            container = libraries_api.create_container(
                library_key=context.target_library_key,
                container_type=container_type,
                slug=target_key.container_id,
                title=title,
                created=context.created_at,
                user_id=context.created_by,
            )
    if container_exists and context.should_skip_strategy:
        return PublishableEntityVersion.objects.get(
            entity_id=container.container_pk,
            version_num=container.draft_version_num,
        ), None

    container_publishable_entity_version = content_api.create_next_container_version(
        container.container_pk,
        title=title,
        entity_rows=[
            content_api.ContainerEntityRow(entity_pk=child.entity_id, version_pk=None)
            for child in children
        ],
        created=context.created_at,
        created_by=context.created_by,
        container_version_cls=container_type.container_model_classes[1],
    ).publishable_entity_version

    # Publish the container
    # Call post publish events synchronously to avoid
    # an error when calling `wait_for_post_publish_events`
    # inside a celery task.
    libraries_api.publish_container_changes(
        container.container_key,
        context.created_by,
        call_post_publish_events_sync=True,
    )
    context.used_container_slugs.add(container.container_key.container_id)
    return container_publishable_entity_version, None


def _migrate_component(
    *,
    context: _MigrationContext,
    source_key: UsageKey,
    olx: str,
    title: str,
) -> tuple[PublishableEntityVersion | None, str | None]:
    """
    Create, update, or replace a component in a library based on a source key and OLX.

    (We assume that the destination is a library rather than some other future kind of learning
     package, but let's keep than an internal assumption.)
    """
    component_type = content_api.get_or_create_component_type("xblock.v1", source_key.block_type)

    target_key = _get_distinct_target_usage_key(
        context,
        source_key,
        component_type,
        title,
    )

    try:
        component = content_api.get_components(context.target_package_id).get(
            component_type=component_type,
            local_key=target_key.block_id,
        )
        component_existed = True
        # Do we have a specific method for this?
        component_deleted = not component.versioning.draft
    except Component.DoesNotExist:
        component_existed = False
        component_deleted = False
        try:
            libraries_api.validate_can_add_block_to_library(
                context.target_library_key, target_key.block_type, target_key.block_id
            )
        except libraries_api.IncompatibleTypesError as e:
            log.error(f"Error validating block for library {context.target_library_key}: {e}")
            return None, str(e)
        except PluginMissingError as e:
            log.error(f"Block type not supported in {context.target_library_key}: {e}")
            return None, f"Invalid block type: {e}"
        component = content_api.create_component(
            context.target_package_id,
            component_type=component_type,
            local_key=target_key.block_id,
            created=context.created_at,
            created_by=context.created_by,
        )

    # Component existed and we do not replace it and it is not deleted previously
    if component_existed and not component_deleted and context.should_skip_strategy:
        return component.versioning.draft.publishable_entity_version, None

    # If component existed and was deleted or we have to replace the current version
    # Create the new component version for it
    component_version = libraries_api.set_library_block_olx(target_key, new_olx_str=olx)
    for filename, content_pk in context.content_by_filename.items():
        filename_no_ext, _ = os.path.splitext(filename)
        if filename_no_ext not in olx:
            continue
        new_path = f"static/{filename}"
        content_api.create_component_version_content(
            component_version.pk, content_pk, key=new_path
        )

    # Publish the component
    libraries_api.publish_component_changes(target_key, context.created_by)
    context.used_component_keys.add(target_key)
    return component_version.publishable_entity_version, None


_MAX_UNIQUE_SLUG_ATTEMPTS = 1000


def _get_distinct_target_container_key(
    context: _MigrationContext,
    source_key: UsageKey,
    container_type: ContainerType,
    title: str,
) -> LibraryContainerLocator:
    """
    Figure out the appropriate target container for this structural block.
    """
    # If we're not forking, then check if this block was part of our past migration.
    # (If we are forking, we will always want a new target key).
    if not context.should_fork_strategy:
        if previous_block_migration := context.previous_block_migrations.get(source_key):
            if isinstance(previous_block_migration, data.ModulestoreBlockMigrationSuccess):
                if isinstance(previous_block_migration.target_key, LibraryContainerLocator):
                    return previous_block_migration.target_key
    # Generate new unique block ID
    base_slug = (
        source_key.block_id
        if context.preserve_url_slugs
        else (slugify(title) or source_key.block_id)
    )
    # Use base base slug if available
    if base_slug not in context.used_container_slugs:
        return LibraryContainerLocator(
            context.target_library_key, container_type.value, base_slug
        )
    # Try numbered variations until we find one that doesn't exist
    for i in range(1, _MAX_UNIQUE_SLUG_ATTEMPTS + 1):
        candidate_slug = f"{base_slug}_{i}"
        if candidate_slug not in context.used_container_slugs:
            return LibraryContainerLocator(
                context.target_library_key, container_type.value, candidate_slug
            )
    # It would be extremely unlikely for us to run out of attempts
    raise RuntimeError(
        f"Unable to find unique slug after {_MAX_UNIQUE_SLUG_ATTEMPTS} attempts for base: {base_slug}"
    )


def _get_distinct_target_usage_key(
    context: _MigrationContext,
    source_key: UsageKey,
    component_type: ComponentType,
    title: str,
) -> LibraryUsageLocatorV2:
    """
    Figure out the appropriate target component for this block.
    """
    # If we're not forking, then check if this block was part of our past migration.
    # (If we are forking, we will always want a new target key).
    if not context.should_fork_strategy:
        if previous_block_migration := context.previous_block_migrations.get(source_key):
            if isinstance(previous_block_migration, data.ModulestoreBlockMigrationSuccess):
                if isinstance(previous_block_migration.target_key, LibraryUsageLocatorV2):
                    return previous_block_migration.target_key
    # Generate new unique block ID
    base_slug = (
        source_key.block_id
        if context.preserve_url_slugs
        else (slugify(title) or source_key.block_id)
    )
    # Use base base slug if available
    base_key = LibraryUsageLocatorV2(  # type: ignore[abstract]
        context.target_library_key, component_type.name, base_slug
    )
    if base_key not in context.used_component_keys:
        return base_key
    # Try numbered variations until we find one that doesn't exist
    for i in range(1, _MAX_UNIQUE_SLUG_ATTEMPTS + 1):
        candidate_slug = f"{base_slug}_{i}"
        candidate_key = LibraryUsageLocatorV2(  # type: ignore[abstract]
            context.target_library_key, component_type.name, candidate_slug
        )
        if candidate_key not in context.used_component_keys:
            return candidate_key
    # It would be extremely unlikely for us to run out of attempts
    raise RuntimeError(f"Unable to find unique slug after {_MAX_UNIQUE_SLUG_ATTEMPTS} attempts for base: {base_slug}")


def _create_migration_artifacts_incrementally(
    root_migrated_node: _MigratedNode,
    source: models.ModulestoreSource,
    migration: models.ModulestoreMigration,
    status: UserTaskStatus,
    source_pk: int | None = None,
) -> None:
    """
    Create ModulestoreBlockSource and ModulestoreBlockMigration objects incrementally.
    """
    nodes = tuple(root_migrated_node.all_source_to_target_pairs())
    total_nodes = len(nodes)
    processed = 0

    # Load a mapping from each modified entity's primary key
    # to the primary key of the changelog record that captures its modification.
    # This will not include any blocks whose migration failed to create a target entity.
    entity_pks_to_change_log_record_pks: dict[int, int] = dict(
        migration.change_log.records.values_list("entity_id", "id")
    ) if migration.change_log else {}

    for source_usage_key, target_version, unsupported_reason in root_migrated_node.all_source_to_target_pairs():
        block_source, _ = models.ModulestoreBlockSource.objects.get_or_create(
            overall_source=source,
            key=source_usage_key
        )
        # target_entity_pk should be None iff the block migration failed
        target_entity_pk: int | None = target_version.entity_id if target_version else None

        change_log_record_pk = entity_pks_to_change_log_record_pks.get(target_entity_pk) if target_entity_pk else None
        # Only create a migration artifact for this source block if:
        #  (a) we have a record of a change occuring, or
        #  (b) it failed.
        # If neither a nor b are true, then this source block was skipped.
        if change_log_record_pk or unsupported_reason:
            models.ModulestoreBlockMigration.objects.create(
                overall_migration=migration,
                source=block_source,
                target_id=target_entity_pk,
                change_log_record_id=change_log_record_pk,
                unsupported_reason=unsupported_reason,
            )

        processed += 1
        if processed % 10 == 0 or processed == total_nodes:
            if source_pk:
                status.set_state(
                    f"{MigrationStep.STAGING.BULK_MIGRATION_PREFIX} ({source_pk}): "
                    f"{MigrationStep.MAPPING_OLD_TO_NEW.value} ({processed}/{total_nodes})"
                )
            else:
                status.set_state(
                    f"{MigrationStep.MAPPING_OLD_TO_NEW.value} ({processed}/{total_nodes})"
                )
