"""
API Serializers for transcripts
"""
from rest_framework import serializers

from cms.djangoapps.contentstore.rest_api.serializers.common import StrictSerializer


class TranscriptSerializer(StrictSerializer):
    """
    Strict Serializer for video transcripts.
    """
    file = serializers.FileField()
    edx_video_id = serializers.CharField()
    language_code = serializers.CharField(required=False, allow_null=True)
    new_language_code = serializers.CharField(required=False, allow_null=True)


class YoutubeTranscriptCheckSerializer(StrictSerializer):
    """
    Strict Serializer for YouTube transcripts check
    """
    html5_local = serializers.ListField(
        child=serializers.CharField()
    )
    html5_equal = serializers.BooleanField()
    is_youtube_mode = serializers.BooleanField()
    youtube_local = serializers.BooleanField()
    youtube_server = serializers.BooleanField()
    youtube_diff = serializers.BooleanField()
    current_item_subs = serializers.ListField(required=False, allow_null=True)
    status = serializers.CharField()
    command = serializers.CharField()


class YoutubeTranscriptUploadSerializer(StrictSerializer):
    """
    Strict Serializer for YouTube transcripts upload
    """
    edx_video_id = serializers.CharField()
    status = serializers.CharField()
