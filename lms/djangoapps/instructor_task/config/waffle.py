"""
This module contains various configuration settings via
waffle switches for the instructor_task app.
"""

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag


WAFFLE_NAMESPACE = 'instructor_task'

# Course override flags
GENERATE_PROBLEM_GRADE_REPORT_VERIFIED_ONLY = CourseWaffleFlag(  # lint-amnesty, pylint: disable=toggle-missing-annotation
    f'{WAFFLE_NAMESPACE}.generate_problem_grade_report_verified_only', __name__
)

GENERATE_COURSE_GRADE_REPORT_VERIFIED_ONLY = CourseWaffleFlag(  # lint-amnesty, pylint: disable=toggle-missing-annotation
    f'{WAFFLE_NAMESPACE}.generate_course_grade_report_verified_only', __name__
)

# .. toggle_name: instructor_task.use_on_disk_grade_reporting
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: When generating grade reports, write chunks to disk to avoid out of memory errors.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2022-12-01
# .. toggle_target_removal_date: 2022-01-10
# .. toggle_tickets: AU-926
USE_ON_DISK_GRADE_REPORTING = CourseWaffleFlag(
    f'{WAFFLE_NAMESPACE}.use_on_disk_grade_reporting', __name__
)


def problem_grade_report_verified_only(course_id):
    """
    Returns True if problem grade reports should only
    return rows for verified students in the given course,
    False otherwise.
    """
    return GENERATE_PROBLEM_GRADE_REPORT_VERIFIED_ONLY.is_enabled(course_id)


def course_grade_report_verified_only(course_id):
    """
    Returns True if problem grade reports should only
    return rows for verified students in the given course,
    False otherwise.
    """
    return GENERATE_COURSE_GRADE_REPORT_VERIFIED_ONLY.is_enabled(course_id)


def use_on_disk_grade_reporting(course_id):
    """
    Returns True if problem grade reports should write
    chunks to disk rather than holding all in memory.
    False otherwise.
    """
    return USE_ON_DISK_GRADE_REPORTING.is_enabled(course_id)
