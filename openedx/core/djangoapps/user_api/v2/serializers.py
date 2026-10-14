"""Serializers for version 2 of the User API."""

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.urls import reverse
from drf_spectacular.utils import extend_schema_field
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from rest_framework import serializers

from openedx.core.djangoapps.user_api.models import UserPreference

#: Value of the ``view`` query parameter that embeds the owning user in each preference.
FULL_VIEW = "full"

#: Value of the ``view`` query parameter that reduces a user to its identifying fields.
MINIMAL_VIEW = "minimal"

#: Properties the ``minimal`` preset keeps of a directory entry.
USER_MINIMAL_FIELDS = ("id", "username", "email")

#: Properties the ``minimal`` preset keeps of a stored preference.
PREFERENCE_MINIMAL_FIELDS = ("username", "key", "value")


class ErrorResponse(ErrorResponseSerializer):  # pylint: disable=abstract-method
    """
    The body of every unsuccessful response.

    ``type`` is a URI naming the kind of failure, ``title`` names it in one
    line, ``status`` repeats the HTTP status code and ``detail`` explains what
    happened; ``errors`` lists the per-field messages of a rejected parameter.
    """


class _KeptFieldsMixin:
    """
    Describes an object reduced to the properties named in ``kept_fields``.

    The response presets reduce an already serialized object, so a preset's
    shape has a describing serializer of its own rather than a serializing one.
    """

    #: Names of the properties the preset returns.
    kept_fields = ()

    def get_fields(self):
        """Return the declared fields narrowed to the ones the preset returns."""
        fields = super().get_fields()
        return {name: field for name, field in fields.items() if name in self.kept_fields}


class _OptionalFieldsMixin:
    """
    Describes an object whose every property is optional.

    A caller that selects properties with ``fields`` decides which ones come
    back, so none of them is guaranteed to be present.
    """

    def get_fields(self):
        """Return the declared fields, none of them mandatory."""
        fields = super().get_fields()
        for field in fields.values():
            field.required = False
            field.read_only = False
        return fields


def full_view_requested(request):
    """Return True when the caller asked for the embedded-user response preset."""
    if request is None:
        return False
    params = getattr(request, "query_params", None)
    if params is None:
        params = getattr(request, "GET", {})
    return params.get("view") == FULL_VIEW


class UserDirectorySerializer(serializers.ModelSerializer):
    """A user of the platform directory, addressed by username."""

    id = serializers.IntegerField(
        read_only=True,
        help_text="Database identifier of the user. Addresses in this API use the username instead.",
    )
    url = serializers.HyperlinkedIdentityField(
        view_name="user_v2:user_detail",
        lookup_field="username",
        help_text="Absolute URL of this user's directory entry.",
    )
    email = serializers.EmailField(read_only=True, help_text="Registered email address of the user.")
    username = serializers.CharField(read_only=True, help_text="Unique name the user is addressed by.")
    name = serializers.SerializerMethodField(
        help_text="Full name recorded on the user's profile, or null when the user has no profile.",
    )
    preferences = serializers.SerializerMethodField(
        help_text="The user's stored preferences, as a mapping of preference key to value.",
    )

    class Meta:
        model = User
        fields = ("id", "url", "email", "name", "username", "preferences")
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_name(self, user):
        """Return the profile name, or None for a user who has no profile row."""
        profile = getattr(user, "profile", None)
        return profile.name if profile is not None else None

    @extend_schema_field(serializers.DictField(child=serializers.CharField()))
    def get_preferences(self, user):
        """Return the user's preferences as a key-to-value mapping."""
        return UserPreference.get_all_preferences(user)


class UserDirectoryMinimalSerializer(_KeptFieldsMixin, UserDirectorySerializer):
    """A directory entry reduced to the properties that identify the user."""

    kept_fields = USER_MINIMAL_FIELDS


class UserDirectoryProjectionSerializer(_OptionalFieldsMixin, UserDirectorySerializer):
    """A directory entry reduced to the properties the caller selected."""


class UserPreferenceRecordSerializer(serializers.ModelSerializer):
    """
    A stored preference, identified by the user that owns it and its key.

    ``user`` carries the owner's whole directory entry and is included only
    when the caller asks for the ``full`` response preset; by default the
    owner appears as the ``username`` string alone.
    """

    url = serializers.SerializerMethodField(help_text="Absolute URL of this preference.")
    username = serializers.CharField(
        source="user.username",
        read_only=True,
        help_text="Username of the user this preference belongs to.",
    )
    key = serializers.CharField(read_only=True, help_text="Name of the preference.")
    value = serializers.CharField(read_only=True, help_text="Stored value of the preference.")
    user = serializers.SerializerMethodField(
        help_text="Directory entry of the owning user; null unless the full response preset is asked for.",
    )

    class Meta:
        model = UserPreference
        fields = ("username", "key", "value", "url", "user")
        read_only_fields = fields

    @extend_schema_field(serializers.URLField())
    def get_url(self, preference):
        """Return the member address of this preference."""
        path = reverse(
            "user_v2:user_preference_detail",
            kwargs={"username": preference.user.username, "key": preference.key},
        )
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request is not None else path

    @extend_schema_field(UserDirectorySerializer(allow_null=True))
    def get_user(self, preference):
        """Embed the owner only when the caller asked for the full preset."""
        if not full_view_requested(self.context.get("request")):
            return None
        return UserDirectorySerializer(preference.user, context=self.context).data


class UserPreferenceRecordMinimalSerializer(_KeptFieldsMixin, UserPreferenceRecordSerializer):
    """A stored preference reduced to its owner, its name and its value."""

    kept_fields = PREFERENCE_MINIMAL_FIELDS


class UserPreferenceRecordProjectionSerializer(_OptionalFieldsMixin, UserPreferenceRecordSerializer):
    """A stored preference reduced to the properties the caller selected."""


class TimeZoneSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A time zone and the way it is displayed to a learner."""

    time_zone = serializers.CharField(help_text="Name of the time zone, for example ``US/Pacific``.")
    description = serializers.CharField(
        help_text="Display form of the time zone, for example ``US/Pacific (PST, UTC-0800)``.",
    )


class TimeZoneQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Query parameters accepted by the time-zone list."""

    country_code = serializers.RegexField(
        r"^[A-Za-z]{2}$",
        required=False,
        # An empty value narrows nothing, the way an empty value of any other
        # parameter does, and is accepted rather than held to the pattern.
        allow_blank=True,
        error_messages={
            "invalid": "Enter a two-letter ISO 3166-1 alpha-2 country code, for example FR.",
        },
        help_text=(
            "Two-letter ISO 3166-1 alpha-2 country code, case-insensitive. "
            "Every time zone is returned when it is omitted or left empty, and when "
            "the code names a country with no time zones of its own."
        ),
    )
