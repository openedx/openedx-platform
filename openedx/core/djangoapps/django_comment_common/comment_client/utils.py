# pylint: disable=missing-docstring,unused-argument,broad-except
"""" Common utilities for comment client wrapper """


import logging

import requests  # pylint: disable=unused-import  # noqa: F401
from opaque_keys.edx.keys import CourseKey

log = logging.getLogger(__name__)


def strip_none(dic):
    return {k: v for k, v in dic.items() if v is not None}  # pylint: disable=consider-using-dict-comprehension


def strip_blank(dic):
    def _is_blank(v):
        return isinstance(v, str) and len(v.strip()) == 0
    return {k: v for k, v in dic.items() if not _is_blank(v)}  # pylint: disable=consider-using-dict-comprehension


def extract(dic, keys):
    if isinstance(keys, str):
        return strip_none({keys: dic.get(keys)})
    else:
        return strip_none({k: dic.get(k) for k in keys})


def clean_forum_params(params):
    """Convert string booleans to actual booleans and remove None values and empty lists from forum parameters."""
    result = {}
    for k, v in params.items():
        if v is not None and v != []:
            if isinstance(v, str):
                if v.lower() == 'true':
                    result[k] = True
                elif v.lower() == 'false':
                    result[k] = False
                else:
                    result[k] = v
            else:
                result[k] = v
    return result


class CommentClientError(Exception):
    pass


class CommentClientRequestError(CommentClientError):
    def __init__(self, msg, status_codes=400):
        super().__init__(msg)
        self.status_code = status_codes


class CommentClient500Error(CommentClientError):
    pass


class CommentClientMaintenanceError(CommentClientError):
    pass


class CommentClientPaginatedResult:
    """ class for paginated results returned from comment services"""

    def __init__(self, collection, page, num_pages, thread_count=0, corrected_text=None):
        self.collection = collection
        self.page = page
        self.num_pages = num_pages
        self.thread_count = thread_count
        self.corrected_text = corrected_text


class SubscriptionsPaginatedResult:
    """ class for paginated results returned from comment services"""

    def __init__(self, collection, page, num_pages, subscriptions_count=0, corrected_text=None):
        self.collection = collection
        self.page = page
        self.num_pages = num_pages
        self.subscriptions_count = subscriptions_count
        self.corrected_text = corrected_text


def get_course_key(course_id: CourseKey | str | None) -> CourseKey | None:
    """
    Returns a CourseKey if the provided course_id is a valid string representation of a CourseKey.
    If course_id is None or already a CourseKey object, it returns the course_id as is.
    Args:
        course_id (CourseKey | str | None): The course ID to be converted.
    Returns:
        CourseKey | None: The corresponding CourseKey object or None if the input is None.
    Raises:
        KeyError: If course_id is not a valid string representation of a CourseKey.
    """
    if course_id and isinstance(course_id, str):
        course_id = CourseKey.from_string(course_id)
    return course_id
