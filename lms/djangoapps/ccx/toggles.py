"""
Toggles for the CCX Coach experience.
"""

from django.conf import settings

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

WAFFLE_FLAG_NAMESPACE = 'ccx'

# .. toggle_name: ccx.legacy_ccx_coach_dashboard
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Serves the legacy Django CCX Coach dashboard instead of the CCX Coach
#   experience in the Instructor Dashboard MFE. By default the MFE is used: the CCX Coach course tab
#   links to it and the legacy dashboard view redirects there. Enable this flag for a course to opt
#   that course back out to the legacy dashboard. Mirrors the equivalent
#   `instructor.legacy_instructor_dashboard` flag. Note that a waffle flag cannot default to on, so
#   an opt-out is expressed by making the enabled state mean "use legacy". Regardless of this flag,
#   the legacy dashboard is still served when CCX_COACH_MICROFRONTEND_URL is not configured, so the
#   MFE is never linked to at an unusable URL.
# .. toggle_use_cases: opt_out, temporary
# .. toggle_creation_date: 2026-09-23
# .. toggle_target_removal_date: None
# .. toggle_tickets: https://github.com/openedx/openedx-platform/issues/39142
LEGACY_CCX_COACH_DASHBOARD = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.legacy_ccx_coach_dashboard', __name__
)


def legacy_ccx_coach_dashboard(course_key):
    """
    Return whether the legacy CCX Coach dashboard is enabled for the course.

    Arguments:
        course_key (CourseKey): the master course or CCX course key.

    Returns:
        bool
    """
    return LEGACY_CCX_COACH_DASHBOARD.is_enabled(course_key)


def use_ccx_coach_mfe(course_key):
    """
    Return whether the CCX Coach MFE should serve the given course.

    ``True`` unless the course has opted out via
    :data:`LEGACY_CCX_COACH_DASHBOARD`. Also ``False`` when
    ``CCX_COACH_MICROFRONTEND_URL`` is not configured, which keeps the legacy
    dashboard in play rather than routing to an unusable URL.

    Arguments:
        course_key (CourseKey): the master course or CCX course key.

    Returns:
        bool
    """
    if not settings.CCX_COACH_MICROFRONTEND_URL:
        return False
    return not legacy_ccx_coach_dashboard(course_key)
