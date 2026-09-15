"""
API Serializers for certificates page
"""

from rest_framework import serializers


class CertificateSignatorySerializer(serializers.Serializer):
    """
    Serializer for representing certificate's signatory.
    """

    id = serializers.IntegerField()
    name = serializers.CharField()
    organization = serializers.CharField(required=False)
    signature_image_path = serializers.CharField()
    title = serializers.CharField()


class CertificateItemSerializer(serializers.Serializer):
    """
    Serializer for representing certificate item created for current course.
    """

    course_title = serializers.CharField(required=False)
    description = serializers.CharField()
    editing = serializers.BooleanField(required=False)
    id = serializers.IntegerField()
    is_active = serializers.BooleanField()
    name = serializers.CharField()
    signatories = CertificateSignatorySerializer(many=True)
    version = serializers.IntegerField()


class CourseCertificatesSerializer(serializers.Serializer):
    """
    Serializer for representing course's certificates.
    """

    certificate_activation_handler_url = serializers.CharField()
    certificate_web_view_url = serializers.CharField(allow_null=True)
    certificates = CertificateItemSerializer(many=True, allow_null=True)
    course_modes = serializers.ListField(child=serializers.CharField())
    has_certificate_modes = serializers.BooleanField()
    is_active = serializers.BooleanField()
    is_global_staff = serializers.BooleanField()
    mfe_proctored_exam_settings_url = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    course_number = serializers.CharField(source="context_course.number")
    course_title = serializers.CharField(source="context_course.display_name_with_default")
    course_number_override = serializers.CharField(source="context_course.display_coursenumber")
    can_manage = serializers.BooleanField(default=False, read_only=True)
