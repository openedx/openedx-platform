"""
Utils methods for Discussion app waffle flags.
"""

from openedx.core.djangoapps.discussions.config.waffle import (
    ENABLE_PAGES_AND_RESOURCES_MICROFRONTEND,
    OVERRIDE_DISCUSSION_LEGACY_SETTINGS_FLAG,
)


def legacy_discussion_experience_enabled(course_key):
    """
    Checks for relevant flags and returns a boolean whether to show legacy discussion settings or not
    """
    return bool(OVERRIDE_DISCUSSION_LEGACY_SETTINGS_FLAG.is_enabled(course_key) or
                not ENABLE_PAGES_AND_RESOURCES_MICROFRONTEND.is_enabled(course_key))
