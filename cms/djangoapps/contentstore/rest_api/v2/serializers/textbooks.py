"""Serializers for course PDF textbooks."""

from rest_framework import serializers


class TextbookChapterSerializer(serializers.Serializer):
    """
    One chapter of a course PDF textbook.

    A stored chapter missing ``title`` or ``url``, or holding null for either,
    is returned with an empty string in its place rather than failing the page.
    """

    title = serializers.CharField(
        default="",
        help_text="Chapter title shown in the textbook's table of contents. Empty when the stored chapter has none.",
    )
    url = serializers.CharField(
        default="",
        help_text=(
            "Address of the chapter's PDF file, usually a course asset path. "
            "Empty when the stored chapter has none."
        ),
    )

    class Meta:
        ref_name = "TextbookChapter"

    def to_representation(self, instance):
        data = super().to_representation(instance)
        return {key: "" if value is None else value for key, value in data.items()}


class CourseTextbookSerializer(serializers.Serializer):
    """
    One PDF textbook of a course, as stored in the course's settings.

    A stored textbook can lack ``id``, ``chapters`` or ``tab_title``: imported
    courses may carry textbooks written by other tools, such as the single-file
    form that has a ``url`` and no chapters. Such a textbook is returned with
    ``null`` for the missing scalar and an empty chapter list, rather than
    failing the whole page. A stored ``null`` chapter list is returned empty
    too. Stored keys not declared here are not returned.
    """

    id = serializers.CharField(
        allow_null=True,
        default=None,
        help_text="Identifier of the textbook, unique within its course. Null when the stored textbook has none.",
    )
    chapters = TextbookChapterSerializer(
        many=True,
        default=list,
        help_text="Chapters in reading order. Empty when the stored textbook has no chapter list.",
    )
    tab_title = serializers.CharField(
        allow_null=True,
        default=None,
        help_text="Title of the course tab that opens the textbook. Null when the stored textbook has none.",
    )

    class Meta:
        ref_name = "CourseTextbook"

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get("chapters") is None:
            data["chapters"] = []
        return data
