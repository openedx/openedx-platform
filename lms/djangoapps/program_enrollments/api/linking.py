"""
Python API function to link program enrollments and external_user_keys to an
LMS user.

Outside of this subpackage, import these functions
from `lms.djangoapps.program_enrollments.api`.
"""


import logging

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from requests.structures import CaseInsensitiveDict

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.api import get_access_role_by_role_name
from common.djangoapps.student.models import CourseEnrollmentException

from .reading import fetch_program_enrollments
from .writing import enroll_in_masters_track

logger = logging.getLogger(__name__)
User = get_user_model()


NO_PROGRAM_ENROLLMENT_TEMPLATE = (
    'No program enrollment found for program uuid={program_uuid} and external student '
    'key={external_user_key}'
)
NO_LMS_USER_TEMPLATE = 'No user found with username {}'
EXISTING_USER_TEMPLATE = (
    'Program enrollment with external_student_key={external_user_key} is already linked to '
    '{account_relation} account username={username}'
)


def link_program_enrollments(program_uuid, external_keys_to_usernames):
    """
    Utility function to link ProgramEnrollments to LMS Users

    Arguments:
        -program_uuid: the program for which we are linking program enrollments
        -external_keys_to_usernames: dict mapping `external_user_keys` to LMS usernames.

    Returns: dict[str: str]
        Map from external keys to errors, for the external keys of users whose
        linking produced errors.

    Raises: ValueError if None is included in external_keys_to_usernames

    This function will look up program enrollments and users, and update the program
    enrollments with the matching user. If the program enrollment has course enrollments, we
    will enroll the user into their waiting program courses.

    For each external_user_key:lms_username, if:
        - The user is not found
        - No enrollment is found for the given program and external_user_key
        - The enrollment already has a user and that user is the same as the given user
    An error message will be logged, and added to a dictionary of error messages keyed by
    external_key. The input will be skipped. All other inputs will be processed and
    enrollments updated, and then the function will return the dictionary of error messages.

    For each external_user_key:lms_username, if the enrollment already has a user, but that user
    is different than the requested user, we do the following. We unlink the existing user from
    the program enrollment and link the requested user to the program enrollment. This is accomplished by
    removing the existing user's link to the program enrollment. If the program enrollment
    has course enrollments, then we unenroll the user. If there is an audit track in the course,
    we also move the enrollment into the audit track. We also remove the association between those
    course enrollments and the program course enrollments. The
    requested user is then linked to the program following the above logic.

    If there is an error while enrolling a user in a waiting program course enrollment, the
    error will be logged, and added to the returned error dictionary, and we will roll back all
    transactions for that user so that their db state will be the same as it was before this
    function was called, to prevent program enrollments to be in a state where they have an LMS
    user but still have waiting course enrollments. All other inputs will be processed
    normally.
    """
    errors = {}
    program_enrollments = _get_program_enrollments_by_ext_key(
        program_uuid, external_keys_to_usernames.keys()
    )
    users_by_username = _get_lms_users(external_keys_to_usernames.values())
    for external_user_key, username in external_keys_to_usernames.items():
        program_enrollment = program_enrollments.get(external_user_key)
        user = users_by_username.get(username)
        if not user:
            error_message = NO_LMS_USER_TEMPLATE.format(username)
        elif not program_enrollment:
            error_message = NO_PROGRAM_ENROLLMENT_TEMPLATE.format(
                program_uuid=program_uuid,
                external_user_key=external_user_key
            )
        # if we're trying to establish a link that already exists
        elif program_enrollment.user and program_enrollment.user == user:
            error_message = _user_already_linked_message(program_enrollment, user)
        else:
            error_message = None
        if error_message:
            logger.warning(error_message)
            errors[external_user_key] = error_message
            continue
        try:
            with transaction.atomic():
                # If the ProgramEnrollment already has a linked edX user that is different than
                # the requested user, then we should sever the link to the existing edX user before
                # linking the ProgramEnrollment to the new user.
                if program_enrollment.user and program_enrollment.user != user:
                    message = ('Unlinking user with username={old_username} from program enrollment with '  # noqa: UP032  # pylint: disable=line-too-long
                               'program uuid={program_uuid} with external_student_key={external_user_key} '
                               'and linking user with username={new_username} '
                               'to program enrollment.').format(
                        old_username=program_enrollment.user.username,
                        program_uuid=program_uuid,
                        external_user_key=external_user_key,
                        new_username=user,
                    )
                    logger.info(_user_already_linked_message(program_enrollment, user))
                    logger.info(message)

                    unlink_program_enrollment(program_enrollment)

                link_program_enrollment_to_lms_user(program_enrollment, user)
        except (CourseEnrollmentException, IntegrityError) as e:
            logger.exception("Rolling back all operations for {}:{}".format(  # noqa: UP032
                external_user_key,
                username,
            ))
            error_message = type(e).__name__
            if str(e):
                error_message += ': '
                error_message += str(e)
            errors[external_user_key] = error_message
    return errors


def _user_already_linked_message(program_enrollment, user):
    """
    Creates an error message that the specified program enrollment is already linked to an lms user
    """
    existing_username = program_enrollment.user.username
    external_user_key = program_enrollment.external_user_key
    return EXISTING_USER_TEMPLATE.format(
        external_user_key=external_user_key,
        account_relation='target' if program_enrollment.user.id == user.id else 'a different',
        username=existing_username,
    )


