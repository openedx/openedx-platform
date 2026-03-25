"""
Waffle flags and switches
"""


from edx_toggles.toggles import WaffleSwitch  # noqa: I001

# .. toggle_name: open_edx_util.display_maintenance_warning
# .. toggle_implementation: WaffleSwitch
# .. toggle_default: False
# .. toggle_description: Displays the maintenance warning, when active.
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2018-03-20
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/17735
DISPLAY_MAINTENANCE_WARNING = WaffleSwitch(
    'open_edx_util.display_maintenance_warning', __name__
)
