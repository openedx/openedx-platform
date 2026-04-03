"""
Automatic tagging of content
"""

import logging

import crum
from django.dispatch import receiver
from openedx_events.content_authoring.data import CourseData, DuplicatedXBlockData, LibraryBlockData, XBlockData
from openedx_events.content_authoring.signals import (
    COURSE_CREATED,
    LIBRARY_BLOCK_CREATED,
    LIBRARY_BLOCK_UPDATED,
    XBLOCK_CREATED,
    XBLOCK_DELETED,
    XBLOCK_DUPLICATED,
    XBLOCK_UPDATED
)

from .api import copy_object_tags
from .tasks import (
    delete_course_tags,
    delete_xblock_tags,
    update_course_tags,
    update_library_block_tags,
    update_xblock_tags
)
from .toggles import CONTENT_TAGGING_AUTO

log = logging.getLogger(__name__)


@receiver(COURSE_CREATED)
def auto_tag_course(**kwargs):
    """
    Automatically tag course based on their metadata
    """
    course_data = kwargs.get("course", None)
    if not course_data or not isinstance(course_data, CourseData):
        log.error("Received null or incorrect data for event")
        return

    if not CONTENT_TAGGING_AUTO.is_enabled(course_data.course_key):
        return

    update_course_tags.delay(str(course_data.course_key))


@receiver(XBLOCK_CREATED)
@receiver(XBLOCK_UPDATED)
def auto_tag_xblock(**kwargs):
    """
    Automatically tag XBlock based on their metadata
    """
    xblock_info = kwargs.get("xblock_info", None)
    if not xblock_info or not isinstance(xblock_info, XBlockData):
        log.error("Received null or incorrect data for event")
        return

    if not CONTENT_TAGGING_AUTO.is_enabled(xblock_info.usage_key.course_key):
        return

    if xblock_info.block_type == 'course_info':
        # We want to add tags only to the course id, not with its XBlock
        return

    if xblock_info.block_type == "course":
        # Course update is handled by XBlock of course type
        update_course_tags.delay(str(xblock_info.usage_key.course_key))
        return

    update_xblock_tags.delay(str(xblock_info.usage_key))


@receiver(XBLOCK_DELETED)
def delete_tag_xblock(**kwargs):
    """
    Automatically delete XBlock auto tags.
    """
    xblock_info = kwargs.get("xblock_info", None)
    if not xblock_info or not isinstance(xblock_info, XBlockData):
        log.error("Received null or incorrect data for event")
        return

    if not CONTENT_TAGGING_AUTO.is_enabled(xblock_info.usage_key.course_key):
        return

    if xblock_info.block_type == "course":
        # Course deletion is handled by XBlock of course type
        delete_course_tags.delay(str(xblock_info.usage_key.course_key))

    delete_xblock_tags.delay(str(xblock_info.usage_key))


@receiver(LIBRARY_BLOCK_CREATED)
@receiver(LIBRARY_BLOCK_UPDATED)
def auto_tag_library_block(**kwargs):
    """
    Automatically tag Library Blocks based on metadata
    """
    if not CONTENT_TAGGING_AUTO.is_enabled():
        return

    library_block_data = kwargs.get("library_block", None)
    if not library_block_data or not isinstance(library_block_data, LibraryBlockData):
        log.error("Received null or incorrect data for event")
        return

    current_request = crum.get_current_request()
    update_library_block_tags.delay(
        str(library_block_data.usage_key), current_request.LANGUAGE_CODE
    )


@receiver(XBLOCK_DUPLICATED)
def duplicate_tags(**kwargs):
    """
    Duplicates tags associated with an XBlock whenever the block is duplicated to a new location.
    """
    xblock_data = kwargs.get("xblock_info", None)
    if not xblock_data or not isinstance(xblock_data, DuplicatedXBlockData):
        log.error("Received null or incorrect data for event")
        return

    copy_object_tags(
        xblock_data.source_usage_key,
        xblock_data.usage_key,
    )
