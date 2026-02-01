"""
Django REST Framework serializers for the User API Accounts sub-application
"""


import json
import logging
import re

from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.exceptions import ObjectDoesNotExist
from django.forms.models import model_to_dict
from django.urls import reverse
from rest_framework import serializers


from common.djangoapps.student.models import (
    LanguageProficiency,
    PendingNameChange,
    SocialLink,
    UserPasswordToggleHistory,
    UserProfile
)
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api import errors
from openedx.core.djangoapps.user_api.accounts.utils import is_secondary_email_feature_enabled
from openedx.core.djangoapps.user_api.models import RetirementState, UserPreference, UserRetirementStatus
from openedx.core.djangoapps.user_api.serializers import ReadOnlyFieldsSerializerMixin
from openedx.core.djangoapps.user_authn.views.registration_form import (
    contains_html, contains_url, get_extended_profile_model
)
from openedx.features.name_affirmation_api.utils import get_name_affirmation_service

from . import (
    ACCOUNT_VISIBILITY_PREF_KEY,
    ALL_USERS_VISIBILITY,
    BIO_MAX_LENGTH,
    CUSTOM_VISIBILITY,
    NAME_MIN_LENGTH,
    PRIVATE_VISIBILITY,
    VISIBILITY_PREFIX
)
from .image_helpers import get_profile_image_urls_for_user
from .utils import format_social_link, validate_social_link

PROFILE_IMAGE_KEY_PREFIX = 'image_url'
LOGGER = logging.getLogger(__name__)


class PhoneNumberSerializer(serializers.BaseSerializer):  # lint-amnesty, pylint: disable=abstract-method
    """
    Class to serialize phone number into a digit only representation.

    This serializer removes all non-numeric characters from the phone number,
    allowing '+' only at the beginning of the number.
    """

    def to_internal_value(self, data):
        """
        Remove all non-numeric characters from the phone number.

        Args:
            data (str): The input phone number string.

        Returns:
            str or None: The cleaned phone number string containing only digits,
                with an optional '+' at the beginning.
        """
        return re.sub(r'(?!^)\+|[^0-9+]', "", data) or None


class LanguageProficiencySerializer(serializers.ModelSerializer):
    """
    Class that serializes the LanguageProficiency model for account
    information.
    """
    class Meta:
        model = LanguageProficiency
        fields = ("code",)

    def get_identity(self, data):
        """
        This is used in bulk updates to determine the identity of an object.
        The default is to use the id of an object, but we want to override that
        and consider the language code to be the canonical identity of a
        LanguageProficiency model.
        """
        try:
            return data.get('code', None)
        except AttributeError:
            return None


class SocialLinkSerializer(serializers.ModelSerializer):
    """
    Class that serializes the SocialLink model for the UserProfile object.
    """
    class Meta:
        model = SocialLink
        fields = ("platform", "social_link")

    def validate_platform(self, platform):
        """
        Validate that the platform value is one of (facebook, x or linkedin)
        """
        valid_platforms = ["facebook", "x", "linkedin"]
        if platform not in valid_platforms:
            raise serializers.ValidationError(
                "The social platform must be facebook, x or linkedin"
            )
        return platform


