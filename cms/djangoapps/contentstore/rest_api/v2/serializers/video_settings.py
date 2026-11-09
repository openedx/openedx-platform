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
