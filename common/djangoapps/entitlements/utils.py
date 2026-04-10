"""
Utility methods for the entitlement application.
"""


import logging

from django.utils import timezone

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.enrollments.api import update_enrollment

log = logging.getLogger("common.entitlements.utils")


def is_course_run_entitlement_fulfillable(
        course_run_key,
        entitlement,
        compare_date=timezone.now(),  # noqa: B008
):
    """
    Checks that the current run meets the following criteria for an entitlement

    1) A User can enroll in or is currently enrolled
    2) A User can upgrade to the entitlement mode

    Arguments:
        course_run_key (CourseKey): The id of the Course run that is being checked.
        entitlement: The Entitlement that we are checking against.
        compare_date: The date and time that we are comparing against.  Defaults to timezone.now()

    Returns:
        bool: True if the Course Run is fullfillable for the CourseEntitlement.
    """
    try:
        course_overview = CourseOverview.get_from_id(course_run_key)
    except CourseOverview.DoesNotExist:
        log.error(('There is no CourseOverview entry available for {course_run_id}, '  # noqa: UP032
                   'course run cannot be applied to entitlement').format(
            course_run_id=str(course_run_key)
        ))
        return False

    # Verify that the course run can currently be enrolled
    enrollment_start = course_overview.enrollment_start
    enrollment_end = course_overview.enrollment_end
    can_enroll = (
        (not enrollment_start or enrollment_start < compare_date)
        and (not enrollment_end or enrollment_end > compare_date)
    )

    # Is the user already enrolled in the Course Run
    is_enrolled = CourseEnrollment.is_enrolled(entitlement.user, course_run_key)

    # Ensure the course run is upgradeable and the mode matches the entitlement's mode
    unexpired_paid_modes = [mode.slug for mode in CourseMode.paid_modes_for_course(course_run_key)]
    can_upgrade = unexpired_paid_modes and entitlement.mode in unexpired_paid_modes

    return course_overview.start and can_upgrade and (is_enrolled or can_enroll)


def revoke_entitlements_and_downgrade_courses_to_audit(course_entitlements, username, awarded_cert_course_ids,
                                                       revocable_entitlement_uuids):
    """
    This method expires the entitlements for provided course_entitlements and also moves the enrollments
    to audit for the course entitlements which have not been completed yet(not a part of the provided exclusion_list).
    """

    log.info('B2C_SUBSCRIPTIONS: Starting revoke_entitlements_and_downgrade_courses_to_audit for '
             'user: [%s], course_entitlements_uuids: %s, awarded_cert_course_ids: %s',
             username,
             revocable_entitlement_uuids,
             awarded_cert_course_ids)
    for course_entitlement in course_entitlements:
        if course_entitlement.enrollment_course_run is None:
            if course_entitlement.expired_at is None:
                course_entitlement.expire_entitlement()
        elif str(course_entitlement.enrollment_course_run.course_id) not in awarded_cert_course_ids:
            course_id = course_entitlement.enrollment_course_run.course_id
            enrollment_mode = course_entitlement.enrollment_course_run.mode
            username = course_entitlement.enrollment_course_run.user.username
            if enrollment_mode == CourseMode.VERIFIED:
                course_entitlement.set_enrollment(None)
                if course_entitlement.expired_at is None:
                    course_entitlement.expire_entitlement()
                update_enrollment(username, str(course_id), CourseMode.AUDIT, include_expired=True)
            else:
                log.warning('B2C_SUBSCRIPTIONS: Enrollment mode mismatch for user: %s and course_id: %s',
                            username,
                            course_id)
    log.info('B2C_SUBSCRIPTIONS: Completed revoke_entitlements_and_downgrade_courses_to_audit for '
             'user: [%s], course_entitlements_uuids: %s, awarded_cert_course_ids: %s',
             username,
             revocable_entitlement_uuids,
             awarded_cert_course_ids)
