"""
Provides Python APIs exposed from Student models.
"""
import datetime
import logging
from zoneinfo import ZoneInfo


from common.djangoapps.student.models import CourseAccessRole as _CourseAccessRole
from common.djangoapps.student.models import CourseEnrollment as _CourseEnrollment
from common.djangoapps.student.models import ManualEnrollmentAudit as _ManualEnrollmentAudit
from common.djangoapps.student.models import (
    ENROLLED_TO_ENROLLED as _ENROLLED_TO_ENROLLED,
    ENROLLED_TO_UNENROLLED as _ENROLLED_TO_UNENROLLED,
    UNENROLLED_TO_ENROLLED as _UNENROLLED_TO_ENROLLED,
    UNENROLLED_TO_UNENROLLED as _UNENROLLED_TO_UNENROLLED,
    UNENROLLED_TO_ALLOWEDTOENROLL as _UNENROLLED_TO_ALLOWEDTOENROLL,
    ALLOWEDTOENROLL_TO_ENROLLED as _ALLOWEDTOENROLL_TO_ENROLLED,
    ALLOWEDTOENROLL_TO_UNENROLLED as _ALLOWEDTOENROLL_TO_UNENROLLED,
    DEFAULT_TRANSITION_STATE as _DEFAULT_TRANSITION_STATE,
)
from common.djangoapps.student.models import PendingNameChange as _PendingNameChange
from common.djangoapps.student.models import UserProfile as _UserProfile

# This is done so that if these strings change within the app, we can keep exported constants the same
ENROLLED_TO_ENROLLED = _ENROLLED_TO_ENROLLED
ENROLLED_TO_UNENROLLED = _ENROLLED_TO_UNENROLLED
UNENROLLED_TO_ENROLLED = _UNENROLLED_TO_ENROLLED
UNENROLLED_TO_UNENROLLED = _UNENROLLED_TO_UNENROLLED
UNENROLLED_TO_ALLOWEDTOENROLL = _UNENROLLED_TO_ALLOWEDTOENROLL
ALLOWEDTOENROLL_TO_ENROLLED = _ALLOWEDTOENROLL_TO_ENROLLED
ALLOWEDTOENROLL_TO_UNENROLLED = _ALLOWEDTOENROLL_TO_UNENROLLED
DEFAULT_TRANSITION_STATE = _DEFAULT_TRANSITION_STATE
log = logging.getLogger(__name__)


def create_manual_enrollment_audit(  # lint-amnesty, pylint: disable=missing-function-docstring
    enrolled_by,
    user_email,
    state_transition,
    reason,
    course_enrollment
):
    _ManualEnrollmentAudit.create_manual_enrollment_audit(
        user=enrolled_by,
        email=user_email,
        state_transition=state_transition,
        reason=reason,
        enrollment=course_enrollment,
    )


def get_course_enrollment(user, course_run_key):
    return _CourseEnrollment.get_enrollment(user, course_run_key)


def get_phone_number(user_id):
    """
    Get a user's phone number from the profile, if
    one exists. Otherwise, return None.
    """
    try:
        student = _UserProfile.objects.get(user_id=user_id)
    except _UserProfile.DoesNotExist as exception:
        log.exception(exception)
        return None
    return student.phone_number or None


def get_name(user_id):
    """
    Get a user's name from their profile, if one exists. Otherwise, return None.
    """
    try:
        student = _UserProfile.objects.get(user_id=user_id)
    except _UserProfile.DoesNotExist:
        log.exception(f'Could not find UserProfile for id {user_id}')
        return None
    return student.name or None


def get_course_access_role(user, org, course_id, role):
    """
    Get a specific CourseAccessRole object. Return None if
    it does not exist.

    Arguments:
        user: User object for the user who has access in a course
        org: the org the course is in
        course_id: the course_id of the CourseAccessRole
        role: the role type of the role
    """
    try:
        course_access_role = _CourseAccessRole.objects.get(
            user=user,
            org=org,
            course_id=course_id,
            role=role,
        )
    except _CourseAccessRole.DoesNotExist:
        log.exception('No CourseAccessRole found for user_id=%(user_id)s, org=%(org)s, '
                      'course_id=%(course_id)s, and role=%(role)s.', {
                          'user': user.id,
                          'org': org,
                          'course_id': course_id,
                          'role': role,
                      })
        return None
    return course_access_role


def get_pending_name_change(user):
    """
    Return the user's pending name change, or None if it does not exist.
    """
    try:
        pending_name_change = _PendingNameChange.objects.get(user=user)
        return pending_name_change
    except _PendingNameChange.DoesNotExist:
        return None


def do_name_change_request(user, new_name, rationale):
    """
    Create a name change request. This either updates the user's current PendingNameChange, or creates
    a new one if it doesn't exist. Returns the PendingNameChange object and a boolean describing whether
    or not a new one was created.
    """
    user_profile = _UserProfile.objects.get(user=user)
    if user_profile.name == new_name:
        log_msg = (
            'user_id={user_id} requested a name change, but the requested name is the same as'
            'their current profile name. Not taking any action.'.format(user_id=user.id)
        )
        log.warning(log_msg)
        return None, False

    pending_name_change, created = _PendingNameChange.objects.update_or_create(
        user=user,
        defaults={
            'new_name': new_name,
            'rationale': rationale
        }
    )

    return pending_name_change, created


def confirm_name_change(user, pending_name_change):
    """
    Confirm a pending name change. This updates the user's profile name and deletes the
    PendingNameChange object.
    """
    user_profile = _UserProfile.objects.get(user=user)

    # Store old name in profile metadata
    meta = user_profile.get_meta()
    if 'old_names' not in meta:
        meta['old_names'] = []
    meta['old_names'].append(
        [user_profile.name, pending_name_change.rationale, datetime.datetime.now(ZoneInfo("UTC")).isoformat()]
    )
    user_profile.set_meta(meta)

    user_profile.name = pending_name_change.new_name
    user_profile.save()
    pending_name_change.delete()
