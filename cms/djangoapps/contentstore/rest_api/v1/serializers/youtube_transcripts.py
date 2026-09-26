"""API Serializers for YouTube transcripts."""

from rest_framework import serializers


class YoutubeTranscriptVideoSerializer(serializers.Serializer):
    """One entry of the video list identifying a source to inspect or import from."""

    type = serializers.CharField(
        help_text=(
            'Source kind. "youtube" names a YouTube video id, "edx_video_id" names a '
            "video in the video pipeline, and any other value is treated as an HTML5 source."
        ),
    )
    video = serializers.CharField(
        help_text="Identifier of the source, interpreted according to the type field.",
    )
    mode = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text='Playback mode of an HTML5 source, such as "mp4" or "webm".',
    )

    class Meta:
        ref_name = "YoutubeTranscriptVideo"


class YoutubeTranscriptCheckRequestSerializer(serializers.Serializer):
    """Input identifying the video block whose transcript state is requested."""

    locator = serializers.CharField(
        help_text="Usage key of the video block to inspect.",
    )
    videos = YoutubeTranscriptVideoSerializer(
        many=True,
        help_text="Video sources declared on the block, used to decide which transcripts to compare.",
    )

    class Meta:
        ref_name = "YoutubeTranscriptCheckRequest"


class YoutubeTranscriptImportRequestSerializer(serializers.Serializer):
    """Input identifying the video block to import a YouTube transcript into."""

    locator = serializers.CharField(
        help_text="Usage key of the video block to import the transcript into.",
    )
    videos = YoutubeTranscriptVideoSerializer(
        many=True,
        help_text='Video sources declared on the block. An entry of type "youtube" is required.',
    )

    class Meta:
        ref_name = "YoutubeTranscriptImportRequest"


class YoutubeTranscriptCheckResultSerializer(serializers.Serializer):
    """Transcript availability of one video block, across its declared sources."""

    html5_local = serializers.ListField(
        child=serializers.CharField(),
        help_text="Identifiers of the HTML5 sources that already have a transcript stored for this course.",
    )
    html5_equal = serializers.BooleanField(
        help_text="Whether exactly two HTML5 sources were found and their stored transcripts are identical.",
    )
    is_youtube_mode = serializers.BooleanField(
        help_text="Whether the block declares a YouTube source, which takes priority over HTML5 sources.",
    )
    youtube_local = serializers.BooleanField(
        help_text="Whether a transcript for the YouTube source is already stored for this course.",
    )
    youtube_server = serializers.BooleanField(
        help_text="Whether YouTube is currently serving a transcript for the video.",
    )
    youtube_diff = serializers.BooleanField(
        help_text="Whether the transcript on YouTube differs from the stored one.",
    )
    current_item_subs = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Name of the transcript currently attached to the block, or null when none is attached.",
    )
    status = serializers.CharField(
        help_text='Outcome of the check: "Success" when the block could be inspected.',
    )
    command = serializers.CharField(
        help_text=(
            "Action the caller should take next: replace, found, import, choose, "
            "use_existing, or not_found."
        ),
    )

    class Meta:
        ref_name = "YoutubeTranscriptCheckResult"


class YoutubeTranscriptImportResultSerializer(serializers.Serializer):
    """Outcome of importing a YouTube transcript into a video block."""

    edx_video_id = serializers.CharField(
        allow_null=True,
        help_text=(
            "Video pipeline identifier the transcript was attached to. Null when the block "
            "lives in a content library, where transcripts are stored on the block itself."
        ),
    )
    status = serializers.CharField(
        help_text='Outcome of the import: "Success" when the transcript was stored.',
    )

    class Meta:
        ref_name = "YoutubeTranscriptImportResult"
