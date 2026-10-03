"""Service layer for the course video uploads resource."""

import logging
from datetime import datetime, timedelta

from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from edx_rest_framework_extensions.errors import register_error_type
from edxval.api import (
    SortDirection,
    VideoSortField,
    get_available_transcript_languages,
    get_course_videos_qset,
    get_video_info,
    get_video_transcript_url,
    get_videos_for_course,
    remove_video_for_course,
)
from edxval.exceptions import ValVideoNotFoundError
from pytz import UTC
from rest_framework.exceptions import (
    APIException,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)

from cms.djangoapps.contentstore.toggles import use_mock_video_uploads
from cms.djangoapps.contentstore.video_storage_handlers import (
    MAX_UPLOAD_HOURS,
    StatusDisplayStrings,
    _get_and_validate_course,
    videos_post,
)

log = logging.getLogger(__name__)

#: Error type of a course whose video uploads are switched off or unconfigured.
VIDEO_UPLOADS_NOT_CONFIGURED_SLUG = "videos/uploads-not-configured"

#: Sort fields the video store can order by, ascending and descending.
VIDEO_ORDERING_CHOICES = tuple(
    sorted(
        [field.value for field in VideoSortField]
        + [f"-{field.value}" for field in VideoSortField]
    )
)
DEFAULT_ORDERING = "-created"

#: VAL statuses that mean transcription has started, which only happens once
#: every encoding is complete on the current video workflow.
_TRANSCRIPTION_STATUSES = frozenset({
    "transcription_in_progress",
    "transcript_ready",
    "partial_failure",
    "transcript_failed",
})


class VideoUploadsNotConfigured(NotFound):
    """The course cannot serve video uploads because the upload pipeline is off or unconfigured."""

    default_detail = "Video uploads are not configured for this course."
    default_code = "video_uploads_not_configured"


register_error_type(
    VideoUploadsNotConfigured,
    VIDEO_UPLOADS_NOT_CONFIGURED_SLUG,
    "Video Uploads Not Configured",
)


class _JsonBody:
    """
    Carrier for a parsed JSON body.

    ``videos_post`` reads the request's already-parsed JSON and nothing else, so
    it is handed this instead of the live request, which keeps the request
    object out of the storage layer.
    """

    def __init__(self, json_body):
        self.json = json_body


def get_course_for_uploads(course_key, user):
    """
    Return the course a video upload operation applies to.

    Enforces the caller's Studio access to the course and the course's upload
    pipeline configuration; a course the caller may not read is refused, and a
    course that does not accept uploads is reported as missing. Returns
    ``None`` only when uploads are mocked for local development, in which case
    no real course is needed.
    """
    try:
        course = _get_and_validate_course(str(course_key), user)
    except DjangoPermissionDenied as error:
        log.info("Course [%s] is not readable in Studio by user [%s]", course_key, user.id)
        raise PermissionDenied() from error
    except Http404 as error:
        log.info("No course [%s] to serve video uploads for", course_key)
        raise NotFound() from error
    if course is None and not use_mock_video_uploads():
        raise VideoUploadsNotConfigured()
    return course


def _require_course(course):
    """Return ``course``, or refuse a read that has no course behind it."""
    if course is None:
        raise VideoUploadsNotConfigured()
    return course


def _parse_ordering(ordering):
    """Split an ordering value into the sort field and direction the video store expects."""
    ordering = ordering or DEFAULT_ORDERING
    descending = ordering.startswith("-")
    return (
        VideoSortField(ordering.lstrip("-")),
        SortDirection.desc if descending else SortDirection.asc,
    )


def list_course_videos(course, ordering=DEFAULT_ORDERING):
    """
    Return every non-hidden video attached to ``course``, in the requested order.

    Rows are returned unenriched: the per-video transcript lookups happen in
    :func:`enrich_course_videos`, so a caller that only shows one page pays for
    one page.
    """
    sort_field, sort_direction = _parse_ordering(ordering)
    videos, __ = get_videos_for_course(str(_require_course(course).id), sort_field, sort_direction)
    return list(videos)


def get_course_video(course, edx_video_id):
    """Return the one video attached to ``course`` under ``edx_video_id``, unenriched."""
    course = _require_course(course)
    attached = get_course_videos_qset(course.id).filter(video__edx_video_id=edx_video_id).exists()
    if not attached:
        raise NotFound()
    try:
        return get_video_info(edx_video_id)
    except ValVideoNotFoundError as error:
        log.info("No video record for edx_video_id [%s] in course [%s]", edx_video_id, course.id)
        raise NotFound() from error


