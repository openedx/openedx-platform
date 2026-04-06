"""
Public data structures for this app.

Guidelines:

1. Make these data structures immutable (frozen=True) wherever possible, as it
   simplifies debugging.
2. This module should not import any other part of the app. This is the module
   that everything else imports, not the other way around. Dependencies should
   be kept to an absolute minimum–the Python stdlib, attr, opaque keys, and some
   Django primitives.
3. Keep the data classes dumb. Business logic should go into the api package
   modules that operate on this data. Do not attach complex objects with methods
   as attributes to data classes, as this makes them more difficult to mock out
   and make guarantees about behavior.
4. These data classes can perform validation, but only if that validation is
   entirely self-contained. They MUST NOT make database calls, network requests,
   or use API functions from other apps. They should not trigger expensive
   computation.

TODO: Validate all datetimes to be UTC.
"""
import logging
from datetime import datetime
from enum import Enum
from typing import Dict, FrozenSet, List, Optional

import attr
from opaque_keys.edx.keys import CourseKey, UsageKey

from openedx.core import types

log = logging.getLogger(__name__)


class CourseVisibility(Enum):
    PRIVATE = "private"
    PUBLIC_OUTLINE = "public_outline"
    PUBLIC = "public"


class ObjectDoesNotExist(Exception):
    """
    Imitating Django model conventions, we put a subclass of this in some of our
    data classes to indicate when something is not found.
    """


@attr.s(frozen=True, auto_attribs=True)
class ContentErrorData:
    """
    A human-readable description of something wrong with the content, to ease
    the debugging of content issues–especially ones where content had to be
    skipped because it was somehow malformed. The messages should be
    comprehensible to course teams and support staff.

    Errors can refer to things that are not anywhere in the outline, such as
    when things don't show up where we expect then to be and we omit them from
    the outline (unknown tag types, sequences where we expect sections, etc.)
    """
    message: str
    usage_key: Optional[UsageKey] = None


@attr.s(frozen=True, auto_attribs=True)
class VisibilityData:
    """
    XBlock attributes that help determine item visibility.
    """
    # This is an obscure, OLX-only flag (there is no UI for it in Studio) that
    # lets you define a Sequence that is reachable by direct URL but not shown
    # in Course navigation. It was used for things like supplementary tutorials
    # that were not considered a part of the normal course path.
    hide_from_toc: bool = False

    # Restrict visibility to course staff, regardless of start date. This is
    # often used to hide content that either still being built out, or is a
    # scratch space of content that will eventually be copied over to other
    # sequences.
    visible_to_staff_only: bool = False


@attr.s(frozen=True, auto_attribs=True)
class ExamData:
    """
    XBlock attributes that describe exams
    """
    is_practice_exam: bool = False
    is_proctored_enabled: bool = False
    is_time_limited: bool = False

    def __bool__(self):
        return self.is_practice_exam or self.is_proctored_enabled or self.is_time_limited


def user_partition_groups_not_empty(instance, attribute, value):  # pylint: disable=unused-argument
    """
    It's not valid to have a user_partition_groups key with no groups.

    For any User Partition, users must be in one group. Associating a piece of
    content with a user partition but no groups within that partition means that
    the content would never be accessible to anyone who is not staff, which is
    likely just an error. There _is_ a use case for this kind of hidden content,
    but we do that with visible_to_staff_only.
    """
    # If it's None or an empty dictionary, we don't have to check it.
    if not value:
        return

    for partition_id, group_list in value.items():
        if not group_list:
            raise ValueError(
                f"{instance.usage_key} has an empty list of groups for "
                f"user_partition_groups[{partition_id}]. User Partitioned "
                f"content must be associated with at least one group."
            )


@attr.s(frozen=True)
class CourseLearningSequenceData:
    """
    A Learning Sequence (a.k.a. subsection) from a Course.

    It's possible that at some point we'll want a LearningSequenceData
    superclass to encapsulate the minimum set of data that is shared between
    learning sequences in Courses vs. Pathways vs. Libraries. Such an object
    would likely not have `visibility` as that holds course-specific concepts.
    """
    usage_key = attr.ib(type=UsageKey)
    title = attr.ib(type=str)
    visibility = attr.ib(type=VisibilityData, default=VisibilityData())
    exam = attr.ib(type=ExamData, default=ExamData())
    inaccessible_after_due = attr.ib(type=bool, default=False)

    # Mapping of UserPartition IDs to list of UserPartition Groups that are
    # associated with this piece of content. See models.UserPartitionGroup
    # for more details.
    user_partition_groups = attr.ib(
        type=Dict[int, FrozenSet[int]],
        factory=dict,
        validator=[user_partition_groups_not_empty],
    )


