"""
Feature toggles used for effort estimation.
"""

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

WAFFLE_FLAG_NAMESPACE = 'effort_estimation'

# .. toggle_name: effort_estimation.disabled
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: If effort estimations are confusing for a given course (e.g. the course team has added manual
#   estimates), you can turn them off case by case here.
# .. toggle_use_cases: opt_out
# .. toggle_creation_date: 2021-07-27
EFFORT_ESTIMATION_DISABLED_FLAG = CourseWaffleFlag(f'{WAFFLE_FLAG_NAMESPACE}.disabled', __name__)
