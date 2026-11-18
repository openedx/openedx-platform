"""Serializers for zip archives of a course's videos."""

from rest_framework import serializers

from cms.djangoapps.contentstore.video_storage_handlers import get_course_video_download_urls


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
    """
    The videos to bundle into one archive.

    Expects the parsed ``course_key`` of the course in its context.
    """

    files = VideoArchiveFileSerializer(
        many=True,
        help_text="The videos to include, in archive order. An empty list gives an empty archive.",
    )

    def validate_files(self, files):
        """Refuse every address that is not one of the course's encoded-video URLs."""
        if not files:
            return files
        allowed_urls = get_course_video_download_urls(str(self.context["course_key"]))
        refused = [file["url"] for file in files if file["url"] not in allowed_urls]
        if refused:
            raise serializers.ValidationError([f"Invalid video download url: {url}" for url in refused])
        return files
