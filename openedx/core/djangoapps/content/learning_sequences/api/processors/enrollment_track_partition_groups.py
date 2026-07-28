# pylint: disable=missing-module-docstring
import logging
from datetime import datetime
from typing import Dict  # noqa: UP035

from opaque_keys.edx.keys import CourseKey

from openedx.core import types
from xmodule.partitions.enrollment_track_partition_generator import (  # pylint: disable=wrong-import-order
    create_enrollment_track_partition_with_course_id,
)
from xmodule.partitions.partitions import (  # pylint: disable=wrong-import-order
    ENROLLMENT_TRACK_PARTITION_ID,
    Group,  # pylint: disable=wrong-import-order
)
from xmodule.partitions.partitions_service import (
    get_user_partition_groups,  # pylint: disable=wrong-import-order
)

from .base import OutlineProcessor

log = logging.getLogger(__name__)


class EnrollmentTrackPartitionGroupsOutlineProcessor(OutlineProcessor):
    """
    Processor for applying all enrollment track user partition groups.

    Confining the processor to only EnrollmentTrack user partition is a
    significant limitation. Nonetheless, it is a step towards the goal of
    supporting all partition schemes in the future.
    """
    def __init__(self, course_key: CourseKey, user: types.User, at_time: datetime):
        super().__init__(course_key, user, at_time)
        self.enrollment_track_groups: Dict[str, Group] = {}  # noqa: UP006
        self.user_group = None

    def load_data(self, full_course_outline) -> None:
        """
        Pull track groups for this course and which group the user is in.
        """
        user_partition = create_enrollment_track_partition_with_course_id(self.course_key)
        self.enrollment_track_groups = get_user_partition_groups(
            self.course_key,
            [user_partition],
            self.user,
            partition_dict_key='id'
        )
        # TODO: fix type annotation: https://github.com/openedx/tcril-engineering/issues/313
        self.user_group = self.enrollment_track_groups.get(ENROLLMENT_TRACK_PARTITION_ID)  # type: ignore

    def _is_user_excluded_by_partition_group(self, user_partition_groups):
        """
        Is the user part of the group to which the block is restricting content?
        """
        if not user_partition_groups:
            return False

        groups = user_partition_groups.get(ENROLLMENT_TRACK_PARTITION_ID)
        if not groups:
            return False

        if self.user_group and self.user_group.id not in groups:
            # If the user's partition group, say Masters,
            # does not belong to the partition of the block, say [verified],
            # the block should be removed
            return True
        return False

    def usage_keys_to_remove(self, full_course_outline):
        """
        Content group exclusions remove the content entirely.

        If you're in the Audit track, there are things in the Verified track
        that you don't even know exists. This processor always removes things
        entirely instead of making them visible-but-inaccessible (like
        ScheduleOutlineProcessor does).
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
