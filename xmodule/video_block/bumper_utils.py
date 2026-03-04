"""
Utils for video bumper
"""


import copy
import json
import logging
from collections import OrderedDict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings

from .video_utils import set_query_parameter

try:
    import edxval.api as edxval_api
except ImportError:
    edxval_api = None

log = logging.getLogger(__name__)


def get_bumper_settings(video):
    """
    Get bumper settings from video instance.
    """
    bumper_settings = copy.deepcopy(getattr(video, 'video_bumper', {}))

    # clean up /static/ prefix from bumper transcripts
    for lang, transcript_url in bumper_settings.get('transcripts', {}).items():
        bumper_settings['transcripts'][lang] = transcript_url.replace("/static/", "")

    return bumper_settings


def is_bumper_enabled(video):
    """
    Check if bumper enabled.

    - Feature flag ENABLE_VIDEO_BUMPER should be set to True
    - Do not show again button should not be clicked by user.
    - Current time minus periodicity must be greater that last time viewed
    - edxval_api should be presented

    Returns:
         bool.
    """
    bumper_last_view_date = getattr(video, 'bumper_last_view_date', None)
    utc_now = datetime.now(ZoneInfo("UTC"))
    periodicity = settings.SHOW_BUMPER_PERIODICITY
    has_viewed = any([
        video.bumper_do_not_show_again,
        (bumper_last_view_date and bumper_last_view_date + timedelta(seconds=periodicity) > utc_now)
    ])
    is_studio = getattr(video.runtime, "is_author_mode", False)
    return bool(
        not is_studio and
        settings.ENABLE_VIDEO_BUMPER and
        get_bumper_settings(video) and
        edxval_api and
        not has_viewed
    )


def bumperize(video):
    """
    Populate video with bumper settings, if they are presented.
    """
    video.bumper = {
        'enabled': False,
        'edx_video_id': "",
        'transcripts': {},
        'metadata': None,
    }

    if not is_bumper_enabled(video):
        return

    bumper_settings = get_bumper_settings(video)

    try:
        video.bumper['edx_video_id'] = bumper_settings['video_id']
        video.bumper['transcripts'] = bumper_settings['transcripts']
    except (TypeError, KeyError):
        log.warning(
            "Could not retrieve video bumper information from course settings"
        )
        return

    sources = get_bumper_sources(video)
    if not sources:
        return

    video.bumper.update({
        'metadata': bumper_metadata(video, sources),
        'enabled': True,  # Video poster needs this.
    })


def get_bumper_sources(video):
    """
    Get bumper sources from edxval.

    Returns list of sources.
    """
    try:
        val_profiles = ["desktop_webm", "desktop_mp4"]
        val_video_urls = edxval_api.get_urls_for_profiles(video.bumper['edx_video_id'], val_profiles)
        bumper_sources = [url for url in [val_video_urls[p] for p in val_profiles] if url]
    except edxval_api.ValInternalError:
        # if no bumper sources, nothing will be showed
        log.warning(
            "Could not retrieve information from VAL for Bumper edx Video ID: %s.", video.bumper['edx_video_id']
        )
        return []

    return bumper_sources


def bumper_metadata(video, sources):
    """
    Generate bumper metadata.
    """
    transcripts = video.get_transcripts_info(is_bumper=True)
    unused_track_url, bumper_transcript_language, bumper_languages = video.get_transcripts_for_student(transcripts)

    metadata = OrderedDict({
        'saveStateUrl': video.ajax_url + '/save_user_state',
        'showCaptions': json.dumps(video.show_captions),
        'sources': sources,
        'streams': '',
        'transcriptLanguage': bumper_transcript_language,
        'transcriptLanguages': bumper_languages,
        'transcriptTranslationUrl': set_query_parameter(
            video.runtime.handler_url(video, 'transcript', 'translation/__lang__').rstrip('/?'), 'is_bumper', 1
        ),
        'transcriptAvailableTranslationsUrl': set_query_parameter(
            video.runtime.handler_url(video, 'transcript', 'available_translations').rstrip('/?'), 'is_bumper', 1
        ),
        'publishCompletionUrl': set_query_parameter(
            video.runtime.handler_url(video, 'publish_completion', '').rstrip('?'), 'is_bumper', 1
        ),
    })

    return metadata
