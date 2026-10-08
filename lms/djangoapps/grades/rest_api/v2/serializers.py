"""Serializers for the Grades API v2."""

from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from rest_framework import serializers
from rest_framework.settings import api_settings

from common.djangoapps.student.models import CourseEnrollment

MAX_OVERRIDES = 100


# pylint: disable=abstract-method
class GradeSectionMarkSerializer(serializers.Serializer):
    """A note the course grader attached to one entry of a grade breakdown."""

    detail = serializers.CharField(
        help_text='Explanation of the note, for example why the entry does not count towards the grade.',
    )

    class Meta:
        ref_name = 'grade_v2.GradeSectionMark'


class CourseGradeSectionSerializer(serializers.Serializer):
    """One entry of the breakdown the course grader produces for a learner's course grade."""

    category = serializers.CharField(
        help_text="Assignment type the entry belongs to, for example 'Homework'.",
    )
    label = serializers.CharField(
        help_text="Short label of the entry, for example 'HW 01' or 'HW Avg'.",
    )
    detail = serializers.CharField(
        help_text="Human-readable summary of the entry, for example 'Homework 1 - Ohms Law - 83% (5/6)'.",
    )
    percent = serializers.FloatField(
        help_text='Fraction of the entry the learner earned, between 0 and 1.',
    )
    usage_key = serializers.CharField(
        source='sequential_id',
        allow_null=True,
        help_text=(
            'Opaque key of the subsection the entry scores, or null for an entry that '
            'summarises an assignment type or stands for a subsection not yet released.'
        ),
    )
    prominent = serializers.BooleanField(
        required=False,
        help_text='Present and true on the entry that summarises an assignment type.',
    )
    mark = GradeSectionMarkSerializer(
        required=False,
        help_text='Present when the grader attached a note to the entry, such as the entry being dropped.',
    )

    class Meta:
        ref_name = 'grade_v2.CourseGradeSection'


class CourseGradeSerializer(serializers.Serializer):
    """A learner's overall grade in one course."""

    username = serializers.CharField(
        help_text='Username of the learner the grade belongs to.',
    )
    course_key = serializers.CharField(
        help_text='Opaque key of the course the grade belongs to.',
    )
    email = serializers.CharField(
        allow_blank=True,
        help_text="Email address of the learner. Empty unless the learner is enrolled in the master's track.",
    )
    passed = serializers.BooleanField(
        help_text='Whether the learner currently meets the passing grade of the course.',
    )
    percent = serializers.FloatField(
        help_text='Fraction of the course the learner has earned, between 0 and 1.',
    )
    letter_grade = serializers.CharField(
        allow_null=True,
        help_text='Letter grade the learner has earned, or null when no grade cutoff has been reached.',
    )
    section_breakdown = CourseGradeSectionSerializer(
        many=True,
        required=False,
        help_text="Breakdown of the grade by subsection and assignment type. Returned only for 'view=full'.",
    )

    class Meta:
        ref_name = 'grade_v2.CourseGrade'


class CourseGradeQuerySerializer(serializers.Serializer):
    """The representation a course-grade request may ask for."""

    view = serializers.ChoiceField(
        choices=['full'],
        required=False,
        help_text="Send 'full' to include the breakdown of each grade.",
    )

    class Meta:
        ref_name = 'grade_v2.CourseGradeQuery'


class GradebookSectionSerializer(serializers.Serializer):
    """One graded subsection's contribution to a learner's row in the gradebook."""

    attempted = serializers.BooleanField(
        help_text='Whether the learner has attempted the subsection or has a grade override on it.',
    )
    category = serializers.CharField(
        help_text="Assignment type of the subsection, for example 'Homework'.",
    )
    label = serializers.CharField(
        allow_null=True,
        help_text="Short label of the subsection, for example 'HW 01', or null when the course grades no such type.",
    )
    usage_key = serializers.CharField(
        help_text='Opaque key of the subsection.',
    )
    percent = serializers.FloatField(
        help_text='Fraction of the subsection the learner earned, between 0 and 1.',
    )
    score_earned = serializers.FloatField(
        help_text='Points the learner earned on the graded content of the subsection; 0 until attempted.',
    )
    score_possible = serializers.FloatField(
        help_text='Points available on the graded content of the subsection; 0 until attempted.',
    )
    subsection_name = serializers.CharField(
        help_text='Display name of the subsection.',
    )

    class Meta:
        ref_name = 'grade_v2.GradebookSection'


