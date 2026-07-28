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

    Returns the content group configuration ID, a flat list of content groups,
    and a link to Studio where instructors can manage content groups.
    """
    id = serializers.IntegerField(
        allow_null=True,
        help_text="ID of the content group configuration (null if none exists)"
    )
    groups = GroupSerializer(many=True)
    studio_content_groups_link = serializers.CharField(
        help_text="Full URL to Studio's content group configuration page"
    )
