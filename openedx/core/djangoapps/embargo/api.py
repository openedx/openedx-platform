"""
The Python API layer of the country access settings. Essentially the middle tier of the project, responsible for all
business logic that is not directly tied to the data itself.

This API is exposed via the middleware(emabargo/middileware.py) layer but may be used directly in-process.

"""

import logging
from typing import List, Optional  # noqa: UP035

from django.conf import settings
from django.core.cache import cache
from edx_django_utils import ip
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from common.djangoapps.student.auth import has_course_author_access
from openedx.core import types
from openedx.core.djangoapps.geoinfo.api import country_code_from_ip

from .models import CountryAccessRule, RestrictedCourse

log = logging.getLogger(__name__)


def redirect_if_blocked(
    request: Request,
    course_key: CourseKey,
    access_point: str = 'enrollment',
    user: Optional[types.User] = None,  # noqa: UP045
) -> Optional[str]:  # noqa: UP045
    """
    Redirect if the user does not have access to the course.

    Even if the user would normally be blocked, if the given access_point is 'courseware' and the course has enabled
    the `is_disabled_access_check` flag, then the user can still view that course.

    Arguments:
        request: The current request to be checked.
        course_key: Location of the course the user is trying to access.
        access_point: Type of page being accessed (e.g. 'courseware', 'enrollment', etc)
        user: User to check for (uses request.user if None)

    Returns:
        If blocked, a URL path to a page explaining why the user was blocked. Else None.
    """
    if settings.FEATURES.get('EMBARGO'):
        client_ips = ip.get_all_client_ips(request)
        user = user or request.user
        is_blocked = not check_course_access(course_key, user=user, ip_addresses=client_ips, url=request.path)
        if is_blocked:
            if access_point == "courseware":
                if not RestrictedCourse.is_disabled_access_check(course_key):
                    return message_url_path(course_key, access_point)
            else:
                return message_url_path(course_key, access_point)


def check_course_access(
        course_key: CourseKey,
        user: Optional[types.User] = None,  # noqa: UP045
        ip_addresses: Optional[List[str]] = None,  # noqa: UP006, UP045
        url: Optional[str] = None,  # noqa: UP045
) -> bool:
    """
    Check is the user with this ip_addresses chain has access to the given course

    Arguments:
        course_key: Location of the course the user is trying to access.
        user: The user making the request. Can be None, in which case the user's profile country will not be checked.
        ip_addresses: The full external chain of IP addresses of the request.
        url: The URL the user is trying to access. Used in log messages.

    Returns:
        True if the user has access to the course; False otherwise

    """
    # No-op if the country access feature is not enabled
    if not settings.FEATURES.get('EMBARGO'):
        return True

    # First, check whether there are any restrictions on the course.
    # If not, then we do not need to do any further checks
    course_is_restricted = RestrictedCourse.is_restricted_course(course_key)

    if not course_is_restricted:
        return True

    # Always give global and course staff access, regardless of embargo settings.
    if user is not None and has_course_author_access(user, course_key):
        return True

    if ip_addresses is not None:
        # Check every IP address provided and deny access if ANY of them fail our country checks
        for ip_address in ip_addresses:
            # Retrieve the country code from the IP address
            # and check it against the allowed countries list for a course
            user_country_from_ip = country_code_from_ip(ip_address)

            if not CountryAccessRule.check_country_access(course_key, user_country_from_ip):
                log.info(
                    (
                        "Blocking user %s from accessing course %s at %s "
                        "because the user's IP address %s appears to be "
                        "located in %s."
                    ),
                    getattr(user, 'id', '<Not Authenticated>'),
                    course_key,
                    url,
                    ip_address,
                    user_country_from_ip
                )
                return False

    if user is not None:
        # Retrieve the country code from the user's profile
        # and check it against the allowed countries list for a course.
        user_country_from_profile = _get_user_country_from_profile(user)

        if not CountryAccessRule.check_country_access(course_key, user_country_from_profile):
            log.info(
                (
                    "Blocking user %s from accessing course %s at %s "
                    "because the user's profile country is %s."
                ),
                user.id, course_key, url, user_country_from_profile
            )
            return False

    return True


def message_url_path(course_key: CourseKey, access_point: str) -> str:
    """
    Determine the URL path for the message explaining why the user was blocked.

    This is configured per-course.  See `RestrictedCourse` in the `embargo.models`
    module for more details.

    Arguments:
        course_key: The location of the course.
        access_point: How the user was trying to access the course. Can be either "enrollment" or "courseware".

    Returns:
        The URL path to a page explaining why the user was blocked.

    Raises:
        InvalidAccessPoint: Raised if access_point is not a supported value.

    """
    return RestrictedCourse.message_url_path(course_key, access_point)


def _get_user_country_from_profile(user: types.User) -> str:
    """
    Check whether the user is embargoed based on the country code in the user's profile.

    Args:
        user (User): The user attempting to access courseware.

    Returns:
        user country from profile.

    """
    cache_key = f'user.{user.id}.profile.country'
    profile_country = cache.get(cache_key)
    if profile_country is None:
        profile = getattr(user, 'profile', None)
        if profile is not None and profile.country.code is not None:
            profile_country = profile.country.code.upper()
        else:
            profile_country = ""
        cache.set(cache_key, profile_country)

    return profile_country


def get_embargo_response(request: Request, course_key: CourseKey, user: types.User) -> Optional[Response]:  # noqa: UP045  # pylint: disable=line-too-long
    """
    Check whether any country access rules block the user from enrollment.

    Args:
        request: The request object
        course_key: The requested course ID
        user: The current user object

    Returns:
        Response of the embargo page if embargoed, None if not

    """
    redirect_url = redirect_if_blocked(request, course_key, user=user)
    if redirect_url:
        return Response(
            status=status.HTTP_403_FORBIDDEN,
            data={
                "message": (  # noqa: UP032
                    "Users from this location cannot access the course '{course_id}'."
                ).format(course_id=str(course_key)),
                "user_message_url": request.build_absolute_uri(redirect_url)
            }
        )
