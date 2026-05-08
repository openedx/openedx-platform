"""
Utils for use in enrollment codebase such as views.
"""
import logging

from django.core.exceptions import ObjectDoesNotExist  # pylint: disable=wrong-import-order
from django.db import transaction

from common.djangoapps.student.models import User
from openedx.core.djangoapps.enrollments import api as enrollment_api
from openedx.core.djangoapps.enrollments.errors import (
    CourseEnrollmentError,
    CourseEnrollmentExistsError,
    CourseEnrollmentNotUpdatableError,
)
from openedx.core.lib.log_utils import audit_log
from openedx.features.enterprise_support.enrollments.exceptions import (
    CourseIdMissingException,
    UserDoesNotExistException,
)

log = logging.getLogger(__name__)


def lms_update_or_create_enrollment(
    username,
    course_id,
    desired_mode,
    is_active,
    enterprise_uuid=None,
    force_enrollment=False,
):
    """
    Update or create the user's course enrollment based on the existing enrollment mode.
    If an enrollment exists and its mode is not equal to the desired mode,
    then it updates the enrollment.
    Otherwise, it creates a new enrollment.
    Enrollment function meant to be called by edx-enterprise to replace the
    current uses of the EnrollmentApiClient
    The REST enrollment endpoint may also eventually also want to reuse this function
    since it's a subset of what the endpoint handles

    Unlike the REST endpoint, this function does not check for enterprise enabled, or user api key
    permissions etc. Those concerns are still going to be used by REST endpoint but this function
    is meant for use from within edx-enterprise hence already presume such privileges.

    Arguments:
     - username (str): User name
     - course_id (obj) : Course key obtained using CourseKey.from_string(course_id_input)
     - desired_mode (CourseMode): desired course mode
     - is_active (bool): A Boolean value that indicates whether the
        enrollment is to be set to inactive (if False). Usually we want a True if enrolling anew.
     - enterprise_uuid (str): Optional. id to identify the enterprise to enroll under
     - force_enrollment (bool):
         Enroll user even if course enrollment_end date is expired (default False). This only has an effect when the
         enrollment is being created, not when it is only updated.

    Returns: A serializable dictionary of the new or updated course enrollment. If it hits
     CourseEnrollmentError or CourseEnrollmentNotUpdatableError, it raises those exceptions.
     In case of the add_enrollment call, it returns None if the enrollment already exists and
     the desired_mode or is_active match the existing enrollment.
    """
    user = _validate_enrollment_inputs(username, course_id)
    current_enrollment = enrollment_api.get_enrollment(username, str(course_id))
    response = None
    if (
        current_enrollment
        and current_enrollment['mode'] == desired_mode
        and current_enrollment['is_active'] == is_active
    ):
        log.info(
            "Existing enrollment [%s] for user [%s] matches desired enrollment. No action taken.",
            current_enrollment,
            username,
        )
        return current_enrollment
    with transaction.atomic():
        try:
            if current_enrollment:
                response = enrollment_api.update_enrollment(
                    username,
                    str(course_id),
                    mode=desired_mode,
                    is_active=is_active,
                    enrollment_attributes=None,
                )
                if not response or (
                    response['mode'] != desired_mode or
                    response['is_active'] != is_active
                ):
                    log.exception(
                        "An error occurred while updating the course enrollment for user "
                        "[%s]: course run = [%s], enterprise_uuid = [%s], is_active = [%s], ",
                        username,
                        course_id,
                        str(enterprise_uuid),
                        is_active,
                    )
                    raise CourseEnrollmentNotUpdatableError(
                        f"Unable to upgrade enrollment for user {username} "
                        "in course {course_id} to {desired_mode} mode."
                        "Response from update_enrollment: {response}"
                    )
            else:
                response = enrollment_api.add_enrollment(
                    username,
                    str(course_id),
                    mode=desired_mode,
                    is_active=is_active,
                    enrollment_attributes=None,
                    enterprise_uuid=enterprise_uuid,
                    force_enrollment=force_enrollment,
                )
                if not response:
                    log.exception(
                        "An error occurred while creating the new course enrollment for user "
                        "[%s] in course run [%s]",
                        username,
                        course_id,
                    )
                    raise CourseEnrollmentError(
                        f"Unable to create enrollment for user {username} in course {course_id}."
                    )
        except CourseEnrollmentExistsError as error:
            # This will rarely be raised when we hit a race condition in adding a net-new enrollment
            log.warning(
                "An enrollment [%s] already exists for user [%s] in course run [%s].",
                error.enrollment,
                username,
                course_id,
            )
            return None
        except (CourseEnrollmentError, CourseEnrollmentNotUpdatableError) as error:
            log.exception(
                "Raising error [%s] for user "
                "[%s]: course run = [%s], enterprise_uuid = [%s], is_active = [%s], force_enrollment = [%s], ",
                error,
                username,
                course_id,
                str(enterprise_uuid),
                is_active,
                force_enrollment,
            )
            raise error
        finally:
            final_enrollment = response or current_enrollment
            audit_log(
                'enrollment_change_requested',
                course_id=str(course_id),
                requested_mode=desired_mode,
                actual_mode=final_enrollment['mode'] if final_enrollment else None,
                requested_activation=is_active,
                actual_activation=final_enrollment['is_active'] if final_enrollment else None,
                user_id=user.id
            )
        return response


def _validate_enrollment_inputs(username, course_id):
    """
    Validates username and course_id.
    Raises:
     - UserDoesNotExistException if user not found.
     - CourseIdMissingException if course_id not provided.
    """
    if not course_id:
        raise CourseIdMissingException("Course ID must be specified to create a new enrollment.")
    if not username:
        raise UserDoesNotExistException('username is a required argument for enrollment')
    try:
        # Lookup the user, instead of using request.user, since request.user may not match the username POSTed.
        user = User.objects.get(username=username)
    except ObjectDoesNotExist as error:
        raise UserDoesNotExistException(f'The user {username} does not exist.') from error
    return user
