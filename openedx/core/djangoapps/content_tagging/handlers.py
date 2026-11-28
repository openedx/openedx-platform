"""
Handlers for Content Tagging
"""

import logging

from django.db import transaction
from django.db.models import ProtectedError
from django.dispatch import receiver
from openedx_events.content_authoring.data import DuplicatedXBlockData, LibraryBlockData, XBlockData
from openedx_events.content_authoring.signals import (
    LIBRARY_BLOCK_DELETED,
    XBLOCK_DELETED,
    XBLOCK_DUPLICATED,
)
from openedx_tagging.models import ObjectTag

from xmodule.modulestore.django import SignalHandler, modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

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
        # The course root block is the course itself, so its course-key tags go too.
        _delete_tags(xblock_info.usage_key.course_key)

    _delete_tags(xblock_info.usage_key)


@receiver(SignalHandler.pre_item_delete)
def delete_descendant_tags(**kwargs):
    """
    Delete the tags of every descendant of a block that is about to be deleted.

    ``delete_tag_xblock`` removes the deleted block's own tags, but it runs on XBLOCK_DELETED,
    after the subtree can no longer be read. This receiver runs on the modulestore's
    ``pre_item_delete`` signal, while the descendants can still be loaded. The signal fires once
    per branch, so a block that still exists in the other branch is walked again, which is
    harmless because deleting tags is idempotent.

    Deleting a block on the draft branch removes its descendants' tags even while the published
    branch still holds those blocks, so a later "discard changes" restores the content without its
    tags. This matches what ``delete_tag_xblock`` does for the deleted block itself.

    A descendant whose tags cannot be deleted because other data protects them is logged and
    skipped. Any other failure is logged and ends the walk. Neither blocks the deletion.
    """
    usage_key = kwargs.get("usage_key")
    if not usage_key or usage_key.block_type == "course":
        # Deleting a whole course goes through ``delete_course``, which emits ``course_deleted``;
        # the ``delete_course_tags`` receiver handles it.
        return

    usage_key = usage_key.for_branch(None)
    try:
        deleted_block = modulestore().get_item(usage_key, depth=None)
        stack = list(deleted_block.get_children())
        while stack:
            block = stack.pop()
            try:
                # The savepoint keeps a failed delete from poisoning the surrounding transaction.
                with transaction.atomic():
                    _delete_tags(block.usage_key)
            except ProtectedError:
                log.warning("Unable to delete the tags of %s, other data protects them", block.usage_key)
            stack.extend(block.get_children())
    except ItemNotFoundError:
        # The signal fires once per branch. When the block is already gone there is nothing to walk.
        log.debug("XBlock %s not found, skipping descendant tag cleanup", usage_key)
    # A failure cleaning up tags must never block the deletion itself, whatever raised it.
    except Exception:  # pylint: disable=broad-except
        log.warning("Unable to delete descendant tags of %s", usage_key, exc_info=True)


@receiver(SignalHandler.course_deleted)
def delete_course_tags(sender, course_key, **kwargs):
    """
    Delete the tags of a deleted course, of the course key itself and of every block in the course.

    ``delete_course`` sends only ``SignalHandler.course_deleted``, never ``XBLOCK_DELETED``, and sends it
    after the course structure is gone. The blocks can no longer be listed, and the tagging API has no lookup
    by course, so the tagged object ids are found by matching the ObjectTag table with
    ``api.course_object_id_clause``. An object whose tags other data protects is logged and skipped. Any
    other failure is logged and ends the cleanup. Neither blocks the deletion.
    """
    try:
        object_ids = list(
            ObjectTag.objects.filter(api.course_object_id_clause(course_key))
            .values_list("object_id", flat=True)
            .distinct()
        )
        deleted_count = 0
        for object_id in object_ids:
            try:
                # The savepoint keeps a failed delete from poisoning the surrounding transaction.
                with transaction.atomic():
                    api.delete_object_tags(object_id)
                deleted_count += 1
            except ProtectedError:
                log.warning("Unable to delete the tags of %s, other data protects them", object_id)
        log.info(
            "Deleted the tags of %d of %d objects of deleted course %s", deleted_count, len(object_ids), course_key
        )
    # A failure cleaning up tags must never block the deletion itself, whatever raised it.
    except Exception:  # pylint: disable=broad-except
        log.warning("Unable to delete tags of deleted course %s", course_key, exc_info=True)


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
