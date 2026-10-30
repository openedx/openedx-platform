"""Serializers for the Studio documentation links."""

from rest_framework import serializers


class HelpUrlsSerializer(serializers.Serializer):
    """
    The documentation link of each Studio page, keyed by help token.

    The fields follow the ``[pages]`` section of the CMS help tokens file, in
    the same order; a token added there has to be declared here as well.
    """

    default = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for Studio pages that have no page-specific link.",
    )
    home = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for the Studio home page.",
    )
    outline = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for the course outline.",
    )
    unit = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for the unit page.",
    )
    visibility = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for controlling content visibility.",
    )
    textbooks = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for the textbooks page.",
    )
    schedule = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for the course schedule and details page.",
    )
    grading = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for the grading settings page.",
    )
    team_course = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for the course team page.",
    )
    team_library = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for adding users to a library.",
    )
    advanced = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for the advanced settings page.",
    )
    import_library = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for importing a legacy library.",
    )
    import_course = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for the course import page.",
    )
    export_library = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for exporting a legacy library.",
    )
    export_course = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for the course export page.",
    )
    welcome = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for the course author documentation home.",
    )
    content_libraries = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for content libraries.",
    )
    content_groups = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for content groups.",
    )
    enrollment_tracks = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for managing course enrollment tracks.",
    )
    group_configurations = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for the group configurations page.",
    )
    container = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for parent and child components.",
    )
    video = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for working with course videos.",
    )
    certificates = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for the certificates page.",
    )
    content_highlights = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for course highlight emails.",
    )
    social_sharing = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for social sharing settings.",
    )
    sync_library_updates = serializers.URLField(
        read_only=True,
        help_text="Documentation page URL for syncing library updates into a course.",
    )

    class Meta:
        ref_name = "HelpUrls"
