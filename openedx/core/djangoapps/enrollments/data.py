"""
Data Aggregation Layer of the Enrollment API. Collects all enrollment specific data into a single
source to be used throughout the API.
"""


import logging

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db import transaction
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import (
    AlreadyEnrolledError,
    CourseEnrollment,
    CourseEnrollmentAttribute,
    CourseFullError,
    EnrollmentClosedError,
    NonExistentCourseError,
)
from common.djangoapps.student.role_helpers import get_course_roles
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.enrollments.errors import (
    CourseEnrollmentClosedError,
    CourseEnrollmentExistsError,
    CourseEnrollmentFullError,
    InvalidEnrollmentAttribute,
    UserNotFoundError,
)
from openedx.core.djangoapps.enrollments.serializers import CourseEnrollmentSerializer, CourseSerializer
from openedx.core.lib.exceptions import CourseNotFoundError

log = logging.getLogger(__name__)


def get_course_enrollments(username, include_inactive=False):
    """Retrieve a list representing all aggregated data for a user's course enrollments.

    Construct a representation of all course enrollment data for a specific user.

    Args:
        username: The name of the user to retrieve course enrollment information for.
        include_inactive (bool): Determines whether inactive enrollments will be included


    Returns:
        A serializable list of dictionaries of all aggregated enrollment data for a user.

    """
    qset = CourseEnrollment.objects.filter(
        user__username=username,
    ).order_by('created')

    if not include_inactive:
        qset = qset.filter(is_active=True)

    enrollments = CourseEnrollmentSerializer(qset, many=True).data

    # Find deleted courses and filter them out of the results
    deleted = []
    valid = []
    for enrollment in enrollments:
        if enrollment.get("course_details") is not None:
            valid.append(enrollment)
        else:
            deleted.append(enrollment)

    if deleted:
        log.warning(
            (
                "Course enrollments for user %s reference "
                "courses that do not exist (this can occur if a course is deleted)."
            ), username,
        )

    return valid


def get_course_enrollment(username, course_id):
    """Retrieve an object representing all aggregated data for a user's course enrollment.

    Get the course enrollment information for a specific user and course.

    Args:
        username (str): The name of the user to retrieve course enrollment information for.
        course_id (str): The course to retrieve course enrollment information for.

    Returns:
        A serializable dictionary representing the course enrollment.

    """
    course_key = CourseKey.from_string(course_id)
    try:
        enrollment = CourseEnrollment.objects.get(
            user__username=username, course_id=course_key
        )
        return CourseEnrollmentSerializer(enrollment).data
    except CourseEnrollment.DoesNotExist:
        return None


def get_user_enrollments(course_key):
    """Based on the course id, return all user enrollments in the course
    Args:
        course_key (CourseKey): Identifier of the course
        from which to retrieve enrollments.
    Returns:
        A course's user enrollments as a queryset
    Raises:
        CourseEnrollment.DoesNotExist
    """
    return CourseEnrollment.objects.filter(
        course_id=course_key,
        is_active=True
    ).order_by('created')


def create_course_enrollment(username, course_id, mode, is_active, enterprise_uuid=None, force_enrollment=False):
    """Create a new course enrollment for the given user.

    Creates a new course enrollment for the specified user username.

    Args:
        username (str): The name of the user to create a new course enrollment for.
        course_id (str): The course to create the course enrollment for.
        mode (str): (Optional) The mode for the new enrollment.
        is_active (boolean): (Optional) Determines if the enrollment is active.
        enterprise_uuid (str): Add course enterprise uuid
        force_enrollment (bool): Enroll user even if course enrollment_end date is expired

    Returns:
        A serializable dictionary representing the new course enrollment.

    Raises:
        CourseNotFoundError
        CourseEnrollmentFullError
        EnrollmentClosedError
        CourseEnrollmentExistsError

    """
    course_key = CourseKey.from_string(course_id)

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        msg = f"Not user with username '{username}' found."
        log.warning(msg)
        raise UserNotFoundError(msg)  # pylint: disable=raise-missing-from  # noqa: B904

    try:
        enrollment = CourseEnrollment.enroll(
            user, course_key, check_access=True, can_upgrade=force_enrollment, enterprise_uuid=enterprise_uuid
        )
        return _update_enrollment(enrollment, is_active=is_active, mode=mode)
    except NonExistentCourseError as err:
        raise CourseNotFoundError(str(err))  # pylint: disable=raise-missing-from  # noqa: B904
    except EnrollmentClosedError as err:
        raise CourseEnrollmentClosedError(str(err))  # pylint: disable=raise-missing-from  # noqa: B904
    except CourseFullError as err:
        raise CourseEnrollmentFullError(str(err))  # pylint: disable=raise-missing-from  # noqa: B904
    except AlreadyEnrolledError as err:
        enrollment = get_course_enrollment(username, course_id)
        raise CourseEnrollmentExistsError(str(err), enrollment)  # pylint: disable=raise-missing-from  # noqa: B904


