"""Serializers for a course's grading policy."""

import math
from collections.abc import Mapping
from datetime import timedelta

from django.core.validators import MinLengthValidator
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.settings import api_settings
from rest_framework.utils import html

GRADING_FULL_VIEW = "full"


class GraderSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One assignment type of a course's grading policy."""

    type = serializers.CharField(
        allow_null=True,
        help_text="Name of the assignment type, for example Homework, or null when the policy stores none.",
    )
    min_count = serializers.IntegerField(help_text="Number of assignments of this type the course expects.")
    drop_count = serializers.IntegerField(
        help_text="Number of lowest-scoring assignments of this type left out of the grade.",
    )
    short_label = serializers.CharField(
        allow_null=True,
        allow_blank=True,
        help_text="Abbreviation shown with the assignment type, for example HW.",
    )
    weight = serializers.IntegerField(
        help_text=(
            "Share of the course grade, in percent. The stored fraction is multiplied by 100 "
            "and truncated to a whole number."
        ),
    )
    id = serializers.IntegerField(help_text="Position of the assignment type in the policy, starting at 0.")

    class Meta:
        ref_name = "authoring_grading.Grader.v3"


class GracePeriodSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """How long after a due date a submission is still accepted."""

    hours = serializers.IntegerField(help_text="Whole hours, with whole days counted as 24 hours.")
    minutes = serializers.IntegerField(help_text="Minutes beyond the whole hours.")
    seconds = serializers.IntegerField(help_text="Seconds beyond the whole minutes.")

    class Meta:
        ref_name = "authoring_grading.GracePeriod.v3"


class CourseGradingSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The grading policy of a course."""

    graders = GraderSerializer(
        many=True,
        allow_empty=True,
        help_text="The assignment types, in stored order.",
    )
    grade_cutoffs = serializers.DictField(
        child=serializers.FloatField(allow_null=True),
        allow_null=True,
        help_text=(
            "Lowest score, from 0 to 1, that earns each letter grade, keyed by the grade's name. "
            "The cutoffs, or a single cutoff, are null when the policy was stored that way."
        ),
    )
    grace_period = GracePeriodSerializer(
        allow_null=True,
        help_text="Grace period after each due date, or null when the course has none.",
    )
    minimum_grade_credit = serializers.FloatField(
        allow_null=True,
        help_text="Lowest course score, from 0 to 1, that earns course credit, or null when none is set.",
    )

    class Meta:
        ref_name = "authoring_grading.CourseGrading.v3"


class CourseGradingFullSerializer(CourseGradingSerializer):  # pylint: disable=abstract-method
    """The grading policy of a course, with the context the Studio grading page shows beside it."""

    course_assignment_lists = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        help_text=(
            "For each assignment type in use, its graded subsections as '<section> - <subsection>'. "
            "Present only with view=full."
        ),
    )
    default_grade_designations = serializers.ListField(
        child=serializers.CharField(),
        help_text="Letter grade names offered for new grade cutoffs. Present only with view=full.",
    )
    is_credit_course = serializers.BooleanField(
        help_text="Whether the course offers course credit. Present only with view=full.",
    )
    show_credit_eligibility = serializers.BooleanField(
        help_text=(
            "Whether the minimum grade for credit applies: true for a credit course when credit "
            "eligibility is enabled on the platform. Present only with view=full."
        ),
    )
    mfe_proctored_exam_settings_url = serializers.CharField(
        allow_blank=True,
        help_text=(
            "Path of the course's proctored exam settings page in the authoring app, or an empty "
            "string when that page is not available. Present only with view=full."
        ),
    )

    class Meta:
        ref_name = "authoring_grading.CourseGradingFull.v3"


class GradingViewQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Query parameters of a grading policy read."""

    view = serializers.ChoiceField(
        choices=[GRADING_FULL_VIEW],
        required=False,
        help_text=(
            "Set to full to add the graded subsections per assignment type, the default letter "
            "grade names, and the credit and proctoring context. Omit it for the policy alone."
        ),
    )


# The request fields below check each value with the conversion the grading
# model applies when it reads it, and the view then hands the model the body as
# sent. A value the model cannot convert or store is refused before anything is
# written.
#
# A body holding every policy field goes to the model's whole-policy update,
# which stores some values outside the documented shape: null or empty cutoffs,
# a grader type of any JSON type, an empty credit minimum. Such a body is
# validated by _WholeGradingPolicyUpdateSerializer, which accepts them. Any other
# body is held to the documented shape, because the model's per-field updates
# store null or empty cutoffs where they break the course's grading.

