"""
Django REST Framework serializers for the User API application
"""


from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from rest_framework import serializers

from lms.djangoapps.verify_student.models import ManualVerification, SoftwareSecurePhotoVerification

from .models import UserPreference


class UserSerializer(serializers.HyperlinkedModelSerializer):
    """
    Serializer that generates a representation of a User entity containing a subset of fields
    """
    name = serializers.SerializerMethodField()
    preferences = serializers.SerializerMethodField()

    def get_name(self, user):
        """
        Return the name attribute from the user profile object if profile exists else none
        """
        return user.profile.name

    def get_preferences(self, user):
        """
        Returns the set of preferences as a dict for the specified user
        """
        return UserPreference.get_all_preferences(user)

    class Meta:
        model = User
        # This list is the minimal set required by the notification service
        fields = ("id", "url", "email", "name", "username", "preferences")
        read_only_fields = ("id", "email", "username")
        # For disambiguating within the drf-yasg swagger schema
        ref_name = 'user_api.User'


class UserPreferenceSerializer(serializers.HyperlinkedModelSerializer):
    """
    Serializer that generates a representation of a UserPreference entity.
    """
    user = UserSerializer()

    class Meta:
        model = UserPreference
        depth = 1
        fields = ('user', 'key', 'value', 'url')


class RawUserPreferenceSerializer(serializers.ModelSerializer):
    """
    Serializer that generates a raw representation of a user preference.
    """
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())

    class Meta:
        model = UserPreference
        depth = 1
        fields = ('user', 'key', 'value', 'url')


class ReadOnlyFieldsSerializerMixin:
    """
    Mixin for use with Serializers that provides a method
    `get_read_only_fields`, which returns a tuple of all read-only
    fields on the Serializer.
    """
    @classmethod
    def get_read_only_fields(cls):
        """
        Return all fields on this Serializer class which are read-only.
        Expects sub-classes implement Meta.explicit_read_only_fields,
        which is a tuple declaring read-only fields which were declared
        explicitly and thus could not be added to the usual
        cls.Meta.read_only_fields tuple.
        """
        return getattr(cls.Meta, 'read_only_fields', '') + getattr(cls.Meta, 'explicit_read_only_fields', '')

    @classmethod
    def get_writeable_fields(cls):
        """
        Return all fields on this serializer that are writeable.
        """
        all_fields = getattr(cls.Meta, 'fields', tuple())
        return tuple(set(all_fields) - set(cls.get_read_only_fields()))


class CountryTimeZoneSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer that generates a list of common time zones for a country
    """
    time_zone = serializers.CharField()
    description = serializers.CharField()


class IDVerificationDetailsSerializer(serializers.Serializer):  # lint-amnesty, pylint: disable=abstract-method, missing-class-docstring
    type = serializers.SerializerMethodField()
    status = serializers.CharField()
    expiration_datetime = serializers.DateTimeField()
    message = serializers.SerializerMethodField()
    updated_at = serializers.DateTimeField()
    receipt_id = serializers.SerializerMethodField()

    def get_type(self, obj):  # lint-amnesty, pylint: disable=missing-function-docstring
        if isinstance(obj, SoftwareSecurePhotoVerification):
            return 'Software Secure'
        elif isinstance(obj, ManualVerification):
            return 'Manual'
        else:
            return 'SSO'

    def get_message(self, obj):  # lint-amnesty, pylint: disable=missing-function-docstring
        if isinstance(obj, SoftwareSecurePhotoVerification):
            return obj.error_msg
        elif isinstance(obj, ManualVerification):
            return obj.reason
        else:
            return ''

    def get_receipt_id(self, obj):
        if isinstance(obj, SoftwareSecurePhotoVerification):
            return obj.receipt_id
        else:
            return None
