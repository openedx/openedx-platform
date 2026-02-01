"""
This module contains various configuration settings via
waffle switches for the discussions app.
"""
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

WAFFLE_FLAG_NAMESPACE = "discussions"

# .. toggle_name: discussions.override_discussion_legacy_settings
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to override visibility of discussion settings for legacy experience.
# .. toggle_use_cases: temporary, open_edx
# .. toggle_creation_date: 2021-06-15
# .. toggle_target_removal_date: 2021-12-31
# .. toggle_warning: When the flag is ON, the discussion settings will be available on legacy experience.
# .. toggle_tickets: TNL-8389
OVERRIDE_DISCUSSION_LEGACY_SETTINGS_FLAG = CourseWaffleFlag(
    f"{WAFFLE_FLAG_NAMESPACE}.override_discussion_legacy_settings", __name__
)

# .. toggle_name: discussions.enable_new_structure_discussions
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to toggle on the new structure for in context discussions
# .. toggle_use_cases: temporary, open_edx
# .. toggle_creation_date: 2022-02-22
# .. toggle_target_removal_date: 2022-12-22
ENABLE_NEW_STRUCTURE_DISCUSSIONS = CourseWaffleFlag(
    f"{WAFFLE_FLAG_NAMESPACE}.enable_new_structure_discussions", __name__
)