@attr.s(frozen=True)
class CourseSectionData:
    """
    A Section in a Course (sometimes called a Chapter).
    """
    usage_key = attr.ib(type=UsageKey)
    title = attr.ib(type=str)
    visibility = attr.ib(type=VisibilityData, default=VisibilityData())
    sequences = attr.ib(type=List[CourseLearningSequenceData], factory=list)

    # Mapping of UserPartition IDs to list of UserPartition Groups that are
    # associated with this piece of content. See models.UserPartitionGroup
    # for more details.
    user_partition_groups = attr.ib(
        type=Dict[int, FrozenSet[int]],
        factory=dict,
        validator=[user_partition_groups_not_empty],
    )


@attr.s(frozen=True)
class CourseOutlineData:
    """
    Course Outline information without any user-specific data.
    """
    MAX_SEQUENCE_COUNT = 1000

    class DoesNotExist(ObjectDoesNotExist):
        pass

    course_key = attr.ib(type=CourseKey)

    @course_key.validator
    def not_deprecated(self, _attribute, value):
        """
        Only non-deprecated course keys (e.g. course-v1:) are supported.
        The older style of "Org/Course/Run" slash-separated keys will not work.
        """
        if value.deprecated:
            raise ValueError("course_key cannot be a slash-separated course key (.deprecated=True)")

    title = attr.ib(type=str)

    # The time the course was last published. This may be different from the
    # time that the course outline data was generated, since course outline
    # generation happens asynchronously and could be severely delayed by
    # operational issues or bugs that prevent the processing of certain courses.
    published_at = attr.ib(type=datetime)

    # String representation of the version information for a course. There is no
    # guarantee as to what this value is (e.g. a serialization of a BSON
    # ObjectID, a base64 encoding of a BLAKE2 hash, etc.). The only guarantee is
    # that it will change to something different every time the underlying
    # course is modified.
    published_version = attr.ib(type=str)

    # The time period (in days) before the official start of the course during which
    # beta testers have access to the course.
    days_early_for_beta = attr.ib(type=Optional[int])

    sections = attr.ib(type=List[CourseSectionData])

    # Defines if course self-paced or instructor-paced.
    self_paced = attr.ib(type=bool)

    # To make sure that our data structure is consistent, this field is
    # derived from what you pass into `sections`. Do not set this directly.
    sequences = attr.ib(type=Dict[UsageKey, CourseLearningSequenceData], init=False)

    course_visibility: CourseVisibility = attr.ib(validator=attr.validators.in_(CourseVisibility))

    # Entrance Exam ID
    entrance_exam_id = attr.ib(type=Optional[str])

    def __attrs_post_init__(self):
        """Post-init hook that validates and inits the `sequences` field."""
        sequences = {}
        for section in self.sections:
            for seq in section.sequences:
                if seq.usage_key in sequences:  # lint-amnesty, pylint: disable=no-else-raise
                    raise ValueError(
                        "Sequence {} appears in more than one Section."
                        .format(seq.usage_key)
                    )
                else:
                    sequences[seq.usage_key] = seq

        # Have to use this to get around the fact that the class is frozen
        # (which we almost always want, but not while we're initializing it).
        object.__setattr__(self, "sequences", sequences)

        # Because this is in the post-init hook, we have to do validation of
        # sequences manually here, instead of in a @sequences.validator
        if len(sequences) > self.MAX_SEQUENCE_COUNT:
            raise ValueError(
                "sequences cannot have more than {} entries ({} attempted)"
                .format(self.MAX_SEQUENCE_COUNT, len(sequences))
            )

    def remove(self, usage_keys):
        """
        Create a new CourseOutlineData by removing a set of UsageKeys.

        The UsageKeys can be for Sequences or Sections/Chapters. Removing a
        Section will remove all Sequences in that Section. It is not an error to
        pass in UsageKeys that do not exist in the outline.
        """
        keys_to_remove = set(usage_keys)

        for section in self.sections:
            section_sequences_keys = {seq.usage_key for seq in section.sequences}

            # If we remove a Section, we also remove all Sequences in that Section.
            if section.usage_key in keys_to_remove:
                keys_to_remove |= section_sequences_keys

            # If a Section is empty or about to be, we remove it.
            elif section_sequences_keys.issubset(keys_to_remove):
                keys_to_remove.add(section.usage_key)

        return attr.evolve(
            self,
            sections=[
                attr.evolve(
                    section,
                    sequences=[
                        seq
                        for seq in section.sequences
                        if seq.usage_key not in keys_to_remove
                    ]
                )
                for section in self.sections
                if section.usage_key not in keys_to_remove
            ]
        )

    @days_early_for_beta.validator
    def validate_days_early_for_beta(self, attribute, value):  # lint-amnesty, pylint: disable=unused-argument
        """
        Ensure that days_early_for_beta isn't negative.
        """
        if value is not None and value < 0:
            raise ValueError(
                "Provided value {} for days_early_for_beta is invalid. The value must be positive or zero. "
                "A positive value will shift back the starting date for Beta users by that many days.".format(value)
            )


