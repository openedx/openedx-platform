""" Serializers for course tabs """
from typing import Dict

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from openedx.core.lib.api.serializers import UsageKeyField
from xmodule.tabs import CourseTab


class CourseTabSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Serializer for course tabs."""

    type = serializers.CharField(read_only=True, help_text=_("Tab type"))
    title = serializers.CharField(read_only=True, help_text=_("Default name for the tab displayed to users"))
    is_hideable = serializers.BooleanField(
        read_only=True,
        help_text=_("True if it's possible to hide the tab for a course"),
    )
    is_hidden = serializers.BooleanField(
        help_text=_("True the tab is hidden for the course"),
    )
    is_movable = serializers.BooleanField(
        read_only=True,
        help_text=_("True if it's possible to reorder the tab in the list of tabs"),
    )
    course_staff_only = serializers.BooleanField(
        read_only=True,
        help_text=_("True if this tab should be displayed only for instructors"),
    )
    name = serializers.CharField(
        read_only=True,
        help_text=_("Name of the tab displayed to users. Overrides title."),
    )
    tab_id = serializers.CharField(
        read_only=True,
        help_text=_("Name of the tab displayed to users. Overrides title."),
    )
    settings = serializers.DictField(
        read_only=True,
        help_text=_("Additional settings specific to the tab"),
    )

    def to_representation(self, instance: CourseTab) -> Dict:
        """
        Returns a dict representation of a ``CourseTab`` that contains more data than its ``to_json`` method.

        Args:
            instance (CourseTab): A course tab instance to serialize

        Returns:
            Dictionary containing key values from course tab.
        """
        tab_data = {
            "type": instance.type,
            "title": instance.title,
            "is_hideable": instance.is_hideable,
            "is_hidden": instance.is_hidden,
            "is_movable": instance.is_movable,
            "course_staff_only": instance.course_staff_only,
            "name": instance.name,
            "tab_id": instance.tab_id,
        }
        tab_settings = {
            key: value for key, value in instance.tab_dict.items() if key not in tab_data and key != "link_func"
        }
        tab_data["settings"] = tab_settings
        return tab_data


class TabIDLocatorSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Serializer for tab locations, used to identify a tab in a course."""

    tab_id = serializers.CharField(required=False, help_text=_("ID of tab to update"))
    tab_locator = UsageKeyField(required=False, help_text=_("Location (Usage Key) of tab to update"))

    def validate(self, attrs: Dict) -> Dict:
        """
        Validates that either the ``tab_id`` or ``tab_locator`` are specified, but not both.

        Args:
            attrs (Dict): A dictionary of attributes to validate
        """
        has_tab_id = "tab_id" in attrs
        has_tab_locator = "tab_locator" in attrs
        if has_tab_locator ^ has_tab_id:
            return super().validate(attrs)
        raise serializers.ValidationError(
            {"non_field_errors": _("Need to supply either a valid tab_id or a tab_location.")}
        )


class CourseTabUpdateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer to update course tabs.
    """

    is_hidden = serializers.BooleanField(
        required=True,
        help_text=_("True to hide the tab, and False to show it."),
    )
