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
    Serializer used by the user modify endpoint to create or update a User and its related UserProfile.
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
                raise serializers.ValidationError(
                    {"username": "Username is required."}
                )

            if "email" not in attrs:
                raise serializers.ValidationError(
                    {"email": "Email is required."}
                )

        return attrs

    def validate_username(self, value: str) -> str:
        """Validate the username.

        Ensures that the username is unique if it is being set for a new user or changed for an existing user.

        :param value: Username to validate.
        :return: Validated username.
        :raises serializers.ValidationError: If the username is not unique.
        """
        if self.instance is None or self.instance.username != value:
            self._check_username_unique(value)
        return value

    def validate_email(self, value: str) -> str:
        """Validate the email.

        Ensures that the email is unique if it is being set for a new user or changed for an existing user.

        :param value: Email to validate.
        :return: Validated email.
        :raises serializers.ValidationError: If the email is not unique.
        """
        if self.instance is None or self.instance.email != value:
            self._check_email_unique(value)
        return value

    def validate_year_of_birth(self, value: int) -> int:
        """Validate the year of birth.

        Ensures that the year of birth is within the valid range defined in UserProfile.VALID_YEARS.

        :param value: Year of birth to validate.
        :return: Validated year of birth.
        :raises serializers.ValidationError: If the year of birth is not within the valid range.
        """
        if value not in UserProfile.VALID_YEARS:
            raise serializers.ValidationError(
                f"Year of birth must be within {UserProfile.VALID_YEARS}"
            )
        return value

    def validate_gender(self, value: str) -> str:
        """Validate the gender.

        Ensures that the gender is one of the valid choices defined in UserProfile.GENDER_CHOICES.

        :param value: Gender to validate.
        :return: Validated gender.
        :raises serializers.ValidationError: If the gender is not one of the valid choices.
        """
        if value not in dict(UserProfile.GENDER_CHOICES):
            choices = list(dict(UserProfile.GENDER_CHOICES).keys())
            raise serializers.ValidationError(f"Gender must be one of {choices}")
        return value

    def validate_level_of_education(self, value: str) -> str:
        """Validate the level of education.

        Ensures that the level of education is one of the valid choices defined in
        UserProfile.LEVEL_OF_EDUCATION_CHOICES.

        :param value: Level of education to validate.
        :return: Validated level of education.
        :raises serializers.ValidationError: If the level of education is not one of the valid choices.
        """
        if value not in dict(UserProfile.LEVEL_OF_EDUCATION_CHOICES):
            choices = list(dict(UserProfile.LEVEL_OF_EDUCATION_CHOICES).keys())
            raise serializers.ValidationError(
                f"Level of education must be one of {choices}"
            )
        return value

    def validate_country(self, value: str) -> str:
        """Validate the country.

        Ensures that the country is one of the valid choices defined in the countries list.

        :param value: Country to validate.
        :return: Validated country.
        :raises serializers.ValidationError: If the country is not valid.
        """
        if value not in dict(countries):
            raise serializers.ValidationError("Invalid country.")
        return value

    def validate_password(self, value: str) -> str:
        """Validate the password.

        Ensures that the password meets the required strength and complexity criteria.

        :param value: Password to validate.
        :return: Validated password.
        :raises serializers.ValidationError: If the password does not meet the required criteria.
        """
        validate_password(value)
        return value

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
            raise serializers.ValidationError(
                "User already exists with this email"
            )

    @staticmethod
    def _check_username_unique(username):
        if username_exists_or_retired(username=username):
            raise serializers.ValidationError(
                "User already exists with this username"
            )

    class Meta:
        model = User
        fields = ("id", "email", "password", "username", "is_active",
                  "is_staff", "is_superuser", "name", "year_of_birth",
                  "gender", "level_of_education", "country")
        read_only_fields = ("id",)
