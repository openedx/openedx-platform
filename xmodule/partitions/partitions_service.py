"""
This is a service-like API that assigns tracks which groups users are in for various
user partitions.  It uses the user_service key/value store provided by the LMS runtime to
persist the assignments.
"""
import logging
from typing import Dict

from django.conf import settings
from django.contrib.auth import get_user_model
from opaque_keys.edx.keys import CourseKey
from openedx.core.lib.cache_utils import request_cached
from openedx.core.lib.dynamic_partitions_generators import DynamicPartitionGeneratorsPluginManager

from xmodule.modulestore.django import modulestore
from xmodule.partitions.partitions import get_partition_from_id
from .partitions import Group

User = get_user_model()

log = logging.getLogger(__name__)

FEATURES = getattr(settings, 'FEATURES', {})


@request_cached()
def get_all_partitions_for_course(course, active_only=False):
    """
    A method that returns all `UserPartitions` associated with a course, as a List.
    This will include the ones defined in course.user_partitions, but it may also
    include dynamically included partitions (such as the `EnrollmentTrackUserPartition`).

    Args:
        course: the course for which user partitions should be returned.
        active_only: if `True`, only partitions with `active` set to True will be returned.

        Returns:
            A List of UserPartitions associated with the course.
    """
    all_partitions = course.user_partitions + _get_dynamic_partitions(course)
    if active_only:
        all_partitions = [partition for partition in all_partitions if partition.active]
    return all_partitions


def get_user_partition_groups(course_key: CourseKey, user_partitions: list, user: User,
                              partition_dict_key: str = 'name') -> Dict[str, Group]:
    """
    Collect group ID for each partition in this course for this user.
     Arguments:
        course_key (CourseKey)
        user_partitions (list[UserPartition])
        user (User)
        partition_dict_key - i.e. 'id', 'name' depending on which partition attribute you want as a key.
     Returns:
        dict[partition_dict_key: Group]: Mapping from user partitions to the group to
            which the user belongs in each partition. If the user isn't
            in a group for a particular partition, then that partition's
            ID will not be in the dict.
    """

    partition_groups = {}
    for partition in user_partitions:
        group = partition.scheme.get_group_for_user(
            course_key,
            user,
            partition,
        )

        if group is not None:
            partition_groups[getattr(partition, partition_dict_key)] = group
    return partition_groups


def _get_dynamic_partitions(course):
    """
    Return the dynamic user partitions for this course.
    If none exists, returns an empty array.
    """
    dynamic_partition_generators = DynamicPartitionGeneratorsPluginManager.get_available_plugins().values()
    generated_partitions = []
    for generator in dynamic_partition_generators:
        generated_partition = generator(course)
        if generated_partition:
            # If the generator returns a list of partitions, add them all to the list.
            # Otherwise, just add the single partition. This is needed for cases where
            # a single generator can return multiple partitions, such as the TeamUserPartition.
            if isinstance(generated_partition, list):
                generated_partitions.extend(generated_partition)
            else:
                generated_partitions.append(generated_partition)

    return generated_partitions


class PartitionService:
    """
    This is an XBlock service that returns information about the user partitions associated
    with a given course.
    """

    def __init__(self, course_id: CourseKey, cache=None, course=None):
        """Create a new ParititonService. This is user-specific."""

        # There is a surprising amount of complexity in how to save the
        # course_id we were passed in this constructor.
        if course_id.org and course_id.course and course_id.run:
            # This is the normal case, where we're instantiated with a CourseKey
            # that has org, course, and run information. It will also often have
            # a version_guid attached in this case, and we will want to strip
            # that off in most cases.
            #
            # The reason for this is that the PartitionService is going to get
            # recreated for every runtime (i.e. every block that's created for a
            # user). Say you do the following:
            #
            # 1. You query the modulestore's get_item() for block A.
            # 2. You update_item() for a different block B
            # 3. You publish block B.
            #
            # When get_item() was called, a SplitModuleStoreRuntime was created
            # for block A and it was given a CourseKey that had the version_guid
            # encoded in it. If we persist that CourseKey with the version guid
            # intact, then it will be incorrect after B is published, and any
            # future access checks on A will break because it will try to query
            # for a version of the course that is no longer published.
            #
            # Note that we still need to keep the branch information, or else
            # this wouldn't work right in preview mode.
            self._course_id = course_id.replace(version_guid=None)
        else:
            # If we're here, it means that the CourseKey we were sent doesn't
            # have an org, course, and run. A much less common (but still legal)
            # way to query by CourseKey involves a version_guid-only query, i.e.
            # everything is None but the version_guid. In this scenario, it
            # doesn't make sense to remove the one identifying piece of
            # information we have, so we just assign the CourseKey without
            # modification. We *could* potentially query the modulestore
            # here and get the more normal form of the CourseKey, but that would
            # be much more expensive and require database access.
            self._course_id = course_id

        self._cache = cache
        self.course = course

    def get_course(self):
        """
        Return the course instance associated with this PartitionService.
        This default implementation looks up the course from the modulestore.
        """
        return self.course or modulestore().get_course(self._course_id)

    @property
    def course_partitions(self):
        """
        Return the set of partitions assigned to self._course_id (both those set directly on the course
        through course.user_partitions, and any dynamic partitions that exist). Note: this returns
        both active and inactive partitions.
        """
        if course := self.get_course():
            return get_all_partitions_for_course(course)
        return []

    def get_user_group_id_for_partition(self, user, user_partition_id):
        """
        If the user is already assigned to a group in user_partition_id, return the
        group_id.

        If not, assign them to one of the groups, persist that decision, and
        return the group_id.

        Args:
            user_partition_id -- an id of a partition that's hopefully in the
                runtime.user_partitions list.

        Returns:
            The id of one of the groups in the specified user_partition_id (as a string).

        Raises:
            ValueError if the user_partition_id isn't found.
        """
        cache_key = "PartitionService.ugidfp.{}.{}.{}".format(
            user.id, self._course_id, user_partition_id
        )

        if self._cache and (cache_key in self._cache):
            return self._cache[cache_key]

        user_partition = self.get_user_partition(user_partition_id)
        if user_partition is None:
            raise ValueError(
                "Configuration problem!  No user_partition with id {} "
                "in course {}".format(user_partition_id, self._course_id)
            )

        group = self.get_group(user, user_partition)
        group_id = group.id if group else None

        if self._cache is not None:
            self._cache[cache_key] = group_id

        return group_id

    def get_user_partition(self, user_partition_id):
        """
        Look for a user partition with a matching id in the course's partitions.
        Note that this method can return an inactive user partition.

        Returns:
            A UserPartition, or None if not found.
        """
        return get_partition_from_id(self.course_partitions, user_partition_id)

    def get_group(self, user, user_partition, assign=True):
        """
        Returns the group from the specified user partition to which the user is assigned.
        If the user has not yet been assigned, a group will be chosen for them based upon
        the partition's scheme.
        """
        return user_partition.scheme.get_group_for_user(
            self._course_id, user, user_partition, assign=assign,
        )
