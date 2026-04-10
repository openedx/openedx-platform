"""
Content libraries data classes related to XBlocks/Components.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext as _  # noqa: F401
from opaque_keys.edx.locator import LibraryUsageLocatorV2

from .libraries import PublishableItem, library_component_usage_key

# The public API is only the following symbols:
__all__ = [
    "LibraryXBlockMetadata",
    "LibraryXBlockStaticFile",
]


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
