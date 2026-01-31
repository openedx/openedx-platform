"""
Serializers for the Agreements app
"""
from rest_framework import serializers

from openedx.core.lib.api.serializers import CourseKeyField

from .models import IntegritySignature, LTIPIISignature


class IntegritySignatureSerializer(serializers.ModelSerializer):
    """
    Serializer for the IntegritySignature model
    """
    username = serializers.CharField(source='user.username')
    course_id = CourseKeyField(source='course_key')
    created_at = serializers.DateTimeField(source='created')

    class Meta:
        model = IntegritySignature
        fields = ('username', 'course_id', 'created_at')


class LTIPIISignatureSerializer(serializers.ModelSerializer):
    """
    Serializer for LTIPIISignature model
    """
    username = serializers.CharField(source='user.username')
    course_id = CourseKeyField(source='course_key')
    created_at = serializers.DateTimeField(source='created')

    class Meta:
        model = LTIPIISignature
        fields = ('username', 'course_id', 'lti_tools', 'created_at')


class UserAgreementSerializer(serializers.Serializer):
    """
    Serializer for UserAgreement model
    """
    type = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    summary = serializers.CharField(read_only=True)
    text = serializers.CharField(read_only=True)
    url = serializers.URLField(read_only=True)
    updated = serializers.DateTimeField(read_only=True)


class UserAgreementRecordSerializer(serializers.Serializer):
    """
    Serializer for UserAgreementRecord model
    """
    username = serializers.CharField(read_only=True)
    agreement_type = serializers.CharField(read_only=True)
    accepted_at = serializers.DateTimeField()
