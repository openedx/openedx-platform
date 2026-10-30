"""Serializers for a course's certificate settings."""

from rest_framework import serializers


class ConfiguredCertificateSignatorySerializer(serializers.Serializer):
    """
    A person whose name and signature appear on a configured certificate.

    Courses imported from other sources can hold a signatory missing a field.
    Such a signatory is returned with ``null`` for the missing value rather
    than failing the whole response.
    """

    id = serializers.IntegerField(
        allow_null=True,
        default=None,
        help_text=(
            "Identifier of the signatory, unique within its certificate. Null when the stored signatory has none."
        ),
    )
    name = serializers.CharField(
        allow_null=True,
        default=None,
        help_text="Full name of the signatory. Null when the stored signatory has none.",
    )
    organization = serializers.CharField(
        required=False,
        help_text="Organization the signatory represents. Absent when none is stored.",
    )
    signature_image_path = serializers.CharField(
        allow_null=True,
        default=None,
        help_text=(
            "Course asset path of the image of the signatory's signature. "
            "Null when the stored signatory has none."
        ),
    )
    title = serializers.CharField(
        allow_null=True,
        default=None,
        help_text="Job title of the signatory. Null when the stored signatory has none.",
    )

    class Meta:
        ref_name = "ConfiguredCertificateSignatory"


class ConfiguredCertificateSerializer(serializers.Serializer):
    """
    A certificate configured for the course, with its signatories.

    Courses imported from other sources can hold a certificate missing a field.
    Such a certificate is returned with ``null`` for the missing value, ``false``
    for a missing active flag and an empty signatory list, rather than failing
    the whole response.
    """

    course_title = serializers.CharField(
        required=False,
        help_text="Course title printed on the certificate. Absent when none is stored.",
    )
    description = serializers.CharField(
        allow_null=True,
        default=None,
        help_text="Description of the certificate. Null when the stored certificate has none.",
    )
    editing = serializers.BooleanField(
        required=False,
        help_text="Whether the certificate was left open for editing in Studio. Absent when none is stored.",
    )
    id = serializers.IntegerField(
        allow_null=True,
        default=None,
        help_text="Identifier of the certificate, unique within its course. Null when the stored certificate has none.",
    )
    is_active = serializers.BooleanField(
        default=False,
        help_text="Whether the certificate is active for the course. False when the stored certificate has no flag.",
    )
    name = serializers.CharField(
        allow_null=True,
        default=None,
        help_text="Name of the certificate. Null when the stored certificate has none.",
    )
    signatories = ConfiguredCertificateSignatorySerializer(
        many=True,
        allow_null=True,
        default=list,
        help_text="People who sign the certificate, in display order. Empty when the stored certificate has none.",
    )
    version = serializers.IntegerField(
        allow_null=True,
        default=None,
        help_text="Version of the certificate's stored schema. Null when the stored certificate has none.",
    )

    class Meta:
        ref_name = "ConfiguredCertificate"


class CertificateSettingsSerializer(serializers.Serializer):
    """The certificate configuration of one course and the context Studio needs to edit it."""

    certificate_activation_handler_url = serializers.CharField(
        help_text="Studio address that activates or deactivates the course's certificates.",
    )
    certificate_web_view_url = serializers.CharField(
        allow_null=True,
        help_text=(
            "Address of the course's certificate preview in the learning site. "
            "Null when the course has no mode that awards a certificate."
        ),
    )
    certificates = ConfiguredCertificateSerializer(
        many=True,
        allow_null=True,
        help_text="Certificates configured for the course, in stored order.",
    )
    course_modes = serializers.ListField(
        child=serializers.CharField(),
        help_text="Slugs of the course's enrollment modes that can award a certificate. The audit mode is excluded.",
    )
    has_certificate_modes = serializers.BooleanField(
        help_text="Whether the course has at least one enrollment mode that can award a certificate.",
    )
    is_active = serializers.BooleanField(help_text="Whether the course's certificates are active.")
    is_global_staff = serializers.BooleanField(help_text="Whether the requester is a global staff member.")
    mfe_proctored_exam_settings_url = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="Address of the proctored exam settings page. Empty or null when that page is not available.",
    )
    course_number = serializers.CharField(
        source="context_course.number",
        help_text="Number of the course.",
    )
    course_title = serializers.CharField(
        source="context_course.display_name_with_default",
        help_text="Display name of the course.",
    )
    course_number_override = serializers.CharField(
        source="context_course.display_coursenumber",
        allow_null=True,
        help_text="Course number shown in place of the course's own number. Null when none is set.",
    )
    can_manage = serializers.BooleanField(
        default=False,
        read_only=True,
        help_text="Whether the requester may change the course's certificate configuration.",
    )

    class Meta:
        ref_name = "CertificateSettings"
