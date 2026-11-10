"""Reads and updates of a course's advanced settings."""

import logging
import re

from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.settings import api_settings

from cms.djangoapps.contentstore.views.course import update_course_advanced_settings
from cms.djangoapps.contentstore.views.permissions import COURSE_NOT_FOUND_MESSAGE
from cms.djangoapps.models.settings.course_metadata import CourseMetadata
from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)

# Reported under the name of a refused setting in place of any explanation
# that is not one of the course rules' own messages, such as the text of an
# exception raised while converting the value. That explanation is logged.
INVALID_SETTING_MESSAGE = "The value is not valid for this setting."
# Reported when the settings could not be saved for a reason tied to no setting.
SETTINGS_NOT_SAVED_MESSAGE = "The settings could not be saved. No setting was changed."


def _pattern(template):
    """Return a regular expression for ``template``, in which each ``{}`` stands for any text."""
    return ".*".join(re.escape(part) for part in template.split("{}"))


def _any_of(patterns):
    """Return a regular expression, as one group, matching any one of ``patterns``."""
    return "(?:{})".format("|".join(patterns))


def _in_order_separated_by_commas(slots):
    """Return a regular expression for one or more of ``slots``, in the given order, separated by commas."""
    return _any_of(
        first + "".join(f"(?:,{later})?" for later in slots[index + 1:])
        for index, first in enumerate(slots)
    )


# The problems the teams rules report about one team-set, joined with commas
# in this order. Each entry lists the problems that can take its place.
_TEAMSET_PROBLEMS = (
    ("type {} is invalid",),
    ("max_team_size must be greater than zero", "max_team_size cannot be greater than 500"),
    ("id attribute must not be empty",),
    ("name attribute must not be empty",),
    ("description attribute must not be empty",),
    ("extra keys: {}",),
)

_MISSING_ESCALATION_EMAIL = "Provider '{}' requires an exam escalation contact."

# The messages the course's rules in ``CourseMetadata`` write about a refused
# setting, by setting name. They are returned to the caller as written. A
# message missing here, including a rule's reworded or new message, is
# reported as ``INVALID_SETTING_MESSAGE``.
_RULE_MESSAGES = {
    "teams_configuration": (
        _pattern("max_team_size must be greater than zero"),
        _pattern("max_team_size cannot be greater than 500"),
        _pattern("duplicate ids: {}"),
        _in_order_separated_by_commas([_any_of(map(_pattern, problems)) for problems in _TEAMSET_PROBLEMS]),
    ),
    "proctoring_provider": (
        _pattern("The proctoring provider cannot be modified after a course has started. Contact {} for assistance"),
        _pattern("The proctoring provider configured for this course, '{}', is not valid."),
        _pattern("The selected proctoring provider, {}, is not a valid provider. Please select from one of {}."),
        _pattern(_MISSING_ESCALATION_EMAIL),
    ),
    "proctoring_escalation_email": (
        _pattern(_MISSING_ESCALATION_EMAIL),
    ),
}
_RULE_MESSAGE_PATTERNS = {name: re.compile(_any_of(patterns), re.DOTALL) for name, patterns in _RULE_MESSAGES.items()}


def is_rule_message(name, message):
    """Return whether ``message`` is one the course's rules write about the setting ``name``."""
    pattern = _RULE_MESSAGE_PATTERNS.get(name)
    return pattern is not None and pattern.fullmatch(message) is not None


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
    nothing is stored and ``ValidationError`` maps each refused setting to its
    messages (see ``_errors_by_setting``).
    Returns the settings course authors edit in Studio, as stored.
    """
    course_block = _course_block(course_key)
    try:
        return update_course_advanced_settings(course_block, changes, user)
    except ValidationError as error:
        raise _errors_by_setting(course_key, error) from error


def _errors_by_setting(course_key, error):
    """
    Return a ``ValidationError`` mapping each refused setting to its messages.

    The settings validator reports one entry per problem, carrying the
    setting's name and an explanation. An explanation the course's rules write
    themselves (a team size limit, a proctoring rule) is returned as written.
    Any other explanation, such as the text of an exception raised while
    converting the value, is logged with the course and setting and returned
    as ``INVALID_SETTING_MESSAGE``. A failure not tied to a setting is logged
    and reported with ``SETTINGS_NOT_SAVED_MESSAGE``.
    """
    detail = error.detail if isinstance(error.detail, list) else [error.detail]
    by_setting = {}
    unattributed = []
    for entry in detail:
        name = entry.get("key") if isinstance(entry, dict) else None
        if not name:
            unattributed.append(entry)
            continue
        name, explanation = str(name), str(entry.get("message", ""))
        if is_rule_message(name, explanation):
            message = explanation
        else:
            log.warning("Advanced setting %s of course %s was refused: %s", name, course_key, explanation)
            message = INVALID_SETTING_MESSAGE
        messages = by_setting.setdefault(name, [])
        if message not in messages:
            messages.append(message)
    if unattributed:
        log.warning("Advanced settings of course %s were not saved: %s", course_key, unattributed)
        by_setting.setdefault(api_settings.NON_FIELD_ERRORS_KEY, []).append(SETTINGS_NOT_SAVED_MESSAGE)
    return ValidationError(by_setting)
