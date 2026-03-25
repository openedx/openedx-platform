"""
Video config toggles
"""
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag  # noqa: I001

WAFFLE_FLAG_NAMESPACE = 'video_config'

# .. toggle_name: video_config.public_video_share
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Gates access to the public social sharing video feature.
# .. toggle_use_cases: temporary, opt_in
# .. toggle_creation_date: 2023-02-02
# .. toggle_target_removal_date: None
PUBLIC_VIDEO_SHARE = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.public_video_share', __name__
)

# .. toggle_name: video_config.transcript_feedback
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Gates access to the transcript feedback widget feature.
# .. toggle_use_cases: temporary, opt_in
# .. toggle_creation_date: 2023-05-10
# .. toggle_target_removal_date: None
TRANSCRIPT_FEEDBACK = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.transcript_feedback', __name__
)

# .. toggle_name: video_config.xpert_translations_ui
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Gates access to the Xpert Translations UI feature.
# .. toggle_use_cases: temporary, opt_in
# .. toggle_creation_date: 2023-10-11
# .. toggle_target_removal_date: None
XPERT_TRANSLATIONS_UI = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.xpert_translations_ui', __name__
)


def use_xpert_translations_component(course_key):
    """
    Returns a boolean if xpert translations ui component is enabled
    """
    return XPERT_TRANSLATIONS_UI.is_enabled(course_key)
