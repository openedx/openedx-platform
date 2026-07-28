"""
Models for Learning Sequences and Course Outline generation.

Conventions:

1. Only things in the `api` package should ever import this file. Do NOT import
from views.py or anywhere else. Even if that means we have to give up some DRF
niceties.

2. The vast majority of what our public API promises should be efficiently
queryable with these models. We might occasionally reach into other systems
built for fast course-level queries (e.g. grading, scheduling), but we should
never touch ModuleStore or Block Transformers.

3. It's okay for some basic validation to happen at the model layer. Constraints
like uniqueness should absolutely be enforced at this layer. But business logic
should happen in the `api` package.

4. Try to avoid blob-like entites (e.g. JSON fields) as much as possible and
push things into normalized tables.

5. In general, keep models as a thin, dumb persistence layer. Let the `api`
package decide when and where it's safe to cache things.

6. Models and data.py datastructures don't have to map 1:1, but the convention
is that the data struct has a "...Data" appended to it. For instance,
LearningContext -> LearningContextData. This is because the Python name for
dataclasses (what attrs is close to), and for better backwards compatibility if
we want to adopt this convention elsewhere.

7. Strongly separate things that are intrinsic to Learning Sequences as a whole
vs. things that only apply to Sequences in the context of a Course. We have
other uses for sequences (e.g. Content Libraries, Pathways) and we want to keep
that separated.

8. Your app _may_ make foreign keys to models in this app, but you should limit
yourself to the LearningContext and LearningSequence models. Other tables are
not guaranteed to stick around, and values may be deleted unexpectedly.
"""
from __future__ import annotations

from django.db import models
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import (  # pylint: disable=unused-import
    LearningContextKeyField,
    UsageKeyField,
)

from .data import CourseVisibility


class LearningContext(TimeStampedModel):
    """
    These are used to group Learning Sequences so that many of them can be
    pulled at once. We use this instead of a foreign key to CourseOverview
    because this table can contain things that are not courses.

    It is okay to make a foreign key against this table.

    .. no_pii:
    """
    id = models.BigAutoField(primary_key=True)
    context_key = LearningContextKeyField(
        max_length=255, db_index=True, unique=True, null=False
    )
    title = models.CharField(max_length=255, db_index=True)
    published_at = models.DateTimeField(null=False)
    published_version = models.CharField(max_length=255)

    class Meta:
        indexes = [
            models.Index(fields=['-published_at']),
        ]

    def __str__(self):
        return f"LearningContext for {self.context_key}"


class CourseContext(TimeStampedModel):
    """
    A model containing course specific information e.g course_visibility

    .. no_pii:
    """
    learning_context = models.OneToOneField(
        LearningContext, on_delete=models.CASCADE, primary_key=True, related_name="course_context"
    )
    # Please note, the tuple is intentionally use value for both actual value and display value
    # because the name of enum constant is written in upper while the values are lower
    course_visibility = models.CharField(
        max_length=32, choices=[(constant.value, constant.value) for constant in CourseVisibility]
    )
    days_early_for_beta = models.IntegerField(null=True, blank=True)
    self_paced = models.BooleanField(default=False)
    entrance_exam_id = models.CharField(max_length=255, null=True)  # noqa: DJ001

    class Meta:
        verbose_name = 'Course'
        verbose_name_plural = 'Courses'

    def __str__(self):
        return f"{self.learning_context.context_key} ({self.learning_context.title})"


class LearningSequence(TimeStampedModel):
    """
    The reason why this model doesn't have a direct foreign key to CourseSection
    is because we eventually want to have LearningSequences that exist outside
    of courses. Attributes that apply directly to all LearningSequences
    (usage_key, title, learning_context, etc.) will apply here, but anything
    that is specific to how a LearningContext is rendered for a course (e.g.
    permissions, staff visibility, is_entrance_exam) wil live in
    CourseSectionSequence.

    It is okay to make a foreign key against this table.

    .. no_pii:
    """
    id = models.BigAutoField(primary_key=True)
    learning_context = models.ForeignKey(
        LearningContext, on_delete=models.CASCADE, related_name='sequences'
    )
    usage_key = UsageKeyField(max_length=255)

    # Yes, it's crazy to have a title 1K chars long. But we have ones at least
    # 270 long, meaning we wouldn't be able to make it indexed anyway in InnoDB.
    title = models.CharField(max_length=1000)

    # Separate field for when this Sequence's content was last changed?
    class Meta:
        unique_together = [
            ['learning_context', 'usage_key'],
        ]


