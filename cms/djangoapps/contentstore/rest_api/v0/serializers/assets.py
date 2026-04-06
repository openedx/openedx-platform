"""
API Serializers for assets
"""
from rest_framework import serializers

from cms.djangoapps.contentstore.rest_api.serializers.common import StrictSerializer


class AssetSerializer(StrictSerializer):
    """
    Strict Serializer for file assets.
    """
    file = serializers.FileField(required=False, allow_null=True)
    locked = serializers.BooleanField(required=False, allow_null=True)