GRADING_POLICY_FIELDS = ("graders", "grade_cutoffs", "grace_period", "minimum_grade_credit")

# The modulestore stores integers in at most 8 bytes and fails part-way through
# a write on a wider one.
_STORABLE_INT_RANGE = range(-(2 ** 63), 2 ** 63)


def _storable(value):
    """Whether the modulestore can store ``value``, at any depth."""
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return value in _STORABLE_INT_RANGE
    if isinstance(value, Mapping):
        return all(_storable(item) for item in value.values())
    if isinstance(value, list):
        return all(_storable(item) for item in value)
    return True


class _SentValueField(serializers.Field):
    """A request value passed to the grading model as sent, after checking it converts and stores."""

    convert = None
    # False where the model stores only the converted value, or nothing.
    stored_as_sent = True
    default_error_messages = {"invalid": "Enter a valid value."}

    def to_internal_value(self, data):
        if self.convert is not None:
            try:
                converted = self.convert(data)  # pylint: disable=not-callable
            except (TypeError, ValueError, OverflowError):
                self.fail("invalid")
            if isinstance(converted, float) and not math.isfinite(converted):
                self.fail("invalid")
            if not _storable(converted):
                self.fail("invalid")
        if self.stored_as_sent and not _storable(data):
            self.fail("invalid")
        return data

    def to_representation(self, value):
        return value


@extend_schema_field(OpenApiTypes.STR)
class _NameField(_SentValueField):
    """A name, which must be a string."""

    default_error_messages = {"invalid": "A string is required."}

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


@extend_schema_field(OpenApiTypes.STR)
class _TextField(_SentValueField):
    """A label the grading model stores as sent, whatever its type."""

    def validate_empty_values(self, data):
        # The grading model stores null here, so it is accepted, though not published.
        if data is None:
            return (True, None)
        return super().validate_empty_values(data)


@extend_schema_field(OpenApiTypes.INT)
class _CountField(_SentValueField):
    """A count the grading model reads with ``int()``."""

    convert = int
    stored_as_sent = False
    default_error_messages = {"invalid": "A whole number is required."}


@extend_schema_field(OpenApiTypes.DOUBLE)
class _NumberField(_SentValueField):
    """A number the grading model reads with ``float()``."""

    convert = float
    default_error_messages = {"invalid": "A finite number is required."}


@extend_schema_field(OpenApiTypes.DOUBLE)
class _ScoreField(_NumberField):
    """A score, which must be a JSON number."""

    def to_internal_value(self, data):
        if isinstance(data, bool) or not isinstance(data, (int, float)):
            self.fail("invalid")
        return super().to_internal_value(data)


@extend_schema_field(OpenApiTypes.DOUBLE)
class _CreditMinimumField(_NumberField):
    """A credit minimum; an empty string is stored as no minimum."""

    def to_internal_value(self, data):
        if data == "":
            return data
        return super().to_internal_value(data)


@extend_schema_field(OpenApiTypes.INT)
class _PercentField(_NumberField):
    """A whole percentage; the grading model also stores a fractional one as sent."""

    stored_as_sent = False


@extend_schema_field(OpenApiTypes.INT)
class _GraderIdField(_TextField):
    """A grader id, which the grading model ignores whatever its value."""

    stored_as_sent = False


class GracePeriodUpdateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A grace period in an update. A unit left out counts as 0."""

    hours = serializers.IntegerField(default=0, help_text="Whole hours.")
    minutes = serializers.IntegerField(default=0, help_text="Minutes beyond the whole hours.")
    seconds = serializers.IntegerField(default=0, help_text="Seconds beyond the whole minutes.")

    class Meta:
        ref_name = "authoring_grading.GracePeriodUpdate.v3"


@extend_schema_field(GracePeriodUpdateSerializer)
class _GracePeriodField(serializers.Field):
    """
    A grace period, in any of the units ``datetime.timedelta`` accepts.

    An object wrapped in a ``grace_period`` key is unwrapped first, as the
    grading model does.
    """

    default_error_messages = {
        "invalid": "Enter the grace period as an object of hours, minutes and seconds.",
    }

    def to_internal_value(self, data):
        try:
            period = data["grace_period"] if "grace_period" in data else data
            timedelta(**period)
        except (TypeError, ValueError, OverflowError):
            self.fail("invalid")
        return data

    def to_representation(self, value):
        return value


class _AnyGradeCutoffsField(serializers.DictField):
    """Letter-grade cutoffs as the whole-policy update stores them: null, or an object of numbers or nulls."""

    def __init__(self, **kwargs):
        super().__init__(child=_NumberField(allow_null=True), **kwargs)

    def validate_empty_values(self, data):
        if data is None:
            return (True, None)
        return super().validate_empty_values(data)


class _GraderListSerializer(serializers.ListSerializer):  # pylint: disable=abstract-method
    """
    Graders as a list.

    The grading model iterates whatever it is given, so it stores an empty
    string or an empty object as an empty list, and both are accepted.
    Anything else that is not a list is refused.
    """

    def to_internal_value(self, data):
        if isinstance(data, (str, Mapping)):
            data = list(data)
        return super().to_internal_value(data)


class GraderUpdateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One assignment type in a grading policy update."""

    type = _NameField(help_text="Name of the assignment type, for example Homework.")
    min_count = _CountField(
        required=False,
        help_text="Number of assignments of this type the course expects. Defaults to 0.",
    )
    drop_count = _CountField(
        required=False,
        help_text="Number of lowest-scoring assignments of this type left out of the grade. Defaults to 0.",
    )
    short_label = _TextField(
        required=False,
        allow_null=True,
        help_text="Abbreviation shown with the assignment type, for example HW.",
    )
    weight = _PercentField(
        required=False,
        help_text="Share of the course grade, in percent. Defaults to 0.",
    )
    id = _GraderIdField(
        required=False,
        help_text=(
            "Ignored: a grader's position in the list is its id. Accepted so that graders "
            "read from this endpoint can be sent back unchanged."
        ),
    )

    class Meta:
        ref_name = "authoring_grading.GraderUpdate.v3"
        list_serializer_class = _GraderListSerializer


class CourseGradingUpdateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    A change to a course's grading policy.

    Every field is optional, and a field left out keeps its stored value.
    """

    graders = GraderUpdateSerializer(
        many=True,
        required=False,
        help_text="Replaces every assignment type, in the order given.",
    )
    grade_cutoffs = serializers.DictField(
        child=_ScoreField(),
        allow_empty=False,
        # Never reached, as allow_empty refuses an empty object first; it publishes the minimum.
        validators=[MinLengthValidator(1)],
        required=False,
        help_text="Lowest score, from 0 to 1, that earns each letter grade, keyed by the grade's name.",
    )
    grace_period = _GracePeriodField(
        required=False,
        allow_null=True,
        help_text=(
            "Grace period after each due date. Null leaves the stored grace period unchanged; "
            "a grace period cannot be removed here."
        ),
    )
    minimum_grade_credit = _NumberField(
        required=False,
        allow_null=True,
        help_text="Lowest course score, from 0 to 1, that earns course credit. Null leaves it unchanged.",
    )

    class Meta:
        ref_name = "authoring_grading.CourseGradingUpdate.v3"

    def to_internal_value(self, data):
        if html.is_html_input(data):
            raise ValidationError({api_settings.NON_FIELD_ERRORS_KEY: ["Send the grading policy as a JSON object."]})
        return super().to_internal_value(data)


class _WholePolicyGraderUpdateSerializer(GraderUpdateSerializer):  # pylint: disable=abstract-method
    """One assignment type in a whole-policy update, whose type may be any value."""

    type = _TextField(help_text="Name of the assignment type, for example Homework.")

    class Meta:
        ref_name = "authoring_grading.WholePolicyGraderUpdate.v3"
        list_serializer_class = _GraderListSerializer


class _WholeGradingPolicyUpdateSerializer(CourseGradingUpdateSerializer):  # pylint: disable=abstract-method
    """
    A grading policy update holding every policy field.

    The grading model's whole-policy update stores some values beyond the
    documented shape, and this accepts them: null cutoffs or cutoff values,
    empty cutoffs, a grader type of any JSON type, and an empty string as the
    credit minimum, which stores no minimum.
    """

    graders = _WholePolicyGraderUpdateSerializer(
        many=True,
        required=False,
        help_text="Replaces every assignment type, in the order given.",
    )
    grade_cutoffs = _AnyGradeCutoffsField(
        required=False,
        help_text="Lowest score, from 0 to 1, that earns each letter grade, keyed by the grade's name.",
    )
    minimum_grade_credit = _CreditMinimumField(
        required=False,
        allow_null=True,
        help_text="Lowest course score, from 0 to 1, that earns course credit. Null leaves it unchanged.",
    )

    class Meta:
        ref_name = "authoring_grading.WholeGradingPolicyUpdate.v3"


def grading_policy_update_serializer(data):
    """Return the serializer that validates the grading policy update body ``data``."""
    if isinstance(data, Mapping) and all(field in data for field in GRADING_POLICY_FIELDS):
        return _WholeGradingPolicyUpdateSerializer(data=data)
    return CourseGradingUpdateSerializer(data=data)
