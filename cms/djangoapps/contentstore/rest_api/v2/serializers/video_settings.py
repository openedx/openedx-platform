"""Serializers for a course's video upload and transcript settings."""

from rest_framework import serializers


class VideoSettingsImageSerializer(serializers.Serializer):
    """Limits that apply to video thumbnail images uploaded to the course."""

    video_image_upload_enabled = serializers.BooleanField(
        help_text="Whether thumbnail images may be uploaded for the course's videos.",
    )
    max_size = serializers.IntegerField(help_text="Largest accepted image, in bytes.")
    min_size = serializers.IntegerField(help_text="Smallest accepted image, in bytes.")
    max_width = serializers.IntegerField(help_text="Largest accepted image width, in pixels.")
    max_height = serializers.IntegerField(help_text="Largest accepted image height, in pixels.")
    supported_file_formats = serializers.DictField(
        child=serializers.CharField(),
        help_text="Accepted image file extensions, each mapped to its MIME type.",
    )

    class Meta:
        ref_name = "VideoSettingsImage"


class VideoSettingsTranscriptSerializer(serializers.Serializer):
    """Studio handler addresses and options for the course's video transcripts."""

    transcript_download_handler_url = serializers.CharField(
        help_text="Studio address that downloads a transcript.",
    )
    transcript_upload_handler_url = serializers.CharField(
        help_text="Studio address that uploads a transcript.",
    )
    transcript_delete_handler_url = serializers.CharField(
        help_text="Studio address that deletes a transcript of this course.",
    )
    transcript_download_file_format = serializers.CharField(
        source="trancript_download_file_format",
        help_text="File format transcripts are downloaded in.",
    )
    transcript_preferences_handler_url = serializers.CharField(
        allow_null=True,
        help_text="Studio address that saves the course's transcript preferences. Null when transcripts are disabled.",
    )
    transcript_credentials_handler_url = serializers.CharField(
        allow_null=True,
        help_text="Studio address that saves transcript provider credentials. Null when transcripts are disabled.",
    )
    transcription_plans = serializers.DictField(
        child=serializers.DictField(),
        allow_null=True,
        help_text=(
            "Third-party transcription plans, keyed by provider name, with each plan's options as the "
            "provider defines them. Null when transcripts are disabled."
        ),
    )

    class Meta:
        ref_name = "VideoSettingsTranscript"


class VideoSettingsTranscriptPreferencesSerializer(serializers.Serializer):
    """The course's saved preferences for third-party transcription."""

    course_id = serializers.CharField(help_text="Key of the course the preferences belong to.")
    provider = serializers.CharField(help_text="Transcription provider.")
    cielo24_fidelity = serializers.CharField(allow_null=True, help_text="Cielo24 fidelity level, when Cielo24 is used.")
    cielo24_turnaround = serializers.CharField(
        allow_null=True,
        help_text="Cielo24 turnaround, when Cielo24 is used.",
    )
    three_play_turnaround = serializers.CharField(
        allow_null=True,
        help_text="3Play Media turnaround, when 3Play Media is used.",
    )
    preferred_languages = serializers.ListField(
        child=serializers.CharField(),
        help_text="Language codes transcripts are requested in.",
    )
    video_source_language = serializers.CharField(
        allow_null=True,
        help_text="Language code of the videos' spoken language.",
    )
    modified = serializers.CharField(help_text="When the preferences were last changed.")

    class Meta:
        ref_name = "VideoSettingsTranscriptPreferences"


class TranscriptLanguageSerializer(serializers.Serializer):
    """A language a transcript may be written in."""

    language_code = serializers.CharField(help_text="Language code.")
    language_text = serializers.CharField(help_text="Display name of the language.")

    class Meta:
        ref_name = "TranscriptLanguage"


