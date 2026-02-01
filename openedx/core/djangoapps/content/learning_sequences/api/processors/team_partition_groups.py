"""
Outline processors for applying team user partition groups.
"""
import logging
from datetime import datetime
from typing import Dict

from opaque_keys.edx.keys import CourseKey

from openedx.core import types
from openedx.core.djangoapps.content.learning_sequences.api.processors.base import OutlineProcessor
from openedx.core.lib.teams_config import create_team_set_partitions_with_course_id, CONTENT_GROUPS_FOR_TEAMS
from xmodule.modulestore.django import modulestore
from xmodule.partitions.partitions import Group
from xmodule.partitions.partitions_service import get_user_partition_groups

log = logging.getLogger(__name__)


class TeamPartitionGroupsOutlineProcessor(OutlineProcessor):
    """
    Processor for applying all user partition groups to the course outline.

    This processor is used to remove content from the course outline based on
    the user's team membership. It is used in the courseware API to remove
    content from the course outline before it is returned to the client.
    """
    def __init__(self, course_key: CourseKey, user: types.User, at_time: datetime):
        """
        Attributes:
            current_user_groups (Dict[str, Group]): The groups to which the user
                belongs in each partition.
        """
        super().__init__(course_key, user, at_time)
        self.current_user_groups: Dict[str, Group] = {}

    def load_data(self, _) -> None:
        """
        Pull team groups for this course and which group the user is in.
        """
        course = modulestore().get_course(self.course_key)
        if not course.teams_enabled or not CONTENT_GROUPS_FOR_TEAMS.is_enabled(self.course_key):
            return

        user_partitions = create_team_set_partitions_with_course_id(self.course_key)
        self.current_user_groups = get_user_partition_groups(
            self.course_key,
            user_partitions,
            self.user,
            partition_dict_key="id",
        )

    def _is_user_excluded_by_partition_group(self, user_partition_groups):
        """
        Is the user part of the group to which the block is restricting content?

        Arguments:
            user_partition_groups (Dict[int, Set(int)]): Mapping from partition
                ID to the groups to which the user belongs in that partition.

        Returns:
            bool: True if the user is excluded from the content, False otherwise.
            The user is excluded from the content if and only if, for a non-empty
            partition group, the user is not in any of the groups for that partition.
        """
        course = modulestore().get_course(self.course_key)
        if not course.teams_enabled or not CONTENT_GROUPS_FOR_TEAMS.is_enabled(self.course_key):
            return False

        if not user_partition_groups:
            return False

        for partition_id, groups in user_partition_groups.items():
            if partition_id not in self.current_user_groups:
                continue
            if self.current_user_groups[partition_id].id in groups:
                return False

        return True

    def usage_keys_to_remove(self, full_course_outline):
        """
        Content group exclusions remove the content entirely.

        This method returns the usage keys of all content that should be
        removed from the course outline based on the user's team membership.
        In this context, a team within a team-set maps to a user partition group.
        """
        removed_usage_keys = set()
        for section in full_course_outline.sections:
            remove_all_children = False
            if self._is_user_excluded_by_partition_group(
                section.user_partition_groups
            ):
                removed_usage_keys.add(section.usage_key)
                remove_all_children = True
            for seq in section.sequences:
                if remove_all_children or self._is_user_excluded_by_partition_group(
                    seq.user_partition_groups
                ):
                    removed_usage_keys.add(seq.usage_key)
        return removed_usage_keys
