"""Serializers for a course's custom pages and tabs."""

from drf_spectacular.utils import extend_schema_field
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from rest_framework import serializers

# Reported under custom_pages when an item is not a string holding a usage key.
NOT_A_USAGE_KEY_MESSAGE = "Each item must be the usage key of a custom page."
# Reported under course_key when the query parameter does not hold the key of a course.
NOT_A_COURSE_KEY_MESSAGE = "The value must be the key of a course, such as course-v1:edX+DemoX+2026."

# Members of a tab that are returned as members of their own; the rest of the
# tab's stored entry is returned under ``settings``.
_TAB_MEMBERS = ("type", "title", "is_hideable", "is_hidden", "is_movable", "course_staff_only", "name", "tab_id")


class CourseTabSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One tab of a course's navigation, as course authors manage it."""

    type = serializers.CharField(help_text="Kind of tab, such as progress, wiki or static_tab for a custom page.")
    title = serializers.CharField(
        allow_null=True,
        help_text="Default name of this kind of tab, or null for kinds without one, such as custom pages.",
    )
    is_hideable = serializers.BooleanField(help_text="Whether course authors may hide the tab from learners.")
    is_hidden = serializers.BooleanField(help_text="Whether the tab is hidden from learners.")
    is_movable = serializers.BooleanField(help_text="Whether course authors may move the tab among the course's tabs.")
    course_staff_only = serializers.BooleanField(help_text="Whether only course staff see the tab.")
    name = serializers.CharField(help_text="Name of the tab as learners see it; it takes precedence over the title.")
    tab_id = serializers.CharField(help_text="Identifier of the tab within the course, such as progress or wiki.")
    settings = serializers.SerializerMethodField(
        help_text="The tab's other stored settings, such as the url_slug of a custom page.",
    )

    class Meta:
        ref_name = "CourseTab.v1"

    def get_settings(self, tab) -> dict:
        """Return the members of the tab's stored entry not returned as members of their own."""
        return {
            key: value for key, value in tab.tab_dict.items()
            if key not in _TAB_MEMBERS and key != "link_func"
        }


class CustomPageSerializer(CourseTabSerializer):  # pylint: disable=abstract-method
    """One custom page of a course: a tab whose content course authors write."""

    id = serializers.CharField(
        source="locator",
        help_text="Usage key of the custom page. Use it to edit the page's content and to order the pages.",
    )

    class Meta:
        ref_name = "CustomPage.v1"


class CustomPageListQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Query parameters of a listing of a course's custom pages."""

    course_key = serializers.CharField(
        help_text="Key of the course whose custom pages to list, such as course-v1:edX+DemoX+2026.",
    )

    class Meta:
        ref_name = "CustomPageListQuery.v1"

    def validate_course_key(self, value):
        """Parse the value as a course key; refuse a value that is not one, or is in the retired slash form."""
        try:
            course_key = CourseKey.from_string(value)
        except InvalidKeyError:
            raise serializers.ValidationError(NOT_A_COURSE_KEY_MESSAGE) from None
        if course_key.deprecated:
            raise serializers.ValidationError(NOT_A_COURSE_KEY_MESSAGE)
        return course_key


@extend_schema_field({"type": "string"})
class _CustomPageItemField(serializers.JSONField):
    """
    One item of a custom page order, accepted as any JSON value.

    Its type is checked by the list's own validation, so every unusable item
    is refused with the same message instead of a per-item error.
    """


class CustomPageOrderSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A new order for a course's custom pages."""

    custom_pages = serializers.ListField(
        child=_CustomPageItemField(allow_null=True, help_text="Usage key of one of the course's custom pages."),
        allow_empty=True,
        help_text=(
            "Usage keys of the course's custom pages, in the order learners are to see them. Custom pages "
            "left out keep their current order after the pages listed."
        ),
    )

    class Meta:
        ref_name = "CustomPageOrder.v1"

    def validate_custom_pages(self, values):
        """Parse each item as a usage key; refuse the list when one is not a string holding one."""
        usage_keys = []
        for value in values:
            if not isinstance(value, str):
                raise serializers.ValidationError(NOT_A_USAGE_KEY_MESSAGE)
            try:
                usage_keys.append(UsageKey.from_string(value))
            except InvalidKeyError:
                raise serializers.ValidationError(NOT_A_USAGE_KEY_MESSAGE) from None
        return usage_keys


class CourseTabUpdateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Changes to one tab of a course."""

    is_hidden = serializers.BooleanField(
        help_text="True to hide the tab from learners, false to show it. Only hideable tabs accept it.",
    )

    class Meta:
        ref_name = "CourseTabUpdate.v1"
