"""
Utilities for course updates.
"""

import hashlib  # noqa: I001
from datetime import datetime

from lms.djangoapps.courseware.courses import get_course_info_section_block
from openedx.core.djangoapps.user_api.course_tag.api import get_course_tag, set_course_tag

STATUS_VISIBLE = 'visible'
STATUS_DELETED = 'deleted'

VIEW_WELCOME_MESSAGE_KEY = 'view-welcome-message'


def _calculate_update_hash(update):
    """
    Returns a hash of the content of a course update. Does not need to be secure.
    """
    hasher = hashlib.md5()
    hasher.update(update['content'].encode('utf-8'))
    return hasher.hexdigest()


def _get_dismissed_hashes(user, course_key):
    """
    Returns a list of dismissed hashes, or None if all updates have been dismissed.
    """
    view_welcome_message = get_course_tag(user, course_key, VIEW_WELCOME_MESSAGE_KEY)
    if view_welcome_message == 'False':  # legacy value, which dismisses all updates
        return None
    return view_welcome_message.split(',') if view_welcome_message else []


def _add_dismissed_hash(user, course_key, new_hash):
    """
    Add a new hash to the list of previously dismissed updates.

    Overwrites a 'False' value with the current hash. Though we likely won't end up in that situation, since
    a 'False' value will never show the update to the user to dismiss in the first place.
    """
    hashes = _get_dismissed_hashes(user, course_key) or []
    hashes.append(new_hash)
    set_course_tag(user, course_key, VIEW_WELCOME_MESSAGE_KEY, ','.join(hashes))


def _safe_parse_date(date):
    """
    Since this is used solely for ordering purposes, use today's date as a default
    """
    try:
        return datetime.strptime(date, '%B %d, %Y')
    except ValueError:  # occurs for ill-formatted date values
        return datetime.today()


def get_ordered_updates(request, course):
    """
    Returns all public course updates in reverse chronological order, including dismissed ones.
    """
    info_block = get_course_info_section_block(request, request.user, course, 'updates')
    if not info_block:
        return []

    info_block = getattr(info_block, '_xmodule', info_block)
    ordered_updates = [update for update in info_block.items if update.get('status') == STATUS_VISIBLE]
    ordered_updates.sort(
        key=lambda item: (_safe_parse_date(item['date']), item['id']),
        reverse=True
    )
    for update in ordered_updates:
        update['content'] = info_block.runtime.service(info_block, "replace_urls").replace_urls(update['content'])
    return ordered_updates


def get_current_update_for_user(request, course):
    """
    Returns the current (most recent) course update HTML.

    Some rules about when we show updates:
    - If the newest update has not been dismissed yet, it gets returned.
    - If the newest update has been dismissed, we will return None.
    - Will return a previously-dismissed newest update if it has been edited since being dismissed.
    - If a current update is deleted and an already dismissed update is now the newest one, we don't want to show that.
    """
    updates = get_ordered_updates(request, course)
    if not updates:
        return None

    dismissed_hashes = _get_dismissed_hashes(request.user, course.id)
    if dismissed_hashes is None:  # all updates dismissed
        return None

    update_hash = _calculate_update_hash(updates[0])
    if update_hash in dismissed_hashes:  # pylint: disable=unsupported-membership-test
        return None

    return updates[0]['content']


def dismiss_current_update_for_user(request, course):
    """
    Marks the current course update for this user as dismissed.

    See get_current_update_for_user for what "current course update" means in practice.
    """
    updates = get_ordered_updates(request, course)
    if not updates:
        return None

    update_hash = _calculate_update_hash(updates[0])
    _add_dismissed_hash(request.user, course.id, update_hash)
