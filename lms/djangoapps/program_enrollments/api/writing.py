"""
Python API functions related to writing program enrollments.

Outside of this subpackage, import these functions
from `lms.djangoapps.program_enrollments.api`.
"""


import logging

from requests.structures import CaseInsensitiveDict
from simple_history.utils import bulk_create_with_history

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment, NonExistentCourseError
from common.djangoapps.student.roles import CourseStaffRole
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

from ..constants import ProgramCourseEnrollmentRoles, ProgramCourseEnrollmentStatuses, ProgramEnrollmentStatuses
from ..constants import ProgramCourseOperationStatuses as ProgramCourseOpStatuses
from ..constants import ProgramOperationStatuses as ProgramOpStatuses
from ..exceptions import ProviderDoesNotExistException
from ..models import CourseAccessRoleAssignment, ProgramCourseEnrollment, ProgramEnrollment
from .reading import fetch_program_course_enrollments_by_students, fetch_program_enrollments, get_users_by_external_keys

logger = logging.getLogger(__name__)


def write_program_enrollments(program_uuid, enrollment_requests, create, update):
    """
    Bulk create/update a set of program enrollments.

    Arguments:
        program_uuid (UUID|str)
        enrollment_requests (list[dict]): dicts in the form:
            * 'external_user_key': str
            * 'status': str from ProgramEnrollmentStatuses
            * 'curriculum_uuid': str, omittable if `create==False`.
        create (bool): non-existent enrollments will be created iff `create`,
            otherwise they will be skipped as 'duplicate'.
        update (bool): existing enrollments will be updated iff `update`,
            otherwise they will be skipped as 'not-in-program'

    At least one of `create` or `update` must be True.

    Returns: dict[str: str]
        Mapping of external user keys to strings from ProgramOperationStatuses.
    """
    if not (create or update):
        raise ValueError("At least one of (create, update) must be True")
    requests_by_key, duplicated_keys = _organize_requests_by_external_key(enrollment_requests)
    external_keys = set(requests_by_key)
    try:
        users_by_key = get_users_by_external_keys(program_uuid, external_keys)
    except ProviderDoesNotExistException:
        # Organization has not yet set up their identity provider.
        # Just act as if none of the external users have been registered.
        users_by_key = {key: None for key in external_keys}

    # Fetch existing program enrollments.
    existing_enrollments = fetch_program_enrollments(
        program_uuid=program_uuid, external_user_keys=external_keys
    )
    existing_enrollments_by_key = CaseInsensitiveDict({key: None for key in external_keys})
    existing_enrollments_by_key.update({
        enrollment.external_user_key: enrollment
        for enrollment in existing_enrollments
    })

    # For each enrollment request, try to create/update:
    # * For creates, build up list `to_save`, which we will bulk-create afterwards.
    # * For updates, do them in place.
    # Update `results` with the new status or an error status for each operation.
    results = {}
    to_save = []
    for external_key, request in requests_by_key.items():
        status = request['status']
        if status not in ProgramEnrollmentStatuses.__ALL__:
            results[external_key] = ProgramOpStatuses.INVALID_STATUS
            continue
        user = users_by_key[external_key]
        existing_enrollment = existing_enrollments_by_key.get(external_key)
        if existing_enrollment:
            if not update:
                results[external_key] = ProgramOpStatuses.CONFLICT
                continue
            results[external_key] = change_program_enrollment_status(
                existing_enrollment, status
            )
        else:
            if not create:
                results[external_key] = ProgramOpStatuses.NOT_IN_PROGRAM
                continue
            new_enrollment = create_program_enrollment(
                program_uuid=program_uuid,
                curriculum_uuid=request['curriculum_uuid'],
                user=user,
                external_user_key=external_key,
                status=status,
                save=False,
            )
            to_save.append(new_enrollment)
            results[external_key] = new_enrollment.status

    # Bulk-create all new program enrollments and corresponding history records
    # Note: this will NOT invoke `save()` or `pre_save`/`post_save` signals!
    # See https://docs.djangoproject.com/en/1.11/ref/models/querysets/#bulk-create.
    if to_save:
        bulk_create_with_history(to_save, ProgramEnrollment)

    results.update({key: ProgramOpStatuses.DUPLICATED for key in duplicated_keys})
    return results


def create_program_enrollment(
        program_uuid,
        curriculum_uuid,
        user,
        external_user_key,
        status,
        save=True,
):
    """
    Create a program enrollment.

    Arguments:
        program_uuid (UUID|str)
        curriculum_uuid (str)
        user (User)
        external_user_key (str)
        status (str): from ProgramEnrollmentStatuses
        save (bool): Whether to save the created ProgamEnrollment.
            Defaults to True. One may set this to False in order to
            bulk-create the enrollments.

    Returns: ProgramEnrollment
    """
    if not (user or external_user_key):
        raise ValueError("At least one of (user, external_user_key) must be ")
    program_enrollment = ProgramEnrollment(
        program_uuid=program_uuid,
        curriculum_uuid=curriculum_uuid,
        user=user,
        external_user_key=external_user_key,
        status=status,
    )
    if save:
        program_enrollment.save()
    return program_enrollment


