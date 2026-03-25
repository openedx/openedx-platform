"""
This module contains various configuration settings via
waffle switches for the learner_dashboard app.
"""

from edx_toggles.toggles import WaffleFlag  # noqa: I001

# .. toggle_name: learner_dashboard.enable_program_tab_view
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to enable new Program discussion experience in tab view for course.
#    This flag is used to decide whether we need to render program data in "tab" view or simple view.
#    In the new tab view, we have tabs like "journey", "live", "discussions"
# .. toggle_use_cases: temporary, open_edx
# .. toggle_creation_date: 2021-08-25
# .. toggle_target_removal_date: 2021-12-31
# .. toggle_warning: When the flag is ON, the new experience for Program discussions will be enabled.
# .. toggle_tickets: TNL-8434
ENABLE_PROGRAM_TAB_VIEW = WaffleFlag(
    'learner_dashboard.enable_program_tab_view',
    __name__,
)


# .. toggle_name: learner_dashboard.enable_masters_program_tab_view
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to enable new Masters Program discussion experience for masters program.
#    This flag is used to decide whether we need to render master program data in "tab" view or simple view.
#    In the new tab view, we have tabs like "journey", "live", "discussions"
# .. toggle_use_cases: temporary, open_edx
# .. toggle_creation_date: 2021-10-19
# .. toggle_target_removal_date: 2021-12-31
# .. toggle_warning: When the flag is ON, the new tabbed experience for Master Program Page will be enabled.
# .. toggle_tickets: TNL-8434
ENABLE_MASTERS_PROGRAM_TAB_VIEW = WaffleFlag(
    'learner_dashboard.enable_masters_program_tab_view',
    __name__,
)
