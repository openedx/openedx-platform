"""
Toggles for course home experience.
"""

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

WAFFLE_FLAG_NAMESPACE = 'course_home'

# .. toggle_name: course_home.course_home_mfe_progress_tab
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: This toggle controls the user interface behavior of the progress tab in
#   the Learning Management System. When set to True, the progress tab utilizes the newly introduced
#   Learning MFE graphs. When set to False (default), it utilizes existing grade graph from edx-platform.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2021-03-12
# .. toggle_target_removal_date: 2024-01-01
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/26978
COURSE_HOME_MICROFRONTEND_PROGRESS_TAB = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.course_home_mfe_progress_tab', __name__
)


# Waffle flag to enable new discussion sidebar view on course home page
#
# .. toggle_name: course_home.new_discussion_sidebar_view
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: This toggle controls the user interface behavior of the discussion sidebar on course home page.
# .. toggle_use_cases: open_edx, temporary
# .. toggle_creation_date: 2024-04-22
# .. toggle_target_removal_date: None
# .. toggle_tickets: INF-1338
COURSE_HOME_NEW_DISCUSSION_SIDEBAR_VIEW = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.new_discussion_sidebar_view', __name__
)


# Waffle flag to enable emission of course progress analytics for students in their courses.
#
# .. toggle_name: course_home.send_course_progress_analytics_for_student
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: This toggle controls whether the system will enqueue a Celery task responsible for emitting an
#   analytics events describing how much course content a learner has completed in a course.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2025-04-02
# .. toggle_target_removal_date: None
COURSE_HOME_SEND_COURSE_PROGRESS_ANALYTICS_FOR_STUDENT = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.send_course_progress_analytics_for_student', __name__
)


# Waffle flag to hide the graded problem count suffix (e.g. "(2 Questions)") from sequential names.
#
# .. toggle_name: course_home.disable_sequence_question_count
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, the outline serializer will not append the "(N Questions)" suffix to
#   graded sequential display names.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2026-06-12
# .. toggle_target_removal_date: None
COURSE_HOME_DISABLE_SEQUENCE_QUESTION_COUNT = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.disable_sequence_question_count', __name__
)


def course_home_mfe_progress_tab_is_active(course_key):
    # Avoiding a circular dependency
    from .models import DisableProgressPageStackedConfig
    return (
        not course_key.deprecated and
        COURSE_HOME_MICROFRONTEND_PROGRESS_TAB.is_enabled(course_key) and
        not DisableProgressPageStackedConfig.current(course_key=course_key).disabled
    )


def new_discussion_sidebar_view_is_enabled(course_key):
    """
    Returns True if the new discussion sidebar view is enabled for the given course.
    """
    return COURSE_HOME_NEW_DISCUSSION_SIDEBAR_VIEW.is_enabled(course_key)


def send_course_progress_analytics_for_student_is_enabled(course_key):
    """
    Returns True if the course completion analytics feature is enabled for a given course.
    """
    return COURSE_HOME_SEND_COURSE_PROGRESS_ANALYTICS_FOR_STUDENT.is_enabled(course_key)


def show_sequence_question_count(course_key):
    """
    Returns True if the "(N Questions)" suffix should be appended to sequential names for the given course.
    """
    return not COURSE_HOME_DISABLE_SEQUENCE_QUESTION_COUNT.is_enabled(course_key)
