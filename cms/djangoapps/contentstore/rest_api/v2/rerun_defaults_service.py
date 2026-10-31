"""Service layer for the v2 course rerun defaults endpoint."""

import logging

from rest_framework.exceptions import NotFound

from cms.djangoapps.contentstore.utils import get_course_rerun_context
from cms.djangoapps.contentstore.views.permissions import COURSE_NOT_FOUND_MESSAGE
from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)


def get_rerun_defaults(course_key, user):
    """
    Return the values a rerun of the course ``course_key`` starts from, for ``user``.

    ``user`` must be global staff. For anyone else, looking up whether they may
    create courses can record a course creator request for them, which a read
    must not do.

    Raises ``NotFound`` when the modulestore has no course ``course_key``.
    """
    store = modulestore()
    with store.bulk_operations(course_key):
        course = store.get_course(course_key)
        if course is None:
            log.info("Rerun defaults requested for course %s, which is not in the modulestore.", course_key)
            raise NotFound(COURSE_NOT_FOUND_MESSAGE)
        context = get_course_rerun_context(course_key, course, user)
    return {**context, "org": course_key.org, "number": course_key.course, "run": course_key.run}
