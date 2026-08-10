"""
API Serializers for course grading
"""

from rest_framework import serializers


class GradersSerializer(serializers.Serializer):
    """ Serializer for graders """
    type = serializers.CharField()
    min_count = serializers.IntegerField()
    drop_count = serializers.IntegerField()
    short_label = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    weight = serializers.IntegerField()
    id = serializers.IntegerField()

    class Meta:
        ref_name = "authoring_grading.Graders.v0"


class GracePeriodSerializer(serializers.Serializer):
    """Serializer for grace period (hours / minutes / seconds)."""
    hours = serializers.IntegerField(default=0)
    minutes = serializers.IntegerField(default=0)
    seconds = serializers.IntegerField(default=0, required=False)

    class Meta:
        ref_name = "authoring_grading.GracePeriod.v0"


class CourseGradingModelSerializer(serializers.Serializer):
    """ Serializer for course grading model data """
    graders = GradersSerializer(many=True, allow_null=True, allow_empty=True)
    grade_cutoffs = serializers.DictField(
        child=serializers.FloatField(),
        required=False,
        help_text=(
            "Mapping of letter grade to minimum score (0.0–1.0). "
            "Required by CourseGradingModel.update_from_json — must be included in every PATCH."
        ),
    )
    grace_period = GracePeriodSerializer(
        allow_null=True,
        required=False,
        help_text="Grace period duration. Pass null to clear the grace period.",
    )
    minimum_grade_credit = serializers.FloatField(
        required=False,
        allow_null=True,
        help_text="Minimum passing score for credit eligibility (0.0–1.0).",
    )

    class Meta:
        ref_name = "authoring_grading.CourseGrading.v0"
