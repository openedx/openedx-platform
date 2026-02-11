"""
Feature toggles used across the platform. Toggles should only be added to this module if we don't have a better place
for them. Generally speaking, they should be added to the most appropriate app or repo.
"""
from edx_toggles.toggles import SettingToggle
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag


# .. toggle_name: ENTRANCE_EXAMS
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: Enable entrance exams feature. When enabled, students see an exam xblock as the first unit
#   of the course.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-12-01
# .. toggle_tickets: https://openedx.atlassian.net/browse/SOL-40
ENTRANCE_EXAMS = SettingToggle(
    "ENTRANCE_EXAMS", default=False, module_name=__name__
)

# .. toggle_name: authz.enable_course_authoring
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: This toggle will enable the new openedx-authz authorization engine for course authoring.
# .. toggle_warning: Enabling this toggle will trigger a data migration to move role assignations between the legacy and the openedx-authz system.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2026-02-05
# .. toggle_target_removal_date: 2027-06-09
# .. toggle_tickets: https://github.com/openedx/openedx-platform/issues/37927
AUTHZ_COURSE_AUTHORING_FLAG = CourseWaffleFlag('authz.enable_course_authoring', __name__)


def enable_authz_course_authoring(course_key):
    """
    Returns a boolean if the AuthZ for course authoring feature is enabled for the given course.
    """
    return AUTHZ_COURSE_AUTHORING_FLAG.is_enabled(course_key)
