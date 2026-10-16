"""Serializers for where a video is used in a course."""

from rest_framework import serializers


class VideoUsageLocationSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One video component of the course that plays the video."""

    display_location = serializers.CharField(
        help_text=(
            "Where the component sits, as '<subsection> - <unit> / <component>' display names. "
            "A subsection or unit with no display name set appears as 'None', and a component with "
            "none as 'Video'. The subsection part is empty for a unit that is in no subsection."
        ),
    )
    url = serializers.CharField(
        help_text=(
            "Studio address of the unit holding the component, anchored at the component: "
            "'/container/<unit usage key>#<component usage key>'."
        ),
    )


class CourseVideoUsageSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The places in a course where one video is used."""

    usage_locations = VideoUsageLocationSerializer(
        many=True,
        help_text=(
            "One entry per video component that references the video, published or not. A "
            "component with no parent in the course outline is left out. Empty when no component "
            "is listed."
        ),
    )
