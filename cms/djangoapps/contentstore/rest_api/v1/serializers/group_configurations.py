"""
API Serializers for course's settings group configurations.
"""

from rest_framework import serializers


class GroupConfigurationUsageSerializer(serializers.Serializer):
    """
    Serializer for representing nested usage inside configuration.
    """

    label = serializers.CharField()
    url = serializers.CharField()
    validation = serializers.DictField(required=False)


class GroupConfigurationGroupSerializer(serializers.Serializer):
    """
    Serializer for representing nested group inside configuration.
    """

    id = serializers.IntegerField()
    name = serializers.CharField()
    usage = GroupConfigurationUsageSerializer(required=False, allow_null=True, many=True)
    version = serializers.IntegerField()


class GroupConfigurationItemSerializer(serializers.Serializer):
    """
    Serializer for representing group configurations item.
    """

    active = serializers.BooleanField()
    description = serializers.CharField()
    groups = GroupConfigurationGroupSerializer(allow_null=True, many=True)
    id = serializers.IntegerField()
    usage = GroupConfigurationUsageSerializer(required=False, allow_null=True, many=True)
    name = serializers.CharField()
    parameters = serializers.DictField()
    read_only = serializers.BooleanField(required=False)
    scheme = serializers.CharField()
    version = serializers.IntegerField()


class CourseGroupConfigurationsSerializer(serializers.Serializer):
    """
    Serializer for representing course's settings group configurations.
    """

    all_group_configurations = GroupConfigurationItemSerializer(many=True)
    experiment_group_configurations = GroupConfigurationItemSerializer(
        allow_null=True, many=True
    )
    mfe_proctored_exam_settings_url = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    should_show_enrollment_track = serializers.BooleanField()
    should_show_experiment_groups = serializers.BooleanField()
    can_manage = serializers.BooleanField(default=False, read_only=True)