def enrich_course_videos(course, videos):
    """
    Return the API representation of ``videos`` for ``course``.

    Each row gains its display status, its course-specific thumbnail, its
    downloadable encoding and its transcript languages and URLs.
    """
    course = _require_course(course)
    course_id = str(course.id)
    upload_token = course.video_upload_pipeline.get("course_video_upload_token")
    return [_enrich_video(video, course_id, upload_token) for video in videos]


def _enrich_video(video, course_id, upload_token):
    """Return the API representation of one video row."""
    encodes_ready = not upload_token and video["status"] in _TRANSCRIPTION_STATUSES
    status = _display_status(video, encodes_ready)
    transcripts = get_available_transcript_languages(video_id=video["edx_video_id"])
    download_link, file_size = _desktop_encoding(video)
    return {
        "edx_video_id": video["edx_video_id"],
        "client_video_id": video["client_video_id"],
        "created": video["created"],
        "duration": video["duration"],
        "status": status,
        "error_description": video["error_description"],
        "course_video_image_url": _course_video_image_url(video, course_id),
        "download_link": download_link,
        "file_size": file_size,
        "transcripts": transcripts,
        "transcription_status": _transcription_status(video, encodes_ready),
        "transcript_urls": {
            language_code: get_video_transcript_url(
                video_id=video["edx_video_id"],
                language_code=language_code,
            )
            for language_code in transcripts
        },
    }


def _transcription_status(video, encodes_ready):
    """
    Return how far transcription has got for one video.

    Only courses on the current video workflow report it: elsewhere the stored
    status never reaches a transcription state. It names the transcription
    state itself, which the display status no longer shows once the encodes are
    complete.
    """
    if not encodes_ready:
        return ""
    return StatusDisplayStrings.get(video["status"])


def _display_status(video, encodes_ready):
    """
    Return the display status of one video.

    A video left in ``upload`` for longer than the upload window is reported as
    failed. Reporting it does not reconcile the stored record.
    """
    created = video.get("created")
    now = datetime.now(created.tzinfo if created else UTC)
    if video["status"] == "upload" and created and (now - created) > timedelta(hours=MAX_UPLOAD_HOURS):
        return StatusDisplayStrings.get("upload_failed")
    if video["status"] == "invalid_token":
        return StatusDisplayStrings.get("youtube_duplicate")
    if encodes_ready:
        return StatusDisplayStrings.get("file_complete")
    return StatusDisplayStrings.get(video["status"])


def _course_video_image_url(video, course_id):
    """Return the thumbnail URL recorded for this video in this course, or None."""
    for course in video["courses"]:
        if course_id in course:
            return course[course_id]
    return None


def _desktop_encoding(video):
    """Return the desktop MP4 download URL and file size, or empty values when absent."""
    for encoding in video["encoded_videos"]:
        if encoding["profile"] == "desktop_mp4":
            return encoding["url"], encoding["file_size"]
    return "", 0


#: The message every refused upload request is answered with. The video store's
#: own wording can name internal storage detail, so it is logged instead.
_UPLOAD_REFUSED = "The requested files cannot be uploaded."

#: How a refusal from the video store is reported, by the status it refused with.
_UPLOAD_REFUSAL_ERRORS = {
    HTTP_403_FORBIDDEN: PermissionDenied,
    HTTP_404_NOT_FOUND: NotFound,
}


def _upload_refusal(status_code):
    """Return the error answering a refusal the video store reported with ``status_code``."""
    if status_code == HTTP_400_BAD_REQUEST:
        return ValidationError({"files": [_UPLOAD_REFUSED]})
    return _UPLOAD_REFUSAL_ERRORS.get(status_code, APIException)(_UPLOAD_REFUSED)


def create_video_uploads(course, files):
    """
    Create one upload slot per entry in ``files`` and return them in request order.

    Each slot carries a newly assigned video identifier and a short-lived
    pre-signed URL the caller PUTs the file to.
    """
    data, status_code = videos_post(course, _JsonBody({"files": files}))
    if status_code == HTTP_200_OK:
        return data
    log.warning(
        "Video upload slots refused with status [%s] for course [%s]: %s",
        status_code,
        getattr(course, "id", None),
        data.get("error"),
    )
    raise _upload_refusal(status_code)


def delete_course_video(course_key, edx_video_id):
    """Detach ``edx_video_id`` from the course; the video itself is kept for other courses."""
    try:
        remove_video_for_course(str(course_key), edx_video_id)
    except ObjectDoesNotExist as error:
        log.info("No video [%s] attached to course [%s] to remove", edx_video_id, course_key)
        raise NotFound() from error
