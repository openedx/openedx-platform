"""
Enrollment API for creating, updating, and deleting enrollments. Also provides access to enrollment information at a
course level, such as available course modes.

"""


import importlib
import logging

from django.conf import settings
from django.core.cache import cache
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from openedx.core.djangoapps.enrollments import errors
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)

DEFAULT_DATA_API = 'openedx.core.djangoapps.enrollments.data'


def get_verified_enrollments(username, include_inactive=False):
    """Retrieves all the courses in which user is enrolled in a verified mode.

    Takes a user and retrieves all relative enrollments in which the learner is enrolled in a verified mode.
    Includes information regarding how the user is enrolled
    in the the course.

    Args:
        username: The username of the user we want to retrieve course enrollment information for.
        include_inactive (bool): Determines whether inactive enrollments will be included

    Returns:
        A list of enrollment information for the given user.

    Examples:
        >>> get_verified_enrollments("Bob")
        [
            {
                "created": "2014-10-25T20:18:00Z",
                "mode": "verified",
                "is_active": True,
                "user": "Bob",
                "course_details": {
                    "course_id": "edX/edX-Insider/2014T2",
                    "course_name": "edX Insider Course",
                    "enrollment_end": "2014-12-20T20:18:00Z",
                    "enrollment_start": "2014-10-15T20:18:00Z",
                    "course_start": "2015-02-03T00:00:00Z",
                    "course_end": "2015-05-06T00:00:00Z",
                    "course_modes": [
                        {
                            "slug": "honor",
                            "name": "Honor Code Certificate",
                            "min_price": 0,
                            "suggested_prices": "",
                            "currency": "usd",
                            "expiration_datetime": null,
                            "description": null,
                            "sku": null,
                            "bulk_sku": null
                        }
                    ],
                    "invite_only": True
                }
            }
        ]

    """
    enrollments = get_enrollments(username, include_inactive)
    enrollments = filter(lambda enrollment: CourseMode.is_verified_slug(enrollment['mode']), enrollments)
    return list(enrollments)


def get_enrollments(username, include_inactive=False):
    """Retrieves all the courses a user is enrolled in.

    Takes a user and retrieves all relative enrollments. Includes information regarding how the user is enrolled
    in the the course.

    Args:
        username: The username of the user we want to retrieve course enrollment information for.
        include_inactive (bool): Determines whether inactive enrollments will be included

    Returns:
        A list of enrollment information for the given user.

    Examples:
        >>> get_enrollments("Bob")
        [
            {
                "created": "2014-10-20T20:18:00Z",
                "mode": "honor",
                "is_active": True,
                "user": "Bob",
                "course_details": {
                    "course_id": "edX/DemoX/2014T2",
                    "course_name": "edX Demonstration Course",
                    "enrollment_end": "2014-12-20T20:18:00Z",
                    "enrollment_start": "2014-10-15T20:18:00Z",
                    "course_start": "2015-02-03T00:00:00Z",
                    "course_end": "2015-05-06T00:00:00Z",
                    "course_modes": [
                        {
                            "slug": "honor",
                            "name": "Honor Code Certificate",
                            "min_price": 0,
                            "suggested_prices": "",
                            "currency": "usd",
                            "expiration_datetime": null,
                            "description": null,
                            "sku": null,
                            "bulk_sku": null
                        }
                    ],
                    "invite_only": False
                }
            },
            {
                "created": "2014-10-25T20:18:00Z",
                "mode": "verified",
                "is_active": True,
                "user": "Bob",
                "course_details": {
                    "course_id": "edX/edX-Insider/2014T2",
                    "course_name": "edX Insider Course",
                    "enrollment_end": "2014-12-20T20:18:00Z",
                    "enrollment_start": "2014-10-15T20:18:00Z",
                    "course_start": "2015-02-03T00:00:00Z",
                    "course_end": "2015-05-06T00:00:00Z",
                    "course_modes": [
                        {
                            "slug": "honor",
                            "name": "Honor Code Certificate",
                            "min_price": 0,
                            "suggested_prices": "",
                            "currency": "usd",
                            "expiration_datetime": null,
                            "description": null,
                            "sku": null,
                            "bulk_sku": null
                        }
                    ],
                    "invite_only": True
                }
            }
        ]

    """
    return _data_api().get_course_enrollments(username, include_inactive)


