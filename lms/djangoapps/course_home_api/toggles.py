"""
Toggles for course home experience.
"""

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag
from openedx.core.lib.cache_utils import request_cached
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment

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


# Waffle flag to enable audit learner preview of course structure visible to verified learners.
#
# .. toggle_name: course_home.audit_learner_verified_preview
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Where enabled, audit learners can see the presence of the sections / units
#    otherwise restricted to verified learners. The content itself remains inaccessible.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2025-11-07
# .. toggle_target_removal_date: None
COURSE_HOME_AUDIT_LEARNER_VERIFIED_PREVIEW = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.audit_learner_verified_preview', __name__
)


# Waffle flag to run the CourseHomeMetadataAPIRenderStarted filter in the course home metadata API.
#
# .. toggle_name: course_home.course_metadata_render_filter
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, the course home metadata API runs the CourseHomeMetadataAPIRenderStarted
#   filter, so plugins can add fields to the metadata. When disabled, the API returns the metadata without
#   running the filter.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2026-11-02
# .. toggle_target_removal_date: 2027-01-27
# .. toggle_tickets: LP-1415
COURSE_HOME_COURSE_METADATA_RENDER_FILTER = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.course_metadata_render_filter', __name__
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


def audit_learner_verified_preview_is_enabled(course_key):
    """
    Returns True if the audit learner verified preview feature is enabled for a given course.
    """
    return COURSE_HOME_AUDIT_LEARNER_VERIFIED_PREVIEW.is_enabled(course_key)


@request_cached()
def learner_can_preview_verified_content(course_key, user):
    """
    Determine if an audit learner can preview verified content in a course.

    Args:
        course_key: The course identifier.
        user: The user object
    Returns:
        True if the learner can preview verified content, False otherwise.
    """
    # To preview verified content, the feature must be enabled for the course...
    feature_enabled = audit_learner_verified_preview_is_enabled(course_key)
    if not feature_enabled:
        return False

    # ... the course must have a verified mode
    course_has_verified_mode = CourseMode.verified_mode_for_course(course_key)
    if not course_has_verified_mode:
        return False

    # ... and the user must be enrolled as audit
    enrollment = CourseEnrollment.get_enrollment(user, course_key)
    user_enrolled_as_audit = enrollment is not None and enrollment.mode == CourseMode.AUDIT
    if not user_enrolled_as_audit:
        return False

    return True
