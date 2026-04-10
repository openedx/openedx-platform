"""
Helper functions for the course complete event that was originally included with the Badging MVP.
"""


import hashlib
import logging

from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

LOGGER = logging.getLogger(__name__)


# NOTE: As these functions are carry-overs from the initial badging implementation, they are used in
# migrations. Please check the badge migrations when changing any of these functions.


def course_slug(course_key, mode):
    """
    Legacy: Not to be used as a model for constructing badge slugs. Included for compatibility with the original badge
    type, awarded on course completion.
    Slug ought to be deterministic and limited in size so it's not too big for Badgr.
    Badgr's max slug length is 255.
    """
    # Seven digits should be enough to realistically avoid collisions. That's what git services use.
    digest = hashlib.sha256(
        f"{str(course_key)}{str(mode)}".encode('utf-8')  # noqa: UP012
    ).hexdigest()[:7]
    base_slug = slugify(str(course_key) + f'_{mode}_')[:248]

    # slugify() now removes leading and trailing dashes and underscores.
    # Reference: Django 3.2 Release Notes https://docs.djangoproject.com/en/3.2/releases/3.2/#miscellaneous
    # TODO: Remove this condition and make this return as default when platform is upgraded to 3.2
    return f'{base_slug}_{digest}'


def badge_description(course, mode):
    """
    Returns a description for the earned badge.
    """
    if course.end:
        return _('Completed the course "{course_name}" ({course_mode}, {start_date} - {end_date})').format(
            start_date=course.start.date(),
            end_date=course.end.date(),
            course_name=course.display_name,
            course_mode=mode,
        )
    else:
        return _('Completed the course "{course_name}" ({course_mode})').format(
            course_name=course.display_name,
            course_mode=mode,
        )


def evidence_url(user_id, course_key):
    """
    Generates a URL to the user's Certificate HTML view, along with a GET variable that will signal the evidence visit
    event.
    """
    return