def change_program_enrollment_status(program_enrollment, new_status):
    """
    Update a program enrollment with a new status.

    Arguments:
        program_enrollment (ProgramEnrollment)
        status (str): from ProgramCourseEnrollmentStatuses

    Returns: str
        String from ProgramOperationStatuses.
    """
    if new_status not in ProgramEnrollmentStatuses.__ALL__:
        return ProgramOpStatuses.INVALID_STATUS
    program_enrollment.status = new_status
    program_enrollment.save()
    return program_enrollment.status


def write_program_course_enrollments(
        program_uuid,
        course_key,
        enrollment_requests,
        create,
        update,
):
    """
    Bulk create/update a set of program-course enrollments.

    Arguments:
        program_uuid (UUID|str)
        enrollment_requests (list[dict]): dicts in the form:
            * 'external_user_key': str
            * 'status': str from ProgramCourseEnrollmentStatuses
            * 'course_staff': Boolean if the user should have the CourseStaff role
        create (bool): non-existent enrollments will be created iff `create`,
            otherwise they will be skipped as 'duplicate'.
        update (bool): existing enrollments will be updated iff `update`,
            otherwise they will be skipped as 'not-in-program'

    At least one of `create` or `update` must be True.

    Returns: dict[str: str]
        Mapping of external user keys to strings from ProgramCourseOperationStatuses.
    """
    if not (create or update):
        raise ValueError("At least one of (create, update) must be True")
    requests_by_key, duplicated_keys = _organize_requests_by_external_key(enrollment_requests)
    processable_external_keys = set(requests_by_key)

    results = {}
    results.update({
        key: ProgramCourseOpStatuses.DUPLICATED for key in duplicated_keys
    })

    if not processable_external_keys:
        return results

    program_enrollments = fetch_program_enrollments(
        program_uuid=program_uuid,
        external_user_keys=processable_external_keys,
    ).prefetch_related('program_course_enrollments')
    program_enrollments_by_key = CaseInsensitiveDict({
        enrollment.external_user_key: enrollment for enrollment in program_enrollments
    })

    # Fetch enrollments regardless of anchored Program Enrollments
    existing_course_enrollments = fetch_program_course_enrollments_by_students(
        external_user_keys=processable_external_keys,
        course_keys=[course_key],
    ).select_related('program_enrollment')

    conflicting_user_key_and_status = _get_conflicting_active_course_enrollments(
        requests_by_key,
        existing_course_enrollments,
        program_uuid,
        course_key
    )

    # Remove the conflicted items from the requests dictionary,
    # so we can continue our processing below
    for conflicting_user_key in conflicting_user_key_and_status:
        del requests_by_key[conflicting_user_key]

    results.update(conflicting_user_key_and_status)

    # Now, limit the course enrollments to the same program uuid
    existing_course_enrollments_of_program_enrollment = existing_course_enrollments.filter(
        program_enrollment__program_uuid=program_uuid
    )

    existing_course_enrollments_by_key = CaseInsensitiveDict({
        key: None for key in processable_external_keys
    })
    existing_course_enrollments_by_key.update({
        enrollment.program_enrollment.external_user_key: enrollment
        for enrollment in existing_course_enrollments_of_program_enrollment
    })

    # For each enrollment request, try to create/update.
    # For creates, build up list `to_save`, which we will bulk-create afterwards.
    # For updates, do them in place in order to preserve history records.
    # For each operation, update `results` with the new status or an error status.
    enrollments_to_save = []
    created_enrollments = []
    updated_enrollments = []
    staff_assignments_by_user_key = CaseInsensitiveDict()
    for external_key, request in requests_by_key.items():
        course_staff = request['course_staff']
        status = request['status']
        if status not in ProgramCourseEnrollmentStatuses.__ALL__:
            results[external_key] = ProgramCourseOpStatuses.INVALID_STATUS
            continue
        program_enrollment = program_enrollments_by_key.get(external_key)
        if not program_enrollment:
            results[external_key] = ProgramCourseOpStatuses.NOT_IN_PROGRAM
            continue
        existing_course_enrollment = existing_course_enrollments_by_key[external_key]
        if existing_course_enrollment:
            if not update:
                results[external_key] = ProgramCourseOpStatuses.CONFLICT
                continue
            results[external_key] = change_program_course_enrollment_status(
                existing_course_enrollment, status
            )
            updated_enrollments.append(existing_course_enrollment)
        else:
            if not create:
                results[external_key] = ProgramCourseOpStatuses.NOT_FOUND
                continue
            new_course_enrollment = create_program_course_enrollment(
                program_enrollment, course_key, status, save=False
            )
            enrollments_to_save.append(new_course_enrollment)
            results[external_key] = new_course_enrollment.status

        if course_staff is not None:
            staff_assignments_by_user_key[external_key] = course_staff

    # Bulk-create all new program-course enrollments and corresponding history records.
    # Note: this will NOT invoke `save()` or `pre_save`/`post_save` signals!
    # See https://docs.djangoproject.com/en/1.11/ref/models/querysets/#bulk-create.
    if enrollments_to_save:
        created_enrollments = bulk_create_with_history(enrollments_to_save, ProgramCourseEnrollment)

    # For every created/updated enrollment, check if the user should be course staff.
    # If that enrollment has a linked user, assign the user the course staff role
    # If that enrollment does not have a linked user, create a CourseAccessRoleAssignment
    # for that enrollment.
    written_enrollments = ProgramCourseEnrollment.objects.filter(
        id__in=[enrollment.id for enrollment in created_enrollments + updated_enrollments]
    ).select_related('program_enrollment')

    _assign_course_staff_role(course_key, written_enrollments, staff_assignments_by_user_key)

    return results


