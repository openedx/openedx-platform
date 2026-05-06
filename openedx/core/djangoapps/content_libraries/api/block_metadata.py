"""
Content libraries data classes related to XBlocks/Components.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict
from uuid import UUID

from django.contrib.auth.models import AbstractUser
from django.utils.translation import gettext as _  # noqa: F401
from opaque_keys.edx.locator import LibraryContainerLocator, LibraryLocatorV2, LibraryUsageLocatorV2
from openedx_content.models_api import PublishableEntityVersion, PublishLogRecord

from openedx.core.djangoapps.user_api.accounts.image_helpers import get_profile_image_urls_for_user

from .libraries import (
    PublishableItem,
    library_component_usage_key,
)

# The public API is only the following symbols:
__all__ = [
    "LibraryXBlockMetadata",
    "LibraryXBlockStaticFile",
    "LibraryHistoryEntry",
    "LibraryHistoryContributor",
    "LibraryPublishHistoryGroup",
]

class ProfileImageUrls(TypedDict):
    """URLs for a user's profile image in different sizes."""

    full: str
    large: str
    medium: str
    small: str


@dataclass(frozen=True, kw_only=True)
class LibraryXBlockMetadata(PublishableItem):
    """
    Class that represents the metadata about an XBlock in a content library.
    """
    usage_key: LibraryUsageLocatorV2

    @classmethod
    def from_component(cls, library_key, component, associated_collections=None):
        """
        Construct a LibraryXBlockMetadata from a Component object.
        """
        # Import content_tagging.api here to avoid circular imports
        from openedx.core.djangoapps.content_tagging.api import get_object_tag_counts
        last_publish_log = component.versioning.last_publish_log

        published_by = None
        if last_publish_log and last_publish_log.published_by:
            published_by = last_publish_log.published_by.username

        draft = component.versioning.draft
        published = component.versioning.published
        last_draft_created = draft.created if draft else None
        last_draft_created_by = draft.publishable_entity_version.created_by if draft else None
        usage_key = library_component_usage_key(library_key, component)
        tags = get_object_tag_counts(str(usage_key), count_implicit=True)

        return cls(
            usage_key=usage_key,
            display_name=draft.title,
            created=component.created,
            created_by=component.created_by.username if component.created_by else None,
            modified=draft.created,
            draft_version_num=draft.version_num,
            published_version_num=published.version_num if published else None,
            published_display_name=published.title if published else None,
            last_published=None if last_publish_log is None else last_publish_log.published_at,
            published_by=published_by,
            last_draft_created=last_draft_created,
            last_draft_created_by=last_draft_created_by,
            has_unpublished_changes=component.versioning.has_unpublished_changes,
            collections=associated_collections or [],
            tags_count=tags.get(str(usage_key), 0),
            can_stand_alone=component.publishable_entity.can_stand_alone,
        )


@dataclass(frozen=True)
class LibraryHistoryEntry:
    """
    One entry in the history of a library component.
    """
    contributor: LibraryHistoryContributor | None
    changed_at: datetime
    title: str  # title at time of change
    item_type: str
    action: str  # "created" | "edited" | "renamed" | "deleted"
    old_version: int
    new_version: int | None


@dataclass(frozen=True)
class LibraryHistoryContributor:
    """
    A contributor in a publish history group, with profile image URLs.
    """
    username: str
    profile_image_urls: ProfileImageUrls

    @classmethod
    def from_user(cls, user, request=None) -> LibraryHistoryContributor:
        return cls(
            username=user.username,
            profile_image_urls=get_profile_image_urls_for_user(user, request),
        )


@dataclass(frozen=True)
class DirectPublishedEntity:
    """
    Represents one entity the user directly requested to publish (direct=True).
    Each entry carries its own title and entity_type so the frontend can display
    the correct label for each directly published item.

    Pre-Verawood groups have exactly one entry (approximated from available data).
    Post-Verawood groups have one entry per direct=True record in the PublishLog.
    """
    entity_key: LibraryUsageLocatorV2 | LibraryContainerLocator
    title: str               # title of the entity at time of publish
    entity_type: str  # e.g. "html", "problem" for components; "unit", "section" for containers


