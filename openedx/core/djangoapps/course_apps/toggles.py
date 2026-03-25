"""
Toggles for course apps.
"""
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag  # noqa: I001

#: Namespace for use by course apps for creating availability toggles
COURSE_APPS_WAFFLE_NAMESPACE = 'course_apps'

# .. toggle_name: course_apps.exams_ida
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Uses exams IDA
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2022-07-27
# .. toggle_target_removal_date: None
# .. toggle_tickets: MST-1520
EXAMS_IDA = CourseWaffleFlag(
    f'{COURSE_APPS_WAFFLE_NAMESPACE}.exams_ida', __name__
)


def exams_ida_enabled(course_key):
    """
    Returns a boolean if exams ida view is enabled for a course.
    """
    return EXAMS_IDA.is_enabled(course_key)
