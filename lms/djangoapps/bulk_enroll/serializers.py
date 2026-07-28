"""
Serializers for Bulk Enrollment.
"""


from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import serializers

from openedx.core.djangoapps.course_groups.cohorts import is_cohort_exists


class StringListField(serializers.ListField):  # pylint: disable=missing-class-docstring
    def to_internal_value(self, data):
        if not data:
            return []
        if isinstance(data, list):
            data = data[0]
        return data.split(',')


class BulkEnrollmentSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Serializes enrollment information for a collection of students/emails.

    This is mainly useful for implementing validation when performing bulk enrollment operations.
    """
    identifiers = serializers.CharField(required=True)
    courses = StringListField(required=True)
    cohorts = StringListField(required=False)
    action = serializers.ChoiceField(
        choices=(
            ('enroll', 'enroll'),
            ('unenroll', 'unenroll')
        ),
        required=True
    )
    auto_enroll = serializers.BooleanField(default=False)
    email_students = serializers.BooleanField(default=False)

    def validate_courses(self, value):
        """
        Check that each course key in list is valid.
        """
        course_keys = value
        for course in course_keys:
            try:
                CourseKey.from_string(course)
            except InvalidKeyError:
                raise serializers.ValidationError(f"Course key not valid: {course}")  # pylint: disable=raise-missing-from  # noqa: B904
        return value

    def validate(self, attrs):
        """
        Check that the cohorts list is the same size as the courses list.
        """
        if attrs.get('cohorts'):
            if attrs['action'] != 'enroll':
                raise serializers.ValidationError("Cohorts can only be used for enrollments.")
            if len(attrs['cohorts']) != len(attrs['courses']):
                raise serializers.ValidationError(
                    "If provided, the cohorts and courses should have equal number of items.")

            for course_id, cohort_name in zip(attrs['courses'], attrs['cohorts']):  # noqa: B905
                if not is_cohort_exists(course_key=CourseKey.from_string(course_id), name=cohort_name):
                    raise serializers.ValidationError("cohort {cohort_name} not found in course {course_id}.".format(  # noqa: UP032  # pylint: disable=line-too-long
                        cohort_name=cohort_name, course_id=course_id)
                    )

        return attrs