class PreviousVideoUploadSerializer(serializers.Serializer):
    """A video uploaded to the course, as listed on the course's video uploads page."""

    client_video_id = serializers.CharField(help_text="File name the video was uploaded with.")
    course_video_image_url = serializers.CharField(
        allow_null=True,
        help_text="Address of the video's thumbnail image in this course. Null when it has none.",
    )
    created = serializers.CharField(
        help_text=(
            "When the video was created, as a date, a space, a time and a UTC offset, "
            "for example '2026-01-31 14:05:09.123456+00:00'."
        ),
    )
    duration = serializers.FloatField(help_text="Length of the video, in seconds.")
    edx_video_id = serializers.CharField(help_text="Identifier of the video.")
    error_description = serializers.CharField(
        allow_null=True,
        help_text="Why processing the video failed. Null when it has not failed.",
    )
    status = serializers.CharField(
        help_text=(
            "Upload and processing status, in English, for example 'Uploading', 'Ready' or 'Failed'. "
            "A video left uploading for more than a day is reported as 'Failed'."
        ),
    )
    file_size = serializers.IntegerField(
        help_text="Size of the video's desktop MP4 encoding, in bytes. 0 when it has none.",
    )
    download_link = serializers.CharField(
        allow_blank=True,
        help_text="Address of the video's desktop MP4 encoding. Empty when it has none.",
    )
    transcript_urls = serializers.DictField(
        child=serializers.CharField(),
        help_text="Address of each of the video's transcripts, keyed by language code.",
    )
    transcription_status = serializers.CharField(
        allow_blank=True,
        help_text="Status of the video's automatic transcription. Empty when none is under way.",
    )
    transcripts = serializers.ListField(
        child=serializers.CharField(),
        help_text="Language codes of the video's transcripts.",
    )

    class Meta:
        ref_name = "PreviousVideoUpload"


class CourseVideoSettingsSerializer(serializers.Serializer):
    """The course's video upload and transcript settings."""

    image_upload_url = serializers.CharField(help_text="Studio address that uploads a video's thumbnail image.")
    video_handler_url = serializers.CharField(help_text="Studio address that manages the course's video uploads.")
    encodings_download_url = serializers.CharField(
        help_text="Studio address that downloads the course's video encodings as CSV.",
    )
    default_video_image_url = serializers.CharField(help_text="Address of the image shown for videos without one.")
    concurrent_upload_limit = serializers.IntegerField(
        help_text="How many videos a client may upload at the same time.",
    )
    video_supported_file_formats = serializers.ListField(
        child=serializers.CharField(),
        help_text="Accepted video file extensions.",
    )
    video_upload_max_file_size = serializers.IntegerField(help_text="Largest accepted video file, in gigabytes.")
    video_image_settings = VideoSettingsImageSerializer(help_text="Limits for video thumbnail images.")
    is_video_transcript_enabled = serializers.BooleanField(
        help_text="Whether third-party transcription is enabled for the course.",
    )
    is_ai_translations_enabled = serializers.BooleanField(
        help_text="Whether AI translation of transcripts is enabled for the course.",
    )
    active_transcript_preferences = VideoSettingsTranscriptPreferencesSerializer(
        allow_null=True,
        help_text="The course's transcription preferences. Null when transcripts are disabled or none are saved.",
    )
    transcript_credentials = serializers.DictField(
        child=serializers.BooleanField(),
        allow_null=True,
        help_text=(
            "Whether the course's organization has credentials for each transcription provider, "
            "keyed by provider name. Null when transcripts are disabled."
        ),
    )
    transcript_available_languages = TranscriptLanguageSerializer(
        many=True,
        help_text="Every language a transcript may be written in, ordered by display name.",
    )
    video_transcript_settings = VideoSettingsTranscriptSerializer(
        help_text="Studio handler addresses and options for transcripts.",
    )

    class Meta:
        ref_name = "CourseVideoSettings"


class CourseVideoSettingsFullSerializer(CourseVideoSettingsSerializer):
    """The course's video settings together with every video uploaded to the course."""

    previous_uploads = PreviousVideoUploadSerializer(
        many=True,
        help_text="Every video uploaded to the course and not deleted from it, newest first.",
    )

    class Meta:
        ref_name = "CourseVideoSettingsFull"


class CourseVideoSettingsQuerySerializer(serializers.Serializer):
    """Query parameters of the course video settings endpoint."""

    view = serializers.ChoiceField(
        choices=["full"],
        required=False,
        help_text="'full' adds the course's uploaded videos as 'previous_uploads'. Omit it for the settings alone.",
    )

    class Meta:
        ref_name = "CourseVideoSettingsQuery"
