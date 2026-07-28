"""
This module contains configuration settings via waffle flags
for the Video Pipeline app.
"""

from edx_toggles.toggles import WaffleFlag

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

# Videos Namespace
WAFFLE_NAMESPACE = 'videos'
LOG_PREFIX = 'Videos: '

# .. toggle_name: videos.deprecate_youtube
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag telling whether youtube is deprecated. When enabled, videos are no longer uploaded
#   to YouTube as part of the video pipeline.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2018-08-03
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/18765
DEPRECATE_YOUTUBE = CourseWaffleFlag(f'{WAFFLE_NAMESPACE}.deprecate_youtube', __name__, LOG_PREFIX)

# .. toggle_name: videos.enable_devstack_video_uploads
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, use Multi-Factor Authentication (MFA) for authenticating to AWS. These short-
#   lived access tokens are well suited for development (probably?). [At the time of annotation, the exact consequences
#   of enabling this feature toggle are uncertain.]
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2020-03-12
# .. toggle_warning: Enabling this feature requires that the ROLE_ARN, MFA_SERIAL_NUMBER, MFA_TOKEN settings are
#   properly defined.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/23375
ENABLE_DEVSTACK_VIDEO_UPLOADS = WaffleFlag(f'{WAFFLE_NAMESPACE}.enable_devstack_video_uploads', __name__, LOG_PREFIX)

ENABLE_VEM_PIPELINE = CourseWaffleFlag(  # pylint: disable=toggle-missing-annotation
    f'{WAFFLE_NAMESPACE}.enable_vem_pipeline', __name__, LOG_PREFIX
)
