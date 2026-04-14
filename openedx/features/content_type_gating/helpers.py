"""
Helper functions used by both content_type_gating and course_duration_limits.
"""

import logging

from django.utils import timezone

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.role_helpers import has_staff_roles
from openedx.core.djangoapps.config_model_utils.utils import is_in_holdback
from xmodule.partitions.partitions import Group  # lint-amnesty, pylint: disable=wrong-import-order

# Studio generates partition IDs starting at 100. There is already a manually generated
# partition for Enrollment Track that uses ID 50, so we'll use 51.
CONTENT_GATING_PARTITION_ID = 51

CONTENT_TYPE_GATE_GROUP_IDS = {
    'limited_access': 1,
    'full_access': 2,
}
LIMITED_ACCESS = Group(CONTENT_TYPE_GATE_GROUP_IDS['limited_access'], 'Limited-access Users')
FULL_ACCESS = Group(CONTENT_TYPE_GATE_GROUP_IDS['full_access'], 'Full-access Users')
LOG = logging.getLogger(__name__)


def correct_modes_for_fbe(course_key=None, enrollment=None, user=None, course=None):
    """
    If CONTENT_TYPE_GATING is enabled use the following logic to determine whether
    enabled_for_enrollment should be false
    """
    if course_key is None and course is None:
        return True

    # Separate these two calls to help with cache hits (most modes_for_course callers pass in a positional course key)
    if course:
        modes = CourseMode.modes_for_course(course=course, include_expired=True, only_selectable=False)
    else:
        modes = CourseMode.modes_for_course(course_key, include_expired=True, only_selectable=False)

    modes_dict = {mode.slug: mode for mode in modes}
    course_key = course_key or course.id

    # If there is no audit mode or no verified mode, FBE will not be enabled
    if (CourseMode.AUDIT not in modes_dict) or (CourseMode.VERIFIED not in modes_dict):
        return False

    if enrollment and user:
        mode_slug = enrollment.mode
        if enrollment.is_active:
            course_mode = CourseMode.mode_for_course(
                course_key,
                mode_slug,
                modes=modes,
            )
            if course_mode is None:
                LOG.error(
                    "User %s is in an unknown CourseMode '%s'"
                    " for course %s. Granting full access to content for this user",
                    user.username,
                    mode_slug,
                    course_key,
                )
                return False

            if mode_slug != CourseMode.AUDIT:
                return False
    return True


def has_full_access_role_in_masquerade(user, course_key):
    """
    The roles of the masquerade user are used to determine whether the content gate displays.

    Returns:
        True if we are masquerading as a full-access generic user
        False if we are masquerading as a non-full-access generic user
        None if we are not masquerading or masquerading as a specific student that should go through normal checks
    """
    # The masquerade module imports from us, so avoid a circular dependency here
    from lms.djangoapps.courseware.masquerade import (
        get_course_masquerade,
        is_masquerading_as_full_access,
        is_masquerading_as_non_audit_enrollment,
        is_masquerading_as_specific_student,
        is_masquerading_as_staff,
    )

    course_masquerade = get_course_masquerade(user, course_key)
    if not course_masquerade or is_masquerading_as_specific_student(user, course_key):
        return None
    return (is_masquerading_as_staff(user, course_key) or
            is_masquerading_as_full_access(user, course_key, course_masquerade) or
            is_masquerading_as_non_audit_enrollment(user, course_key, course_masquerade))


def enrollment_date_for_fbe(user, course_key=None, course=None):
    """
    Gets the enrollment date for the given user and course, if FBE is enabled.

    One of course_key or course must be provided.

    Returns:
        None if FBE is disabled for either this user or course
        The enrollment creation date if an enrollment exists
        now() if no enrollment.
    """
    if user is None or (course_key is None and course is None):
        raise ValueError('Both user and either course_key or course must be specified if no enrollment is provided')

    course_key = course_key or course.id

    full_access_masquerade = has_full_access_role_in_masquerade(user, course_key)
    if full_access_masquerade:
        return None
    elif full_access_masquerade is False:
        user = None  # we are masquerading as a generic user, not a specific one -- avoid all user checks below

    if user and user.id and has_staff_roles(user, course_key):
        return None

    enrollment = user and CourseEnrollment.get_enrollment(user, course_key, ['fbeenrollmentexclusion'])

    if is_in_holdback(enrollment):
        return None

    if not correct_modes_for_fbe(enrollment=enrollment, user=user, course_key=course_key, course=course):
        return None

    # If the user isn't enrolled, act as if the user enrolled today
    return enrollment.created if enrollment else timezone.now()
