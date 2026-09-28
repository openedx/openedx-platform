"""Service layer for the v2 course textbooks endpoint."""

import logging

from rest_framework.exceptions import NotFound

from cms.djangoapps.contentstore.utils import get_textbooks_context
from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)

COURSE_NOT_FOUND_MESSAGE = "The requested course does not exist."


def get_course_textbooks(course_key):
    """
    Return the stored PDF textbooks of the course ``course_key``, in stored order.

    Raises ``NotFound`` when the course cannot be loaded from the modulestore.
    """
    store = modulestore()
    with store.bulk_operations(course_key):
        course = store.get_course(course_key)
        if course is None:
            log.info("Textbooks requested for course %s, which is not in the modulestore.", course_key)
            raise NotFound(COURSE_NOT_FOUND_MESSAGE)
        return get_textbooks_context(course)["textbooks"]
