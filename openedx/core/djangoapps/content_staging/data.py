"""
Public python data types for content staging
"""
from __future__ import annotations

from datetime import datetime

from attrs import field, frozen, validators
from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _
from opaque_keys.edx.keys import AssetKey, ContainerKey, LearningContextKey, UsageKey

from openedx.core.djangoapps.content_tagging.api import TagValuesByObjectIdDict


class StagedContentStatus(TextChoices):
    """ The status of this staged content. """
    # LOADING: We are actively (asynchronously) writing the OLX and related data into the staging area.
    # It is not ready to be read.
    LOADING = "loading", _("Loading")
    # READY: The content is staged and ready to be read.
    READY = "ready", _("Ready")
    # The content has expired and this row can be deleted, along with any associated data.
    EXPIRED = "expired", _("Expired")
    # ERROR: The content could not be staged.
    ERROR = "error", _("Error")


# Value of the "purpose" field on StagedContent objects used for clipboards.
CLIPBOARD_PURPOSE = "clipboard"

# Value of the "purpose" field on StagedContent objects used for library to course sync.
LIBRARY_SYNC_PURPOSE = "library_sync"

# There may be other valid values of "purpose" which aren't defined within this app.


@frozen
class StagedContentData:
    """
    Read-only data model representing StagedContent

    (OLX content that isn't part of any course at the moment)
    """
    id: int = field(validator=validators.instance_of(int))
    user_id: int = field(validator=validators.instance_of(int))
    created: datetime = field(validator=validators.instance_of(datetime))
    purpose: str = field(validator=validators.instance_of(str))
    status: StagedContentStatus = field(validator=validators.in_(StagedContentStatus), converter=StagedContentStatus)
    block_type: str = field(validator=validators.instance_of(str))
    display_name: str = field(validator=validators.instance_of(str))
    tags: TagValuesByObjectIdDict = field(validator=validators.optional(validators.instance_of(dict)))
    version_num: int = field(validator=validators.instance_of(int))


@frozen
class StagedContentFileData:
    """Read-only data model for a single file used by some staged content."""
    filename: str = field(validator=validators.instance_of(str))
    # Everything below is optional:
    data: bytes | None = field(validator=validators.optional(validators.instance_of(bytes)))
    # If this asset came from Files & Uploads in a course, this is an AssetKey
    # as a string. If this asset came from an XBlock's filesystem, this is the
    # UsageKey of the XBlock.
    source_key: AssetKey | UsageKey | ContainerKey | None = field(
        validator=validators.optional(validators.instance_of((AssetKey, UsageKey, ContainerKey)))
    )
    md5_hash: str | None = field(validator=validators.optional(validators.instance_of(str)))


@frozen
class UserClipboardData:
    """ Read-only data model for User Clipboard data (copied OLX) """
    content: StagedContentData = field(validator=validators.instance_of(StagedContentData))
    source_usage_key: UsageKey | ContainerKey
    source_context_title: str

    @property
    def source_context_key(self) -> LearningContextKey:
        """ Get the context (course/library) that this was copied from """
        return self.source_usage_key.context_key
