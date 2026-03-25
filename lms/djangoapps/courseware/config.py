"""
Waffle flags and switches
"""

from edx_toggles.toggles import WaffleSwitch  # noqa: I001

WAFFLE_NAMESPACE = 'courseware'

# .. toggle_name: courseware.enable_new_financial_assistance_flow
# .. toggle_implementation: WaffleSwitch
# .. toggle_default: False
# .. toggle_description: enables new internal only financial assistance flow, when active.
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2022-03-25
# .. toggle_tickets: https://openedx.atlassian.net/browse/PROD-2588
ENABLE_NEW_FINANCIAL_ASSISTANCE_FLOW = WaffleSwitch(
    f"{WAFFLE_NAMESPACE}.enable_new_financial_assistance_flow", __name__
)
