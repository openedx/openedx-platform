"""Serializers for zip archives of a course's videos."""

from rest_framework import serializers


class VideoArchiveFileSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One video to put in the archive."""

    # Values are used exactly as sent: the URL is matched character for
    # character against the course's video URLs, and the name becomes the entry
    # name in the archive.
    url = serializers.CharField(
        trim_whitespace=False,
        help_text=(
            "Address of one of the course's encoded video files, exactly as the course's video "
            "listing reports it. Any other address is refused."
        ),
    )
    name = serializers.CharField(
        trim_whitespace=False,
        allow_blank=True,
        help_text=(
            "File name of the video inside the archive. When it does not already end with the "
            "extension of the video's content type, that extension is appended."
        ),
    )


class VideoArchiveRequestSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The videos to bundle into one archive."""

    files = VideoArchiveFileSerializer(
        many=True,
        help_text="The videos to include, in archive order. An empty list gives an empty archive.",
    )