def _get_program_enrollments_by_ext_key(program_uuid, external_user_keys):
    """
    Does a bulk read of ProgramEnrollments for a given program and list of external student keys
    and returns a dict keyed by external student key
    """
    program_enrollments = fetch_program_enrollments(
        program_uuid=program_uuid,
        external_user_keys=external_user_keys,
    ).prefetch_related(
        'program_course_enrollments'
    ).select_related('user')
    return CaseInsensitiveDict({
        program_enrollment.external_user_key: program_enrollment
        for program_enrollment in program_enrollments
    })


def _get_lms_users(lms_usernames):
    """
    Does a bulk read of Users by username and returns a dict keyed by username
    """
    return {
        user.username: user
        for user in User.objects.filter(username__in=lms_usernames)
    }


def unlink_program_enrollment(program_enrollment):
    """
    Unlinks CourseEnrollments from the ProgramEnrollment by doing the following for
    each ProgramCourseEnrollment associated with the Program Enrollment.
        1. unenrolling the corresponding user from the course
        2. moving the user into the audit track, if the track exists
        3. removing the link between the ProgramCourseEnrollment and the CourseEnrollment

    Arguments:
        program_enrollment: the ProgramEnrollment object
    """
    program_course_enrollments = program_enrollment.program_course_enrollments.all()

    for pce in program_course_enrollments:
        course_key = pce.course_enrollment.course.id
        modes = CourseMode.modes_for_course_dict(course_key)

        update_enrollment_kwargs = {
            'is_active': False,
            'skip_refund': True,
        }

        if CourseMode.contains_audit_mode(modes):
            # if the course contains an audit mode, move the
            # learner's enrollment into the audit mode
            update_enrollment_kwargs['mode'] = 'audit'

        # deactive the learner's course enrollment and move them into the
        # audit track, if it exists
        pce.course_enrollment.update_enrollment(**update_enrollment_kwargs)

        # sever ties to the user from the ProgramCourseEnrollment
        pce.course_enrollment = None
        pce.save()

    program_enrollment.user = None
    program_enrollment.save()


def link_program_enrollment_to_lms_user(program_enrollment, user):
    """
    Attempts to link the given program enrollment to the given user
    If the enrollment has any program course enrollments, enroll the user in those courses as well

    Raises: CourseEnrollmentException if there is an error enrolling user in a waiting
            program course enrollment
            IntegrityError if we try to create invalid records.
    """
    link_log_info = 'user id={} with external_user_key={} for program uuid={}'.format(  # noqa: UP032
        user.id,
        program_enrollment.external_user_key,
        program_enrollment.program_uuid,
    )
    logger.info("Linking %s", link_log_info)
    program_enrollment.user = user
    try:
        program_enrollment.save()
        program_course_enrollments = program_enrollment.program_course_enrollments.all()
        for pce in program_course_enrollments:
            pce.course_enrollment = enroll_in_masters_track(
                user, pce.course_key, pce.status
            )
            pce.save()
            _fulfill_course_access_roles(user, pce)
    except IntegrityError:
        logger.error("Integrity error while linking %s", link_log_info)
        raise
    except CourseEnrollmentException as e:
        logger.error(
            "CourseEnrollmentException while linking {}: {}".format(  # noqa: UP032
                link_log_info, str(e)
            )
        )
        raise


def _fulfill_course_access_roles(user, program_course_enrollment):
    """
    Convert any CourseAccessRoleAssignment objects, which represent pending CourseAccessRoles, into fulfilled
    CourseAccessRole objects as part of a program course enrollment.

    Arguments:
        user: User object for whom we are fulfilling CourseAccessRoleAssignments into CourseAccessRoles
        program_course_enrollment: the ProgramCourseEnrollment object that represents the course the user
            should be granted a CourseAccessRole in the context of
    """
    role_assignments = program_course_enrollment.courseaccessroleassignment_set.all()
    program_enrollment = program_course_enrollment.program_enrollment

    for role_assignment in role_assignments:
        # currently, we only allow for an assignment of a "staff" role, but
        # this allows us to expand the functionality to other roles, if need be
        # get_access_role_by_role_name gets us the class, so we need to instantiate it
        role = get_access_role_by_role_name(role_assignment.role)(program_course_enrollment.course_key)

        logger_format_values = {
            'course_key': program_course_enrollment.course_key,
            'program_course_enrollment': program_course_enrollment,
            'program_uuid': program_enrollment.program_uuid,
            'role': role_assignment.role,
            'user_id': user.id,
            'user_key': program_enrollment.external_user_key,
        }

        try:
            with transaction.atomic():
                logger.info('Creating access role %(role)s for user with user id %(user_id)s and '
                            'external user key %(user_key)s for course with course key %(course_key)s '
                            'in program with uuid %(program_uuid)s.',
                            logger_format_values
                            )
                # if the user already has the role, then the add users method ignores this
                # and the operation is a no-op
                role.add_users(user)
                # because the user now has a corresponding CourseAccessRole, we no longer need
                # the CourseAccessRoleAssignment object
                role_assignment.delete()
        except Exception:  # pylint: disable=broad-except
            logger.error('Unable to create access role %(role)s for user with user id %(user_id)s and '
                         'external user key %(user_key)s for course with course key %(course_key)s '
                         'in program with uuid %(program_uuid)s or to delete the CourseAccessRoleAssignment '
                         'with role %(role)s and ProgramCourseEnrollment %(program_course_enrollment)r.',
                         logger_format_values
                         )
