""" API v1 serializers. """


from datetime import datetime

import pytz
from django.utils.translation import gettext as _
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import serializers

from common.djangoapps.course_modes.models import CourseMode
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from .models import UNDEFINED, Course


class CourseModeSerializer(serializers.ModelSerializer):
    """ CourseMode serializer. """
    name = serializers.CharField(source='mode_slug')
    price = serializers.IntegerField(source='min_price')
    expires = serializers.DateTimeField(
        source='expiration_datetime',
        required=False,
        allow_null=True,
        format=None
    )

    def get_identity(self, data):
        try:
            return data.get('name', None)
        except AttributeError:
            return None

    class Meta:
        model = CourseMode
        fields = ('name', 'currency', 'price', 'sku', 'bulk_sku', 'expires', 'android_sku', 'ios_sku')
        # For disambiguating within the drf-yasg swagger schema
        ref_name = 'commerce.CourseMode'


def validate_course_id(course_id):
    """
    Check that course id is valid and exists in modulestore.
    """
    try:
        course_key = CourseKey.from_string(str(course_id))
    except InvalidKeyError:
        raise serializers.ValidationError(  # pylint: disable=raise-missing-from  # noqa: B904
            _("{course_id} is not a valid course key.").format(
                course_id=course_id
            )
        )

    if not modulestore().has_course(course_key):
        raise serializers.ValidationError(
            _('Course {course_id} does not exist.').format(
                course_id=course_id
            )
        )


class PossiblyUndefinedDateTimeField(serializers.DateTimeField):
    """
    We need a DateTime serializer that can deal with the non-JSON-serializable
    UNDEFINED object.
    """
    def to_representation(self, value):
        if value is UNDEFINED:
            return None
        return super().to_representation(value)


class CourseSerializer(serializers.Serializer):
    """ Course serializer. """
    id = serializers.CharField(validators=[validate_course_id])  # pylint: disable=invalid-name
    name = serializers.CharField(read_only=True)
    verification_deadline = PossiblyUndefinedDateTimeField(format=None, allow_null=True, required=False)
    modes = CourseModeSerializer(many=True)

    class Meta:
        # For disambiguating within the drf-yasg swagger schema
        ref_name = 'commerce.Course'

    def validate(self, attrs):
        """ Ensure the verification deadline occurs AFTER the course mode enrollment deadlines. """
        verification_deadline = attrs.get('verification_deadline', None)

        if verification_deadline:
            upgrade_deadline = None

            # Find the earliest upgrade deadline
            for mode in attrs['modes']:
                expires = mode.get("expiration_datetime")
                if expires:
                    # If we don't already have an upgrade_deadline value, use datetime.max so that we can actually
                    # complete the comparison.
                    upgrade_deadline = min(expires, upgrade_deadline or datetime.max.replace(tzinfo=pytz.utc))

            # In cases where upgrade_deadline is None (e.g. the verified professional mode), allow a verification
            # deadline to be set anyway.
            if upgrade_deadline is not None and verification_deadline < upgrade_deadline:
                raise serializers.ValidationError(
                    'Verification deadline must be after the course mode upgrade deadlines.')

        return attrs

    def create(self, validated_data):
        """
        Create course modes for a course.

        arguments:
            validated_data: The result of self.validate() - a dictionary containing 'id', 'modes', and optionally
            a 'verification_deadline` key.
        returns:
            A ``commerce.api.v1.models.Course`` object.
        """
        kwargs = {}
        if 'verification_deadline' in validated_data:
            kwargs['verification_deadline'] = validated_data['verification_deadline']

        course = Course(
            validated_data["id"],
            self._new_course_mode_models(validated_data["modes"]),
            **kwargs
        )
        course.save()
        return course

    def update(self, instance, validated_data):
        """Update course modes for an existing course. """
        validated_data["modes"] = self._new_course_mode_models(validated_data["modes"])

        instance.update(validated_data)
        instance.save()
        return instance

    @staticmethod
    def _new_course_mode_models(modes_data):
        """Convert validated course mode data to CourseMode objects. """
        return [
            CourseMode(**modes_dict)
            for modes_dict in modes_data
        ]
