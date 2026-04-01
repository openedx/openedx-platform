"""
API for kicking off new migrations
"""
from __future__ import annotations

from celery.result import AsyncResult
from opaque_keys.edx.locator import LibraryLocatorV2
from openedx_content.api import get_collection

from openedx.core.types.user import AuthUser
from openedx.core.djangoapps.content_libraries.api import get_library

from ..data import SourceContextKey, CompositionLevel, RepeatHandlingStrategy
from .. import tasks, models


__all__ = (
    'start_migration_to_library',
    'start_bulk_migration_to_library'
)


def start_migration_to_library(
    *,
    user: AuthUser,
    source_key: SourceContextKey,
    target_library_key: LibraryLocatorV2,
    target_collection_slug: str | None = None,
    create_collection: bool = False,
    composition_level: CompositionLevel,
    repeat_handling_strategy: RepeatHandlingStrategy,
    preserve_url_slugs: bool,
    forward_source_to_target: bool | None
) -> AsyncResult:
    """
    Import a course or legacy library into a V2 library (or, a collection within a V2 library).
    """
    return start_bulk_migration_to_library(
        user=user,
        source_key_list=[source_key],
        target_library_key=target_library_key,
        target_collection_slug_list=[target_collection_slug],
        create_collections=create_collection,
        composition_level=composition_level,
        repeat_handling_strategy=repeat_handling_strategy,
        preserve_url_slugs=preserve_url_slugs,
        forward_source_to_target=forward_source_to_target,
    )


def start_bulk_migration_to_library(
    *,
    user: AuthUser,
    source_key_list: list[SourceContextKey],
    target_library_key: LibraryLocatorV2,
    target_collection_slug_list: list[str | None] | None = None,
    create_collections: bool = False,
    composition_level: CompositionLevel,
    repeat_handling_strategy: RepeatHandlingStrategy,
    preserve_url_slugs: bool,
    forward_source_to_target: bool | None,
) -> AsyncResult:
    """
    Import a list of courses or legacy libraries into a V2 library (or, a collections within a V2 library).
    """
    target_library = get_library(target_library_key)
    # get_library ensures that the library is connected to a learning package.
    target_package_id: int = target_library.learning_package_id  # type: ignore[assignment]

    sources_pks: list[int] = []
    for source_key in source_key_list:
        source, _ = models.ModulestoreSource.objects.get_or_create(key=str(source_key))
        sources_pks.append(source.id)

    target_collection_pks: list[int | None] = []
    if target_collection_slug_list:
        for target_collection_slug in target_collection_slug_list:
            if target_collection_slug:
                target_collection_id = get_collection(target_package_id, target_collection_slug).id
                target_collection_pks.append(target_collection_id)
            else:
                target_collection_pks.append(None)

    return tasks.bulk_migrate_from_modulestore.delay(
        user_id=user.id,
        sources_pks=sources_pks,
        target_library_key=str(target_library_key),
        target_collection_pks=target_collection_pks,
        create_collections=create_collections,
        composition_level=composition_level.value,
        repeat_handling_strategy=repeat_handling_strategy.value,
        preserve_url_slugs=preserve_url_slugs,
        forward_source_to_target=forward_source_to_target,
    )
