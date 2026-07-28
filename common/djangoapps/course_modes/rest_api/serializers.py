"""
Course modes API serializers.
"""


from opaque_keys.edx.keys import CourseKey
from rest_framework import serializers

from common.djangoapps.course_modes.models import CourseMode
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


class CourseModeSerializer(serializers.Serializer):
    """
    Serializer for the CourseMode model.
    The ``course_id``, ``mode_slug``, ``mode_display_name``, and ``currency`` fields
    are all required for a create operation.  Neither the ``course_id``
    nor the ``mode_slug`` fields can be modified during an update operation.
    """
    UNCHANGEABLE_FIELDS = {'course_id', 'mode_slug'}

    course_id = serializers.CharField()
    mode_slug = serializers.CharField()
    mode_display_name = serializers.CharField()
    min_price = serializers.IntegerField(required=False)
    currency = serializers.CharField()
    expiration_datetime = serializers.DateTimeField(required=False)
    expiration_datetime_is_explicit = serializers.BooleanField(required=False)
    description = serializers.CharField(required=False)
    sku = serializers.CharField(required=False)
    bulk_sku = serializers.CharField(required=False)

    class Meta:
        # For disambiguating within the drf-yasg swagger schema
        ref_name = 'course_modes.CourseMode'

    def create(self, validated_data):
        """
        This method must be implemented for use in our
        ListCreateAPIView.
        """
        if 'course_id' in validated_data:
            course_id = CourseKey.from_string(validated_data['course_id'])
            course_overview = CourseOverview.get_from_id(course_id)
            del validated_data['course_id']
            validated_data['course'] = course_overview

        return CourseMode.objects.create(**validated_data)

    def update(self, instance, validated_data):
        """
        This method must be implemented for use in our
        RetrieveUpdateDestroyAPIView.
        """
        errors = {}

        for field in validated_data:
            if field in self.UNCHANGEABLE_FIELDS:
                errors[field] = ['This field cannot be modified.']

        if errors:
            raise serializers.ValidationError(errors)

        for modifiable_field in validated_data:
            setattr(
                instance,
                modifiable_field,
                validated_data.get(modifiable_field, getattr(instance, modifiable_field))
            )
        instance.save()
        return instance