@dataclass(frozen=True)
class LibraryPublishHistoryGroup:
    """
    Summary of a publish event for a library item.

    Each instance represents one or more PublishLogRecords, and includes the
    set of contributors who authored draft changes between the previous publish
    and this one.

    Pre-Verawood (direct=None): one group per entity × publish event.
    Post-Verawood (direct!=None): one group per unique PublishLog.
    """
    publish_log_uuid: UUID
    published_by: AbstractUser | None
    published_at: datetime
    contributors: list[LibraryHistoryContributor]  # distinct authors of versions in this group
    # Each element is one entity the user directly requested to publish.
    # Pre-Verawood: single approximated entry derived from the group's entity.
    # Post-Verawood: one entry per direct=True record in the PublishLog.
    direct_published_entities: list[DirectPublishedEntity]
    # Key to pass as scope_entity_key when fetching entries for this group.
    # Pre-Verawood: the specific entity key for this group (container or usage key).
    # Post-Verawood container groups: None — frontend must use currentContainerKey.
    # Component history (all eras): usage_key.
    scope_entity_key: LibraryUsageLocatorV2 | LibraryContainerLocator | None


@dataclass(frozen=True)
class LibraryXBlockStaticFile:
    """
    Class that represents a static file in a content library, associated with
    a particular XBlock.
    """
    # File path e.g. "diagram.png"
    # In some rare cases it might contain a folder part, e.g. "en/track1.srt"
    path: str
    # Publicly accessible URL where the file can be downloaded
    url: str
    # Size in bytes
    size: int



def get_entity_item_type(entity) -> str:
    """
    Return the item type string for a PublishableEntity (component or container).
    """
    if hasattr(entity, 'component'):
        return entity.component.component_type.name
    if hasattr(entity, 'container'):
        return entity.container.container_type.type_code
    raise ValueError(f"Entity {entity} is neither a component nor a container.")


def make_contributor(user, request=None) -> LibraryHistoryContributor | None:
    """
    Convert a single User (or None) to a LibraryHistoryContributor.

    None input produces None output — frontend renders as default/anonymous.
    """
    return LibraryHistoryContributor.from_user(user, request) if user else None


def resolve_change_action(
    old_version: PublishableEntityVersion | None,
    new_version: PublishableEntityVersion | None,
) -> str:
    """
    Derive a human-readable action label from a draft history record's versions.
    """
    if old_version is None:
        return "created"
    if new_version is None:
        return "deleted"
    if old_version.title != new_version.title:
        return "renamed"
    return "edited"


def direct_published_entity_from_record(
    record: PublishLogRecord,
    lib_key: LibraryLocatorV2,
) -> DirectPublishedEntity:
    """
    Build a DirectPublishedEntity from a PublishLogRecord.

    lib_key is used only to construct locator strings — entity_key is always
    derived from record.entity itself, never from an external container key.

    Callers must ensure the record is fetched with:
        select_related(
            'entity__component__component_type',
            'entity__container__container_type',
            'new_version',
            'old_version',
        )
    """
    # Import here to avoid circular imports (container_metadata imports block_metadata).
    from .container_metadata import library_container_locator  # noqa: PLC0415

    # Use new_version title when available; fall back to old_version for soft-deletes (new_version=None).
    version = record.new_version or record.old_version
    title = version.title if version else ""
    if hasattr(record.entity, 'component'):
        component = record.entity.component
        return DirectPublishedEntity(
            entity_key=LibraryUsageLocatorV2(  # type: ignore[abstract]
                lib_key=lib_key,
                block_type=component.component_type.name,
                usage_id=component.component_code,
            ),
            title=title,
            entity_type=component.component_type.name,
        )
    if hasattr(record.entity, 'container'):
        container = record.entity.container
        return DirectPublishedEntity(
            entity_key=library_container_locator(lib_key, container),
            title=title,
            entity_type=container.container_type.type_code,
        )
    raise ValueError(f"PublishableEntity {record.entity.pk!r} is neither a Component nor a Container")
