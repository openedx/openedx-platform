"""Service layer for the v2 course outline endpoint."""

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext as _
from rest_framework.exceptions import NotFound, ValidationError

from cms.djangoapps.contentstore.config.waffle import CUSTOM_RELATIVE_DATES
from cms.djangoapps.contentstore.rest_api.v2.serializers.course_outline import NOT_AN_OUTLINE_BLOCK_MESSAGE
from cms.djangoapps.contentstore.utils import (
    get_lms_link_for_item,
    get_pages_and_resources_url,
    get_proctored_exam_settings_url,
    reverse_course_url,
)
from cms.djangoapps.contentstore.views.course import (
    _deprecated_blocks_info,
    course_outline_initial_state,
    user_can_reindex_course,
)
from cms.djangoapps.contentstore.views.permissions import COURSE_NOT_FOUND_MESSAGE
from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import create_xblock_info
from cms.djangoapps.models.settings.course_metadata import CourseMetadata
from common.djangoapps.course_action_state.managers import CourseActionStateItemNotFoundError
from common.djangoapps.course_action_state.models import CourseRerunState
from common.djangoapps.util.date_utils import get_default_time_display
from common.djangoapps.xblock_django.api import deprecated_xblocks
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.course_block import DEFAULT_START_DATE  # pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


def _has_outline_children(block):
    """Whether the outline lists children under ``block``: every container except a unit does."""
    return block.has_children and block.category != "vertical"


def _is_outline_container(store, block):
    """
    Whether ``block`` is a container, other than the course, that the outline reaches.

    The outline opens a block's children only when ``_has_outline_children``
    holds for it, so a block is reached exactly when that holds for every
    ancestor up to the course. A block no parent links to is not reached.
    These are the blocks a ``children_url`` can name.
    """
    if not block.has_children or block.category == "course":
        return False
    parent_location = store.get_parent_location(block.location)
    while parent_location is not None:
        parent = store.get_item(parent_location)
        if not _has_outline_children(parent):
            return False
        if parent.category == "course":
            return True
        parent_location = store.get_parent_location(parent_location)
    return False


def _load_course(course_key):
    """Return the course block of ``course_key``, or raise ``NotFound`` when the modulestore lacks it."""
    course = modulestore().get_course(course_key)
    if course is None:
        log.info("Outline requested for course %s, which is not in the modulestore.", course_key)
        raise NotFound(COURSE_NOT_FOUND_MESSAGE)
    return course


def _outline_node(block, levels_left, children_url):
    """
    Return the outline node of ``block`` with its descendants ``levels_left`` levels deep.

    A container cut at the depth limit carries ``children_url`` in place of
    ``children`` when it has children. A unit, or any block the outline does
    not open, carries neither.
    """
    node = {
        "id": str(block.location),
        "category": block.category,
        "display_name": block.display_name_with_default,
    }
    if not _has_outline_children(block):
        return node
    if levels_left > 0:
        node["children"] = [
            _outline_node(child, levels_left - 1, children_url) for child in block.get_children()
        ]
    elif block.children:
        node["children_url"] = children_url(block)
    else:
        node["children"] = []
    return node


def get_outline_tree(request, course_key, depth, usage_key=None):
    """
    Return the outline of ``course_key`` as a tree of nodes, ``depth`` levels below its root.

    The root is the course, or the container ``usage_key`` when given: any
    block of the outline that has children, such as a section, subsection,
    unit or a split test placed in a subsection. ``usage_key`` must already be
    known to name a block of the course; ``ValidationError`` is raised when
    the block does not exist or is not such a container. Nothing is written.
    """
    outline_url = request.build_absolute_uri(
        reverse("authoring_v2:course_outline", kwargs={"course_key": course_key})
    )

    def children_url(block):
        return f"{outline_url}?{urlencode({'usage_key': str(block.location), 'depth': 1})}"

    store = modulestore()
    with store.bulk_operations(course_key):
        root = _load_course(course_key)
        if usage_key is not None:
            try:
                root = store.get_item(usage_key)
            except ItemNotFoundError as error:
                log.info("Outline of %s requested under %s, which does not exist: %s", course_key, usage_key, error)
                raise ValidationError({"usage_key": [NOT_AN_OUTLINE_BLOCK_MESSAGE]}) from error
            if not _is_outline_container(store, root):
                log.info("Outline of %s requested under %s, which is not a container in it.", course_key, usage_key)
                raise ValidationError({"usage_key": [NOT_AN_OUTLINE_BLOCK_MESSAGE]})
        return _outline_node(root, depth, children_url)


def _rerun_notification(course_key):
    """Return the course's rerun status still shown to authors, or None."""
    try:
        return CourseRerunState.objects.find_first(course_key=course_key, should_display=True)
    except (ItemNotFoundError, CourseActionStateItemNotFoundError):
        return None


def get_full_outline(request, course_key, show=None):
    """
    Return the course outline page's data for ``course_key``.

    ``course_structure`` is the whole outline down to units, in the shape the
    Studio outline page reads, and ``initial_state`` expands the block ``show``
    when it is in the outline. ``reindex_link`` is set only for callers allowed
    to reindex the course.

    Nothing is written: release dates before 1900 are reported as stored, and
    the course's discussion settings are copied before the configuration
    address is added to them.
    """
    store = modulestore()
    with store.bulk_operations(course_key):
        course = _load_course(course_key)
        course_structure = create_xblock_info(
            course,
            include_child_info=True,
            course_outline=True,
            include_children_predicate=lambda block: block.category != "vertical",
        )
        reindex_link = None
        if settings.ENABLE_COURSEWARE_INDEX and user_can_reindex_course(course_key, request.user):
            reindex_link = f"/course/{course_key}/search_reindex"
        rerun = _rerun_notification(course_key)
        deprecated_blocks_info = _deprecated_blocks_info(course, [block.name for block in deprecated_xblocks()])
        proctoring_errors = CourseMetadata.validate_proctoring_settings(
            course, CourseMetadata.fetch(course), request.user,
        )
        discussions_settings = {
            **course.discussions_settings,
            "discussion_configuration_url": f"{get_pages_and_resources_url(course.id)}/discussion/settings",
        }
        overview = CourseOverview.objects.filter(id=course.id).values("created").first()
        return {
            "course_release_date": (
                get_default_time_display(course.start) if course.start != DEFAULT_START_DATE else _("Set Date")
            ),
            "course_structure": course_structure,
            "deprecated_blocks_info": deprecated_blocks_info,
            "discussions_incontext_feedback_url": settings.DISCUSSIONS_INCONTEXT_FEEDBACK_URL,
            "discussions_incontext_learnmore_url": settings.DISCUSSIONS_INCONTEXT_LEARNMORE_URL,
            "discussions_settings": discussions_settings,
            "initial_state": course_outline_initial_state(show, course_structure) if show else None,
            "language_code": request.LANGUAGE_CODE,
            "lms_link": get_lms_link_for_item(course.location),
            "mfe_proctored_exam_settings_url": get_proctored_exam_settings_url(course.id),
            "notification_dismiss_url": reverse_course_url(
                "course_notifications_handler", rerun.course_key, kwargs={"action_state_id": rerun.id},
            ) if rerun else None,
            "proctoring_errors": proctoring_errors,
            "reindex_link": reindex_link,
            "rerun_notification_id": rerun.id if rerun else None,
            "advance_settings_url": reverse_course_url("advanced_settings_handler", course.id),
            "is_custom_relative_dates_active": CUSTOM_RELATIVE_DATES.is_enabled(course_key),
            "created_on": overview["created"] if overview else None,
        }
