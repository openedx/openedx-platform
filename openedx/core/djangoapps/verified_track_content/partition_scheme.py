"""
UserPartitionScheme for enrollment tracks.
"""


import logging

from django.conf import settings
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.courseware.masquerade import (
    get_course_masquerade,
    get_masquerading_user_group,
    is_masquerading_as_specific_student,
)
from xmodule.partitions.partitions import Group, UserPartition  # lint-amnesty, pylint: disable=wrong-import-order

LOGGER = logging.getLogger(__name__)


# These IDs must be less than 100 so that they do not overlap with Groups in
# CohortUserPartition or RandomUserPartitionScheme
# (CMS' course_group_config uses a minimum value of 100 for all generated IDs).
ENROLLMENT_GROUP_IDS = settings.COURSE_ENROLLMENT_MODES


class EnrollmentTrackUserPartition(UserPartition):
    """
    Extends UserPartition to support dynamic groups pulled from the current course Enrollment tracks.
    """

    @property
    def groups(self):
        """
        Return the groups (based on CourseModes) for the course associated with this
        EnrollmentTrackUserPartition instance. Note that only groups based on selectable
        CourseModes are returned (which means that Credit will never be returned).
        """
        course_key = CourseKey.from_string(self.parameters["course_id"])

        return [
            Group(ENROLLMENT_GROUP_IDS[mode.slug]["id"], str(mode.name))
            for mode in CourseMode.modes_for_course(course_key, include_expired=True)
        ]


class EnrollmentTrackPartitionScheme:
    """
    This scheme uses learner enrollment tracks to map learners into partition groups.
    """

    read_only = True

    @classmethod
    def get_group_for_user(cls, course_key, user, user_partition, **kwargs):  # pylint: disable=unused-argument
        """
        Returns the Group from the specified user partition to which the user
        is assigned, via enrollment mode. If a user is in a Credit mode, the Verified or
        Professional mode for the course is returned instead.
        """
        # First, check if we have to deal with masquerading.
        # If the current user is masquerading as a specific student, use the
        # same logic as normal to return that student's group. If the current
        # user is masquerading as a generic student in a specific group, then
        # return that group.
        if get_course_masquerade(user, course_key) and not is_masquerading_as_specific_student(user, course_key):
            return get_masquerading_user_group(course_key, user, user_partition)

        mode_slug, is_active = CourseEnrollment.enrollment_mode_for_user(user, course_key)
        if mode_slug and is_active:
            course_mode = CourseMode.mode_for_course(
                course_key,
                mode_slug,
                modes=CourseMode.modes_for_course(course_key, include_expired=True, only_selectable=False),
            )
            if course_mode and CourseMode.is_credit_mode(course_mode):
                # We want the verified track even if the upgrade deadline has passed, since we
                # are determining what content to show the user, not whether the user can enroll
                # in the verified track.
                course_mode = CourseMode.verified_mode_for_course(course_key, include_expired=True)
            if not course_mode:
                course_mode = CourseMode.DEFAULT_MODE
            return Group(ENROLLMENT_GROUP_IDS[course_mode.slug]["id"], str(course_mode.name))
        else:
            return None

    @classmethod
    def create_user_partition(cls, id, name, description, groups=None, parameters=None, active=True):  # pylint: disable=redefined-builtin, invalid-name, unused-argument
        """
        Create a custom UserPartition to support dynamic groups.

        A Partition has an id, name, scheme, description, parameters, and a list
        of groups. The id is intended to be unique within the context where these
        are used. (e.g., for partitions of users within a course, the ids should
        be unique per-course). The scheme is used to assign users into groups.
        The parameters field is used to save extra parameters e.g., location of
        the course ID for this partition scheme.

        Partitions can be marked as inactive by setting the "active" flag to False.
        Any group access rule referencing inactive partitions will be ignored
        when performing access checks.
        """
        return EnrollmentTrackUserPartition(
            id,
            str(name),
            str(description),
            [],
            cls,
            parameters,
            active
        )
