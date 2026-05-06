"""
A service-like user_info interface.  Could be made into an http API later, but for now
just in-process.  Exposes global and per-course key-value pairs for users.

Implementation note:
Stores global metadata using the UserPreference model, and per-course metadata using the
UserCourseTag model.
"""


from collections import defaultdict

from openedx.core.lib.cache_utils import get_cache

from ..models import UserCourseTag

# Scopes
# (currently only allows per-course tags.  Can be expanded to support
# global tags (e.g. using the existing UserPreferences table))
COURSE_SCOPE = 'course'


class BulkCourseTags:  # pylint: disable=missing-class-docstring
    CACHE_NAMESPACE = 'user_api.course_tag.api'

    @classmethod
    def prefetch(cls, course_id, users):
        """
        Prefetches the value of the course tags for the specified users
        for the specified course_id.

        Args:
            users: iterator of User objects
            course_id: course identifier (CourseKey)

        Returns:
            course_tags: a dict of dicts,
                where the primary key is the user's id
                and the secondary key is the course tag's key
        """
        course_tags = defaultdict(dict)
        for tag in UserCourseTag.objects.filter(user__in=users, course_id=course_id).select_related('user'):
            course_tags[tag.user.id][tag.key] = tag.value
        get_cache(cls.CACHE_NAMESPACE)[cls._cache_key(course_id)] = course_tags

    @classmethod
    def get_course_tag(cls, user_id, course_id, key):
        return get_cache(cls.CACHE_NAMESPACE)[cls._cache_key(course_id)][user_id][key]

    @classmethod
    def is_prefetched(cls, course_id):
        return cls._cache_key(course_id) in get_cache(cls.CACHE_NAMESPACE)

    @classmethod
    def _cache_key(cls, course_id):
        return f'course_tag.{course_id}'


def get_course_tag(user, course_id, key):
    """
    Gets the value of the user's course tag for the specified key in the specified
    course_id.

    Args:
        user: the User object for the course tag
        course_id: course identifier (string)
        key: arbitrary (<=255 char string)

    Returns:
        string value, or None if there is no value saved
    """
    if user.is_anonymous:
        return None

    if BulkCourseTags.is_prefetched(course_id):
        try:
            return BulkCourseTags.get_course_tag(user.id, course_id, key)
        except KeyError:
            return None
    try:
        record = UserCourseTag.objects.get(
            user=user,
            course_id=course_id,
            key=key)

        return record.value
    except UserCourseTag.DoesNotExist:
        return None


def set_course_tag(user, course_id, key, value):
    """
    Sets the value of the user's course tag for the specified key in the specified
    course_id.  Overwrites any previous value.

    The intention is that the values are fairly short, as they will be included in all
    analytics events about this user.

    Args:
        user: the User object
        course_id: course identifier (string)
        key: arbitrary (<=255 char string)
        value: arbitrary string
    """
    # pylint: disable=fixme
    # TODO: There is a risk of IntegrityErrors being thrown here given
    # simultaneous calls from many processes. Handle by retrying after
    # a short delay?

    if not user.id:
        return

    record, _ = UserCourseTag.objects.get_or_create(
        user=user,
        course_id=course_id,
        key=key)

    record.value = value
    record.save()