def update_course_enrollment(username, course_id, mode=None, is_active=None):
    """Modify a course enrollment for a user.

    Allows updates to a specific course enrollment.

    Args:
        username (str): The name of the user to retrieve course enrollment information for.
        course_id (str): The course to retrieve course enrollment information for.
        mode (str): (Optional) If specified, modify the mode for this enrollment.
        is_active (boolean): (Optional) Determines if the enrollment is active.

    Returns:
        A serializable dictionary representing the modified course enrollment.

    """
    course_key = CourseKey.from_string(course_id)

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        msg = f"Not user with username '{username}' found."
        log.warning(msg)
        raise UserNotFoundError(msg)  # pylint: disable=raise-missing-from  # noqa: B904

    try:
        enrollment = CourseEnrollment.objects.get(user=user, course_id=course_key)
        return _update_enrollment(enrollment, is_active=is_active, mode=mode)
    except CourseEnrollment.DoesNotExist:
        return None


def add_or_update_enrollment_attr(username, course_id, attributes):
    """Set enrollment attributes for the enrollment of given user in the
    course provided.

    Args:
        course_id (str): The Course to set enrollment attributes for.
        username: The User to set enrollment attributes for.
        attributes (list): Attributes to be set.

    Example:
        >>>add_or_update_enrollment_attr(
            "Bob",
            "course-v1-edX-DemoX-1T2015",
            [
                {
                    "namespace": "credit",
                    "name": "provider_id",
                    "value": "hogwarts",
                },
            ]
        )
    """
    course_key = CourseKey.from_string(course_id)
    user = _get_user(username)
    enrollment = CourseEnrollment.get_enrollment(user, course_key)
    if not _invalid_attribute(attributes) and enrollment is not None:
        CourseEnrollmentAttribute.add_enrollment_attr(enrollment, attributes)


def get_enrollment_attributes(username, course_id):
    """Retrieve enrollment attributes for given user for provided course.

    Args:
        username: The User to get enrollment attributes for
        course_id (str): The Course to get enrollment attributes for.

    Example:
        >>>get_enrollment_attributes("Bob", "course-v1-edX-DemoX-1T2015")
        [
            {
                "namespace": "credit",
                "name": "provider_id",
                "value": "hogwarts",
            },
        ]

    Returns: list
    """
    course_key = CourseKey.from_string(course_id)
    user = _get_user(username)
    enrollment = CourseEnrollment.get_enrollment(user, course_key)
    return CourseEnrollmentAttribute.get_enrollment_attributes(enrollment)


def unenroll_user_from_all_courses(username):
    """
    Set all of a user's enrollments to inactive.
    :param username: The user being unenrolled.
    :return: A list of all courses from which the user was unenrolled.
    """
    user = _get_user(username)
    enrollments = CourseEnrollment.objects.filter(user=user)
    with transaction.atomic():
        for enrollment in enrollments:
            _update_enrollment(enrollment, is_active=False)

    return {str(enrollment.course_id.org) for enrollment in enrollments}  # pylint: disable=consider-using-set-comprehension


def _get_user(username):
    """Retrieve user with provided username

    Args:
        username: username of the user for which object is to retrieve

    Returns: obj
    """
    try:
        return User.objects.get(username=username)
    except User.DoesNotExist:
        msg = f"Not user with username '{username}' found."
        log.warning(msg)
        raise UserNotFoundError(msg)  # pylint: disable=raise-missing-from  # noqa: B904


def _update_enrollment(enrollment, is_active=None, mode=None):
    enrollment.update_enrollment(is_active=is_active, mode=mode)
    enrollment.save()
    return CourseEnrollmentSerializer(enrollment).data


def _invalid_attribute(attributes):
    """Validate enrollment attribute

    Args:
        attributes(List): List of attribute dicts

    Return:
        list of invalid attributes
    """
    invalid_attributes = []
    for attribute in attributes:
        if "namespace" not in attribute:
            msg = "'namespace' not in enrollment attribute"
            log.warning(msg)
            invalid_attributes.append("namespace")
            raise InvalidEnrollmentAttribute(msg)
        if "name" not in attribute:
            msg = "'name' not in enrollment attribute"
            log.warning(msg)
            invalid_attributes.append("name")
            raise InvalidEnrollmentAttribute(msg)
        if "value" not in attribute:
            msg = "'value' not in enrollment attribute"
            log.warning(msg)
            invalid_attributes.append("value")
            raise InvalidEnrollmentAttribute(msg)

    return invalid_attributes


def get_course_enrollment_info(course_id, include_expired=False):
    """Returns all course enrollment information for the given course.

    Based on the course id, return all related course information.

    Args:
        course_id (str): The course to retrieve enrollment information for.

        include_expired (bool): Boolean denoting whether expired course modes
        should be included in the returned JSON data.

    Returns:
        A serializable dictionary representing the course's enrollment information.

    Raises:
        CourseNotFoundError

    """
    course_key = CourseKey.from_string(course_id)

    try:
        course = CourseOverview.get_from_id(course_key)
    except CourseOverview.DoesNotExist:
        msg = f"Requested enrollment information for unknown course {course_id}"
        log.warning(msg)
        raise CourseNotFoundError(msg)  # pylint: disable=raise-missing-from  # noqa: B904
    return CourseSerializer(course, include_expired=include_expired).data


def get_user_roles(username):
    """
    Returns a set of all roles that this user has.
    :param username: The id of the selected user.
    :return: All roles for all courses that this user has.
    """
    user = _get_user(username)
    return set(get_course_roles(user))


def serialize_enrollments(enrollments):
    """
    Take CourseEnrollment objects and return them in a serialized list.
    """
    return CourseEnrollmentSerializer(enrollments, many=True).data
