"""
Toggles for the CCX Coach experience.
"""

from django.conf import settings

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

WAFFLE_FLAG_NAMESPACE = 'ccx'

# .. toggle_name: ccx.enable_ccx_coach_mfe
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Routes the CCX Coach experience to the Instructor Dashboard MFE instead of
#   the legacy Django coach dashboard. When enabled, the CCX Coach course tab links to the MFE and
#   the legacy dashboard view redirects there. When disabled (default), the legacy dashboard is
#   served unchanged. This is an opt-in rather than an opt-out — unlike the equivalent
#   `instructor.legacy_instructor_dashboard` flag — because the MFE does not yet implement every
#   legacy capability (notably the student grades page), so operators enable it per course to pilot
#   the new experience. The flag has no effect unless CCX_COACH_MICROFRONTEND_URL is configured.
# .. toggle_use_cases: opt_in, temporary
# .. toggle_creation_date: 2026-09-23
# .. toggle_target_removal_date: None
# .. toggle_tickets: https://github.com/openedx/openedx-platform/issues/39142
ENABLE_CCX_COACH_MFE = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.enable_ccx_coach_mfe', __name__
)


def use_ccx_coach_mfe(course_key):
    """
    Return whether the CCX Coach MFE should serve the given course.

    ``True`` only when the flag is enabled for the course *and*
    ``CCX_COACH_MICROFRONTEND_URL`` is configured; a missing setting keeps the
    legacy dashboard in play rather than redirecting to an unusable URL.

    Arguments:
        course_key (CourseKey): the master course or CCX course key.

    Returns:
        bool
    """
    if not settings.CCX_COACH_MICROFRONTEND_URL:
        return False
    return ENABLE_CCX_COACH_MFE.is_enabled(course_key)
