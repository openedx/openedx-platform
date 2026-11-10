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
# .. toggle_target_removal_date: 2027-04-01
# .. toggle_warning: Requires CCX_COACH_MICROFRONTEND_URL to be set for the MFE to be reachable.
#   That setting defaults to None, so when it is unset the legacy dashboard is served regardless of
#   this flag.
# .. toggle_tickets: https://github.com/openedx/openedx-platform/issues/39142, https://github.com/openedx/openedx-platform/issues/38432
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


# .. toggle_name: ccx.v0_course_modules_use_schedule
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Makes the ``course_modules`` field of the CCX REST API v0 read and write the
#   CCX schedule (the ``visible_to_staff_only`` overrides that decide what learners see, and that the
#   coach dashboard and CCX Coach API v2 edit) instead of the ``CustomCourseForEdX.structure_json``
#   list, which nothing uses to decide what learners see. With the flag on, v0 also rejects chapters
#   that are visible to staff only in the master course, accepts ``?dry_run=true`` on POST and PATCH,
#   returns an ``ETag`` and honours ``If-Match`` on the CCX detail endpoint, and treats
#   ``"course_modules": null`` on PATCH as "no change". ``structure_json`` is still written so that
#   turning the flag off restores the old responses. Keyed on the master course, so it can be rolled
#   out per course or per organization.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2026-11-10
# .. toggle_target_removal_date: 2027-05-01
# .. toggle_warning: Turning the flag on for a course changes what v0 GET reports for its CCXs.
#   Run the ``ccx_course_modules_drift`` management command first to see which CCXs will change.
V0_COURSE_MODULES_USE_SCHEDULE = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.v0_course_modules_use_schedule', __name__
)


def v0_course_modules_use_schedule(course_key):
    """
    Return whether the CCX REST API v0 ``course_modules`` field is backed by the CCX schedule.

    Arguments:
        course_key (CourseKey): the master course key.

    Returns:
        bool
    """
    return V0_COURSE_MODULES_USE_SCHEDULE.is_enabled(course_key)
