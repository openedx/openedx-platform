"""
The enrollment_track dynamic partition generation to be part of the
openedx.dynamic_partition plugin.
"""
import logging

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from xmodule.partitions.partitions import (
    ENROLLMENT_TRACK_PARTITION_ID,
    UserPartition,
    UserPartitionError,
    get_partition_from_id,
)

log = logging.getLogger(__name__)

FEATURES = getattr(settings, 'FEATURES', {})


def create_enrollment_track_partition_with_course_id(course_id):
    """
    Create and return the dynamic enrollment track user partition based only on course_id.
    If it cannot be created, None is returned.
    """
    if not FEATURES.get('ENABLE_ENROLLMENT_TRACK_USER_PARTITION'):
        return None

    try:
        enrollment_track_scheme = UserPartition.get_scheme("enrollment_track")
    except UserPartitionError:
        log.warning("No 'enrollment_track' scheme registered, EnrollmentTrackUserPartition will not be created.")
        return None

    partition = enrollment_track_scheme.create_user_partition(
        id=ENROLLMENT_TRACK_PARTITION_ID,
        name=_("Enrollment Track Groups"),
        description=_("Partition for segmenting users by enrollment track"),
        parameters={"course_id": str(course_id)}
    )
    return partition


def create_enrollment_track_partition(course):
    """
    Create and return the dynamic enrollment track user partition.
    If it cannot be created, None is returned.
    """

    used_ids = {p.id for p in course.user_partitions}
    if ENROLLMENT_TRACK_PARTITION_ID in used_ids:
        log.warning(
            "Can't add 'enrollment_track' partition, as ID {id} is assigned to {partition} in course {course}.".format(
                id=ENROLLMENT_TRACK_PARTITION_ID,
                partition=get_partition_from_id(course.user_partitions, ENROLLMENT_TRACK_PARTITION_ID).name,
                course=str(course.id)
            )
        )
        return None

    return create_enrollment_track_partition_with_course_id(course.id)
