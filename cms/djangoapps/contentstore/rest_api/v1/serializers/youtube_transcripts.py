"""
Serializers for the YouTube transcripts resource (v1 — ADR 0025).

These mirror the v0 ``YoutubeTranscriptCheckSerializer`` /
``YoutubeTranscriptUploadSerializer`` field shapes exactly (response parity is
required — see the survey), but are actually used to serialize the DRF
``Response`` body in v1, instead of being declared-but-unused as they are in
v0.
"""
from rest_framework import serializers

from cms.djangoapps.contentstore.rest_api.serializers.common import StrictSerializer


class YoutubeTranscriptCheckRequestSerializer(serializers.Serializer):
    """
    Validates the ``data`` query-parameter JSON payload the legacy
    ``check_transcripts()`` function expects (see
    ``transcripts_ajax.py:518`` — read from ``request.GET['data']``
    regardless of HTTP verb). The view parses and validates this payload
    through this serializer before calling the legacy function, rather than
    leaving it as an opaque, unvalidated query string.
    """
    locator = serializers.CharField(help_text="Usage key string identifying the video xblock.")
    videos = serializers.ListField(
        child=serializers.DictField(),
        help_text=(
            "List of video descriptors, e.g. "
            "[{'type': 'youtube', 'video': 'abc123', 'mode': 'youtube'}, "
            "{'type': 'html5', 'video': 'vid1', 'mode': 'mp4'}]."
        ),
    )


class YoutubeTranscriptUploadRequestSerializer(serializers.Serializer):
    """
    Validates the ``data`` query-parameter JSON payload the legacy
    ``replace_transcripts()`` function expects. Same shape as the check
    request; ``videos`` must include a ``youtube`` entry for the
    upload/replace operation to succeed (enforced by the underlying legacy
    function).
    """
    locator = serializers.CharField(help_text="Usage key string identifying the video xblock.")
    videos = serializers.ListField(
        child=serializers.DictField(),
        help_text=(
            "List of video descriptors; must include a "
            "{'type': 'youtube', 'video': '<youtube_id>', 'mode': 'youtube'} entry."
        ),
    )


class YoutubeTranscriptCheckSerializer(StrictSerializer):
    """
    Strict serializer for the YouTube transcripts check response (v1).

    Field-for-field identical to the v0 declaration (response parity per the
    survey) — this is now actually used to serialize the view's output
    instead of being declared but unused.
    """
    html5_local = serializers.ListField(child=serializers.CharField())
    html5_equal = serializers.BooleanField()
    is_youtube_mode = serializers.BooleanField()
    youtube_local = serializers.BooleanField()
    youtube_server = serializers.BooleanField()
    youtube_diff = serializers.BooleanField()
    # `transcripts_ajax.py:387` sets this to `item.sub`, a plain string field
    # on the video block (not a list) — the v0 declaration as `ListField` was
    # wrong and would 400 any real response where `sub` is set. Corrected
    # here to match actual legacy behavior (response parity), not a
    # business-logic change.
    current_item_subs = serializers.CharField(required=False, allow_null=True)
    status = serializers.CharField()
    command = serializers.CharField()


class YoutubeTranscriptUploadSerializer(StrictSerializer):
    """
    Strict serializer for the YouTube transcripts upload response (v1).

    Field-for-field identical to the v0 declaration (response parity per the
    survey), except ``edx_video_id`` is corrected to ``allow_null=True``:
    ``transcripts_ajax.py:706-721,748`` shows ``replace_transcripts`` returns
    ``{'edx_video_id': None, 'status': 'Success'}`` for a video hosted in a
    V2 content library (``LibraryLocatorV2``) — the YouTube download,
    transcript save, and ``video.save()`` have already completed
    successfully by the time that response is built. A non-nullable
    ``CharField`` would reject that already-committed success as a 400. This
    is a serializer correction to match actual legacy behavior, not a
    business-logic change.
    """
    edx_video_id = serializers.CharField(allow_null=True)
    status = serializers.CharField()
