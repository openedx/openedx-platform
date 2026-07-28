# pylint: disable=unused-import
"""
Python APIs exposed by the student app to other in-process apps.
"""


import logging
from typing import TYPE_CHECKING

from django.conf import settings  # noqa: F401
from django.contrib.auth import get_user_model
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.models_api import ALLOWEDTOENROLL_TO_ENROLLED as _ALLOWEDTOENROLL_TO_ENROLLED
from common.djangoapps.student.models_api import ALLOWEDTOENROLL_TO_UNENROLLED as _ALLOWEDTOENROLL_TO_UNENROLLED
from common.djangoapps.student.models_api import DEFAULT_TRANSITION_STATE as _DEFAULT_TRANSITION_STATE
from common.djangoapps.student.models_api import ENROLLED_TO_ENROLLED as _ENROLLED_TO_ENROLLED
from common.djangoapps.student.models_api import ENROLLED_TO_UNENROLLED as _ENROLLED_TO_UNENROLLED
from common.djangoapps.student.models_api import UNENROLLED_TO_ALLOWEDTOENROLL as _UNENROLLED_TO_ALLOWEDTOENROLL
from common.djangoapps.student.models_api import UNENROLLED_TO_ENROLLED as _UNENROLLED_TO_ENROLLED
from common.djangoapps.student.models_api import UNENROLLED_TO_UNENROLLED as _UNENROLLED_TO_UNENROLLED
from common.djangoapps.student.models_api import create_manual_enrollment_audit as _create_manual_enrollment_audit
from common.djangoapps.student.models_api import get_course_access_role  # noqa: F401
from common.djangoapps.student.models_api import get_course_enrollment as _get_course_enrollment
from common.djangoapps.student.roles import REGISTERED_ACCESS_ROLES as _REGISTERED_ACCESS_ROLES
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole, GlobalStaff
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers  # noqa: F401

if TYPE_CHECKING:
    from django.contrib.auth.models import AnonymousUser, User  # pylint: disable=imported-auth-user
    from django.db.models.query import QuerySet


# This is done so that if these strings change within the app, we can keep exported constants the same
ENROLLED_TO_ENROLLED = _ENROLLED_TO_ENROLLED
ENROLLED_TO_UNENROLLED = _ENROLLED_TO_UNENROLLED
UNENROLLED_TO_ENROLLED = _UNENROLLED_TO_ENROLLED
UNENROLLED_TO_UNENROLLED = _UNENROLLED_TO_UNENROLLED
UNENROLLED_TO_ALLOWEDTOENROLL = _UNENROLLED_TO_ALLOWEDTOENROLL
ALLOWEDTOENROLL_TO_ENROLLED = _ALLOWEDTOENROLL_TO_ENROLLED
ALLOWEDTOENROLL_TO_UNENROLLED = _ALLOWEDTOENROLL_TO_UNENROLLED
DEFAULT_TRANSITION_STATE = _DEFAULT_TRANSITION_STATE

TRANSITION_STATES = (
    ENROLLED_TO_ENROLLED,
    ENROLLED_TO_UNENROLLED,
    UNENROLLED_TO_ENROLLED,
    UNENROLLED_TO_UNENROLLED,
    UNENROLLED_TO_ALLOWEDTOENROLL,
    ALLOWEDTOENROLL_TO_ENROLLED,
    ALLOWEDTOENROLL_TO_UNENROLLED,
    DEFAULT_TRANSITION_STATE,
)

COURSE_DASHBOARD_PLUGIN_VIEW_NAME = "course_dashboard"

log = logging.getLogger()


def create_manual_enrollment_audit(
    enrolled_by,
    user_email,
    transition_state,
    reason,
    course_run_key=None,
):
    """
    Creates an audit item for a manual enrollment.
    Parameters:
        enrolled_by: <auth.User> of the person that is manually enrolling
        user_email: <str> email of the user being enrolled
        transition_state: <str> state of enrollment transition state from _TRANSITIONS_STATES
        reason: <str> Reason why user was manually enrolled
        course_run_key: <str> Used to link the audit enrollment to the actual enrollment

    Note: We purposefully *exclude* passing items like CourseEnrollment objects to prevent callers from needed to
    know about model level code.
    """
    if transition_state not in TRANSITION_STATES:
        raise ValueError(f"State `{transition_state}` not in allow states: `{TRANSITION_STATES}`")

    User = get_user_model()
    try:
        enrolled_user = User.objects.get(email=user_email)
    except User.DoesNotExist:
        enrolled_user = None

    if enrolled_user and course_run_key:
        enrollment = _get_course_enrollment(enrolled_user, course_run_key)
    else:
        enrollment = None

    _create_manual_enrollment_audit(enrolled_by, user_email, transition_state, reason, enrollment)


def get_access_role_by_role_name(role_name):
    """
    Get the concrete child class of the AccessRole abstract class associated with the string role_name
    by looking in REGISTERED_ACCESS_ROLES. If there is no class associated with this name, return None.

    Note that this will only return classes that are registered in _REGISTERED_ACCESS_ROLES.

    Arguments:
        role_name: the name of the role
    """
    return _REGISTERED_ACCESS_ROLES.get(role_name, None)


def is_user_enrolled_in_course(student, course_key):
    """
    Determines if a learner is enrolled in a given course-run.
    """
    log.info(f"Checking if {student.id} is enrolled in course {course_key}")
    return CourseEnrollment.is_enrolled(student, course_key)


def is_user_staff_or_instructor_in_course(user, course_key):
    """
    Determines if a user is an Instructor or part of the given course's course staff.

    Also returns true for GlobalStaff.
    """
    if not isinstance(course_key, CourseKey):
        course_key = CourseKey.from_string(course_key)

    return (
        GlobalStaff().has_user(user)
        or CourseStaffRole(course_key).has_user(user)
        or CourseInstructorRole(course_key).has_user(user)
    )


def get_course_enrollments(
    user: "AnonymousUser | User",
    is_filtered: bool = False,
    course_ids: list[str | None] | None = None,
) -> "QuerySet[CourseEnrollment]":
    """
    Return enrollments for a user, potentially filtered by course_id.

    Because an empty `course_ids` value is a meaningful filter, the easiest way to verify
    that the list should be filtered intentionally is to specify `is_filtered`.

    Arguments:

    * is_filtered (bool): whether or not the list is filtered
    * course_ids (list): a list of course IDs to filter by.
    """
    course_enrollments = CourseEnrollment.enrollments_for_user(user).select_related("course")

    if is_filtered:
        course_enrollments = course_enrollments.filter(course_id__in=course_ids)

    return course_enrollments
