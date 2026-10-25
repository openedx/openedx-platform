"""Reads and changes of a course's custom pages and tabs."""

import logging

from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.settings import api_settings

from cms.djangoapps.contentstore.views.permissions import COURSE_NOT_FOUND_MESSAGE
from cms.djangoapps.contentstore.views.tabs import edit_tab_handler, get_course_tabs, reorder_tabs_handler
from xmodule.modulestore.django import modulestore
from xmodule.tabs import CourseTabList, StaticTab

log = logging.getLogger(__name__)

# Reported under custom_pages when an item names no custom page of the course.
UNKNOWN_CUSTOM_PAGE_MESSAGE = "Each item must be the usage key of a custom page of this course."
# Reported under custom_pages when the same custom page is named twice.
REPEATED_CUSTOM_PAGE_MESSAGE = "Each custom page may be listed only once."
# Reported when the course's tabs could not be saved in the new order.
ORDER_NOT_SAVED_MESSAGE = "The custom pages could not be reordered. Their order is unchanged."
# Reported when the course has no tab with the requested identifier.
TAB_NOT_FOUND_MESSAGE = "The requested tab does not exist."
# Reported under is_hidden when the tab may not be hidden or shown.
TAB_NOT_HIDEABLE_MESSAGE = "This tab cannot be hidden or shown."


def _course_block(course_key):
    """Return the course block of ``course_key``, or raise ``NotFound`` when the modulestore has none."""
    course_block = modulestore().get_course(course_key)
    if course_block is None:
        raise NotFound(COURSE_NOT_FOUND_MESSAGE)
    return course_block


def _custom_page_slug(course_key, usage_key):
    """
    Return the ``url_slug`` a custom page of ``course_key`` named by ``usage_key`` would have, or None.

    The key may carry a branch or version of the course. A key of another
    course or library, or of a block that is not a custom page, gives None.
    """
    if not isinstance(usage_key, BlockUsageLocator) or usage_key.block_type != "static_tab":
        return None
    context = usage_key.course_key
    if not isinstance(context, CourseLocator):
        return None
    if (context.org, context.course, context.run) != (course_key.org, course_key.course, course_key.run):
        return None
    return usage_key.block_id


def list_custom_pages(course_key, user):
    """
    Return the course's custom pages in the order of the course's tabs.

    Each page carries its usage key as ``locator``. Built-in tabs are never
    returned, whether or not hidden.
    """
    course_block = _course_block(course_key)
    return [tab for tab in get_course_tabs(course_block, user) if isinstance(tab, StaticTab)]


def reorder_custom_pages(course_key, usage_keys, user):
    """
    Put the course's custom pages in the order of ``usage_keys``, as ``user``.

    Custom pages not named follow the named ones in their current order. As
    when Studio reorders pages, the course's tabs are then sorted by kind, so
    built-in tabs take their usual places around the custom pages. Raises
    ``ValidationError`` when a usage key names no custom page of the course or
    names one twice, and when the reordered tabs could not be saved; nothing
    is stored then.
    """
    course_block = _course_block(course_key)
    pages = {tab.url_slug: tab for tab in course_block.tabs if isinstance(tab, StaticTab)}
    named = []
    for usage_key in usage_keys:
        page = pages.get(_custom_page_slug(course_key, usage_key))
        if page is None:
            raise ValidationError({"custom_pages": [UNKNOWN_CUSTOM_PAGE_MESSAGE]})
        if any(page is other for other in named):
            raise ValidationError({"custom_pages": [REPEATED_CUSTOM_PAGE_MESSAGE]})
        named.append(page)
    rest = [page for page in pages.values() if all(page is not other for other in named)]
    try:
        reorder_tabs_handler(course_block, [{"tab_id": page.tab_id} for page in named + rest], user)
    except ValidationError as error:
        log.warning("The custom pages of course %s were not reordered: %s", course_key, error.detail)
        raise ValidationError({api_settings.NON_FIELD_ERRORS_KEY: [ORDER_NOT_SAVED_MESSAGE]}) from error


def update_tab(course_key, tab_id, changes, user):
    """
    Apply ``changes`` (``{"is_hidden": bool}`` or nothing) to the course tab ``tab_id``, as ``user``.

    Returns the tab as stored. Raises ``NotFound`` when the course has no such
    tab, and ``ValidationError`` on ``is_hidden`` when the tab may not be
    hidden or shown.
    """
    course_block = _course_block(course_key)
    tab = CourseTabList.get_tab_by_id(course_block.tabs, tab_id)
    if tab is None:
        raise NotFound(TAB_NOT_FOUND_MESSAGE)
    if "is_hidden" not in changes:
        return tab
    if not tab.is_hideable:
        raise ValidationError({"is_hidden": [TAB_NOT_HIDEABLE_MESSAGE]})
    edit_tab_handler(course_block, {"tab_id_locator": {"tab_id": tab_id}, "is_hidden": changes["is_hidden"]}, user)
    return tab
