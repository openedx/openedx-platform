"""
Toggles for content tagging
"""

from edx_toggles.toggles import WaffleFlag

# .. toggle_name: content_tagging.disabled
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Setting this disables the tagging feature
# .. toggle_type: feature_flag
# .. toggle_category: admin
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2024-04-30
DISABLE_TAGGING_FEATURE = WaffleFlag('content_tagging.disabled', __name__)


def is_tagging_feature_disabled():
    """
    Returns a boolean if tagging feature list page is disabled
    """
    return DISABLE_TAGGING_FEATURE.is_enabled()
