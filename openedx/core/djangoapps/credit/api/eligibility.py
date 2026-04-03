"""
APIs for configuring credit eligibility requirements and tracking
whether a user has satisfied those requirements.
"""


import logging

from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from openedx.core.djangoapps.credit.email_utils import send_credit_notifications
from openedx.core.djangoapps.credit.exceptions import InvalidCreditCourse, InvalidCreditRequirements
from openedx.core.djangoapps.credit.models import (
    CreditCourse,
    CreditEligibility,
    CreditRequest,
    CreditRequirement,
    CreditRequirementStatus,
)

# TODO: Cleanup this mess! ECOM-2908

log = logging.getLogger(__name__)


def is_credit_course(course_key):
    """
    Check whether the course has been configured for credit.

    Args:
        course_key (CourseKey): Identifier of the course.

    Returns:
        bool: True iff this is a credit course.

    """
    return CreditCourse.is_credit_course(course_key=course_key)


def set_credit_requirements(course_key, requirements):
    """
    Add requirements to given course.

    Args:
        course_key(CourseKey): The identifier for course
        requirements(list): List of requirements to be added

    Example:
        >>> set_credit_requirements(
                "course-v1-edX-DemoX-1T2015",
                [
                    {
                        "namespace": "proctored_exam",
                        "name": "i4x://edX/DemoX/proctoring-block/final_uuid",
                        "display_name": "Final Exam",
                        "criteria": {},
                    },
                    {
                        "namespace": "grade",
                        "name": "grade",
                        "display_name": "Grade",
                        "criteria": {"min_grade": 0.8},
                    },
                ])

    Raises:
        InvalidCreditRequirements

    Returns:
        None
    """

    invalid_requirements = _validate_requirements(requirements)
    if invalid_requirements:
        invalid_requirements = ", ".join(invalid_requirements)
        raise InvalidCreditRequirements(invalid_requirements)

    try:
        credit_course = CreditCourse.get_credit_course(course_key=course_key)
    except CreditCourse.DoesNotExist:
        raise InvalidCreditCourse()  # lint-amnesty, pylint: disable=raise-missing-from

    old_requirements = CreditRequirement.get_course_requirements(course_key=course_key)
    requirements_to_disable = _get_requirements_to_disable(old_requirements, requirements)
    if requirements_to_disable:
        CreditRequirement.disable_credit_requirements(requirements_to_disable)

    for sort_value, requirement in enumerate(requirements):
        CreditRequirement.add_or_update_course_requirement(credit_course, requirement, sort_value)


def get_credit_requirements(course_key, namespace=None):
    """
    Get credit eligibility requirements of a given course and namespace.

    Args:
        course_key(CourseKey): The identifier for course
        namespace(str): Namespace of requirements

    Example:
        >>> get_credit_requirements("course-v1-edX-DemoX-1T2015")
            {
                requirements =
                [
                    {
                        "namespace": "proctored_exam",
                        "name": "i4x://edX/DemoX/proctoring-block/final_uuid",
                        "display_name": "Final Exam",
                        "criteria": {},
                    },
                    {
                        "namespace": "grade",
                        "name": "grade",
                        "display_name": "Grade",
                        "criteria": {"min_grade": 0.8},
                    },
                ]
            }

    Returns:
        Dict of requirements in the given namespace

    """

    requirements = CreditRequirement.get_course_requirements(course_key, namespace)
    return [
        {
            "namespace": requirement.namespace,
            "name": requirement.name,
            "display_name": requirement.display_name,
            "criteria": requirement.criteria
        }
        for requirement in requirements
    ]


def is_user_eligible_for_credit(username, course_key):
    """
    Returns a boolean indicating if the user is eligible for credit for
    the given course

    Args:
        username(str): The identifier for user
        course_key (CourseKey): The identifier for course

    Returns:
        True if user is eligible for the course else False
    """
    return CreditEligibility.is_user_eligible_for_credit(course_key, username)


