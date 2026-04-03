"""
Provides partition support to the user service.
"""


import logging
import random

from eventtracking import tracker

import openedx.core.djangoapps.user_api.course_tag.api as course_tag_api
from xmodule.partitions.partitions import (  # lint-amnesty, pylint: disable=wrong-import-order
    NoSuchUserPartitionGroupError,
    UserPartitionError,
)

log = logging.getLogger(__name__)


class NotImplementedPartitionScheme:
    """
    This "scheme" allows previously-defined schemes to be purged, while giving existing
    course data definitions a safe entry point to load.
    """

    @classmethod
    def get_group_for_user(cls, course_key, user, user_partition, assign=True):  # pylint: disable=unused-argument
        """
        Returning None is equivalent to saying "This user is not in any groups
        using this partition scheme", be sure the scheme you're removing is
        compatible with that assumption.
        """
        return None


class ReturnGroup1PartitionScheme:
    """
    This scheme is needed to allow verification partitions to be killed, see EDUCATOR-199
    """
    @classmethod
    def get_group_for_user(cls, course_key, user, user_partition, assign=True):  # pylint: disable=unused-argument
        """
        The previous "allow" definition for verification was defined as 1, so return that.
        Details at https://github.com/openedx/edx-platform/pull/14913/files#diff-feff1466ec4d1b8c38894310d8342a80
        """
        return user_partition.get_group(1)


class RandomUserPartitionScheme:
    """
    This scheme randomly assigns users into the partition's groups.
    """
    RANDOM = random.Random()

    @classmethod
    def get_group_for_user(cls, course_key, user, user_partition, assign=True):
        """
        Returns the group from the specified user position to which the user is assigned.
        If the user has not yet been assigned, a group will be randomly chosen for them if assign flag is True.
        """
        partition_key = cls.key_for_partition(user_partition)
        group_id = course_tag_api.get_course_tag(user, course_key, partition_key)

        group = None
        if group_id is not None:
            # attempt to look up the presently assigned group
            try:
                group = user_partition.get_group(int(group_id))
            except NoSuchUserPartitionGroupError:
                # jsa: we can turn off warnings here if this is an expected case.
                log.warning(
                    "group not found in RandomUserPartitionScheme: %r",
                    {
                        "requested_partition_id": user_partition.id,
                        "requested_group_id": group_id,
                    },
                    exc_info=True
                )
            except ValueError:
                log.error("Bad group_id %r for user: %r", group_id, user)

        if group is None and assign and not course_tag_api.BulkCourseTags.is_prefetched(course_key):
            if not user_partition.groups:
                raise UserPartitionError('Cannot assign user to an empty user partition')

            # pylint: disable=fixme
            # TODO: had a discussion in arch council about making randomization more
            # deterministic (e.g. some hash).  Could do that, but need to be careful not
            # to introduce correlation between users or bias in generation.
            group = cls.RANDOM.choice(user_partition.groups)

            # persist the value as a course tag
            course_tag_api.set_course_tag(user, course_key, partition_key, group.id)

            # emit event for analytics
            # FYI - context is always user ID that is logged in, NOT the user id that is
            # being operated on. If instructor can move user explicitly, then we should
            # put in event_info the user id that is being operated on.
            event_name = 'xmodule.partitions.assigned_user_to_partition'
            event_info = {
                'group_id': group.id,
                'group_name': group.name,
                'partition_id': user_partition.id,
                'partition_name': user_partition.name
            }
            # pylint: disable=fixme
            # TODO: Use the XBlock publish api instead
            with tracker.get_tracker().context(event_name, {}):
                tracker.emit(
                    event_name,
                    event_info,
                )

        return group

    @classmethod
    def key_for_partition(cls, user_partition):
        """
        Returns the key to use to look up and save the user's group for a given user partition.
        """
        return f'xblock.partition_service.partition_{user_partition.id}'