@attr.s(frozen=True, auto_attribs=True)
class ScheduleItemData:
    """
    Scheduling specific data (start/end/due dates) for a single item.
    """
    usage_key: UsageKey

    # Start date that is specified for this item
    start: Optional[datetime]

    # Effective release date that it's available (may be affected by parents)
    effective_start: Optional[datetime]
    due: Optional[datetime]


@attr.s(frozen=True, auto_attribs=True)
class ScheduleData:
    """
    Overall course schedule data.
    """
    course_start: Optional[datetime]
    course_end: Optional[datetime]
    sections: Dict[UsageKey, ScheduleItemData]
    sequences: Dict[UsageKey, ScheduleItemData]


@attr.s(frozen=True, auto_attribs=True)
class SpecialExamAttemptData:
    """
    Overall special exam attempt data.
    """
    sequences: Dict[UsageKey, Dict]


@attr.s(frozen=True, auto_attribs=True)
class UserCourseOutlineData(CourseOutlineData):
    """
    A course outline that has been customized for a specific user and time.

    This is a subclass of CourseOutlineData that has been trimmed to only show
    those things that a user is allowed to know exists. That being said, this
    class is a pretty dumb container that doesn't understand anything about how
    to derive that trimmed-down state. It's the responsibility of functions in
    the learning_sequences.api package to figure out how to derive the correct
    values to instantiate this class.
    """
    # The CourseOutlineData that this UserCourseOutlineData is ultimately
    # derived from. If we have an instance of a UserCourseOutlineData and need
    # to reach up into parts of a Course that the user is not normally allowed
    # to know the existence of (e.g. Sequences marked `visible_to_staff_only`),
    # we can use this attribute.
    base_outline: CourseOutlineData

    # Django User representing who we've customized this outline for. This may
    # be the AnonymousUser.
    user: types.User

    # UTC TZ time representing the time for which this user course outline was
    # created. It is possible to create UserCourseOutlineData for a time in the
    # future (i.e. "What will this user be able to see next week?")
    at_time: datetime

    # What Sequences is this `user` allowed to access? Anything in the `outline`
    # is something that the `user` is allowed to know exists, but they might not
    # be able to actually interact with it. For example:
    # * A user might see an exam that has closed, but not be able to access it
    #   any longer.
    # * If anonymous course access is enabled in "public_outline" mode,
    #   unauthenticated users (AnonymousUser) will see the course outline but
    #   not be able to access anything inside.
    accessible_sequences: FrozenSet[UsageKey]


@attr.s(frozen=True, auto_attribs=True)
class UserCourseOutlineDetailsData:
    """
    Class that has a user's course outline plus useful details (like schedules).
    Will eventually expand to include other systems like Completion.
    """
    outline: UserCourseOutlineData
    schedule: ScheduleData
    special_exam_attempts: SpecialExamAttemptData
