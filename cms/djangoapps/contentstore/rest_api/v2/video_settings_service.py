"""Service layer for the v2 course video settings endpoint."""

import logging
from datetime import datetime, timedelta

from edxval.api import (
    SortDirection,
    VideoSortField,
    get_available_transcript_languages,
    get_video_transcript_url,
    get_videos_for_course,
)
from rest_framework.exceptions import NotFound

from cms.djangoapps.contentstore.utils import get_course_video_settings_context
from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)

# ``video_storage_handlers`` is imported inside the functions that use it: a
# module-level import fails whenever this module is the first to load it,
# because that module imports ``contentstore.views``, which imports it back.

COURSE_NOT_FOUND_MESSAGE = "The requested course does not exist."

# Statuses VAL reports once every encoding of a video is complete and its
# transcription has started.
TRANSCRIPTION_STATUSES = ("transcription_in_progress", "transcript_ready", "partial_failure", "transcript_failed")


def get_course_video_settings(course_key, include_uploads=False):
    """
    Return the video settings of the course ``course_key``.

    With ``include_uploads``, the course's uploaded videos are added under
    ``previous_uploads``. Raises ``NotFound`` when the course cannot be loaded
    from the modulestore. Nothing is written.
    """
    store = modulestore()
    with store.bulk_operations(course_key):
        course = store.get_course(course_key)
    if course is None:
        log.info("Video settings requested for course %s, which is not in the modulestore.", course_key)
        raise NotFound(COURSE_NOT_FOUND_MESSAGE)

    video_settings = get_course_video_settings_context(course)
    if include_uploads:
        video_settings["previous_uploads"] = get_previous_uploads(course)
    return video_settings


def get_previous_uploads(course):
    """
    Return every video uploaded to ``course`` and not deleted from it, newest first.

    A video still reported as uploading more than ``MAX_UPLOAD_HOURS`` after
    it was created is reported as failed. Its stored status is left as it is.
    """
    course_id = str(course.id)
    videos, __ = get_videos_for_course(course_id, VideoSortField.created, SortDirection.desc, None)
    # Courses without a per-course upload token use the upload workflow in
    # which transcription starts only once every encoding is complete, so a
    # transcription status there also means the encodings are ready.
    uses_upload_token = bool(course.video_upload_pipeline.get("course_video_upload_token"))
    return [_previous_upload(video, course_id, uses_upload_token) for video in videos]


def _previous_upload(video, course_id, uses_upload_token):
    """Return the listing entry of one VAL ``video`` of the course ``course_id``."""
    from cms.djangoapps.contentstore.video_storage_handlers import StatusDisplayStrings

    encodes_ready = not uses_upload_token and video["status"] in TRANSCRIPTION_STATUSES
    transcripts = get_available_transcript_languages(video_id=video["edx_video_id"])

    course_image = [entry for entry in video["courses"] if course_id in entry]
    download_link, file_size = "", 0
    for encoding in video["encoded_videos"]:
        if encoding["profile"] == "desktop_mp4":
            download_link, file_size = encoding["url"], encoding["file_size"]

    return {
        "edx_video_id": video["edx_video_id"],
        "client_video_id": video["client_video_id"],
        "created": video["created"],
        "duration": video["duration"],
        "status": _display_status(video, encodes_ready),
        "course_video_image_url": course_image[0][course_id] if course_image else None,
        "download_link": download_link,
        "file_size": file_size,
        "transcripts": transcripts,
        "transcription_status": StatusDisplayStrings.get(video["status"]) if encodes_ready else "",
        "transcript_urls": {
            language_code: get_video_transcript_url(video_id=video["edx_video_id"], language_code=language_code)
            for language_code in transcripts
        },
        "error_description": video["error_description"],
    }


def _display_status(video, encodes_ready):
    """Return the English display status of a VAL ``video``."""
    from cms.djangoapps.contentstore.video_storage_handlers import MAX_UPLOAD_HOURS, StatusDisplayStrings

    created = video["created"]
    if video["status"] == "upload" and datetime.now(created.tzinfo) - created > timedelta(hours=MAX_UPLOAD_HOURS):
        return StatusDisplayStrings.get("upload_failed")
    if video["status"] == "invalid_token":
        return StatusDisplayStrings.get("youtube_duplicate")
    if encodes_ready:
        return StatusDisplayStrings.get("file_complete")
    return StatusDisplayStrings.get(video["status"])
