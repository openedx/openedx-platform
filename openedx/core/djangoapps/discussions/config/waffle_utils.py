"""
Utils methods for Discussion app waffle flags.
"""

from openedx.core.djangoapps.discussions.config.waffle import OVERRIDE_DISCUSSION_LEGACY_SETTINGS_FLAG


def legacy_discussion_experience_enabled(course_key):
    """
    Checks for relevant flags and returns a boolean whether to show legacy discussion settings or not
    """
    return OVERRIDE_DISCUSSION_LEGACY_SETTINGS_FLAG.is_enabled(course_key)
