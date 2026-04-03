"""
Simple utility functions for computing access.
It allows us to share code between access.py and block transformers.
"""

from datetime import datetime, timedelta
from logging import getLogger

from crum import get_current_request
from django.conf import settings
from enterprise.models import EnterpriseCourseEnrollment, EnterpriseCustomerUser
from pytz import UTC

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseBetaTesterRole
from lms.djangoapps.courseware.access_response import (
    AccessResponse,
    AuthenticationRequiredAccessError,
    DataSharingConsentRequiredAccessError,
    EnrollmentRequiredAccessError,
    IncorrectActiveEnterpriseAccessError,
    StartDateEnterpriseLearnerError,
    StartDateError,
)
from lms.djangoapps.courseware.masquerade import get_course_masquerade, is_masquerading_as_student
from openedx.features.course_experience import (
    COURSE_ENABLE_UNENROLLED_ACCESS_FLAG,
    COURSE_PRE_START_ACCESS_FLAG,
    ENFORCE_MASQUERADE_START_DATES,
)
from xmodule.course_block import COURSE_VISIBILITY_PUBLIC  # lint-amnesty, pylint: disable=wrong-import-order

DEBUG_ACCESS = False
log = getLogger(__name__)

ACCESS_GRANTED = AccessResponse(True)
ACCESS_DENIED = AccessResponse(False)


def debug(*args, **kwargs):
    """
    Helper function for local debugging.
    """
    # to avoid overly verbose output, this is off by default
    if DEBUG_ACCESS:
        log.debug(*args, **kwargs)


def adjust_start_date(user, days_early_for_beta, start, course_key):
    """
    If user is in a beta test group, adjust the start date by the appropriate number of
    days.

    Returns:
        A datetime.  Either the same as start, or earlier for beta testers.
    """
    if days_early_for_beta is None:
        # bail early if no beta testing is set up
        return start

    if CourseBetaTesterRole(course_key).has_user(user):
        debug("Adjust start time: user in beta role for %s", course_key)
        # timedelta.max days from now is in the year 2739931, so that's probably pretty safe
        delta = timedelta(min(days_early_for_beta, timedelta.max.days))
        try:
            return start - delta
        except OverflowError:
            return start

    return start


def enterprise_learner_enrolled(request, user, course_key):
    """
    Determine if the learner should be redirected to the enterprise learner portal by checking their enterprise
    memberships/enrollments.  If all of the following are true, then we are safe to redirect the learner:

    * The learner is linked to an enterprise customer,
    * The enterprise customer has subsidized the learner's enrollment in the requested course,
    * The enterprise customer has the learner portal enabled.

    NOTE: This function MUST be called from a view, or it will throw an exception.

    Args:
        request (django.http.HttpRequest): The current request being handled.  Must not be None.
        user (User): The requesting enter, potentially an enterprise learner.
        course_key (str): The requested course to check for enrollment.

    Returns:
        bool: True if the learner is enrolled via a linked enterprise customer and can safely be redirected to the
        enterprise learner dashboard.
    """
    from openedx.features.enterprise_support.api import enterprise_customer_from_session_or_learner_data

    if not user.is_authenticated:
        return False

    # enterprise_customer_data is either None (if learner is not linked to any customer) or a serialized
    # EnterpriseCustomer representing the learner's active linked customer.
    enterprise_customer_data = enterprise_customer_from_session_or_learner_data(request)
    learner_portal_enabled = enterprise_customer_data and enterprise_customer_data["enable_learner_portal"]
    if not learner_portal_enabled:
        return False

    # Additionally make sure the enterprise learner is actually enrolled in the requested course, subsidized
    # via the discovered customer.
    enterprise_enrollments = EnterpriseCourseEnrollment.objects.filter(
        course_id=course_key,
        enterprise_customer_user__user_id=user.id,
        enterprise_customer_user__enterprise_customer__uuid=enterprise_customer_data["uuid"],
    )
    enterprise_enrollment_exists = enterprise_enrollments.exists()
    log.info(
        (
            "[enterprise_learner_enrolled] Checking for an enterprise enrollment for "
            "lms_user_id=%s in course_key=%s via enterprise_customer_uuid=%s. "
            "Exists: %s"
        ),
        user.id,
        course_key,
        enterprise_customer_data["uuid"],
        enterprise_enrollment_exists,
    )
    return enterprise_enrollment_exists


def check_start_date(user, days_early_for_beta, start, course_key, display_error_to_user=True, now=None):
    """
    Verifies whether the given user is allowed access given the
    start date and the Beta offset for the given course.

    Arguments:
        display_error_to_user: If True, display this error to users in the UI.

    Returns:
        AccessResponse: Either ACCESS_GRANTED or StartDateError.
    """
    start_dates_disabled = settings.FEATURES["DISABLE_START_DATES"]
    masquerading_as_student = is_masquerading_as_student(user, course_key)

    if start_dates_disabled and not masquerading_as_student:
        return ACCESS_GRANTED
    else:
        if start is None:
            return ACCESS_GRANTED

        if not ENFORCE_MASQUERADE_START_DATES.is_enabled(course_key) and get_course_masquerade(user, course_key):
            return ACCESS_GRANTED

        if now is None:
            now = datetime.now(UTC)
        effective_start = adjust_start_date(user, days_early_for_beta, start, course_key)

        should_grant_access = now > effective_start
        if should_grant_access:
            return ACCESS_GRANTED

        # Before returning a StartDateError, determine if the learner should be redirected to the enterprise learner
        # portal by returning StartDateEnterpriseLearnerError instead.
        request = get_current_request()
        if request and enterprise_learner_enrolled(request, user, course_key):
            return StartDateEnterpriseLearnerError(start, display_error_to_user=display_error_to_user)

        return StartDateError(start, display_error_to_user=display_error_to_user)