class GradebookEntrySerializer(serializers.Serializer):
    """A learner's row in the gradebook of one course."""

    username = serializers.CharField(
        help_text='Username of the learner the row belongs to.',
    )
    full_name = serializers.CharField(
        required=False,
        help_text="Full name of the learner. Present only for learners enrolled in the master's track.",
    )
    email = serializers.CharField(
        allow_blank=True,
        required=False,
        help_text=(
            "Email address of the learner. Empty unless the learner is enrolled in the master's track. "
            "Left out for 'view=minimal'."
        ),
    )
    external_user_key = serializers.CharField(
        required=False,
        help_text='Key the learner is known by in the program they enrolled through. Present only when there is one.',
    )
    percent = serializers.FloatField(
        help_text='Fraction of the course the learner has earned, between 0 and 1.',
    )
    section_breakdown = GradebookSectionSerializer(
        many=True,
        required=False,
        help_text="The learner's score on each graded subsection of the course. Left out for 'view=minimal'.",
    )

    class Meta:
        ref_name = 'grade_v2.GradebookEntry'


class GradebookEntryQuerySerializer(serializers.Serializer):
    """The representation a gradebook request may ask for."""

    view = serializers.ChoiceField(
        choices=['minimal'],
        required=False,
        help_text="Send 'minimal' to reduce each row to the learner's username and overall percent.",
    )

    class Meta:
        ref_name = 'grade_v2.GradebookEntryQuery'


_OVERRIDE_VALUE_HELP = (
    'Points to record in place of the computed {what}. Leave it out to keep the '
    'computed value; send null to clear an earlier override of it.'
)


class SubsectionGradeOverrideRequestItemSerializer(serializers.Serializer):
    """One learner's override of one subsection grade, as a client sends it."""

    username = serializers.CharField(
        help_text='Username of a learner enrolled in the course.',
    )
    usage_key = serializers.CharField(
        help_text='Opaque key of a subsection of the course.',
    )
    earned_all_override = serializers.FloatField(
        required=False, allow_null=True,
        help_text=_OVERRIDE_VALUE_HELP.format(what='points earned on all content'),
    )
    possible_all_override = serializers.FloatField(
        required=False, allow_null=True,
        help_text=_OVERRIDE_VALUE_HELP.format(what='points possible on all content'),
    )
    earned_graded_override = serializers.FloatField(
        required=False, allow_null=True,
        help_text=_OVERRIDE_VALUE_HELP.format(what='points earned on graded content'),
    )
    possible_graded_override = serializers.FloatField(
        required=False, allow_null=True,
        help_text=_OVERRIDE_VALUE_HELP.format(what='points possible on graded content'),
    )
    comment = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=300,
        help_text='Why the grade was overridden, kept with the history of the grade.',
    )

    class Meta:
        ref_name = 'grade_v2.SubsectionGradeOverrideRequestItem'

    def validate_usage_key(self, value):
        """Return the parsed usage key, refusing one that is malformed or of another course."""
        try:
            usage_key = UsageKey.from_string(value)
        except InvalidKeyError as exc:
            raise serializers.ValidationError('Not a valid usage key.') from exc
        if usage_key.deprecated:
            raise serializers.ValidationError('Not a valid usage key.')
        in_course = usage_key.course_key == self.context['course_key']
        if not in_course or self.context['course'].get_child(usage_key) is None:
            raise serializers.ValidationError('No such subsection in this course.')
        return usage_key

    def validate_username(self, value):
        """Return the enrollment of the learner named, refusing a learner not enrolled in the course."""
        enrollment = CourseEnrollment.objects.select_related('user').filter(
            user__username=value, course_id=self.context['course_key'],
        ).first()
        if enrollment is None:
            raise serializers.ValidationError('No learner with this username is enrolled in this course.')
        return enrollment