class UserReadOnlySerializer(serializers.Serializer):  # lint-amnesty, pylint: disable=abstract-method
    """
    Class that serializes the User model and UserProfile model together.
    """

    def __init__(self, *args, **kwargs):
        # Don't pass the 'configuration' arg up to the superclass
        self.configuration = kwargs.pop('configuration', None)
        if not self.configuration:
            self.configuration = settings.ACCOUNT_VISIBILITY_CONFIGURATION

        # Don't pass the 'custom_fields' arg up to the superclass
        self.custom_fields = kwargs.pop('custom_fields', [])

        super().__init__(*args, **kwargs)

    def to_representation(self, user):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Overwrite to_native to handle custom logic since we are serializing three models as one here
        :param user: User object
        :return: Dict serialized account
        """
        try:
            user_profile = user.profile
        except ObjectDoesNotExist:
            user_profile = None
            LOGGER.warning("user profile for the user [%s] does not exist", user.username)

        try:
            account_recovery = user.account_recovery
        except ObjectDoesNotExist:
            account_recovery = None

        try:
            activation_key = user.registration.activation_key
        except ObjectDoesNotExist:
            activation_key = None

        data = {
            "username": user.username,
            "url": self.context.get('request').build_absolute_uri(
                reverse('accounts_api', kwargs={'username': user.username})
            ),
            "email": user.email,
            "id": user.id,
            # For backwards compatibility: Tables created after the upgrade to Django 1.8 will save microseconds.
            # However, mobile apps are not expecting microsecond in the serialized value. If we set it to zero the
            # DRF JSONEncoder will not include it in the serialized value.
            # https://docs.djangoproject.com/en/1.8/ref/databases/#fractional-seconds-support-for-time-and-datetime-fields
            "date_joined": user.date_joined.replace(microsecond=0),
            "last_login": user.last_login,
            "is_active": user.is_active,
            "activation_key": activation_key,
            "bio": None,
            "country": None,
            "state": None,
            "profile_image": None,
            "language_proficiencies": None,
            "name": None,
            "gender": None,
            "goals": None,
            "year_of_birth": None,
            "level_of_education": None,
            "mailing_address": None,
            "requires_parental_consent": None,
            "account_privacy": self.configuration.get('default_visibility'),
            "social_links": None,
            "extended_profile_fields": None,
            "phone_number": None,
            "pending_name_change": None,
            "verified_name": None,
        }

        if user_profile:
            data.update(
                {
                    "bio": AccountLegacyProfileSerializer.convert_empty_to_None(user_profile.bio),
                    "country": AccountLegacyProfileSerializer.convert_empty_to_None(user_profile.country.code),
                    "state": AccountLegacyProfileSerializer.convert_empty_to_None(user_profile.state),
                    "profile_image": AccountLegacyProfileSerializer.get_profile_image(
                        user_profile, user, self.context.get('request')
                    ),
                    "language_proficiencies": LanguageProficiencySerializer(
                        user_profile.language_proficiencies.all().order_by('code'), many=True
                    ).data,
                    "name": user_profile.name,
                    "gender": AccountLegacyProfileSerializer.convert_empty_to_None(user_profile.gender),
                    "goals": user_profile.goals,
                    "year_of_birth": user_profile.year_of_birth,
                    "level_of_education": AccountLegacyProfileSerializer.convert_empty_to_None(
                        user_profile.level_of_education
                    ),
                    "mailing_address": user_profile.mailing_address,
                    "requires_parental_consent": user_profile.requires_parental_consent(),
                    "account_privacy": get_profile_visibility(user_profile, user, self.configuration),
                    "social_links": SocialLinkSerializer(
                        user_profile.social_links.all().order_by('platform'), many=True
                    ).data,
                    "extended_profile": get_extended_profile(user_profile),
                    "phone_number": user_profile.phone_number,
                }
            )

        try:
            pending_name_change = PendingNameChange.objects.get(user=user)
            data.update({"pending_name_change": pending_name_change.new_name})
        except PendingNameChange.DoesNotExist:
            pass

        name_affirmation_service = get_name_affirmation_service()
        if name_affirmation_service:
            verified_name_obj = name_affirmation_service.get_verified_name(user, is_verified=True)
            if verified_name_obj:
                data.update({"verified_name": verified_name_obj.verified_name})

        if is_secondary_email_feature_enabled():
            data.update(
                {
                    "secondary_email": account_recovery.secondary_email if account_recovery else None,
                    "secondary_email_enabled": True,
                }
            )

        if self.custom_fields:
            fields = self.custom_fields
        elif user_profile:
            fields = _visible_fields(user_profile, user, self.configuration)
        else:
            fields = self.configuration.get('public_fields')

        return self._filter_fields(
            fields,
            data
        )

    def _filter_fields(self, field_whitelist, serialized_account):
        """
        Filter serialized account Dict to only include whitelisted keys
        """
        visible_serialized_account = {}

        for field_name in field_whitelist:
            visible_serialized_account[field_name] = serialized_account.get(field_name, None)

        return visible_serialized_account


class UserAccountDisableHistorySerializer(serializers.ModelSerializer):
    """
    Class that serializes User account disable history
    """
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = UserPasswordToggleHistory
        fields = ("created", "comment", "disabled", "created_by")

    def get_created_by(self, user_password_toggle_history):
        return user_password_toggle_history.created_by.username


class AccountUserSerializer(serializers.HyperlinkedModelSerializer, ReadOnlyFieldsSerializerMixin):
    """
    Class that serializes the portion of User model needed for account information.
    """
    password_toggle_history = UserAccountDisableHistorySerializer(many=True, required=False)

    class Meta:
        model = User
        fields = ("username", "email", "date_joined", "is_active", "password_toggle_history")
        read_only_fields = fields
        explicit_read_only_fields = ()


class AccountLegacyProfileSerializer(serializers.HyperlinkedModelSerializer, ReadOnlyFieldsSerializerMixin):
    """
    Class that serializes the portion of UserProfile model needed for account information.
    """
    profile_image = serializers.SerializerMethodField("_get_profile_image")
    requires_parental_consent = serializers.SerializerMethodField()
    language_proficiencies = LanguageProficiencySerializer(many=True, required=False)
    social_links = SocialLinkSerializer(many=True, required=False)
    phone_number = PhoneNumberSerializer(required=False)

    class Meta:
        model = UserProfile
        fields = (
            "name", "gender", "goals", "year_of_birth", "level_of_education", "country", "state", "social_links",
            "mailing_address", "bio", "profile_image", "requires_parental_consent", "language_proficiencies",
            "phone_number", "city"
        )
        # Currently no read-only field, but keep this so view code doesn't need to know.
        read_only_fields = ()
        explicit_read_only_fields = ("profile_image", "requires_parental_consent")

    def validate_bio(self, new_bio):
        """ Enforce maximum length for bio. """
        if len(new_bio) > BIO_MAX_LENGTH:
            raise serializers.ValidationError(
                f"The about me field must be at most {BIO_MAX_LENGTH} characters long."
            )
        return new_bio

    def validate_name(self, new_name):
        """ Enforce minimum length for name. """
        if len(new_name) < NAME_MIN_LENGTH:
            raise serializers.ValidationError(
                f"The name field must be at least {NAME_MIN_LENGTH} character long."
            )
        return new_name

    def validate_language_proficiencies(self, value):
        """
        Enforce all languages are unique.
        """
        language_proficiencies = list(value)
        unique_language_proficiencies = {language["code"] for language in language_proficiencies}
        if len(language_proficiencies) != len(unique_language_proficiencies):
            raise serializers.ValidationError("The language_proficiencies field must consist of unique languages.")
        return value

    def validate_social_links(self, value):
        """
        Enforce only one entry for a particular social platform.
        """
        social_links = list(value)
        unique_social_links = {social_link["platform"] for social_link in social_links}
        if len(social_links) != len(unique_social_links):
            raise serializers.ValidationError("The social_links field must consist of unique social platforms.")
        return value

    def transform_gender(self, user_profile, value):  # pylint: disable=unused-argument
        """
        Converts empty string to None, to indicate not set. Replaced by to_representation in version 3.
        """
        return AccountLegacyProfileSerializer.convert_empty_to_None(value)

    def transform_country(self, user_profile, value):  # pylint: disable=unused-argument
        """
        Converts empty string to None, to indicate not set. Replaced by to_representation in version 3.
        """
        return AccountLegacyProfileSerializer.convert_empty_to_None(value)

    def transform_level_of_education(self, user_profile, value):  # pylint: disable=unused-argument
        """
        Converts empty string to None, to indicate not set. Replaced by to_representation in version 3.
        """
        return AccountLegacyProfileSerializer.convert_empty_to_None(value)

    def transform_bio(self, user_profile, value):  # pylint: disable=unused-argument
        """
        Converts empty string to None, to indicate not set. Replaced by to_representation in version 3.
        """
        return AccountLegacyProfileSerializer.convert_empty_to_None(value)

    def transform_phone_number(self, user_profile, value):  # pylint: disable=unused-argument
        """
        Converts empty string to None, to indicate not set. Replaced by to_representation in version 3.
        """
        return AccountLegacyProfileSerializer.convert_empty_to_None(value)

    @staticmethod
    def convert_empty_to_None(value):
        """
        Helper method to convert empty string to None (other values pass through).
        """
        return None if value == "" else value

    @staticmethod
    def get_profile_image(user_profile, user, request=None):
        """
        Returns metadata about a user's profile image.
        """
        data = {'has_image': user_profile.has_profile_image}
        urls = get_profile_image_urls_for_user(user, request)
        data.update({
            f'{PROFILE_IMAGE_KEY_PREFIX}_{size_display_name}': url
            for size_display_name, url in urls.items()
        })
        return data

    def get_requires_parental_consent(self, user_profile):
        """
        Returns a boolean representing whether the user requires parental controls.
        """
        return user_profile.requires_parental_consent()

    def _get_profile_image(self, user_profile):
        """
        Returns metadata about a user's profile image

        This protected method delegates to the static 'get_profile_image' method
        because 'serializers.SerializerMethodField("_get_profile_image")' will
        call the method with a single argument, the user_profile object.
        """
        return AccountLegacyProfileSerializer.get_profile_image(user_profile, user_profile.user)

    def _update_social_links(self, instance, requested_social_links):
        """
        Update the given profile instance's social links as requested.
        """
        try:
            new_social_links = []
            deleted_social_platforms = []
            for requested_link_data in requested_social_links:
                requested_platform = requested_link_data['platform']
                requested_link_url = requested_link_data['social_link']
                validate_social_link(requested_platform, requested_link_url)
                formatted_link = format_social_link(requested_platform, requested_link_url)
                if not formatted_link:
                    deleted_social_platforms.append(requested_platform)
                else:
                    new_social_links.append(
                        SocialLink(user_profile=instance, platform=requested_platform, social_link=formatted_link)
                    )

            platforms_of_new_social_links = [s.platform for s in new_social_links]
            current_social_links = list(instance.social_links.all())
            unreplaced_social_links = [
                social_link for social_link in current_social_links
                if social_link.platform not in platforms_of_new_social_links
            ]
            pruned_unreplaced_social_links = [
                social_link for social_link in unreplaced_social_links
                if social_link.platform not in deleted_social_platforms
            ]
            merged_social_links = new_social_links + pruned_unreplaced_social_links

            instance.social_links.all().delete()
            instance.social_links.bulk_create(merged_social_links)

        except ValueError as err:
            # If we have encountered any validation errors, return them to the user.
            raise errors.AccountValidationError({
                'social_links': {
                    "developer_message": f"Error when adding new social link: '{str(err)}'",
                    "user_message": str(err)
                }
            })

    def update(self, instance, validated_data):
        """
        Update the profile, including nested fields.

        Raises:
        errors.AccountValidationError: the update was not attempted because validation errors were found with
            the supplied update
        """
        language_proficiencies = validated_data.pop("language_proficiencies", None)

        # Update all fields on the user profile that are writeable,
        # except for "language_proficiencies" and "social_links", which we'll update separately
        update_fields = set(self.get_writeable_fields()) - {"language_proficiencies"} - {"social_links"}
        for field_name in update_fields:
            default = getattr(instance, field_name)
            field_value = validated_data.get(field_name, default)
            setattr(instance, field_name, field_value)

        # Update the related language proficiency
        if language_proficiencies is not None:
            instance.language_proficiencies.all().delete()
            instance.language_proficiencies.bulk_create([
                LanguageProficiency(user_profile=instance, code=language["code"])
                for language in language_proficiencies
            ])

        # Update the user's social links
        requested_social_links = self._kwargs['data'].get('social_links')  # lint-amnesty, pylint: disable=no-member
        if requested_social_links:
            self._update_social_links(instance, requested_social_links)

        instance.save()
        return instance


class RetirementUserProfileSerializer(serializers.ModelSerializer):
    """
    Serialize a small subset of UserProfile data for use in RetirementStatus APIs
    """
    class Meta:
        model = UserProfile
        fields = ('id', 'name')


class RetirementUserSerializer(serializers.ModelSerializer):
    """
    Serialize a small subset of User data for use in RetirementStatus APIs
    """
    profile = RetirementUserProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'profile')


class RetirementStateSerializer(serializers.ModelSerializer):
    """
    Serialize a small subset of RetirementState data for use in RetirementStatus APIs
    """
    class Meta:
        model = RetirementState
        fields = ('id', 'state_name', 'state_execution_order')


class UserRetirementStatusSerializer(serializers.ModelSerializer):
    """
    Perform serialization for the RetirementStatus model
    """
    user = RetirementUserSerializer(read_only=True)
    current_state = RetirementStateSerializer(read_only=True)
    last_state = RetirementStateSerializer(read_only=True)

    class Meta:
        model = UserRetirementStatus
        exclude = ['responses', ]


class UserSearchEmailSerializer(serializers.ModelSerializer):
    """
    Perform serialization for the User model used in accounts/search_emails endpoint.
    """
    class Meta:
        model = User
        fields = ('email', 'id', 'username')


class UserRetirementPartnerReportSerializer(serializers.Serializer):
    """
    Perform serialization for the UserRetirementPartnerReportingStatus model
    """
    user_id = serializers.IntegerField()
    student_id = serializers.CharField(required=False)
    original_username = serializers.CharField()
    original_email = serializers.EmailField()
    original_name = serializers.CharField()
    orgs = serializers.ListField(child=serializers.CharField())
    orgs_config = serializers.ListField(required=False)
    created = serializers.DateTimeField()

    # Required overrides of abstract base class methods, but we don't use them
    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass


class PendingNameChangeSerializer(serializers.Serializer):  # lint-amnesty, pylint: disable=abstract-method
    """
    Serialize the PendingNameChange model
    """
    new_name = serializers.CharField()

    class Meta:
        model = PendingNameChange
        fields = ('new_name',)

    def validate_new_name(self, new_name):
        if contains_html(new_name):
            raise serializers.ValidationError('Name cannot contain the following characters: < >')
        if contains_url(new_name):
            raise serializers.ValidationError('Name cannot contain a URL')


def get_extended_profile(user_profile: UserProfile) -> list[dict[str, str]]:
    """
    Retrieve extended user profile fields for API serialization.

    This function extracts custom profile fields that extend beyond the standard
    UserProfile model. It first attempts to get data from a custom extended profile
    model (if configured), then falls back to the `user_profile.meta` JSON field.
    The returned data is filtered to include only fields specified in the
    `extended_profile_fields` site configuration.

    The function supports two data sources:
    1. Custom model: If `REGISTRATION_EXTENSION_FORM` setting points to a form with
       a `Meta.model`, data is retrieved from that model using `model_to_dict()`
    2. Fallback: JSON data stored in `UserProfile.meta` field

    Args:
        user_profile (UserProfile): The user profile instance to get extended fields from.

    Returns:
        list[dict[str, str]]: A list of dictionaries, each containing:
            - field_name: The name of the extended profile field
            - field_value: The value of the field (converted to string)
    """

    def get_extended_profile_data():
        extended_profile_model = get_extended_profile_model()

        if extended_profile_model:
            try:
                profile_obj = extended_profile_model.objects.get(user=user_profile.user)
                return model_to_dict(profile_obj)
            except (AttributeError, extended_profile_model.DoesNotExist):
                return {}

        try:
            return json.loads(user_profile.meta or "{}")
        except (ValueError, TypeError, AttributeError):
            return {}

    data = get_extended_profile_data()
    field_names = configuration_helpers.get_value("extended_profile_fields", [])
    return [{"field_name": name, "field_value": data.get(name, "")} for name in field_names]


def get_profile_visibility(user_profile, user, configuration):
    """
    Returns the visibility level for the specified user profile.
    """
    if user_profile.requires_parental_consent():
        return PRIVATE_VISIBILITY

    # Calling UserPreference directly because the requesting user may be different from existing_user
    # (and does not have to be is_staff).
    profile_privacy = UserPreference.get_value(user, ACCOUNT_VISIBILITY_PREF_KEY)
    if profile_privacy:
        return profile_privacy
    else:
        return configuration.get('default_visibility')


def _visible_fields(user_profile, user, configuration=None):
    """
    Return what fields should be visible based on user's preferences

    :param user_profile: User profile object
    :param user: User object
    :param configuration: A visibility configuration dictionary.
    :return: whitelist List of fields to be shown
    """
    if not configuration:
        configuration = settings.ACCOUNT_VISIBILITY_CONFIGURATION

    profile_visibility = get_profile_visibility(user_profile, user, configuration)
    if profile_visibility == ALL_USERS_VISIBILITY:
        return configuration.get('bulk_shareable_fields')

    elif profile_visibility == CUSTOM_VISIBILITY:
        return _visible_fields_from_custom_preferences(user, configuration)

    else:
        return configuration.get('public_fields')


def _visible_fields_from_custom_preferences(user, configuration):
    """
    Returns all fields that are marked to be shared with other users in the
    given user's preferences. Includes fields that are always public.
    """
    preferences = UserPreference.get_all_preferences(user)
    fields_shared_with_all_users = [
        field_name for field_name in configuration.get('custom_shareable_fields')
        if preferences.get(f'{VISIBILITY_PREFIX}{field_name}') == 'all_users'
    ]
    return set(fields_shared_with_all_users + configuration.get('public_fields'))
