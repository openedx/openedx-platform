"""
Views for viewing, adding, updating and deleting course updates.

Current db representation:
{
    "_id" : locationjson,
    "definition" : {
        "data" : "<ol>[<li><h2>date</h2>content</li>]</ol>"},
        "items" : [{"id": ID, "date": DATE, "content": CONTENT}]
        "metadata" : ignored
    }
}
"""


import logging
import re

from django.http import HttpResponseBadRequest
from django.utils.translation import gettext as _

from cms.djangoapps.contentstore.utils import track_course_update_event, send_course_update_notification
from openedx.core.lib.xblock_utils import get_course_update_items
from xmodule.html_block import CourseInfoBlock  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # lint-amnesty, pylint: disable=wrong-import-order

# # This should be in a class which inherits from XModuleDescriptor
log = logging.getLogger(__name__)


def get_course_updates(location, provided_id, user_id):
    """
    Retrieve the relevant course_info updates and unpack into the model which the client expects:
    [{id : index, date : string, content : html string}]
    """
    store = modulestore()
    try:
        course_updates = store.get_item(location)
    except ItemNotFoundError:
        course_updates = store.create_item(user_id, location.course_key, location.block_type, location.block_id)

    course_update_items = get_course_update_items(course_updates, _get_index(provided_id))
    return _get_visible_update(course_update_items)


def update_course_updates(location, update, passed_id=None, user=None, request_method=None):
    """
    Either add or update the given course update.
    Add:
        If the passed_id is absent or None, the course update is added.
    Update:
        It will update it if it has a passed_id which has a valid value.
        Until updates have distinct values, the passed_id is the location url + an index into the html structure.
    """
    store = modulestore()
    try:
        course_updates = store.get_item(location)
    except ItemNotFoundError:
        course_updates = store.create_item(user.id, location.course_key, location.block_type, location.block_id)

    course_update_items = list(reversed(get_course_update_items(course_updates)))
    course_update_dict = None

    if passed_id is not None:
        passed_index = _get_index(passed_id)

        # if passed_index in course_update_items_ids:
        for course_update_item in course_update_items:
            if course_update_item["id"] == passed_index:
                course_update_dict = course_update_item
                course_update_item["date"] = update["date"]
                course_update_item["content"] = update["content"]
                break
        if course_update_dict is None:
            return HttpResponseBadRequest(_("Invalid course update id."))
    else:
        course_update_items_ids = [course_update_item['id'] for course_update_item in course_update_items]

        course_update_dict = {
            # if no course updates then the id will be 1 otherwise maxid + 1
            "id": max(course_update_items_ids) + 1 if course_update_items_ids else 1,
            "date": update["date"],
            "content": update["content"],
            "status": CourseInfoBlock.STATUS_VISIBLE
        }
        course_update_items.append(course_update_dict)

    # update db record
    save_course_update_items(location, course_updates, course_update_items, user)

    if request_method == "POST":
        # track course update event
        track_course_update_event(location.course_key, user, course_update_dict)

        # send course update notification
        send_course_update_notification(
            location.course_key, course_update_dict["content"], user,
        )

    # remove status key
    if "status" in course_update_dict:
        del course_update_dict["status"]
    return course_update_dict


def _make_update_dict(update):
    """
    Return course update item as a dictionary with required keys ('id', "date" and "content").
    """
    return {
        "id": update["id"],
        "date": update["date"],
        "content": update["content"],
    }


def _get_visible_update(course_update_items):
    """
    Filter course update items which have status "deleted".
    """
    if isinstance(course_update_items, dict):
        # single course update item
        if course_update_items.get("status") != CourseInfoBlock.STATUS_DELETED:
            return _make_update_dict(course_update_items)
        else:
            # requested course update item has been deleted (soft delete)
            return {"error": _("Course update not found."), "status": 404}

    return ([_make_update_dict(update) for update in course_update_items
             if update.get("status") != CourseInfoBlock.STATUS_DELETED])


# pylint: disable=unused-argument
def delete_course_update(location, update, passed_id, user):
    """
    Don't delete course update item from db.
    Delete the given course_info update by settings "status" flag to 'deleted'.
    Returns the resulting course_updates.
    """
    if not passed_id:
        return HttpResponseBadRequest()

    try:
        course_updates = modulestore().get_item(location)
    except ItemNotFoundError:
        return HttpResponseBadRequest()

    course_update_items = list(reversed(get_course_update_items(course_updates)))
    passed_index = _get_index(passed_id)

    # delete update item from given index
    for course_update_item in course_update_items:
        if course_update_item["id"] == passed_index:
            # soft delete course update item
            course_update_item["status"] = CourseInfoBlock.STATUS_DELETED
            # update db record
            save_course_update_items(location, course_updates, course_update_items, user)
            return _get_visible_update(course_update_items)

    return HttpResponseBadRequest(_("Invalid course update id."))


def _get_index(passed_id=None):
    """
    From the url w/ index appended, get the index.
    """
    if passed_id:
        index_matcher = re.search(r'.*?/?(\d+)$', passed_id)
        if index_matcher:
            return int(index_matcher.group(1))

    # return 0 if no index found
    return 0


def save_course_update_items(location, course_updates, course_update_items, user=None):
    """
    Save list of course_updates data dictionaries in new field ("course_updates.items")
    and html related to course update in 'data' ("course_updates.data") field.
    """
    course_updates.items = course_update_items
    course_updates.data = ""

    # update db record
    modulestore().update_item(course_updates, user.id)

    return course_updates
