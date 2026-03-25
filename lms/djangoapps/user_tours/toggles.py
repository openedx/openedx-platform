"""
Toggles for the User Tours Experience.
"""

from edx_toggles.toggles import WaffleFlag  # noqa: I001

# .. toggle_name: user_tours.tours_disabled
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: This flag disables user tours in LMS.
# .. toggle_warnings: None
# .. toggle_use_cases: opt_out
# .. toggle_creation_date: 2024-02-06
# .. toggle_target_removal_date: None
USER_TOURS_DISABLED = WaffleFlag('user_tours.tours_disabled', module_name=__name__, log_prefix='user_tours')
