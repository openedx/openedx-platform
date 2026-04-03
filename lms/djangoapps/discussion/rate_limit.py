"""
Contains all code related to rate limit
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django_ratelimit import ALL
from django_ratelimit.core import is_ratelimited as _ratelimit
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import CourseAccessRole
from lms.djangoapps.discussion.toggles import ENABLE_RATE_LIMIT_IN_DISCUSSION
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_GROUP_MODERATOR,
    FORUM_ROLE_MODERATOR,
    Role
)

CONTENT_CREATION_GROUP = "content_creation"


def is_rate_limited(request, group, key="user", rate='10/m', method=ALL, increment=True, course_key=None):
    """
    Check if the request is rate limited
    Args:
        request: The HTTP request object.
        group: The group to which the rate limit applies
        key: The key to identify (user or ip).
        rate: The rate limit (default is '10/m' - 10 requests per minute).
        method: The HTTP method to check (default is 'POST').
        increment: Whether to increment the rate limit counter (default is True).
        course_key: The course key for which the rate limit is applied (optional).
    Returns:
        bool: True if the request is rate limited, False otherwise.
    """
    if ENABLE_RATE_LIMIT_IN_DISCUSSION.is_enabled(course_key):
        org_method = request.method
        if increment is False and method != ALL:
            request.method = method
        rate_limited = _ratelimit(request, group=group, key=key, rate=rate, method=method, increment=increment)
        request.method = org_method
        return rate_limited
    return False


def is_content_creation_rate_limited(request, course_key=None, increment=True):
    """
    Check if the request is rate limited for content creation in discussions.
    """
    if course_key and isinstance(course_key, str):
        course_key = CourseKey.from_string(course_key)

    user = request.user
    num_days = settings.SKIP_RATE_LIMIT_ON_ACCOUNT_AFTER_DAYS
    if user.is_staff or (user.date_joined < (timezone.now() - timedelta(days=num_days))):
        return False

    course_roles = ["instructor", "staff", "limited_staff"]
    if CourseAccessRole.objects.filter(user=user, course_id=course_key, role__in=course_roles).exists():
        return False

    forum_roles = {FORUM_ROLE_ADMINISTRATOR, FORUM_ROLE_MODERATOR, FORUM_ROLE_GROUP_MODERATOR, FORUM_ROLE_COMMUNITY_TA}
    user_roles = set(Role.objects.filter(users=user, course_id=course_key).values_list('name', flat=True).distinct())
    if bool(user_roles & forum_roles):
        return False

    return is_rate_limited(request, CONTENT_CREATION_GROUP, key='user', rate=settings.DISCUSSION_RATELIMIT,
                           method='POST', increment=increment, course_key=course_key)