def create_program_course_enrollment(program_enrollment, course_key, status, save=True):
    """
    Create a program course enrollment.

    If `program_enrollment` is realized (i.e., has a non-null User),
    then also create a course enrollment.

    Arguments:
        program_enrollment (ProgramEnrollment)
        course_key (CourseKey|str)
        status (str): from ProgramCourseEnrollmentStatuses
        save (bool): Whether to save the created ProgamCourseEnrollment.
            Defaults to True. One may set this to False in order to
            bulk-create the enrollments.
            Note that if a CourseEnrollment is created, it will be saved
            regardless of this value.

    Returns: ProgramCourseEnrollment

    Raises: NonExistentCourseError
    """
    _ensure_course_exists(course_key, program_enrollment.external_user_key)
    course_enrollment = (
        enroll_in_masters_track(program_enrollment.user, course_key, status)
        if program_enrollment.user
        else None
    )
    program_course_enrollment = ProgramCourseEnrollment(
        program_enrollment=program_enrollment,
        course_key=course_key,
        course_enrollment=course_enrollment,
        status=status,
    )
    if save:
        program_course_enrollment.save()
    return program_course_enrollment


def change_program_course_enrollment_status(program_course_enrollment, new_status):
    """
    Update a program course enrollment with a new status.

    If `program_course_enrollment` is realized with a CourseEnrollment,
    then also update that.

    Arguments:
        program_course_enrollment (ProgramCourseEnrollment)
        status (str): from ProgramCourseEnrollmentStatuses

    Returns: str
        String from ProgramOperationCourseStatuses.
    """
    if new_status == program_course_enrollment.status:
        return new_status
    if new_status == ProgramCourseEnrollmentStatuses.ACTIVE:
        active = True
    elif new_status == ProgramCourseEnrollmentStatuses.INACTIVE:
        active = False
    else:
        return ProgramCourseOpStatuses.INVALID_STATUS
    if program_course_enrollment.course_enrollment:
        if active:
            program_course_enrollment.course_enrollment.activate()
        else:
            program_course_enrollment.course_enrollment.deactivate()
    program_course_enrollment.status = new_status
    program_course_enrollment.save()
    return program_course_enrollment.status


def enroll_in_masters_track(user, course_key, status):
    """
    Ensure that the user is enrolled in the Master's track of course.
    Either creates or updates a course enrollment.

    Arguments:
        user (User)
        course_key (CourseKey|str)
        status (str): from ProgramCourseEnrollmentStatuses

    Returns: CourseEnrollment

    Raises: NonExistentCourseError
    """
    _ensure_course_exists(course_key, user.id)
    if status not in ProgramCourseEnrollmentStatuses.__ALL__:
        raise ValueError(f"invalid ProgramCourseEnrollmentStatus: {status}")
    if CourseEnrollment.is_enrolled(user, course_key):
        course_enrollment = CourseEnrollment.objects.get(
            user=user,
            course_id=course_key,
        )
        if course_enrollment.mode in {CourseMode.AUDIT, CourseMode.HONOR}:
            course_enrollment.mode = CourseMode.MASTERS
            course_enrollment.save()
            message_template = (
                "Converted course enrollment for user id={} "
                "and course key={} from mode {} to Master's."
            )
            logger.info(
                message_template.format(user.id, course_key, course_enrollment.mode)
            )
        elif course_enrollment.mode != CourseMode.MASTERS:
            error_message = (
                "Cannot convert CourseEnrollment to Master's from mode {}. "
                "user id={}, course_key={}."
            ).format(
                course_enrollment.mode, user.id, course_key
            )
            logger.error(error_message)
    else:
        course_enrollment = CourseEnrollment.enroll(
            user,
            course_key,
            mode=CourseMode.MASTERS,
            check_access=False,
        )
    if course_enrollment.mode == CourseMode.MASTERS:
        if status == ProgramCourseEnrollmentStatuses.INACTIVE:
            course_enrollment.deactivate()
    return course_enrollment


