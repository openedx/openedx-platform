"""
Serializers for the Enrollment API — v2.

Only contains the serializers introduced by ADR 0025 (replacing inline
dict construction in role-listing endpoints). The other v1 serializers
(:class:`CourseEnrollmentSerializer`, :class:`CourseSerializer`,
:class:`CourseEnrollmentAllowedSerializer`, :class:`CourseEnrollmentsApiListSerializer`)
are unchanged in shape between v1 and v2 — v2 view code imports them
directly from :mod:`openedx.core.djangoapps.enrollments.serializers`.

If a future v3 needs to break any of those response shapes, fork them
into a new v3/serializers.py at that time.
"""

from rest_framework import serializers


class UserRoleSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Serializes a single course-level role entry for a user (ADR 0025)."""

    org = serializers.CharField()
    course_id = serializers.SerializerMethodField()
    role = serializers.CharField()

    def get_course_id(self, obj):
        """Return course_id as a string."""
        return str(obj.course_id)


class UserRolesResponseSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Serializes the full response payload for UserRolesViewSet (ADR 0025)."""

    roles = UserRoleSerializer(many=True)
    is_staff = serializers.BooleanField()
