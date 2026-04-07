"""
Shared utility code related to discussions.
"""
import logging
from typing import Dict, List, Optional, Tuple

from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.courseware.access import has_access
from openedx.core.djangoapps.course_groups.cohorts import get_cohort_names, is_course_cohorted
from openedx.core.djangoapps.django_comment_common.models import CourseDiscussionSettings
from openedx.core.lib.cache_utils import request_cached
from openedx.core.lib.courses import get_course_by_id
from openedx.core.types import User
from xmodule.course_block import CourseBlock  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.discussion_block import DiscussionXBlock
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.partitions.partitions import (  # lint-amnesty, pylint: disable=wrong-import-order
    ENROLLMENT_TRACK_PARTITION_ID,
    Group,
)
from xmodule.partitions.partitions_service import PartitionService  # lint-amnesty, pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


def get_divided_discussions(
    course: CourseBlock,
    discussion_settings: CourseDiscussionSettings,
) -> Tuple[List[str], List[str]]:
    """
    Returns the course-wide and inline divided discussion ids separately.
    """
    divided_course_wide_discussions = []
    divided_inline_discussions = []

    course_wide_discussions = [topic['id'] for __, topic in course.discussion_topics.items()]
    all_discussions = get_discussion_categories_ids(course, None, include_all=True)

    for divided_discussion_id in discussion_settings.divided_discussions:
        if divided_discussion_id in course_wide_discussions:
            divided_course_wide_discussions.append(divided_discussion_id)
        elif divided_discussion_id in all_discussions:
            divided_inline_discussions.append(divided_discussion_id)

    return divided_course_wide_discussions, divided_inline_discussions


def get_discussion_categories_ids(course: CourseBlock, user: Optional[User], include_all: bool = False) -> List[str]:
    """
    Returns a list of available ids of categories for the course that
    are accessible to the given user.

    Args:
        course: Course for which to get the ids.
        user:  User to check for access.
        include_all: Whether categories from all blocks should be included.

    """
    accessible_discussion_ids = [
        xblock.discussion_id for xblock in get_accessible_discussion_xblocks(course, user, include_all)
    ]
    return course.top_level_discussion_topic_ids + accessible_discussion_ids


def get_accessible_discussion_xblocks(
    course: CourseBlock,
    user: Optional[User],
    include_all: bool = False,
) -> List[DiscussionXBlock]:
    """
    Return a list of all valid discussion xblocks in this course that
    are accessible to the given user.
    """
    include_all = include_all or getattr(user, 'is_community_ta', False)
    return get_accessible_discussion_xblocks_by_course_id(course.id, user, include_all=include_all)


@request_cached()
def get_accessible_discussion_xblocks_by_course_id(
    course_id: CourseKey,
    user: Optional[User] = None,
    include_all: bool = False
) -> List[DiscussionXBlock]:
    """
    Return a list of all valid discussion xblocks in this course.
    Checks for the given user's access if include_all is False.
    """
    all_xblocks = modulestore().get_items(course_id, qualifiers={'category': 'discussion'}, include_orphans=False)

    return [
        xblock for xblock in all_xblocks
        if has_required_keys(xblock) and (include_all or has_access(user, 'load', xblock, course_id))
    ]


def available_division_schemes(course_key: CourseKey) -> List[str]:
    """
    Returns a list of possible discussion division schemes for this course.
    This takes into account if cohorts are enabled and if there are multiple
    enrollment tracks. If no schemes are available, returns an empty list.
    Args:
        course_key: CourseKey

    Returns: list of possible division schemes (for example, CourseDiscussionSettings.COHORT)
    """
    available_schemes = []
    if is_course_cohorted(course_key):
        available_schemes.append(CourseDiscussionSettings.COHORT)
    if enrollment_track_group_count(course_key) > 1:
        available_schemes.append(CourseDiscussionSettings.ENROLLMENT_TRACK)
    return available_schemes


def has_required_keys(xblock: DiscussionXBlock):
    """
    Returns True iff xblock has the proper attributes for generating metadata
    with get_discussion_id_map_entry()
    """
    for key in ('discussion_id', 'discussion_category', 'discussion_target'):
        if getattr(xblock, key, None) is None:
            log.debug(
                "Required key '%s' not in discussion %s, leaving out of category map",
                key,
                xblock.location
            )
            return False
    return True


def enrollment_track_group_count(course_key: CourseKey) -> int:
    """
    Returns the count of possible enrollment track division schemes for this course.
    Args:
        course_key: CourseKey
    Returns:
        Count of enrollment track division scheme
    """
    return len(_get_enrollment_track_groups(course_key))


def _get_enrollment_track_groups(course_key: CourseKey) -> List[Group]:
    """
    Helper method that returns an array of the Groups in the EnrollmentTrackUserPartition for the given course.
    If no such partition exists on the course, an empty array is returned.
    """
    partition_service = PartitionService(course_key)
    partition = partition_service.get_user_partition(ENROLLMENT_TRACK_PARTITION_ID)
    return partition.groups if partition else []


def get_group_names_by_id(course_discussion_settings: CourseDiscussionSettings) -> Dict[str, str]:
    """
    Creates of a dict of group_id to learner-facing group names, for the division_scheme
    in use as specified by course_discussion_settings.
    Args:
        course_discussion_settings: CourseDiscussionSettings model instance

    Returns: dict of group_id to learner-facing group names. If no division_scheme
    is in use, returns an empty dict.
    """
    division_scheme = get_course_division_scheme(course_discussion_settings)
    course_key = course_discussion_settings.course_id
    if division_scheme == CourseDiscussionSettings.COHORT:
        return get_cohort_names(get_course_by_id(course_key))
    elif division_scheme == CourseDiscussionSettings.ENROLLMENT_TRACK:
        # We negate the group_ids from dynamic partitions so that they will not conflict
        # with cohort IDs (which are an auto-incrementing integer field, starting at 1).
        return {-1 * group.id: group.name for group in _get_enrollment_track_groups(course_key)}
    else:
        return {}


def get_course_division_scheme(course_discussion_settings: CourseDiscussionSettings) -> str:
    """
    Returns the division scheme used by the course, from the course discussion settings.
    Args:
        course_discussion_settings (CourseDiscussionSettings): An instance of the CourseDiscussionSettings model

    Returns:
        (string) Returns 'cohort', 'enrollment_track' or 'none'
            depending on the division scheme used by the course.

    """
    division_scheme = course_discussion_settings.division_scheme
    if (
        division_scheme == CourseDiscussionSettings.COHORT and
        not is_course_cohorted(course_discussion_settings.course_id)
    ):
        division_scheme = CourseDiscussionSettings.NONE
    elif (
        division_scheme == CourseDiscussionSettings.ENROLLMENT_TRACK and
        enrollment_track_group_count(course_discussion_settings.course_id) <= 1
    ):
        division_scheme = CourseDiscussionSettings.NONE
    return division_scheme