def get_eligibilities_for_user(username, course_key=None):
    """
    Retrieve all courses or particular course for which the user is eligible
    for credit.

    Arguments:
        username (unicode): Identifier of the user.
        course_key (unicode): Identifier of the course.

    Example:
        >>> get_eligibilities_for_user("ron")
        [
            {
                "course_key": "edX/Demo_101/Fall",
                "deadline": "2015-10-23"
            },
            {
                "course_key": "edX/Demo_201/Spring",
                "deadline": "2015-11-15"
            },
            ...
        ]

    Returns: list

    """
    eligibilities = CreditEligibility.get_user_eligibilities(username)
    if course_key:
        course_key = CourseKey.from_string(str(course_key))
        eligibilities = eligibilities.filter(course__course_key=course_key)

    return [
        {
            "course_key": str(eligibility.course.course_key),
            "deadline": eligibility.deadline,
        }
        for eligibility in eligibilities
    ]


def set_credit_requirement_status(user, course_key, req_namespace, req_name, status="satisfied", reason=None):
    """
    Update the user's requirement status.

    This will record whether the user satisfied or failed a particular requirement
    in a course.  If the user has satisfied all requirements, the user will be marked
    as eligible for credit in the course.

    Args:
        user(User): User object to set credit requirement for.
        course_key (CourseKey): Identifier for the course associated with the requirement.
        req_namespace (str): Namespace of the requirement (e.g. "grade" or "reverification")
        req_name (str): Name of the requirement (e.g. "grade" or the location of the ICRV XBlock)

    Keyword Arguments:
        status (str): Status of the requirement (either "satisfied" or "failed")
        reason (dict): Reason of the status
    """
    # Check whether user has credit eligible enrollment.
    enrollment_mode, is_active = CourseEnrollment.enrollment_mode_for_user(user, course_key)
    has_credit_eligible_enrollment = (CourseMode.is_credit_eligible_slug(enrollment_mode) and is_active)

    # Refuse to set status of requirement if the user enrollment is not credit eligible.
    if not has_credit_eligible_enrollment:
        return

    # Do not allow students who have requested credit to change their eligibility
    if CreditRequest.get_user_request_status(user.username, course_key):
        log.info(
            'Refusing to set status of requirement with namespace "%s" and name "%s" because the '
            'user "%s" has already requested credit for the course "%s".',
            req_namespace, req_name, user.username, course_key
        )
        return

    # Do not allow a student who has earned eligibility to un-earn eligibility
    eligible_before_update = CreditEligibility.is_user_eligible_for_credit(course_key, user.username)
    if eligible_before_update and status == 'failed':
        log.info(
            'Refusing to set status of requirement with namespace "%s" and name "%s" to "failed" because the '
            'user "%s" is already eligible for credit in the course "%s".',
            req_namespace, req_name, user.username, course_key
        )
        return

    # Retrieve all credit requirements for the course
    # We retrieve all of them to avoid making a second query later when
    # we need to check whether all requirements have been satisfied.
    reqs = CreditRequirement.get_course_requirements(course_key)

    # Find the requirement we're trying to set
    req_to_update = next((
        req for req in reqs
        if req.namespace == req_namespace and req.name == req_name
    ), None)

    # If we can't find the requirement, then the most likely explanation
    # is that there was a lag updating the credit requirements after the course
    # was published.  We *could* attempt to create the requirement here,
    # but that could cause serious performance issues if many users attempt to
    # lock the row at the same time.
    # Instead, we skip updating the requirement and log an error.
    if req_to_update is None:
        log.error(
            (
                'Could not update credit requirement in course "%s" '
                'with namespace "%s" and name "%s" '
                'because the requirement does not exist. '
                'The user "%s" should have had their status updated to "%s".'
            ),
            str(course_key), req_namespace, req_name, user.username, status
        )
        return

    # Update the requirement status
    CreditRequirementStatus.add_or_update_requirement_status(
        user.username, req_to_update, status=status, reason=reason
    )

    # If we're marking this requirement as "satisfied", there's a chance that the user has met all eligibility
    # requirements, and should be notified. However, if the user was already eligible, do not send another notification.
    if status == "satisfied" and not eligible_before_update:
        is_eligible, eligibility_record_created = CreditEligibility.update_eligibility(reqs, user.username, course_key)
        if eligibility_record_created and is_eligible:
            try:
                send_credit_notifications(user.username, course_key)
            except Exception:  # pylint: disable=broad-except
                log.exception("Error sending email")


