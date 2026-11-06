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
