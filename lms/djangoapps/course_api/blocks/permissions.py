"""
Encapsulates permissions checks for Course Blocks API
"""
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseStaffRole
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.access_response import AccessResponse
from lms.djangoapps.courseware.access_utils import ACCESS_DENIED, ACCESS_GRANTED, check_public_access
from lms.djangoapps.courseware.courses import get_course
from lms.djangoapps.courseware.exceptions import CourseRunNotFound
from openedx.core.djangoapps.content.course_overviews.models import (
    CourseOverview,  # lint-amnesty, pylint: disable=unused-import
)
from xmodule.course_block import COURSE_VISIBILITY_PUBLIC  # lint-amnesty, pylint: disable=wrong-import-order


def can_access_all_blocks(requesting_user, course_key):
    """
    Returns whether the requesting_user can access all the blocks
    in the course.
    """
    return has_access(requesting_user, CourseStaffRole.ROLE, course_key)


def can_access_others_blocks(requesting_user, course_key):
    """
    Returns whether the requesting_user can access the blocks for
    other users in the given course.
    """
    return has_access(requesting_user, CourseStaffRole.ROLE, course_key)


def can_access_self_blocks(requesting_user: User, course_key: CourseKey) -> AccessResponse:
    """
    Returns whether the requesting_user can access own blocks.
    """
    user_is_enrolled_or_staff = (  # pylint: disable=consider-using-ternary
        (requesting_user.id and CourseEnrollment.is_enrolled(requesting_user, course_key)) or
        has_access(requesting_user, CourseStaffRole.ROLE, course_key)
    )
    if user_is_enrolled_or_staff:
        return ACCESS_GRANTED
    return is_course_public(course_key)


def is_course_public(course_key: CourseKey) -> AccessResponse:
    """
    This checks if a course is publicly accessible or not.
    """
    try:
        course = get_course(course_key, depth=0)
    except CourseRunNotFound:
        return ACCESS_DENIED
    return check_public_access(course, [COURSE_VISIBILITY_PUBLIC])
