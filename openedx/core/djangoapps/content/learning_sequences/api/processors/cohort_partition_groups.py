# lint-amnesty, pylint: disable=missing-module-docstring
import logging
from datetime import datetime
from typing import Union

from opaque_keys.edx.keys import CourseKey

from openedx.core import types
from openedx.core.djangoapps.course_groups.cohorts import (
    get_cohort,
    get_cohorted_user_partition_id,
    get_group_info_for_cohort,
)

from .base import OutlineProcessor

log = logging.getLogger(__name__)


class CohortPartitionGroupsOutlineProcessor(OutlineProcessor):
    """
    Processor for applying cohort user partition groups.

    """
    def __init__(self, course_key: CourseKey, user: types.User, at_time: datetime):
        super().__init__(course_key, user, at_time)
        self.user_cohort_group_id: Union[int, None] = None
        self.cohorted_partition_id: Union[int, None] = None

    def load_data(self, full_course_outline) -> None:
        """
        Load the cohorted partition id and the user's group id.
        """

        # It is possible that a cohort is not linked to any content group/partition.
        # This is why the cohorted_partition_id needs to be set independently
        # of a particular user's cohort.
        self.cohorted_partition_id = get_cohorted_user_partition_id(self.course_key)

        if self.cohorted_partition_id:
            user_cohort = get_cohort(self.user, self.course_key)

            if user_cohort:
                self.user_cohort_group_id, _ = get_group_info_for_cohort(user_cohort)

    def _is_user_excluded_by_partition_group(self, user_partition_groups) -> bool:
        """
        Is the user part of the group to which the block is restricting content?
        """
        if not user_partition_groups:
            return False

        groups = user_partition_groups.get(self.cohorted_partition_id)
        if not groups:
            return False

        if self.user_cohort_group_id not in groups:
            # If the user's group (cohort) does not belong
            # to the partition of the block or the user's cohort
            # is not linked to a content group (user_cohort_group_id is None),
            # the block should be removed
            return True
        return False

    def usage_keys_to_remove(self, full_course_outline):
        """
        Content group exclusions remove the content entirely.

        Remove sections and sequences inacessible by the user's
        cohort.
        """
        if not self.cohorted_partition_id:
            return frozenset()

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
