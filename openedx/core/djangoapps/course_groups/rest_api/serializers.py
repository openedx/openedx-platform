"""
Serializers for content group configurations REST API.
"""
from rest_framework import serializers


class GroupSerializer(serializers.Serializer):
    """
    Serializer for a single group within a content group configuration.
    """
    id = serializers.IntegerField()
    name = serializers.CharField(max_length=255)
    version = serializers.IntegerField()
    usage = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list
    )


class ContentGroupConfigurationSerializer(serializers.Serializer):
    """
    Serializer for a content group configuration (UserPartition with scheme='cohort').
    """
    id = serializers.IntegerField()
    name = serializers.CharField(max_length=255)
    scheme = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    parameters = serializers.DictField()
    groups = GroupSerializer(many=True)
    active = serializers.BooleanField()
    version = serializers.IntegerField()
    is_read_only = serializers.BooleanField(required=False, default=False)


class ContentGroupsListResponseSerializer(serializers.Serializer):
    """
    Response serializer for listing all content groups.
    """
    all_group_configurations = ContentGroupConfigurationSerializer(many=True)
    should_show_enrollment_track = serializers.BooleanField()
    should_show_experiment_groups = serializers.BooleanField()
    context_course = serializers.JSONField(required=False, allow_null=True)
    group_configuration_url = serializers.CharField()
    course_outline_url = serializers.CharField()
