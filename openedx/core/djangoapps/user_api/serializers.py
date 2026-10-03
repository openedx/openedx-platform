"""
Django REST Framework serializers for the User API application
"""


from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.exceptions import ValidationError
from django.db import transaction
from django_countries import countries
from rest_framework import serializers

from common.djangoapps.student.models.user import UserProfile, email_exists_or_retired, username_exists_or_retired
from common.djangoapps.util.password_policy_validators import normalize_password, validate_password
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


class IDVerificationDetailsSerializer(serializers.Serializer):  # pylint: disable=abstract-method, missing-class-docstring
    type = serializers.SerializerMethodField()
    status = serializers.CharField()
    expiration_datetime = serializers.DateTimeField()
    message = serializers.SerializerMethodField()
    updated_at = serializers.DateTimeField()
    receipt_id = serializers.SerializerMethodField()

    def get_type(self, obj):  # pylint: disable=missing-function-docstring
        if isinstance(obj, SoftwareSecurePhotoVerification):
            return 'Software Secure'
        elif isinstance(obj, ManualVerification):
            return 'Manual'
        else:
            return 'SSO'

    def get_message(self, obj):  # pylint: disable=missing-function-docstring
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


class UserProfileSerializer(serializers.Serializer):
    """
    Serializer for the User model and related profile attributes.
    """

    email = serializers.EmailField(required=False, allow_blank=False)
    username = serializers.CharField(required=False, allow_blank=False)
    password = serializers.CharField(write_only=True, required=False, allow_blank=False)
    is_active = serializers.BooleanField(required=False)
    is_staff = serializers.BooleanField(required=False)
    is_superuser = serializers.BooleanField(required=False)
    name = serializers.CharField(required=False, allow_blank=False)
    year_of_birth = serializers.IntegerField(required=False)
    gender = serializers.CharField(required=False, allow_blank=False)
    level_of_education = serializers.CharField(required=False, allow_blank=False)
    country = serializers.CharField(required=False, allow_blank=False)

    def create(self, validated_data: dict) -> User:
        user_data = {}
        profile_data = {}
        password = validated_data.pop("password", None)
        if password is not None:
            password = normalize_password(password)

        for field_name, field_value in validated_data.items():
            if field_name in {"name", "year_of_birth", "gender", "level_of_education", "country"}:
                profile_data[field_name] = field_value
            else:
                user_data[field_name] = field_value

        with transaction.atomic():
            user = User.objects.create_user(password=password, **user_data)
            UserProfile.objects.create(user=user, **profile_data)

        return user

    def validate(self, attrs: dict) -> dict:
        """
        Validate the input data for the User serializer.

        :param attrs: Dictionary with user's data.
        :return: Validated data dictionary.
        """

        if self.instance is None:
            if "username" not in attrs:
                raise ValidationError("Username is required.")

            if "email" not in attrs:
                raise ValidationError("Email is required.")

        if "username" in attrs:
            if self.instance is None or self.instance.username != attrs["username"]:
                self._check_username_unique(attrs["username"])

        if "email" in attrs:
            if self.instance is None or self.instance.email != attrs["email"]:
                self._check_email_unique(attrs["email"])

        if "year_of_birth" in attrs:
            if attrs["year_of_birth"] not in UserProfile.VALID_YEARS:
                raise ValidationError(f"Year of birth must be within {UserProfile.VALID_YEARS}")

        if "gender" in attrs:
            if attrs["gender"] not in dict(UserProfile.GENDER_CHOICES):
                raise ValidationError(f"Gender must be one of {list(dict(UserProfile.GENDER_CHOICES).keys())}")

        if "level_of_education" in attrs:
            if attrs["level_of_education"] not in dict(UserProfile.LEVEL_OF_EDUCATION_CHOICES):
                raise ValidationError(
                    f"Level of education must be one of {list(dict(UserProfile.LEVEL_OF_EDUCATION_CHOICES).keys())}"
                )

        if "country" in attrs:
            if attrs["country"] not in dict(countries):
                raise ValidationError("Invalid country.")

        if "password" in attrs:
            validate_password(attrs["password"])

        return attrs

    def update(self, instance: User, validated_data: dict) -> User:
        """
        Update User instance by the data dictionary.

        Method updates User instance if data is correct.
        :param instance: User instance.
        :param validated_data: Dictionary with user's data.
        :return: User instance.
        """

        if not isinstance(instance, User):
            raise ValidationError("The instance must be the User type.")

        if not isinstance(validated_data, dict):
            raise ValidationError("The data must be the Dictionary type.")

        if "username" in validated_data:
            raise ValidationError("Username cannot be changed.")

        user_data = {}
        profile_data = {}
        password = validated_data.pop("password", None)

        for field_name, field_value in validated_data.items():
            if field_name in {"name", "year_of_birth", "gender", "level_of_education", "country"}:
                profile_data[field_name] = field_value
            else:
                user_data[field_name] = field_value

        with transaction.atomic():
            if user_data:
                for field_name, field_value in user_data.items():
                    setattr(instance, field_name, field_value)
                instance.save()

            if password:
                instance.set_password(normalize_password(password))
                instance.save()

            if profile_data:
                profile = instance.profile
                for field_name, field_value in profile_data.items():
                    setattr(profile, field_name, field_value)
                profile.save()

        return instance

    @staticmethod
    def _check_email_unique(email):
        if email_exists_or_retired(email=email):
            raise ValidationError("User already exists with this email")

    @staticmethod
    def _check_username_unique(username):
        if username_exists_or_retired(username=username):
            raise ValidationError("User already exists with this username")

    class Meta:
        model = User
        fields = ("id", "email", "password", "username", "is_active",
                  "is_staff", "is_superuser", "name", "year_of_birth",
                  "gender", "level_of_education", "country")
        read_only_fields = ("id",)
