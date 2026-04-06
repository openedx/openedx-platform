"""
Waffle flags for instructor dashboard.
"""

from edx_toggles.toggles import WaffleFlag

# Namespace for instructor waffle flags.
WAFFLE_FLAG_NAMESPACE = 'instructor'

# Waffle flag enable new data download UI on specific course.
# .. toggle_name: instructor.enable_data_download_v2
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: instructor
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2020-07-8
# .. toggle_tickets: PROD-1309
DATA_DOWNLOAD_V2 = WaffleFlag(f'{WAFFLE_FLAG_NAMESPACE}.enable_data_download_v2', __name__)

# .. toggle_name: verify_student.optimised_is_small_course
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Supports staged rollout to improved is_small_course method.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2020-07-02
# .. toggle_warning: Description mentions staged rollout, but the use case is not set as temporary.
#      This may actually be a temporary toggle.
# .. toggle_tickets: PROD-1740
OPTIMISED_IS_SMALL_COURSE = WaffleFlag(f'{WAFFLE_FLAG_NAMESPACE}.optimised_is_small_course', __name__)


def data_download_v2_is_enabled():
    """
    check if data download v2 is enabled.
    """
    return DATA_DOWNLOAD_V2.is_enabled()


def use_optimised_is_small_course():
    return OPTIMISED_IS_SMALL_COURSE.is_enabled()
