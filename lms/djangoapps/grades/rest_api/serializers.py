"""
API Serializers
"""


from collections import defaultdict

from rest_framework import serializers


# pylint: disable=abstract-method
class GradingPolicySerializer(serializers.Serializer):
    """
    Serializer for course grading policy.
    """
    assignment_type = serializers.CharField(source='type')
    count = serializers.IntegerField(source='min_count')
    dropped = serializers.IntegerField(source='drop_count')
    weight = serializers.FloatField()

    def to_representation(self, instance):
        """
        Return a representation of the grading policy.
        """
        # Backwards compatibility with the behavior of DRF v2.
        # When the grader dictionary was missing keys, DRF v2 would default to None;
        # DRF v3 unhelpfully raises an exception.
        return dict(
            super().to_representation(
                defaultdict(lambda: None, instance)
            )
        )


class SectionBreakdownSerializer(serializers.Serializer):
    """
    Serializer for the `section_breakdown` portion of a gradebook entry.
    """
    attempted = serializers.BooleanField()
    category = serializers.CharField()
    label = serializers.CharField()
    module_id = serializers.CharField()
    percent = serializers.FloatField()
    score_earned = serializers.FloatField()
    score_possible = serializers.FloatField()
    subsection_name = serializers.CharField()


class StudentGradebookEntrySerializer(serializers.Serializer):
    """
    Serializer for student gradebook entry.
    """
    user_id = serializers.IntegerField()
    username = serializers.CharField()
    full_name = serializers.CharField(required=False)
    email = serializers.EmailField()
    external_user_key = serializers.CharField(required=False)
    percent = serializers.FloatField()
    section_breakdown = SectionBreakdownSerializer(many=True)
    profile_name = serializers.CharField(required=False, allow_null=True)

class SubsectionGradeOverrideSerializer(serializers.Serializer):
    """
    Serializer for subsection grade override.
    """
    earned_all_override = serializers.FloatField()
    possible_all_override = serializers.FloatField()
    earned_graded_override = serializers.FloatField()
    possible_graded_override = serializers.FloatField()


class SubsectionGradeSerializer(serializers.Serializer):
    """
    Serializer for subsection grade.
    """
    earned_all = serializers.FloatField()
    possible_all = serializers.FloatField()
    earned_graded = serializers.FloatField()
    possible_graded = serializers.FloatField()


class SubsectionGradeOverrideSimpleHistorySerializer(serializers.Serializer):
    """
    Serializer for subsection grade override history.
    """
    created = serializers.DateTimeField()
    grade_id = serializers.IntegerField()
    history_id = serializers.IntegerField()
    earned_all_override = serializers.FloatField()
    earned_graded_override = serializers.FloatField()
    override_reason = serializers.CharField()
    system = serializers.CharField()
    history_date = serializers.DateTimeField()
    history_type = serializers.CharField()
    history_user = serializers.CharField()
    history_user_id = serializers.IntegerField()
    id = serializers.IntegerField()
    possible_all_override = serializers.FloatField()
    possible_graded_override = serializers.FloatField()


class SubsectionGradeResponseSerializer(serializers.Serializer):
    """
    Serializer for subsection grade response.
    """
    success = serializers.BooleanField()
    error_message = serializers.CharField(required=False)
    subsection_id = serializers.CharField()
    user_id = serializers.IntegerField()
    course_id = serializers.CharField()
    original_grade = SubsectionGradeSerializer()
    override = SubsectionGradeOverrideSerializer()
    history = SubsectionGradeOverrideSimpleHistorySerializer(many=True)
