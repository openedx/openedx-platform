"""
Serializers for the contentstore.views module.

This module contains DRF serializers for various features such as certificates, blocks, and others.
Add new serializers here as needed for API endpoints in this module.
"""

from django.core.exceptions import PermissionDenied
from rest_framework import serializers

from cms.djangoapps.contentstore.views.certificate_manager import (
    CERTIFICATE_SCHEMA_VERSION,
    Certificate,
    CertificateManager,
)
from common.djangoapps.student.roles import GlobalStaff


class CertificateActivationSerializer(serializers.Serializer):
    """
    Serializer for activating or deactivating course certificates.
    """
    # This field indicates whether the certificate should be activated or deactivated.
    is_active = serializers.BooleanField(required=False, default=False)


class SignatorySerializer(serializers.Serializer):
    """
    Serializer for signatories in a course certificate.
    """
    id = serializers.IntegerField(required=False)
    name = serializers.CharField(required=False, allow_blank=True)
    title = serializers.CharField(required=False, allow_blank=True)
    organization = serializers.CharField(required=False, allow_blank=True)
    signature_image_path = serializers.CharField(required=False, allow_blank=True)
    certificate = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class CertificateSerializer(serializers.Serializer):
    """
    Serializer for course certificates.
    """
    id = serializers.IntegerField(read_only=True)
    version = serializers.IntegerField(default=CERTIFICATE_SCHEMA_VERSION)
    name = serializers.CharField(required=True, allow_blank=False)
    description = serializers.CharField(required=True, allow_blank=False)
    is_active = serializers.BooleanField(default=False)
    course_title = serializers.CharField(required=False, allow_blank=True)

    signatories = SignatorySerializer(many=True, required=False, default=list)

    def validate(self, data):
        """
        Validate the certificate data.
        """
        certificate_id = self.context.get("certificate_id")
        course = self.context.get("course")
        request = self.context.get("request")

        if certificate_id and course:
            active_certificates = CertificateManager.get_certificates(course, only_active=True)
            active_ids = [int(cert["id"]) for cert in active_certificates]

            if int(certificate_id) in active_ids:
                if not GlobalStaff().has_user(request.user):
                    raise PermissionDenied()

        return data

    def create(self, validated_data):
        """
        Create a new Certificate instance with the provided validated data.
        """
        course = self.context.get("course")
        certificate_id = self.context.get("certificate_id")

        validated_data = CertificateManager.assign_id(course, validated_data, certificate_id)
        return Certificate(course=course, certificate_data=validated_data)

    def update(self, instance, validated_data):
        """
        Update an existing Certificate instance with the provided validated data.
        """
        instance.certificate_data.update(validated_data)   # pylint: disable=protected-access
        return instance

    def to_representation(self, instance):
        """
        Convert the Certificate instance to a dictionary representation.
        """
        data = instance.certificate_data
        result = {
            "id": data.get("id"),
            "name": data.get("name"),
            "description": data.get("description"),
            "is_active": data.get("is_active", False),
            "version": data.get("version", CERTIFICATE_SCHEMA_VERSION),
            "signatories": data.get("signatories", []),
        }
        if data.get("course_title"):
            result["course_title"] = data.get("course_title")
        return result