class SubsectionGradeOverrideRequestSerializer(serializers.Serializer):
    """A batch of subsection grade overrides, applied together or not at all."""

    overrides = SubsectionGradeOverrideRequestItemSerializer(
        many=True,
        allow_empty=False,
        max_length=MAX_OVERRIDES,
        help_text=(
            f'The overrides to record, at most {MAX_OVERRIDES}. When any of them is not valid, '
            "none is recorded, and each error is keyed by the path of the member at fault, "
            "such as 'overrides[1].username' "
            'for the second item, counting from zero.'
        ),
    )

    class Meta:
        ref_name = 'grade_v2.SubsectionGradeOverrideRequest'

    def errors_by_path(self):
        """
        Return the validation errors keyed by the path of the member at fault.

        Errors on one item of the batch are keyed like ``overrides[1].username``,
        so a client can tell which items to fix, and every value is a list of
        messages, as in any other validation error.
        """
        return dict(_error_paths(self.errors))


def _error_paths(errors, path=''):
    """Yield ``(path, messages)`` for every leaf of a nested validation error."""
    if isinstance(errors, dict):
        for key, value in errors.items():
            if key == api_settings.NON_FIELD_ERRORS_KEY and path:
                yield from _error_paths(value, path)
            else:
                yield from _error_paths(value, f'{path}.{key}' if path else key)
    elif errors and all(isinstance(item, (dict, list)) for item in errors):
        for index, item in enumerate(errors):
            if item:
                yield from _error_paths(item, f'{path}[{index}]')
    else:
        yield path, [str(message) for message in errors]


class SubsectionGradeOverrideSerializer(serializers.Serializer):
    """An override recorded on one learner's grade for one subsection."""

    username = serializers.CharField(
        help_text='Username of the learner whose grade was overridden.',
    )
    usage_key = serializers.CharField(
        help_text='Opaque key of the subsection.',
    )
    earned_all_override = serializers.FloatField(
        allow_null=True, help_text='Points recorded as earned on all content, or null when not overridden.',
    )
    possible_all_override = serializers.FloatField(
        allow_null=True, help_text='Points recorded as possible on all content, or null when not overridden.',
    )
    earned_graded_override = serializers.FloatField(
        allow_null=True, help_text='Points recorded as earned on graded content, or null when not overridden.',
    )
    possible_graded_override = serializers.FloatField(
        allow_null=True, help_text='Points recorded as possible on graded content, or null when not overridden.',
    )
    comment = serializers.CharField(
        allow_null=True,
        help_text='Why the grade was overridden, or null when no reason was given.',
    )

    class Meta:
        ref_name = 'grade_v2.SubsectionGradeOverride'


class SubsectionGradeOverrideListSerializer(serializers.Serializer):
    """The overrides a batch recorded, in the order they were sent."""

    overrides = SubsectionGradeOverrideSerializer(
        many=True, help_text='The overrides recorded, in the order they were sent.',
    )

    class Meta:
        ref_name = 'grade_v2.SubsectionGradeOverrideList'


class SubsectionScoreSerializer(serializers.Serializer):
    """The points a learner has earned on a subsection, as computed from their answers."""

    earned_all = serializers.FloatField(help_text='Points earned on all content of the subsection.')
    possible_all = serializers.FloatField(help_text='Points available on all content of the subsection.')
    earned_graded = serializers.FloatField(help_text='Points earned on the graded content of the subsection.')
    possible_graded = serializers.FloatField(help_text='Points available on the graded content of the subsection.')

    class Meta:
        ref_name = 'grade_v2.SubsectionScore'


