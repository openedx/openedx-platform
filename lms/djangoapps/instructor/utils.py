"""
Utility functions for student enrollment operations.

This module contains reusable functions for processing student enrollments
that can be used in both synchronous and asynchronous contexts.
"""

import logging
from typing import Callable, Optional

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import (
    ALLOWEDTOENROLL_TO_ENROLLED,
    ALLOWEDTOENROLL_TO_UNENROLLED,
    DEFAULT_TRANSITION_STATE,
    ENROLLED_TO_ENROLLED,
    ENROLLED_TO_UNENROLLED,
    UNENROLLED_TO_ALLOWEDTOENROLL,
    UNENROLLED_TO_ENROLLED,
    UNENROLLED_TO_UNENROLLED,
    CourseEnrollment,
    EnrollStatusChange,
    ManualEnrollmentAudit,
)
from lms.djangoapps.instructor.enrollment import (
    enroll_email,
    get_email_params,
    get_user_email_language,
    unenroll_email,
)
from common.djangoapps.student.models import get_user_by_username_or_email
from openedx.core.lib.courses import get_course_by_id

log = logging.getLogger(__name__)


User = get_user_model()


def _determine_enroll_state_transition(before_state: dict, after_state: dict) -> str:
    """
    Determine the state transition for an enrollment operation.

    Args:
        before_state (dict): State before enrollment with keys 'enrollment', 'user', 'allowed'
        after_state (dict): State after enrollment with keys 'enrollment', 'allowed'

    Returns:
        str: The state transition constant
    """
    # User was not registered before
    if not before_state["user"]:
        if after_state["allowed"]:
            return UNENROLLED_TO_ALLOWEDTOENROLL
        return DEFAULT_TRANSITION_STATE

    # User was registered and enrolled successfully
    if after_state["enrollment"]:
        if before_state["enrollment"]:
            return ENROLLED_TO_ENROLLED
        if before_state["allowed"]:
            return ALLOWEDTOENROLL_TO_ENROLLED
        return UNENROLLED_TO_ENROLLED

    return DEFAULT_TRANSITION_STATE


def _determine_unenroll_state_transition(before_state: dict) -> str:
    """
    Determine the state transition for an unenrollment operation.

    Args:
        before_state (dict): State before unenrollment with keys 'enrollment', 'allowed'

    Returns:
        str: The state transition constant
    """
    if before_state["enrollment"]:
        return ENROLLED_TO_UNENROLLED
    if before_state["allowed"]:
        return ALLOWEDTOENROLL_TO_UNENROLLED
    return UNENROLLED_TO_UNENROLLED


def process_single_student_enrollment(
    request_user,
    course_key: CourseKey,
    action: str,
    identifier: str,
    auto_enroll: bool,
    email_students: bool,
    reason: str | None,
    email_params: dict | None,
):
    """
    Process enrollment/unenrollment for a single student.

    Args:
        request_user (User): User who initiated the enrollment operation
        course_key (CourseKey): CourseKey object for the course
        action (str): 'enroll' or 'unenroll'
        identifier (str): Student identifier (email or username)
        auto_enroll (bool): Whether to auto-enroll in verified track if applicable
        email_students (bool): Whether to send enrollment emails
        reason (str | None): Optional reason for enrollment change
        email_params (dict | None): Pre-computed email parameters (optional)

    Returns:
        dict: Result of the enrollment operation with keys:
            - identifier: The student identifier
            - success: Boolean indicating if operation was successful
            - before: State before operation (if successful)
            - after: State after operation (if successful)
            - error_type: Type of error ('invalid_identifier', 'validation_error', 'general_error')
            - error_message: Error message (if failed)
    """
    enrollment_obj = None
    state_transition = DEFAULT_TRANSITION_STATE
    identified_user = None
    email = None
    language = None

    try:
        identified_user = get_user_by_username_or_email(identifier)
    except User.DoesNotExist:
        email = identifier
    else:
        email = identified_user.email
        language = get_user_email_language(identified_user)

    try:
        validate_email(email)  # Raises ValidationError if invalid

        if action == EnrollStatusChange.enroll:
            before, after, enrollment_obj = enroll_email(
                course_key, email, auto_enroll, email_students, {**email_params}, language=language
            )
            before_state = before.to_dict()
            after_state = after.to_dict()
            state_transition = _determine_enroll_state_transition(before_state, after_state)

        elif action == EnrollStatusChange.unenroll:
            before, after = unenroll_email(
                course_key, email, email_students, {**email_params}, language=language
            )
            before_state = before.to_dict()
            after_state = after.to_dict()
            state_transition = _determine_unenroll_state_transition(before_state)
            enrollment_obj = CourseEnrollment.get_enrollment(identified_user, course_key) if identified_user else None

        # Create audit record
        ManualEnrollmentAudit.create_manual_enrollment_audit(
            request_user, email, state_transition, reason, enrollment_obj
        )

        return {
            "identifier": identifier,
            "before": before_state,
            "after": after_state,
            "success": True,
            "state_transition": state_transition,
        }
    except ValidationError:
        return {
            "identifier": identifier,
            "invalidIdentifier": True,
            "success": False,
            "error_type": "invalid_identifier",
            "error_message": "Invalid email address",
        }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log.exception("Error while processing student")
        log.exception(exc)
        return {
            "identifier": identifier,
            "error": True,
            "success": False,
            "error_type": "general_error",
            "error_message": str(exc),
        }


def process_student_enrollment_batch(
    request_user,
    course_key: CourseKey,
    action: str,
    identifiers: list[str],
    auto_enroll: bool,
    email_students: bool,
    reason: str | None,
    secure: bool,
    progress_callback: Optional[Callable] = None,
):
    """
    Process a batch of student enrollment/unenrollment operations.

    Args:
        request_user (User): User who initiated the batch operation
        course_key (CourseKey): CourseKey object for the course
        action (str): 'enroll' or 'unenroll'
        identifiers (list[str]): List of student identifiers (emails or usernames)
        auto_enroll (bool): Whether to auto-enroll in verified track if applicable
        email_students (bool): Whether to send enrollment emails
        reason (str | None): Optional reason for enrollment change
        secure (bool): Whether the request is secure (HTTPS)
        progress_callback (Optional[Callable]): Optional callback function to report progress
            Should accept (current, total, results) parameters

    Returns:
        dict: Batch processing results with keys:
            - action: The action performed
            - auto_enroll: Auto-enrollment setting
            - results: List of individual enrollment results
            - successful_operations: Count of successful operations
            - failed_operations: Count of failed operations
            - total_students: Total number of students processed
    """
    email_params = {}
    if email_students:
        course = get_course_by_id(course_key)
        email_params = get_email_params(course, auto_enroll, secure=secure)

    results = []
    successful_operations = 0
    failed_operations = 0
    total_students = len(identifiers)

    for idx, identifier in enumerate(identifiers):
        result = process_single_student_enrollment(
            request_user=request_user,
            course_key=course_key,
            action=action,
            identifier=identifier,
            auto_enroll=auto_enroll,
            email_students=email_students,
            reason=reason,
            email_params=email_params,
        )

        results.append(result)

        if result["success"]:
            successful_operations += 1
        else:
            failed_operations += 1

        if progress_callback:
            progress_callback(idx + 1, total_students, results)

    return {
        "action": action,
        "auto_enroll": auto_enroll,
        "results": results,
        "successful_operations": successful_operations,
        "failed_operations": failed_operations,
        "total_students": total_students,
    }
