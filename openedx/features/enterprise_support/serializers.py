"""
Defines serializers for enterprise_support.
"""


from rest_framework import serializers

try:
    from enterprise.api.v1.serializers import (
        EnterpriseCourseEnrollmentReadOnlySerializer as BaseEnterpriseCourseEnrollmentSerializer,
    )
    from enterprise.models import EnterpriseCourseEnrollment
except ImportError:  # pragma: no cover
    pass


class EnterpriseCourseEnrollmentSerializer(BaseEnterpriseCourseEnrollmentSerializer):
    """
    Serializer for EnterpriseCourseEnrollment model.
    """

    enterprise_customer_name = serializers.SerializerMethodField()
    license = serializers.SerializerMethodField()

    class Meta:
        model = EnterpriseCourseEnrollment
        fields = (
            'course_id',
            'enterprise_customer_name',
            'enterprise_customer_user_id',
            'license',
            'saved_for_later'
        )

    def get_enterprise_customer_name(self, obj):
        return obj.enterprise_customer_user.enterprise_customer.name

    def get_license(self, obj):
        licensed_ece = obj.license

        if licensed_ece:
            return {
                'uuid': str(licensed_ece.license_uuid),
                'is_revoked': licensed_ece.is_revoked
            }
