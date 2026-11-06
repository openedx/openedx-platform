"""Reads and updates of a course's advanced settings."""

import logging

from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.settings import api_settings

from cms.djangoapps.contentstore.views.course import update_course_advanced_settings
from cms.djangoapps.contentstore.views.permissions import COURSE_NOT_FOUND_MESSAGE
from cms.djangoapps.models.settings.course_metadata import CourseMetadata
from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)

# Reported under the name of each setting whose value the course refused. The
# validator's own explanation is logged, not returned.
INVALID_SETTING_MESSAGE = "The value is not valid for this setting."
# Reported when the settings could not be saved for a reason tied to no setting.
SETTINGS_NOT_SAVED_MESSAGE = "The settings could not be saved. No setting was changed."


def _course_block(course_key):
    """Return the course block of ``course_key``, or raise ``NotFound`` when the modulestore has none."""
    course_block = modulestore().get_course(course_key)
    if course_block is None:
        raise NotFound(COURSE_NOT_FOUND_MESSAGE)
    return course_block


def settings_authors_do_not_edit(course_key):
    """Return the names of the course's settings that are not edited as advanced settings."""
    return frozenset(CourseMetadata.get_exclude_list_of_fields(course_key))


def get_advanced_settings(course_key, full=False):
    """
    Return the advanced settings of the course, keyed by setting name.

    By default only the settings course authors edit in Studio are returned;
    with ``full`` every setting of the course is.
    """
    course_block = _course_block(course_key)
    if full:
        return CourseMetadata.fetch_all(course_block)
    return CourseMetadata.fetch(course_block)


def update_advanced_settings(course_key, changes, user):
    """
    Apply ``changes`` (``{name: {"value": value}}``) to the course as ``user``.

    Every value is validated before any is stored; when one is refused,
    nothing is stored and ``ValidationError`` maps each refused setting to
    ``INVALID_SETTING_MESSAGE``.
    Returns the settings course authors edit in Studio, as stored.
    """
    course_block = _course_block(course_key)
    try:
        return update_course_advanced_settings(course_block, changes, user)
    except ValidationError as error:
        raise _errors_by_setting(course_key, error) from error


def _errors_by_setting(course_key, error):
    """
    Return a ``ValidationError`` mapping each refused setting to a fixed message.

    The settings validator reports one entry per refused setting, carrying the
    setting's name and its own explanation, which can be the text of a parsing
    exception. The explanation is logged with the course and setting; the
    caller gets ``INVALID_SETTING_MESSAGE`` under the setting's name. Any
    failure not tied to a setting is logged and reported with
    ``SETTINGS_NOT_SAVED_MESSAGE``.
    """
    detail = error.detail if isinstance(error.detail, list) else [error.detail]
    by_setting = {}
    unattributed = []
    for entry in detail:
        name = entry.get("key") if isinstance(entry, dict) else None
        if name:
            log.warning(
                "Advanced setting %s of course %s was refused: %s", name, course_key, entry.get("message", ""),
            )
            by_setting[str(name)] = [INVALID_SETTING_MESSAGE]
        else:
            unattributed.append(entry)
    if unattributed:
        log.warning("Advanced settings of course %s were not saved: %s", course_key, unattributed)
        by_setting.setdefault(api_settings.NON_FIELD_ERRORS_KEY, []).append(SETTINGS_NOT_SAVED_MESSAGE)
    return ValidationError(by_setting)
