"""
The Python API layer of the country access settings. Essentially the middle tier of the project, responsible for all
business logic that is not directly tied to the data itself.

This API is exposed via the middleware(emabargo/middileware.py) layer but may be used directly in-process.

"""

import logging
from typing import List, NamedTuple, Optional  # noqa: UP035

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

from .models import CountryAccessRule, GlobalRestrictedCountry, RestrictedCourse

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
    the `is_disabled_access_check` flag, then the user can still view that course - unless the block is coming from
    `GlobalRestrictedCountry`, which `is_disabled_access_check` (a per-course override) can never bypass.

    Arguments:
        request: The current request to be checked.
        course_key: Location of the course the user is trying to access.
        access_point: Type of page being accessed (e.g. 'courseware', 'enrollment', etc)
        user: User to check for (uses request.user if None)

    Returns:
        If blocked, a URL path to a page explaining why the user was blocked. Else None.
    """
    if settings.EMBARGO:
        client_ips = ip.get_all_client_ips(request)
        user = user or request.user
        result = _check_course_access(course_key, user=user, ip_addresses=client_ips, url=request.path)
        if not result.allowed:
            if access_point == "courseware":
                if not RestrictedCourse.is_disabled_access_check(course_key) or result.blocked_globally:
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

    A country listed in `GlobalRestrictedCountry` blocks every course, regardless
    of whether the course has a `RestrictedCourse` entry. `CountryAccessRule`
    checks only apply on top of that for courses that do have one.

    Arguments:
        course_key: Location of the course the user is trying to access.
        user: The user making the request. Can be None, in which case the user's profile country will not be checked.
        ip_addresses: The full external chain of IP addresses of the request.
        url: The URL the user is trying to access. Used in log messages.

    Returns:
        True if the user has access to the course; False otherwise

    """
    return _check_course_access(course_key, user=user, ip_addresses=ip_addresses, url=url).allowed


class _AccessCheckResult(NamedTuple):
    """
    Result of `_check_course_access`.

    `blocked_globally` reflects only whether the request's country matched
    `GlobalRestrictedCountry` - it's set independently of `allowed` (a staff
    user can have `allowed=True` and `blocked_globally=True` at once, since
    staff bypass every block). Callers that care "was this actually denied,
    and for which reason" should check `blocked_globally` together with
    `not allowed`, not on its own.
    """
    allowed: bool
    blocked_globally: bool


def _check_course_access(
        course_key: CourseKey,
        user: Optional[types.User] = None,  # noqa: UP045
        ip_addresses: Optional[List[str]] = None,  # noqa: UP006, UP045
        url: Optional[str] = None,  # noqa: UP045
) -> _AccessCheckResult:
    """
    Does the real work for `check_course_access`, also reporting whether a block came from
    `GlobalRestrictedCountry` - `redirect_if_blocked` needs that to know whether a per-course
    `disable_access_check` override may apply (it may only override a `CountryAccessRule` block,
    never a global one).

    The global check runs across the profile country and every IP address before any
    per-course `CountryAccessRule` check is considered, so a global match is never missed
    just because an earlier signal happened to also fail a per-course rule first. The
    (cached) profile country goes first so a globally blocked user never pays for GeoIP.
    """
    # No-op if the country access feature is not enabled
    if not settings.EMBARGO:
        return _AccessCheckResult(True, False)

    # Check whether there are any per-course or global restrictions at all.
    # If neither applies, skip the (non-free) IP/profile country lookups below.
    course_is_restricted = RestrictedCourse.is_restricted_course(course_key)
    globally_restricted_countries = GlobalRestrictedCountry.get_countries()

    if not course_is_restricted and not globally_restricted_countries:
        return _AccessCheckResult(True, False)

    # The profile country is cached (see `_get_user_country_from_profile`), so it's the
    # cheapest signal we have: check it against the global list first, and a globally
    # blocked user costs no GeoIP lookups at all.
    profile_country = _get_user_country_from_profile(user) if user is not None else None

    if profile_country is not None and profile_country in globally_restricted_countries:
        return _AccessCheckResult(_deny_unless_staff(
            user, course_key,
            (
                "Blocking user %s from accessing course %s at %s "
                "because the user's profile country %s is globally restricted."
            ),
            user.id, course_key, url, profile_country,
        ), True)

    # Resolve each IP's country lazily and cache it in `ip_countries`, so a global
    # block on an early IP skips GeoIP lookups for the rest of the chain, while the
    # per-course pass further down still reuses whatever was already resolved here.
    ip_countries = []
    for ip_address in (ip_addresses or []):
        country = country_code_from_ip(ip_address)
        ip_countries.append((ip_address, country))
        if country in globally_restricted_countries:
            return _AccessCheckResult(_deny_unless_staff(
                user, course_key,
                (
                    "Blocking user %s from accessing course %s at %s "
                    "because the user's IP address %s appears to be "
                    "located in globally restricted country %s."
                ),
                getattr(user, 'id', '<Not Authenticated>'), course_key, url, ip_address, country,
            ), True)

    if not course_is_restricted:
        return _AccessCheckResult(True, False)

    # Per-course pass: only relevant once we know the request isn't globally blocked.
    for ip_address, country in ip_countries:
        if not CountryAccessRule.check_country_access(course_key, country):
            return _AccessCheckResult(_deny_unless_staff(
                user, course_key,
                (
                    "Blocking user %s from accessing course %s at %s "
                    "because the user's IP address %s appears to be "
                    "located in %s."
                ),
                getattr(user, 'id', '<Not Authenticated>'), course_key, url, ip_address, country,
            ), False)

    if profile_country is not None and not CountryAccessRule.check_country_access(course_key, profile_country):
        return _AccessCheckResult(_deny_unless_staff(
            user, course_key,
            (
                "Blocking user %s from accessing course %s at %s "
                "because the user's profile country is %s."
            ),
            user.id, course_key, url, profile_country,
        ), False)

    return _AccessCheckResult(True, False)


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


def _deny_unless_staff(
    user: Optional[types.User],  # noqa: UP045
    course_key: CourseKey,
    log_message: str,
    *log_args,
) -> bool:
    """
    Deny access (after logging why), unless the user is global or course staff.

    Global and course staff always get access, regardless of embargo settings.
    Callers should only invoke this once a block would otherwise occur - the
    underlying role lookup is not free, and most requests are never blocked.
    """
    if user is not None and has_course_author_access(user, course_key):
        return True
    log.info(log_message, *log_args)
    return False


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
        if profile is not None and profile.country is not None:
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