def _assign_course_staff_role(course_key, enrollments, staff_assignments):
    """
    Grant or remove the course staff role for a set of enrollments on a course.
    For enrollment without a linked user, a CourseAccessRoleAssignment will be
    created (or removed) for that enrollment.

    Arguments:
        enrollments (list): ProgramCourseEnrollments to update
        staff_assignments (dict): Maps an enrollment's external key to a course staff value
    """
    enrollment_role_assignments_to_delete = []
    for enrollment in enrollments:
        if enrollment.course_key != course_key:
            continue

        external_key = enrollment.program_enrollment.external_user_key
        user = enrollment.program_enrollment.user
        course_staff = staff_assignments.get(external_key)

        if user:
            if course_staff is True:
                CourseStaffRole(course_key).add_users(user)
            elif course_staff is False:
                CourseStaffRole(course_key).remove_users(user)
        else:
            if course_staff is True:
                CourseAccessRoleAssignment.objects.update_or_create(
                    enrollment=enrollment,
                    role=ProgramCourseEnrollmentRoles.COURSE_STAFF
                )
            elif course_staff is False:
                enrollment_role_assignments_to_delete.append(enrollment)

    if enrollment_role_assignments_to_delete:
        CourseAccessRoleAssignment.objects.filter(
            enrollment__in=enrollment_role_assignments_to_delete
        ).delete()


def _ensure_course_exists(course_key, user_key_or_id):
    """
    Log and raise an error if `course_key` does not refer to a real course run.

    `user_key_or_id` should be a non-PII value identifying the user that
    can be used in the log message.
    """
    if CourseOverview.course_exists(course_key):
        return
    logger.error(
        "Cannot enroll user={} in non-existent course={}".format(
            user_key_or_id,
            course_key,
        )
    )
    raise NonExistentCourseError


def _organize_requests_by_external_key(enrollment_requests):
    """
    Get dict of enrollment requests by external key.
    External keys associated with more than one request are split out into a set,
        and their enrollment requests thrown away.

    Arguments:
        enrollment_requests (list[dict])

    Returns:
        (requests_by_key, duplicated_keys)
        where requests_by_key is dict[str: dict]
          and duplicated_keys is set[str].
    """
    requests_by_key = CaseInsensitiveDict()
    duplicated_keys = set()
    for request in enrollment_requests:
        key = request['external_user_key']
        if key in duplicated_keys:
            continue
        if key in requests_by_key:
            duplicated_keys.add(key)
            del requests_by_key[key]
            continue
        requests_by_key[key] = request
    return requests_by_key, duplicated_keys


def _get_conflicting_active_course_enrollments(
    requests_by_key,
    existing_course_enrollments,
    program_uuid,
    course_key
):
    """
    Process the list of existing course enrollments together with
    the enrollment request list stored in 'requests_by_key'. Detect
    whether we have conflicting ACTIVE ProgramCourseEnrollment entries.
    When detected, log about it and return the conflicting entry with
    duplicated status.

    Arguments:
        requests_by_key (dict)
        existing_course_enrollments (queryset[ProgramCourseEnrollment]),
        program_uuid (UUID|str),
        course_key (str)

    Returns:
        results (dict) with detected conflict entry, or empty dict.
    """
    conflicted_by_user_key = CaseInsensitiveDict()

    requested_statuses_by_user_key = CaseInsensitiveDict({
        key: request.get('status') for key, request in requests_by_key.items()
    })

    for existing_enrollment in existing_course_enrollments:
        external_user_key = existing_enrollment.program_enrollment.external_user_key
        requested_status = requested_statuses_by_user_key.get(
            existing_enrollment.program_enrollment.external_user_key
        )
        if (
            requested_status
            and requested_status == ProgramCourseEnrollmentStatuses.ACTIVE
            and existing_enrollment.is_active
            and str(existing_enrollment.program_enrollment.program_uuid) != str(program_uuid)
        ):
            logger.error(
                'Detected conflicting active ProgramCourseEnrollment. This is happening on'
                ' The program_uuid [{}] with course_key [{}] for external_user_key [{}]'.format(
                    program_uuid,
                    course_key,
                    external_user_key
                ))
            conflicted_by_user_key[external_user_key] = ProgramCourseOpStatuses.CONFLICT
    return conflicted_by_user_key