class CourseContentVisibilityMixin(models.Model):
    """
    This mixin stores XBlock information that affects outline level visibility
    for a single LearningSequence or Section in a course.

    We keep the XBlock field names here, even if they're somewhat misleading.
    Please read the comments carefully for each field.

    .. no_pii:
    """
    # This is an obscure, OLX-only flag (there is no UI for it in Studio) that
    # lets you define a Sequence that is reachable by direct URL but not shown
    # in Course navigation. It was used for things like supplementary tutorials
    # that were not considered a part of the normal course path.
    hide_from_toc = models.BooleanField(null=False, default=False)

    # Restrict visibility to course staff, regardless of start date. This is
    # often used to hide content that either still being built out, or is a
    # scratch space of content that will eventually be copied over to other
    # sequences.
    visible_to_staff_only = models.BooleanField(null=False, default=False)

    class Meta:
        abstract = True


class UserPartitionGroup(models.Model):  # noqa: DJ008
    """
    Each row represents a Group in a UserPartition.

    UserPartitions is a pluggable interface. Some IDs are static (with values
    less than 100). Others are dynamic, picking a range between 100 and 2^31-1.
    That means that technically, we could use IntegerField instead of
    BigIntegerField, but a) that limit isn't actually enforced as far as I can
    tell; and b) it's not _that_ much extra storage, so I'm using BigInteger
    instead (2^63-1).

    It's a pluggable interface (entry points: openedx.user_partition_scheme,
    openedx.dynamic_partition_generator), so there's no "UserPartition" model.
    We need to actually link this against the values passed back from the
    partitions service in order to map them to names and descriptions.

    Any CourseSection or CourseSectionSequence may be associated with any number
    of UserPartitionGroups. An individual _user_ may only be in one Group for
    any given User Partition, but a piece of _content_ can be associated with
    multiple groups. So for instance, for the Enrollment Track user partition,
    a piece of content may be associated with both "Verified" and "Masters"
    tracks, while a user may only be in one or the other.

    UserPartitionGroups are not associated with LearningSequence directly
    because User Partitions often carry course-level assumptions (e.g.
    Enrollment Track) that don't make sense outside of a Course.

    .. no_pii:
    """
    id = models.BigAutoField(primary_key=True)
    partition_id = models.BigIntegerField(null=False)
    group_id = models.BigIntegerField(null=False)

    class Meta:
        unique_together = [
            ['partition_id', 'group_id'],
        ]
        indexes = [
            models.Index(fields=['partition_id', 'group_id']),
        ]


class CourseSection(CourseContentVisibilityMixin, TimeStampedModel):  # noqa: DJ008
    """
    Course Section data, mapping to the 'chapter' block type.

    .. no_pii:
    """
    id = models.BigAutoField(primary_key=True)
    course_context = models.ForeignKey(
        CourseContext, on_delete=models.CASCADE, related_name='sections'
    )
    usage_key = UsageKeyField(max_length=255)
    title = models.CharField(max_length=1000)

    # What is our position within the Course? (starts with 0)
    ordering = models.PositiveIntegerField(null=False)

    new_user_partition_groups: models.ManyToManyField[UserPartitionGroup, models.Model] = models.ManyToManyField(
        UserPartitionGroup,
        db_index=True,
        related_name='sec_user_partition_groups',
        related_query_name='sec_user_partition_group',
        through='SectionPartitionGroup'
    )

    class Meta:
        unique_together = [
            ['course_context', 'usage_key'],
        ]
        indexes = [
            models.Index(fields=['course_context', 'ordering'], name='course_context_ordering_idx'),
        ]


class SectionPartitionGroup(models.Model):  # noqa: DJ008
    """
    Model which maps user partition groups to course sections.
    Used for the user_partition_groups ManyToManyField field in the CourseSection model above.
    Adds a cascading delete which will delete these many-to-many relations
    whenever a UserPartitionGroup or CourseSection object is deleted.

    .. no_pii:
    """
    class Meta:
        unique_together = [
            ['user_partition_group', 'course_section'],
        ]

    user_partition_group = models.ForeignKey(UserPartitionGroup, on_delete=models.CASCADE)  # noqa: DJ012
    course_section = models.ForeignKey(CourseSection, on_delete=models.CASCADE)