def get_enrollment(username, course_id):
    """Retrieves all enrollment information for the user in respect to a specific course.

    Gets all the course enrollment information specific to a user in a course.

    Args:
        username: The user to get course enrollment information for.
        course_id (str): The course to get enrollment information for.

    Returns:
        A serializable dictionary of the course enrollment.

    Example:
        >>> get_enrollment("Bob", "edX/DemoX/2014T2")
        {
            "created": "2014-10-20T20:18:00Z",
            "mode": "honor",
            "is_active": True,
            "user": "Bob",
            "course_details": {
                "course_id": "edX/DemoX/2014T2",
                "course_name": "edX Demonstration Course",
                "enrollment_end": "2014-12-20T20:18:00Z",
                "enrollment_start": "2014-10-15T20:18:00Z",
                "course_start": "2015-02-03T00:00:00Z",
                "course_end": "2015-05-06T00:00:00Z",
                "course_modes": [
                    {
                        "slug": "honor",
                        "name": "Honor Code Certificate",
                        "min_price": 0,
                        "suggested_prices": "",
                        "currency": "usd",
                        "expiration_datetime": null,
                        "description": null,
                        "sku": null,
                        "bulk_sku": null
                    }
                ],
                "invite_only": False
            }
        }

    """
    return _data_api().get_course_enrollment(username, course_id)


def add_enrollment(
    username,
    course_id,
    mode=None,
    is_active=True,
    enrollment_attributes=None,
    enterprise_uuid=None,
    force_enrollment=False,
    include_expired=False
):
    """Enrolls a user in a course.

    Enrolls a user in a course. If the mode is not specified, this will default to `CourseMode.DEFAULT_MODE_SLUG`.

    Arguments:
        username: The user to enroll.
        course_id (str): The course to enroll the user in.
        mode (str): Optional argument for the type of enrollment to create. Ex. 'audit', 'honor', 'verified',
            'professional'. If not specified, this defaults to the default course mode.
        is_active (boolean): Optional argument for making the new enrollment inactive. If not specified, is_active
            defaults to True.
        enrollment_attributes (list): Attributes to be set the enrollment.
        enterprise_uuid (str): Add course enterprise uuid
        force_enrollment (bool): Enroll user even if course enrollment_end date is expired
        include_expired (bool): Boolean denoting whether expired course modes should be included.

    Returns:
        A serializable dictionary of the new course enrollment.

    Example:
        >>> add_enrollment("Bob", "edX/DemoX/2014T2", mode="audit")
        {
            "created": "2014-10-20T20:18:00Z",
            "mode": "audit",
            "is_active": True,
            "user": "Bob",
            "course_details": {
                "course_id": "edX/DemoX/2014T2",
                "course_name": "edX Demonstration Course",
                "enrollment_end": "2014-12-20T20:18:00Z",
                "enrollment_start": "2014-10-15T20:18:00Z",
                "course_start": "2015-02-03T00:00:00Z",
                "course_end": "2015-05-06T00:00:00Z",
                "course_modes": [
                    {
                        "slug": "audit",
                        "name": "Audit",
                        "min_price": 0,
                        "suggested_prices": "",
                        "currency": "usd",
                        "expiration_datetime": null,
                        "description": null,
                        "sku": null,
                        "bulk_sku": null
                    }
                ],
                "invite_only": False
            }
        }
    """
    if mode is None:
        mode = _default_course_mode(course_id)
    validate_course_mode(course_id, mode, is_active=is_active, include_expired=include_expired)
    enrollment = _data_api().create_course_enrollment(
        username, course_id, mode, is_active, enterprise_uuid, force_enrollment=force_enrollment
    )

    if enrollment_attributes is not None:
        set_enrollment_attributes(username, course_id, enrollment_attributes)

    return enrollment


