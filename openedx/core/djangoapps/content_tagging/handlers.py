"""
Handlers for Content Tagging
"""

import logging

from django.dispatch import receiver
from openedx_events.content_authoring.data import DuplicatedXBlockData, LibraryBlockData, XBlockData
from openedx_events.content_authoring.signals import (
    LIBRARY_BLOCK_DELETED,
    XBLOCK_DELETED,
    XBLOCK_DUPLICATED,
)

from . import api
from .types import ContentKey

log = logging.getLogger(__name__)

def _delete_tags(content_object: ContentKey) -> None:
    """Delete all tags associated with the given XBlock/course/etc."""
    log.info("Deleting tags for %s", content_object)
    # This is super fast; no need to do it from a celery task.
    api.delete_object_tags(str(content_object))


@receiver(XBLOCK_DELETED)
def delete_tag_xblock(**kwargs):
    """
    Delete an XBlock's tags when the block itself is deleted.
    """
    xblock_info = kwargs.get("xblock_info", None)
    if not xblock_info or not isinstance(xblock_info, XBlockData):
        log.error("Received null or incorrect data for event")
        return

    if xblock_info.block_type == "course":
        # Course deletion is handled by XBlock of course type
        _delete_tags(xblock_info.usage_key.course_key)

    _delete_tags(xblock_info.usage_key)


@receiver(XBLOCK_DUPLICATED)
def duplicate_tags(**kwargs):
    """
    Duplicates tags associated with an XBlock whenever the block is duplicated to a new location.
    """
    xblock_data = kwargs.get("xblock_info", None)
    if not xblock_data or not isinstance(xblock_data, DuplicatedXBlockData):
        log.error("Received null or incorrect data for event")
        return

    api.copy_object_tags(
        xblock_data.source_usage_key,
        xblock_data.usage_key,
    )


@receiver(LIBRARY_BLOCK_DELETED)
def library_block_deleted(**kwargs) -> None:
    """
    Delete an XBlock's tags when the block itself is deleted.
    """
    library_block_data = kwargs.get("library_block", None)
    if not library_block_data or not isinstance(library_block_data, LibraryBlockData):  # pragma: no cover
        log.error("Received null or incorrect data for event")
        return

    _delete_tags(library_block_data.usage_key)
