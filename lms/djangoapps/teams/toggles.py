"""
Togglable settings for Teams behavior
"""
from edx_toggles.toggles import SettingToggle

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

# Course Waffle inherited from edx/edx-ora2
WAFFLE_NAMESPACE = "openresponseassessment"
TEAM_SUBMISSIONS_FLAG = "team_submissions"

# .. toggle_name: ENABLE_ORA_TEAM_SUBMISSIONS
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: Set to True to enable team-based ORA submissions.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2020-03-03
# .. toggle_target_removal_date: None
# .. toggle_tickets: https://openedx.atlassian.net/browse/EDUCATOR-4951
# .. toggle_warning: This temporary feature toggle does not have a target removal date. This can be overridden by a
#      course waffle flags or a waffle switch with identical name.
# TODO: this should be moved to edx/edx-ora2
TEAM_SUBMISSIONS_FEATURE = SettingToggle(
    "ENABLE_ORA_TEAM_SUBMISSIONS", default=False, module_name=__name__
)


def are_team_submissions_enabled(course_key):
    """
    Checks to see if the CourseWaffleFlag or Django setting for team submissions is enabled
    """
    if CourseWaffleFlag(f'{WAFFLE_NAMESPACE}.{TEAM_SUBMISSIONS_FLAG}', __name__).is_enabled(
        course_key
    ):
        return True

    # TODO: this behaviour differs from edx-ora2, where the WaffleSwitch overrides the setting.
    # https://github.com/openedx/edx-ora2/blob/ac502d8301cb987c9885aaefbaeddaf456c13fb9/openassessment/xblock/config_mixin.py#L96

    if TEAM_SUBMISSIONS_FEATURE.is_enabled():
        return True

    return False