class CourseSectionSequence(CourseContentVisibilityMixin, TimeStampedModel):
    """
    This is a join+ordering table, with entries that could get wiped out and
    recreated with every course publish. Do NOT make a ForeignKey against this
    table before implementing smarter replacement logic when publishing happens,
    or you'll see deletes all the time.

    CourseContentVisibilityMixin is applied here (and not in LearningSequence)
    because CourseContentVisibilityMixin describes attributes that are part of
    how a LearningSequence is used within a course, and may not apply to other
    kinds of LearningSequences.

    Do NOT make a foreign key against this table, as the values are deleted and
    re-created on course publish.

    .. no_pii:
    """
    id = models.BigAutoField(primary_key=True)
    course_context = models.ForeignKey(
        CourseContext, on_delete=models.CASCADE, related_name='section_sequences'
    )
    section = models.ForeignKey(CourseSection, on_delete=models.CASCADE)
    sequence = models.ForeignKey(LearningSequence, on_delete=models.CASCADE)

    # Make the sequence inaccessible from the outline after the due date has passed
    inaccessible_after_due = models.BooleanField(null=False, default=False)

    # Ordering, starts with 0, but global for the course. So if you had 200
    # sequences across 20 sections, the numbering here would be 0-199.
    ordering = models.PositiveIntegerField(null=False)

    new_user_partition_groups: models.ManyToManyField[UserPartitionGroup, models.Model] = models.ManyToManyField(
        UserPartitionGroup,
        db_index=True,
        related_name='secseq_user_partition_groups',
        related_query_name='secseq_user_partition_group',
        through='SectionSequencePartitionGroup'
    )

    class Meta:
        unique_together = [
            ['course_context', 'ordering'],
        ]
        verbose_name = 'Course Sequence'
        verbose_name_plural = 'Course Sequences'

    def __str__(self):
        return f"{self.section.title} > {self.sequence.title}"


class SectionSequencePartitionGroup(models.Model):  # noqa: DJ008
    """
    Model which maps user partition groups to course section sequences.
    Used for the user_partition_groups ManyToManyField field in the CourseSectionSequence model above.
    Adds a cascading delete which will delete these many-to-many relations
    whenever a UserPartitionGroup or CourseSectionSequence object is deleted.

    .. no_pii:
    """
    class Meta:
        unique_together = [
            ['user_partition_group', 'course_section_sequence'],
        ]

    user_partition_group = models.ForeignKey(UserPartitionGroup, on_delete=models.CASCADE)  # noqa: DJ012
    course_section_sequence = models.ForeignKey(CourseSectionSequence, on_delete=models.CASCADE)


class CourseSequenceExam(TimeStampedModel):
    """
    This model stores XBlock information that affects outline level information
    pertaining to special exams

    .. no_pii:
    """
    course_section_sequence = models.OneToOneField(CourseSectionSequence, on_delete=models.CASCADE, related_name='exam')

    is_practice_exam = models.BooleanField(default=False)
    is_proctored_enabled = models.BooleanField(default=False)
    is_time_limited = models.BooleanField(default=False)


class PublishReport(models.Model):  # noqa: DJ008
    """
    A report about the content that generated this LearningContext publish.

    All these fields could be derived with aggregate SQL functions, but it would
    be slower and make the admin code more complex. Since we only write at
    publish time, keeping things in sync is less of a concern.

    .. no_pii:
    """
    learning_context = models.OneToOneField(
        LearningContext, on_delete=models.CASCADE, related_name='publish_report'
    )
    num_errors = models.PositiveIntegerField(null=False, db_index=True)
    num_sections = models.PositiveIntegerField(null=False, db_index=True)
    num_sequences = models.PositiveIntegerField(null=False, db_index=True)


class ContentError(models.Model):  # noqa: DJ008
    """
    Human readable content errors.

    If something got here, it means that we were able to make _something_ (or
    there would be no LearningContext at all), but something about the published
    state of the content is wrong and should be flagged to course and support
    teams. In many cases, this will be some malformed course structure that gets
    uploaded via OLX import–a process that is more forgiving than Studio's UI.

    It's a little weird to store errors in such a freeform manner like this. It
    would be more efficient and flexible in terms of i18n if we were to store
    error codes, and leave the message generation to the time of display. The
    problem with that is that we don't know up front what the parameterization
    for such errors would be. The current error messages being created are
    fairly complicated and include references to multiple attributes of multiple
    pieces of content with supporting breadcrumbs. Other future errors might
    just be about display_name string length.

    So instead of trying to model all that internally, I'm just allowing for
    freeform messages. It is quite possible that at some point we will come up
    with a more comprehensive taxonomy of error messages, at which point we
    could do a backfill to regenerate this data in a more normalized way.

    .. no_pii:
    """
    id = models.BigAutoField(primary_key=True)
    publish_report = models.ForeignKey(
        PublishReport, on_delete=models.CASCADE, related_name='content_errors'
    )
    usage_key = UsageKeyField(max_length=255, null=True)
    message = models.TextField(max_length=10000, blank=False, null=False)