class SubsectionGradeOverrideValuesSerializer(serializers.Serializer):
    """The values an override records in place of the computed points."""

    earned_all_override = serializers.FloatField(
        allow_null=True, help_text='Points recorded as earned on all content, or null when not overridden.',
    )
    possible_all_override = serializers.FloatField(
        allow_null=True, help_text='Points recorded as possible on all content, or null when not overridden.',
    )
    earned_graded_override = serializers.FloatField(
        allow_null=True, help_text='Points recorded as earned on graded content, or null when not overridden.',
    )
    possible_graded_override = serializers.FloatField(
        allow_null=True, help_text='Points recorded as possible on graded content, or null when not overridden.',
    )

    class Meta:
        ref_name = 'grade_v2.SubsectionGradeOverrideValues'


class SubsectionGradeSerializer(serializers.Serializer):
    """One learner's grade on one subsection, and the override recorded on it."""

    username = serializers.CharField(help_text='Username of the learner the grade belongs to.')
    usage_key = serializers.CharField(help_text='Opaque key of the subsection.')
    course_key = serializers.CharField(help_text='Opaque key of the course the subsection belongs to.')
    original_grade = SubsectionScoreSerializer(
        help_text=(
            'The points computed from the learner\'s answers. For a learner who has not attempted '
            'the subsection, the points available and none earned.'
        ),
    )
    override = SubsectionGradeOverrideValuesSerializer(
        allow_null=True, help_text='The override recorded on the grade, or null when there is none.',
    )

    class Meta:
        ref_name = 'grade_v2.SubsectionGrade'


class SubsectionGradeOverrideHistoryRecordSerializer(serializers.Serializer):
    """One change to the override recorded on a subsection grade."""

    history_id = serializers.IntegerField(help_text='Identifier of this history record.')
    history_date = serializers.DateTimeField(help_text='When the change was made.')
    history_type = serializers.CharField(
        help_text="Kind of change: '+' the override was created, '~' changed, '-' removed.",
    )
    history_user = serializers.CharField(
        source='history_user.username', allow_null=True,
        help_text='Username of whoever made the change, or null when it was made by the system.',
    )
    earned_all_override = serializers.FloatField(
        allow_null=True, help_text='Points recorded as earned on all content, or null when not overridden.',
    )
    possible_all_override = serializers.FloatField(
        allow_null=True, help_text='Points recorded as possible on all content, or null when not overridden.',
    )
    earned_graded_override = serializers.FloatField(
        allow_null=True, help_text='Points recorded as earned on graded content, or null when not overridden.',
    )
    possible_graded_override = serializers.FloatField(
        allow_null=True, help_text='Points recorded as possible on graded content, or null when not overridden.',
    )
    override_reason = serializers.CharField(
        allow_null=True, help_text='Why the grade was overridden, or null when no reason was given.',
    )
    system = serializers.CharField(
        allow_null=True, help_text="Feature the override was made through, for example 'GRADEBOOK'.",
    )

    class Meta:
        ref_name = 'grade_v2.SubsectionGradeOverrideHistoryRecord'


class AssignmentTypeSerializer(serializers.Serializer):
    """One assignment type a course grades against."""

    type = serializers.CharField(help_text="Name of the assignment type, for example 'Homework'.")
    short_label = serializers.CharField(
        allow_null=True,
        help_text="Prefix of the short labels of assignments of this type, for example 'HW', or null.",
    )
    min_count = serializers.IntegerField(help_text='Number of assignments of this type the grade expects.')
    drop_count = serializers.IntegerField(help_text='Number of lowest-scored assignments of this type left out.')
    weight = serializers.FloatField(help_text='Share of the course grade this type carries, between 0 and 1.')

    class Meta:
        ref_name = 'grade_v2.AssignmentType'


