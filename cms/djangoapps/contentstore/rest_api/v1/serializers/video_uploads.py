"""API Serializers for course video uploads."""

from edx_rest_framework_extensions.shaping import MinimalViewMixin
from rest_framework import serializers
from rest_framework.settings import api_settings

from cms.djangoapps.contentstore.rest_api.serializers.common import StrictSerializer
from cms.djangoapps.contentstore.rest_api.v1.video_uploads_service import VIDEO_ORDERING_CHOICES
from cms.djangoapps.contentstore.video_storage_handlers import VIDEO_SUPPORTED_FILE_FORMATS

SUPPORTED_UPLOAD_CONTENT_TYPES = sorted(set(VIDEO_SUPPORTED_FILE_FORMATS.values()))

NON_FIELD_ERRORS_KEY = api_settings.NON_FIELD_ERRORS_KEY

#: The fields one video is reduced to under the minimal response preset.
MINIMAL_VIDEO_FIELDS = ("edx_video_id", "client_video_id", "status", "created", "duration")


def _flat_messages(detail, path=""):
    """
    Return validation ``detail`` as a map of field path to messages.

    Errors reported for the entries of a list of objects are nested one or two
    levels below the field they belong to. Flattening them to
    ``{"files[0].content_type": ["..."]}`` gives a client the message of every
    invalid field without walking the containers it is reported inside.
    """
    if isinstance(detail, dict):
        flat = {}
        for field, value in detail.items():
            nested = path if field == NON_FIELD_ERRORS_KEY and path else _join(path, field)
            flat.update(_flat_messages(value, nested))
        return flat
    if isinstance(detail, list) and any(isinstance(entry, (dict, list)) for entry in detail):
        flat = {}
        for index, entry in enumerate(detail):
            if entry:
                flat.update(_flat_messages(entry, f"{path}[{index}]"))
        return flat
    messages = detail if isinstance(detail, list) else [detail]
    return {path or NON_FIELD_ERRORS_KEY: [str(message) for message in messages]}


def _join(path, field):
    """Return the path of ``field`` inside ``path``."""
    return f"{path}.{field}" if path else str(field)


class VideoUploadFileSerializer(StrictSerializer):
    """One requested upload slot: the file the caller intends to PUT."""

    file_name = serializers.CharField(
        help_text="Name of the video file being uploaded. ASCII characters only.",
    )
    content_type = serializers.ChoiceField(
        choices=SUPPORTED_UPLOAD_CONTENT_TYPES,
        help_text="MIME type of the video file. Only these types can be stored.",
    )

    def validate_file_name(self, value):
        """Reject names the storage backend cannot carry in its object metadata."""
        try:
            value.encode("ascii")
        except UnicodeEncodeError as error:
            raise serializers.ValidationError(
                "The file name must contain only ASCII characters."
            ) from error
        return value


class VideoUploadRequestSerializer(StrictSerializer):
    """Request body for creating upload slots."""

    files = VideoUploadFileSerializer(
        many=True,
        allow_empty=False,
        help_text="Files to create upload slots for. One slot is returned per entry, in order.",
    )

    def run_validation(self, data=serializers.empty):
        """Validate the body, reporting every invalid field as a list of messages."""
        try:
            return super().run_validation(data)
        except serializers.ValidationError as error:
            raise serializers.ValidationError(_flat_messages(error.detail)) from error


class VideoUploadLinkSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One created upload slot."""

    file_name = serializers.CharField(
        help_text="Name of the video file, echoed from the request.",
    )
    upload_url = serializers.CharField(
        help_text="Short-lived pre-signed URL to PUT the video file to. Expires after 24 hours.",
    )
    edx_video_id = serializers.CharField(
        help_text="Identifier assigned to the video. Use it to address the video afterwards.",
    )


class VideoUploadResponseSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Response body for creating upload slots."""

    files = VideoUploadLinkSerializer(
        many=True,
        help_text="Created upload slots, one per requested file, in request order.",
    )


class CourseVideoSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One video asset attached to a course."""

    edx_video_id = serializers.CharField(
        help_text="Identifier of the video.",
    )
    client_video_id = serializers.CharField(
        allow_blank=True,
        help_text="Original file name the video was uploaded under.",
    )
    created = serializers.DateTimeField(
        allow_null=True,
        help_text="Time the video record was created.",
    )
    duration = serializers.FloatField(
        allow_null=True,
        help_text="Length of the video in seconds; 0 until the video has been processed.",
    )
    status = serializers.CharField(
        help_text=(
            "Processing state of the video, in stable English: for example Uploading, "
            "In Progress, Ready, Failed, YouTube Duplicate."
        ),
    )
    error_description = serializers.CharField(
        allow_null=True,
        allow_blank=True,
        help_text="Details of the processing failure, when the video failed to process.",
    )
    course_video_image_url = serializers.CharField(
        allow_null=True,
        help_text="Thumbnail image URL for this video in this course, or null when none is set.",
    )
    download_link = serializers.CharField(
        allow_blank=True,
        help_text="URL of the desktop MP4 encoding, or an empty string when it is not ready.",
    )
    file_size = serializers.IntegerField(
        help_text="Size of the desktop MP4 encoding in bytes; 0 when it is not ready.",
    )
    transcripts = serializers.ListField(
        child=serializers.CharField(),
        help_text="Language codes the video has transcripts for.",
    )
    transcription_status = serializers.CharField(
        allow_blank=True,
        help_text=(
            "Transcription state for courses on the current video workflow, in stable English; "
            "empty for courses still using a course video upload token."
        ),
    )
    transcript_urls = serializers.DictField(
        child=serializers.CharField(),
        help_text="Transcript download URL keyed by language code.",
    )


class CourseVideoMinimalSerializer(CourseVideoSerializer):
    """One video asset, reduced to the fields that identify it."""

    def get_fields(self):
        """Return only the fields the minimal preset keeps."""
        fields = super().get_fields()
        return {name: field for name, field in fields.items() if name in MINIMAL_VIDEO_FIELDS}


class CourseVideoQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Query parameters accepted by every course video address."""

    view = serializers.ChoiceField(
        choices=[MinimalViewMixin.minimal_view_value],
        required=False,
        help_text="Response preset to return. Omit it for the full representation.",
    )


class CourseVideoListQuerySerializer(CourseVideoQuerySerializer):
    """Query parameters accepted by the course video collection."""

    ordering = serializers.ChoiceField(
        choices=VIDEO_ORDERING_CHOICES,
        required=False,
        help_text="Field to sort by. Prefix the field name with '-' to sort descending.",
    )
    page = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Number of the page to return.",
    )
    page_size = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Number of videos per page; values above the maximum are reduced to it.",
    )
