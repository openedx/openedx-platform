"""
This module contains various configuration settings via
waffle switches for the teams app.
"""

from edx_toggles.toggles import WaffleFlag  # noqa: I001

# .. toggle_name: teams.enable_teams_app
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to enable teams app for a course
# .. toggle_use_cases: temporary, open_edx
# .. toggle_creation_date: 2021-10-07
# .. toggle_target_removal_date: 2021-11-01
# .. toggle_warning: When the flag is ON, the teams app will be visible in the new course authoring mfe.
# .. toggle_tickets: TNL-8816
ENABLE_TEAMS_APP = WaffleFlag(
    'teams.enable_teams_app',
    __name__,
)