def check_course_open_for_learner(user, course):
    """
    Check if the course is open for learners based on the start date.

    Returns:
        AccessResponse: Either ACCESS_GRANTED or StartDateError.
    """
    if COURSE_PRE_START_ACCESS_FLAG.is_enabled():
        return ACCESS_GRANTED
    return check_start_date(user, course.days_early_for_beta, course.start, course.id)


def check_enrollment(user, course):
    """
    Check if the course requires a learner to be enrolled for access.

    Returns:
        AccessResponse: Either ACCESS_GRANTED or EnrollmentRequiredAccessError.
    """
    if check_public_access(course, [COURSE_VISIBILITY_PUBLIC]):
        return ACCESS_GRANTED

    if CourseEnrollment.is_enrolled(user, course.id):
        return ACCESS_GRANTED

    return EnrollmentRequiredAccessError()


def check_authentication(user, course):
    """
    Grants access if the user is authenticated, or if the course allows public access.

    Returns:
        AccessResponse: Either ACCESS_GRANTED or AuthenticationRequiredAccessError
    """
    if user.is_authenticated:
        return ACCESS_GRANTED

    if check_public_access(course, [COURSE_VISIBILITY_PUBLIC]):
        return ACCESS_GRANTED

    return AuthenticationRequiredAccessError()


def check_public_access(course, visibilities):
    """
    This checks if the unenrolled access waffle flag for the course is set
    and the course visibility matches any of the input visibilities.

    The "visibilities" argument is one of these constants from xmodule.course_block:
    - COURSE_VISIBILITY_PRIVATE
    - COURSE_VISIBILITY_PUBLIC
    - COURSE_VISIBILITY_PUBLIC_OUTLINE

    Returns:
        AccessResponse: Either ACCESS_GRANTED or ACCESS_DENIED.
    """

    unenrolled_access_flag = COURSE_ENABLE_UNENROLLED_ACCESS_FLAG.is_enabled(course.id)
    allow_access = unenrolled_access_flag and course.course_visibility in visibilities
    if allow_access:
        return ACCESS_GRANTED

    return ACCESS_DENIED


def check_data_sharing_consent(course_id):
    """
    Grants access if the user is do not need DataSharing consent, otherwise returns data sharing link.

    Returns:
        AccessResponse: Either ACCESS_GRANTED or DataSharingConsentRequiredAccessError
    """
    from openedx.features.enterprise_support.api import get_enterprise_consent_url

    consent_url = get_enterprise_consent_url(
        request=get_current_request(),
        course_id=str(course_id),
        return_to="courseware",
        enrollment_exists=True,
        source="CoursewareAccess",
    )
    if consent_url:
        return DataSharingConsentRequiredAccessError(consent_url=consent_url)
    return ACCESS_GRANTED


def check_correct_active_enterprise_customer(user, course_id):
    """
    Grants access if the user's active enterprise customer is same as  EnterpriseCourseEnrollment's Enterprise.
    Also, Grant access if enrollment is not Enterprise

    Returns:
        AccessResponse: Either ACCESS_GRANTED or IncorrectActiveEnterpriseAccessError
    """
    enterprise_enrollments = EnterpriseCourseEnrollment.objects.filter(
        course_id=course_id, enterprise_customer_user__user_id=user.id
    )
    if not enterprise_enrollments.exists():
        return ACCESS_GRANTED

    try:
        active_enterprise_customer_user = EnterpriseCustomerUser.objects.get(user_id=user.id, active=True)
        if enterprise_enrollments.filter(enterprise_customer_user=active_enterprise_customer_user).exists():
            return ACCESS_GRANTED

        active_enterprise_name = active_enterprise_customer_user.enterprise_customer.name
    except (EnterpriseCustomerUser.DoesNotExist, EnterpriseCustomerUser.MultipleObjectsReturned):
        # Ideally this should not happen. As there should be only 1 active enterprise customer in our system
        log.error("Multiple or No Active Enterprise found for the user %s.", user.id)
        active_enterprise_name = "Incorrect"

    enrollment_enterprise_name = enterprise_enrollments.first().enterprise_customer_user.enterprise_customer.name
    return IncorrectActiveEnterpriseAccessError(enrollment_enterprise_name, active_enterprise_name)


def is_priority_access_error(access_error):
    """
    Check if given access error is a priority Access Error or not.
    Priority Access Error can not be bypassed by staff users.
    """
    priority_access_errors = [
        DataSharingConsentRequiredAccessError,
        IncorrectActiveEnterpriseAccessError,
    ]
    for priority_access_error in priority_access_errors:
        if isinstance(access_error, priority_access_error):
            return True
    return False
