"""
This module contains various configuration settings via
waffle switches for the Grades app.
"""


from edx_toggles.toggles import WaffleSwitch  # noqa: I001

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

# Namespace
WAFFLE_NAMESPACE = 'grades'
LOG_PREFIX = 'Grades: '

# Switches

# .. toggle_name: grades.disable_regrade_on_policy_change
# .. toggle_implementation: WaffleSwitch
# .. toggle_default: False
# .. toggle_description: When enabled, a change in grading policy will not trigger re-grading.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2017-08-03
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/15733
DISABLE_REGRADE_ON_POLICY_CHANGE = WaffleSwitch(f'{WAFFLE_NAMESPACE}.disable_regrade_on_policy_change', __name__)

# Course Flags

# .. toggle_name: grades.rejected_exam_overrides_grade
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, a rejected exam will trigger a grade override. Note that this flag is not used
#   in edx-platform, but only in edx-proctoring.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2019-05-29
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/20719
# TODO: After removing this flag, add a migration to remove waffle flag in a follow-up deployment.
REJECTED_EXAM_OVERRIDES_GRADE = CourseWaffleFlag(
    f'{WAFFLE_NAMESPACE}.rejected_exam_overrides_grade', __name__, LOG_PREFIX
)
# .. toggle_name: grades.rejected_exam_overrides_grade
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, grades can no longer be updated 30 days after a course has ended. Note that this
#   is only valid for courses which actually have an end date.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2018-10-01
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/19026
# TODO: After removing this flag, add a migration to remove waffle flag in a follow-up deployment.
ENFORCE_FREEZE_GRADE_AFTER_COURSE_END = CourseWaffleFlag(
    f'{WAFFLE_NAMESPACE}.enforce_freeze_grade_after_course_end', __name__, LOG_PREFIX
)

# .. toggle_name: grades.writable_gradebook
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, add GET/POST endpoints for updating gradebook entries in bulk. Also, a link to
#   the writable gradebook is added to the instructor dashboard.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2018-10-03
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/19054
# .. toggle_warning: Enabling this requires that the `WRITABLE_GRADEBOOK_URL` setting be properly defined.
# TODO: After removing this flag, add a migration to remove waffle flag in a follow-up deployment.
WRITABLE_GRADEBOOK = CourseWaffleFlag(f'{WAFFLE_NAMESPACE}.writable_gradebook', __name__, LOG_PREFIX)

# .. toggle_name: grades.bulk_management
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, bulk features are visible for management in masters course. As far
#   as we understand, this feature is now unused and obsolete.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2019-08-20
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/21389
BULK_MANAGEMENT = CourseWaffleFlag(f'{WAFFLE_NAMESPACE}.bulk_management', __name__, LOG_PREFIX)


def is_writable_gradebook_enabled(course_key):
    """
    Returns whether the writable gradebook app is enabled for the given course.
    """
    return WRITABLE_GRADEBOOK.is_enabled(course_key)


def gradebook_bulk_management_enabled(course_key):
    """
    Returns whether bulk management features should be specially enabled for a given course.
    """
    return BULK_MANAGEMENT.is_enabled(course_key)
