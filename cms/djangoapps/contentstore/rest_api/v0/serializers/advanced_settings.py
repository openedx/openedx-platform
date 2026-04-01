""" Serializers for course advanced settings"""
from typing import Type, Dict as DictType

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.fields import Field as SerializerField
from xblock.fields import (
    Boolean,
    Date,
    DateTime,
    Dict,
    Field as XBlockField,
    Float,
    Integer,
    List,
    String,
)
from xmodule.course_block import CourseFields, EmailString

from cms.djangoapps.models.settings.course_metadata import CourseMetadata

# Maps xblock fields to their corresponding Django Rest Framework serializer field
XBLOCK_DRF_FIELD_MAP = [
    (Boolean, serializers.BooleanField),
    (String, serializers.CharField),
    (List, serializers.ListField),
    (Dict, serializers.DictField),
    (Date, serializers.DateField),
    (DateTime, serializers.DateTimeField),
    (Integer, serializers.IntegerField),
    (EmailString, serializers.EmailField),
    (Float, serializers.FloatField),
]


class AdvancedSettingsFieldSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for a single course setting field.

    This serializer accepts a ``value_field`` parameter that allows you to
    specify what field to use for a particular instance of this serializer.

    Args:
        value_field (SerializerField): The ``value`` field will have this type
    """

    deprecated = serializers.BooleanField(read_only=True, help_text=_("Marks a field as deprecated."))
    display_name = serializers.CharField(read_only=True, help_text=_("User-friendly display name for the field"))
    help = serializers.CharField(read_only=True, help_text=_("Help text that describes the setting."))

    def __init__(self, value_field: SerializerField, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["value"] = value_field


class CourseAdvancedSettingsSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for course advanced settings.
    """

    @staticmethod
    def _get_drf_field_type_from_xblock_field(xblock_field: XBlockField) -> Type[SerializerField]:
        """
        Return the corresponding DRF Serializer field for an XBlock field.

        Args:
            xblock_field (XBlockField): An XBlock field

        Returns:
            Type[SerializerField]: Return the DRF Serializer type
            corresponding to the XBlock field.
        """
        for xblock_type, drf_type in XBLOCK_DRF_FIELD_MAP:
            if isinstance(xblock_field, xblock_type):
                return drf_type
        return serializers.JSONField

    def get_fields(self) -> DictType[str, SerializerField]:
        """
        Return the fields for this serializer.

        This method dynamically generates the fields and field types based on
        fields available on the Course.

        Returns:
            DictType[str, SerializerField]: A mapping of field names to field serializers
        """
        fields = {}
        for field, field_type in vars(CourseFields).items():
            if isinstance(field_type, XBlockField) and field not in CourseMetadata.FIELDS_EXCLUDE_LIST:
                fields[field] = AdvancedSettingsFieldSerializer(
                    required=False,
                    label=field_type.name,
                    help_text=field_type.help,
                    value_field=self._get_drf_field_type_from_xblock_field(field_type)(),
                )
        return fields