class GradingSubsectionSerializer(serializers.Serializer):
    """One subsection of a course that learners can see."""

    usage_key = serializers.CharField(help_text='Opaque key of the subsection.')
    display_name = serializers.CharField(
        allow_null=True, help_text='Display name of the subsection, or null when the course gives it none.',
    )
    graded = serializers.BooleanField(help_text='Whether the subsection counts towards the grade.')
    assignment_type = serializers.CharField(
        allow_null=True, help_text='Assignment type of the subsection, or null when it has none.',
    )
    short_label = serializers.CharField(
        allow_null=True, help_text="Short label of a graded subsection, for example 'HW 01', else null.",
    )

    class Meta:
        ref_name = 'grade_v2.GradingSubsection'


class CourseGradingPolicySerializer(serializers.Serializer):
    """How one course is graded."""

    grade_cutoffs = serializers.DictField(
        child=serializers.FloatField(),
        help_text="Lowest fraction of the course, between 0 and 1, that earns each letter grade, keyed by letter.",
    )
    assignment_types = AssignmentTypeSerializer(
        many=True, help_text='The assignment types the course grades against, in the order the course lists them.',
    )
    subsections = GradingSubsectionSerializer(
        many=True, required=False,
        help_text="The subsections learners can see, in course order. Left out for 'view=minimal'.",
    )
    grades_frozen = serializers.BooleanField(
        required=False,
        help_text=(
            "Whether the course ended long enough ago that its grades may no longer change. "
            "Left out for 'view=minimal'."
        ),
    )
    bulk_management_enabled = serializers.BooleanField(
        required=False,
        help_text="Whether bulk grade management is offered for the course. Left out for 'view=minimal'.",
    )

    class Meta:
        ref_name = 'grade_v2.CourseGradingPolicy'


class CourseGradingPolicyQuerySerializer(serializers.Serializer):
    """The options a grading-policy request may send."""

    view = serializers.ChoiceField(
        choices=['minimal'], required=False,
        help_text="Send 'minimal' for the grade cutoffs and assignment types only.",
    )
    graded_only = serializers.BooleanField(
        default=False, help_text='Send true to leave the subsections that carry no grade out.',
    )

    class Meta:
        ref_name = 'grade_v2.CourseGradingPolicyQuery'


class SubmissionSerializer(serializers.Serializer):
    """One recorded state of a learner's answer to a problem."""

    state = serializers.JSONField(
        allow_null=True, help_text='The stored state of the problem for the learner at the time, or null.',
    )
    grade = serializers.FloatField(allow_null=True, help_text='Points earned at the time, or null when not scored.')
    max_grade = serializers.FloatField(
        allow_null=True, help_text='Points available at the time, or null when not scored.',
    )

    class Meta:
        ref_name = 'grade_v2.Submission'


class ProblemSubmissionHistorySerializer(serializers.Serializer):
    """The recorded submissions of one learner to one problem."""

    usage_key = serializers.CharField(help_text='Opaque key of the problem.')
    display_name = serializers.CharField(allow_null=True, help_text='Display name of the problem, or null.')
    submissions = SubmissionSerializer(many=True, help_text='The recorded states, newest first.')
    data = serializers.CharField(
        required=False, help_text="The definition of the problem. Returned only for 'view=full'.",
    )

    class Meta:
        ref_name = 'grade_v2.ProblemSubmissionHistory'


class SubmissionHistorySerializer(serializers.Serializer):
    """One learner's submission history across the scored problems of a course."""

    username = serializers.CharField(help_text='Username of the learner.')
    problems = ProblemSubmissionHistorySerializer(
        many=True, help_text='The problems the learner has a recorded submission to, in course order.',
    )

    class Meta:
        ref_name = 'grade_v2.SubmissionHistory'


class SubmissionHistoryQuerySerializer(serializers.Serializer):
    """The representation a submission-history request may ask for."""

    view = serializers.ChoiceField(
        choices=['full'], required=False, help_text="Send 'full' to include the definition of each problem.",
    )

    class Meta:
        ref_name = 'grade_v2.SubmissionHistoryQuery'
