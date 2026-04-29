"""
A tiny app that checks for a status message.
"""


import logging

from .models import GlobalStatusMessage

log = logging.getLogger(__name__)


def get_site_status_msg(course_key):
    """
    Pull the status message from the database.

    Caches the message by course.
    """
    try:
        # The current() value for GlobalStatusMessage is cached.
        if not GlobalStatusMessage.current().enabled:
            return None

        return GlobalStatusMessage.current().full_message(course_key)
    # Make as general as possible, because something broken here should not
    # bring down the whole site.
    except:  # noqa: E722
        log.exception("Error while getting a status message.")
        return None
