"""Serializers for a course's advanced settings."""

from collections.abc import Mapping

from drf_spectacular.extensions import OpenApiSerializerExtension
from drf_spectacular.plumbing import build_object_type, get_doc
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.settings import api_settings

MINIMAL_VIEW = "minimal"
FULL_VIEW = "full"


class AdvancedSettingSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One advanced setting of a course, as Studio presents it for editing."""

    value = serializers.JSONField(
        allow_null=True,
        help_text="Current value of the setting. Its type depends on the setting.",
    )
    display_name = serializers.CharField(help_text="Name of the setting as Studio shows it.")
    help = serializers.CharField(
        allow_null=True,
        help_text="Text explaining what the setting does, or null when the setting has none.",
    )
    deprecated = serializers.BooleanField(help_text="Whether the setting is deprecated.")
    hide_on_enabled_publisher = serializers.BooleanField(
        help_text="Whether Studio hides the setting when the course is managed by a publishing service.",
    )

    class Meta:
        ref_name = "AdvancedSetting.v1"


class AdvancedSettingUpdateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The new value of one advanced setting."""

    value = serializers.JSONField(
        allow_null=True,
        help_text="New value of the setting. Its type depends on the setting.",
    )

    class Meta:
        ref_name = "AdvancedSettingUpdate.v1"


class _SettingsByNameSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    An object whose members are settings keyed by setting name.

    The set of names depends on the course, so the members cannot be declared
    as fields; each one is handled by ``setting_serializer_class``. Names given
    in the ``ignored_settings`` context entry are dropped before any member is
    validated.
    """

    setting_serializer_class = None

    def to_representation(self, instance):
        return {
            name: self.setting_serializer_class(setting).data
            for name, setting in instance.items()
        }

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            message = self.error_messages["invalid"].format(datatype=type(data).__name__)
            raise ValidationError({api_settings.NON_FIELD_ERRORS_KEY: [message]}, code="invalid")
        ignored = self.context.get("ignored_settings", frozenset())
        validated, errors = {}, {}
        for name, item in data.items():
            if name in ignored:
                continue
            setting = self.setting_serializer_class(data=item)
            if setting.is_valid():
                validated[name] = dict(setting.validated_data)
            else:
                errors[name] = [str(message) for messages in setting.errors.values() for message in messages]
        if errors:
            raise ValidationError(errors)
        return validated


class CourseAdvancedSettingsSerializer(_SettingsByNameSerializer):  # pylint: disable=abstract-method
    """A course's advanced settings, keyed by setting name in the order the course declares them."""

    setting_serializer_class = AdvancedSettingSerializer

    class Meta:
        ref_name = "CourseAdvancedSettings.v1"


class CourseAdvancedSettingsUpdateSerializer(_SettingsByNameSerializer):  # pylint: disable=abstract-method
    """
    New values for some of a course's advanced settings, keyed by setting name.

    Settings that authors do not edit are ignored, whatever their entry holds;
    settings the course does not have are ignored once their entry is valid.
    """

    setting_serializer_class = AdvancedSettingUpdateSerializer

    class Meta:
        ref_name = "CourseAdvancedSettingsUpdate.v1"


class AdvancedSettingsQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Query parameters of a read of a course's advanced settings."""

    view = serializers.ChoiceField(
        choices=[MINIMAL_VIEW, FULL_VIEW],
        default=MINIMAL_VIEW,
        help_text=(
            "Which settings to return. 'minimal' returns the settings course authors edit in Studio; "
            "'full' returns every setting of the course, including those Studio manages on other pages "
            "and those of features that are turned off."
        ),
    )
    fields = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=(
            "Comma-separated names of the settings to return, out of those the view returns. "
            "Names the view does not return are ignored."
        ),
    )

    class Meta:
        ref_name = "AdvancedSettingsQuery.v1"


class SettingsByNameSerializerExtension(OpenApiSerializerExtension):
    """Publish a settings-by-name serializer as an object whose members are its setting component."""

    target_class = _SettingsByNameSerializer
    match_subclasses = True

    def map_serializer(self, auto_schema, direction):
        setting = auto_schema.resolve_serializer(self.target.setting_serializer_class, direction)
        return build_object_type(
            description=get_doc(self.target.__class__),
            additionalProperties=setting.ref,
        )
