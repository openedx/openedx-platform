"""
Toggles for course modes experience.
"""

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

WAFFLE_FLAG_NAMESPACE = "course_modes"

# .. toggle_name: course_modes.mfe_track_selection
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, GET /course_modes/choose/ redirects to the Learning MFE track
#   selection page (plugin-owned UI). POST enrollment remains on edx-platform.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2026-07-27
# .. toggle_target_removal_date: 2027-01-27
# .. toggle_tickets: LP-837
COURSE_MODES_MFE_TRACK_SELECTION = CourseWaffleFlag(
    f"{WAFFLE_FLAG_NAMESPACE}.mfe_track_selection", __name__
)

# .. toggle_name: course_modes.track_selection_options
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, the track selection API runs the TrackSelectionAPIRenderStarted
#   filter and picks the options of the page: it adds the options and the program to the page data,
#   reads the upgrade intent, and redirects when one option or none is left. When disabled, the API
#   returns the page data as it did before these were added. It only has an effect where
#   course_modes.mfe_track_selection is enabled.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2026-11-02
# .. toggle_target_removal_date: 2027-01-27
# .. toggle_tickets: LP-1447
COURSE_MODES_TRACK_SELECTION_OPTIONS = CourseWaffleFlag(
    f"{WAFFLE_FLAG_NAMESPACE}.track_selection_options", __name__
)


def course_modes_mfe_track_selection_is_active(course_key):
    return not course_key.deprecated and COURSE_MODES_MFE_TRACK_SELECTION.is_enabled(
        course_key
    )
