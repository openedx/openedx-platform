"""
Provides utility methods for video sharing functionality.
"""

import logging

from django.conf import settings
from opaque_keys.edx.keys import UsageKey

from openedx.core.djangoapps.video_config.toggles import PUBLIC_VIDEO_SHARE
from openedx.core.lib.courses import get_course_by_id

log = logging.getLogger(__name__)

# Video sharing constants
COURSE_VIDEO_SHARING_PER_VIDEO = 'per-video'
COURSE_VIDEO_SHARING_ALL_VIDEOS = 'all-on'
COURSE_VIDEO_SHARING_NONE = 'all-off'


@staticmethod
def get_public_video_url(usage_id: UsageKey) -> str:
    """
    Returns the public video url
    """
    return fr'{settings.LMS_ROOT_URL}/videos/{str(usage_id)}'


@staticmethod
def is_public_sharing_enabled(usage_key: UsageKey, public_access: bool) -> bool:
    """
    Check if public sharing is enabled for a video.

    Args:
        usage_key: The usage key of the video block
        public_access: Whether the video block has public access enabled
    """
    if not usage_key.context_key.is_course:
        return False  # Only courses support this feature (not libraries)

    try:
        # Video share feature must be enabled for sharing settings to take effect
        feature_enabled = PUBLIC_VIDEO_SHARE.is_enabled(usage_key.context_key)
    except Exception as err:  # pylint: disable=broad-except  # noqa: F841
        log.exception(f"Error retrieving course for course ID: {usage_key.context_key}")
        return False

    if not feature_enabled:
        return False

    # Check if the course specifies a general setting
    course_video_sharing_option = get_course_video_sharing_override(usage_key)

    # Course can override all videos to be shared
    if course_video_sharing_option == COURSE_VIDEO_SHARING_ALL_VIDEOS:
        return True

    # ... or no videos to be shared
    elif course_video_sharing_option == COURSE_VIDEO_SHARING_NONE:
        return False

    # ... or can fall back to per-video setting
    # Equivalent to COURSE_VIDEO_SHARING_PER_VIDEO or None / unset
    else:
        return public_access


@staticmethod
def get_course_video_sharing_override(usage_key: UsageKey) -> str | None:
    """
    Return course video sharing options override
    """
    if not usage_key.context_key.is_course:
        return False  # Only courses support this feature (not libraries)

    try:
        course = get_course_by_id(usage_key.context_key)
        return getattr(course, 'video_sharing_options', None)
    except Exception as err:  # pylint: disable=broad-except  # noqa: F841
        log.exception(f"Error retrieving course for course ID: {usage_key.context_key}")
        return None