def update_enrollment(
    username, course_id, mode=None, is_active=None, enrollment_attributes=None, include_expired=False
):
    """Updates the course mode for the enrolled user.

    Update a course enrollment for the given user and course.

    Arguments:
        username: The user associated with the updated enrollment.
        course_id (str): The course associated with the updated enrollment.

    Keyword Arguments:
        mode (str): The new course mode for this enrollment.
        is_active (bool): Sets whether the enrollment is active or not.
        enrollment_attributes (list): Attributes to be set the enrollment.
        include_expired (bool): Boolean denoting whether expired course modes should be included.

    Returns:
        A serializable dictionary representing the updated enrollment.

    Example:
        >>> update_enrollment("Bob", "edX/DemoX/2014T2", "honor")
        {
            "created": "2014-10-20T20:18:00Z",
            "mode": "honor",
            "is_active": True,
            "user": "Bob",
            "course_details": {
                "course_id": "edX/DemoX/2014T2",
                "course_name": "edX Demonstration Course",
                "enrollment_end": "2014-12-20T20:18:00Z",
                "enrollment_start": "2014-10-15T20:18:00Z",
                "course_start": "2015-02-03T00:00:00Z",
                "course_end": "2015-05-06T00:00:00Z",
                "course_modes": [
                    {
                        "slug": "honor",
                        "name": "Honor Code Certificate",
                        "min_price": 0,
                        "suggested_prices": "",
                        "currency": "usd",
                        "expiration_datetime": null,
                        "description": null,
                        "sku": null,
                        "bulk_sku": null
                    }
                ],
                "invite_only": False
            }
        }

    """
    log.info('Starting Update Enrollment process for user {user} in course {course} to mode {mode}'.format(  # noqa: UP032  # pylint: disable=line-too-long
        user=username,
        course=course_id,
        mode=mode,
    ))
    if mode is not None:
        validate_course_mode(course_id, mode, is_active=is_active, include_expired=include_expired)
    enrollment = _data_api().update_course_enrollment(username, course_id, mode=mode, is_active=is_active)
    if enrollment is None:  # pylint: disable=no-else-raise
        msg = f"Course Enrollment not found for user {username} in course {course_id}"
        log.warning(msg)
        raise errors.EnrollmentNotFoundError(msg)
    else:
        if enrollment_attributes is not None:
            set_enrollment_attributes(username, course_id, enrollment_attributes)
    log.info('Course Enrollment updated for user {user} in course {course} to mode {mode}'.format(  # noqa: UP032
        user=username,
        course=course_id,
        mode=mode
    ))
    return enrollment


def get_course_enrollment_details(course_id, include_expired=False):
    """Get the course modes for course. Also get enrollment start and end date, invite only, etc.

    Given a course_id, return a serializable dictionary of properties describing course enrollment information.

    Args:
        course_id (str): The Course to get enrollment information for.

        include_expired (bool): Boolean denoting whether expired course modes
        should be included in the returned JSON data.

    Returns:
        A serializable dictionary of course enrollment information.

    Example:
        >>> get_course_enrollment_details("edX/DemoX/2014T2")
        {
            "course_id": "edX/DemoX/2014T2",
            "course_name": "edX Demonstration Course",
            "enrollment_end": "2014-12-20T20:18:00Z",
            "enrollment_start": "2014-10-15T20:18:00Z",
            "course_start": "2015-02-03T00:00:00Z",
            "course_end": "2015-05-06T00:00:00Z",
            "course_modes": [
                {
                    "slug": "honor",
                    "name": "Honor Code Certificate",
                    "min_price": 0,
                    "suggested_prices": "",
                    "currency": "usd",
                    "expiration_datetime": null,
                    "description": null,
                    "sku": null,
                    "bulk_sku": null
                }
            ],
            "invite_only": False
        }

    """
    cache_key = f'enrollment.course.details.{course_id}.{include_expired}'
    cached_enrollment_data = None
    try:
        cached_enrollment_data = cache.get(cache_key)
    except Exception:  # pylint: disable=broad-except
        # The cache backend could raise an exception (for example, memcache keys that contain spaces)
        log.exception("Error occurred while retrieving course enrollment details from the cache")

    if cached_enrollment_data:
        return cached_enrollment_data

    course_enrollment_details = _data_api().get_course_enrollment_info(course_id, include_expired)

    try:
        cache_time_out = getattr(settings, 'ENROLLMENT_COURSE_DETAILS_CACHE_TIMEOUT', 60)
        cache.set(cache_key, course_enrollment_details, cache_time_out)
    except Exception:
        # Catch any unexpected errors during caching.
        log.exception("Error occurred while caching course enrollment details for course %s", course_id)
        raise errors.CourseEnrollmentError("An unexpected error occurred while retrieving course enrollment details.")  # pylint: disable=raise-missing-from,line-too-long  # noqa: B904

    return course_enrollment_details


def set_enrollment_attributes(username, course_id, attributes):
    """Set enrollment attributes for the enrollment of given user in the
    course provided.

    Args:
        course_id: The Course to set enrollment attributes for.
        username: The User to set enrollment attributes for.
        attributes (list): Attributes to be set.

    Example:
        >>>set_enrollment_attributes(
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
    _data_api().add_or_update_enrollment_attr(username, course_id, attributes)


def get_enrollment_attributes(username, course_id):
    """Retrieve enrollment attributes for given user for provided course.

    Args:
        username: The User to get enrollment attributes for
        course_id: The Course to get enrollment attributes for.

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
    return _data_api().get_enrollment_attributes(username, course_id)


def _default_course_mode(course_id):
    """Return the default enrollment for a course.

    Special case the default enrollment to return if nothing else is found.

    Arguments:
        course_id (str): The course to check against for available course modes.

    Returns:
        str
    """
    course_modes = CourseMode.modes_for_course(CourseKey.from_string(course_id))
    available_modes = [m.slug for m in course_modes]

    if CourseMode.DEFAULT_MODE_SLUG in available_modes:
        return CourseMode.DEFAULT_MODE_SLUG
    elif 'audit' in available_modes:
        return 'audit'
    elif 'honor' in available_modes:
        return 'honor'

    return CourseMode.DEFAULT_MODE_SLUG