def remove_credit_requirement_status(username, course_key, req_namespace, req_name):
    """
    Remove the user's requirement status.

    This will remove the record from the credit requirement status table.
    The user will still be eligible for the credit in a course.

    Args:
        username (str): Username of the user
        course_key (CourseKey): Identifier for the course associated
                                with the requirement.
        req_namespace (str): Namespace of the requirement
                            (e.g. "grade" or "reverification")
        req_name (str): Name of the requirement
                        (e.g. "grade" or the location of the ICRV XBlock)

    """

    # Find the requirement we're trying to remove
    req_to_remove = CreditRequirement.get_course_requirement(course_key, req_namespace, req_name)

    # If we can't find the requirement, then the most likely explanation
    # is that there was a lag removing the credit requirements after the course
    # was published.  We *could* attempt to remove the requirement here,
    # but that could cause serious performance issues if many users attempt to
    # lock the row at the same time.
    # Instead, we skip removing the requirement and log an error.
    if not req_to_remove:
        log.error(
            (
                'Could not remove credit requirement in course "%s" '
                'with namespace "%s" and name "%s" '
                'because the requirement does not exist. '
            ),
            str(course_key), req_namespace, req_name
        )
        return

    # Remove the requirement status
    CreditRequirementStatus.remove_requirement_status(
        username, req_to_remove
    )


def get_credit_requirement_status(course_key, username, namespace=None, name=None):
    """ Retrieve the user's status for each credit requirement in the course.

    Args:
        course_key (CourseKey): The identifier for course
        username (str): The identifier of the user

    Example:
        >>> get_credit_requirement_status("course-v1-edX-DemoX-1T2015", "john")

                [
                    {
                        "namespace": "proctored_exam",
                        "name": "i4x://edX/DemoX/proctoring-block/final_uuid",
                        "display_name": "Proctored Mid Term Exam",
                        "criteria": {},
                        "reason": {},
                        "status": "satisfied",
                        "status_date": "2015-06-26 11:07:42",
                        "order": 1,
                    },
                    {
                        "namespace": "grade",
                        "name": "i4x://edX/DemoX/proctoring-block/final_uuid",
                        "display_name": "Minimum Passing Grade",
                        "criteria": {"min_grade": 0.8},
                        "reason": {"final_grade": 0.95},
                        "status": "satisfied",
                        "status_date": "2015-06-26 11:07:44",
                        "order": 2,
                    },
                ]

    Returns:
        list of requirement statuses
    """
    requirements = CreditRequirement.get_course_requirements(course_key, namespace=namespace, name=name)
    requirement_statuses = CreditRequirementStatus.get_statuses(requirements, username)
    requirement_statuses = {o.requirement: o for o in requirement_statuses}
    statuses = []
    for requirement in requirements:
        requirement_status = requirement_statuses.get(requirement)
        statuses.append({
            "namespace": requirement.namespace,
            "name": requirement.name,
            "display_name": requirement.display_name,
            "criteria": requirement.criteria,
            "reason": requirement_status.reason if requirement_status else None,
            "status": requirement_status.status if requirement_status else None,
            "status_date": requirement_status.modified if requirement_status else None,
            # We retain the old name "order" in the API because changing APIs takes a lot more coordination.
            "order": requirement.sort_value,
        })
    return statuses


def _get_requirements_to_disable(old_requirements, new_requirements):
    """
    Get the ids of 'CreditRequirement' entries to be disabled that are
    deleted from the courseware.

    Args:
        old_requirements(QuerySet): QuerySet of CreditRequirement
        new_requirements(list): List of requirements being added

    Returns:
        List of ids of CreditRequirement that are not in new_requirements
    """
    requirements_to_disable = []
    for old_req in old_requirements:
        found_flag = False
        for req in new_requirements:
            # check if an already added requirement is modified
            if req["namespace"] == old_req.namespace and req["name"] == old_req.name:
                found_flag = True
                break
        if not found_flag:
            requirements_to_disable.append(old_req.id)
    return requirements_to_disable


def _validate_requirements(requirements):
    """
    Validate the requirements.

    Args:
        requirements(list): List of requirements

    Returns:
        List of strings of invalid requirements
    """
    invalid_requirements = []
    for requirement in requirements:
        invalid_params = []
        if not requirement.get("namespace"):
            invalid_params.append("namespace")
        if not requirement.get("name"):
            invalid_params.append("name")
        if not requirement.get("display_name"):
            invalid_params.append("display_name")
        if "criteria" not in requirement:
            invalid_params.append("criteria")

        if invalid_params:
            invalid_requirements.append(
                "{requirement} has missing/invalid parameters: {params}".format(
                    requirement=requirement,
                    params=invalid_params,
                )
            )
    return invalid_requirements
