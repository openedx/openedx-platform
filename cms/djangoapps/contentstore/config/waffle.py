"""
This module contains various configuration settings via
waffle switches for the contentstore app.
"""


from edx_toggles.toggles import WaffleSwitch

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

# Namespace
WAFFLE_NAMESPACE = 'studio'
LOG_PREFIX = 'Studio: '

# Switches
ENABLE_ACCESSIBILITY_POLICY_PAGE = WaffleSwitch(  # pylint: disable=toggle-missing-annotation
    f'{WAFFLE_NAMESPACE}.enable_policy_page', __name__
)

# TODO: After removing this flag, add a migration to remove waffle flag in a follow-up deployment.
ENABLE_CHECKLISTS_QUALITY = CourseWaffleFlag(  # pylint: disable=toggle-missing-annotation
    f'{WAFFLE_NAMESPACE}.enable_checklists_quality', __name__, LOG_PREFIX
)


# .. toggle_name: studio.custom_relative_dates
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to enable custom pacing input for Personalized Learner Schedule (PLS).
# ..    This flag guards an input in Studio for a self paced course, where the user can enter date offsets
# ..    for a subsection.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2021-07-12
# .. toggle_target_removal_date: 2021-12-31
# .. toggle_warning: Flag course_experience.relative_dates should also be active for relative dates functionalities to work.
# .. toggle_tickets: https://openedx.atlassian.net/browse/AA-844
CUSTOM_RELATIVE_DATES = CourseWaffleFlag(f'{WAFFLE_NAMESPACE}.custom_relative_dates', __name__)