def validate_course_mode(course_id, mode, is_active=None, include_expired=False):
    """Checks to see if the specified course mode is valid for the course.

    If the requested course mode is not available for the course, raise an error with corresponding
    course enrollment information.

    Arguments:
        course_id (str): The course to check against for available course modes.
        mode (str): The slug for the course mode specified in the enrollment.

    Keyword Arguments:
        is_active (bool): Whether the enrollment is to be activated or deactivated.
        include_expired (bool): Boolean denoting whether expired course modes should be included.

    Returns:
        None

    Raises:
        CourseModeNotFound: raised if the course mode is not found.
    """
    # If the client has requested an enrollment deactivation, we want to include expired modes
    # in the set of available modes. This allows us to unenroll users from expired modes.
    # If include_expired is set as True we should not redetermine its value.
    if not include_expired:
        include_expired = not is_active if is_active is not None else False

    course_enrollment_info = _data_api().get_course_enrollment_info(course_id, include_expired=include_expired)
    course_modes = course_enrollment_info["course_modes"]
    available_modes = [m['slug'] for m in course_modes]
    if mode not in available_modes:
        msg = (
            "Specified course mode '{mode}' unavailable for course {course_id}.  "
            "Available modes were: {available}"
        ).format(
            mode=mode,
            course_id=course_id,
            available=", ".join(available_modes)
        )
        log.warning(msg)
        raise errors.CourseModeNotFoundError(msg, course_enrollment_info)


def unenroll_user_from_all_courses(username):
    """
    Unenrolls a specified user from all of the courses they are currently enrolled in.
    :param username: The id of the user being unenrolled.
    :return: The IDs of all of the organizations from which the learner was unenrolled.
    """
    return _data_api().unenroll_user_from_all_courses(username)


def get_user_roles(username):
    """
    Returns a list of all roles that this user has.
    :param username: The id of the selected user.
    :return: All roles for all courses that this user has.
    """
    return _data_api().get_user_roles(username)


def serialize_enrollments(enrollments):
    """
    Takes a list of CourseEnrollment objects and serializes them.

    Serialized result will be compatible will the results from `get_enrollments`. If
    the `get_enrollments` function changes to return non-serialized data, this will
    need to change as well.

    Args:
        enrollments: list of CourseEnrollment objects to be serialized

    Returns:
        A list of enrollments
    """
    return _data_api().serialize_enrollments(enrollments)


def is_enrollment_valid_for_proctoring(username, course_id):
    """
    Returns a boolean value regarding whether user's course enrollment is eligible for proctoring.

    Returns false if:
        * special exams aren't enabled
        * the enrollment is not active
        * proctored exams aren't enabled for the course
        * the course mode is audit

    Arguments:
        * username (str): The user associated with the enrollment.
        * course_id (str): The course id associated with the enrollment.
    """
    if not settings.FEATURES.get('ENABLE_SPECIAL_EXAMS'):
        return False

    # Verify that the learner's enrollment is active
    enrollment = _data_api().get_course_enrollment(username, str(course_id))
    if not enrollment or not enrollment['is_active']:
        return False

    # Check that the course has proctored exams enabled
    course_block = modulestore().get_course(course_id)
    if not course_block or not course_block.enable_proctored_exams:
        return False

    # Only allow verified modes
    appropriate_modes = [
        CourseMode.VERIFIED, CourseMode.MASTERS, CourseMode.PROFESSIONAL, CourseMode.EXECUTIVE_EDUCATION
    ]

    # If the proctoring provider allows learners in honor mode to take exams, include it
    if settings.PROCTORING_BACKENDS.get(course_block.proctoring_provider, {}).get('allow_honor_mode'):
        appropriate_modes.append(CourseMode.HONOR)

    if enrollment['mode'] not in appropriate_modes:
        return False

    return True


def _data_api():
    """Returns a Data API.
    This relies on Django settings to find the appropriate data API.

    """
    # We retrieve the settings in-line here (rather than using the
    # top-level constant), so that @override_settings will work
    # in the test suite.
    api_path = getattr(settings, "ENROLLMENT_DATA_API", DEFAULT_DATA_API)

    try:
        return importlib.import_module(api_path)
    except (ImportError, ValueError):
        log.exception(f"Could not load module at '{api_path}'")
        raise errors.EnrollmentApiLoadError(api_path)  # pylint: disable=raise-missing-from  # noqa: B904
