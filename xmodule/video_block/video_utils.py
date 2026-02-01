"""
Module contains utils specific for video_block but not for transcripts.
"""


import logging
from collections import OrderedDict
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

log = logging.getLogger(__name__)


def create_youtube_string(block):
    """
    Create a string of Youtube IDs from `block`'s metadata
    attributes. Only writes a speed if an ID is present in the
    block.  Necessary for backwards compatibility with XML-based
    courses.
    """
    youtube_ids = [
        block.youtube_id_1_0,
    ]
    youtube_speeds = ['1.00']
    return ','.join([
        ':'.join(pair)
        for pair
        in zip(youtube_speeds, youtube_ids)
        if pair[1]
    ])


def rewrite_video_url(cdn_base_url, original_video_url):
    """
    Returns a re-written video URL for cases when an alternate source
    has been configured and is selected using factors like
    user location.

    Re-write rules for country codes are specified via the
    EDX_VIDEO_CDN_URLS configuration structure.

    :param cdn_base_url: The scheme, hostname, port and any relevant path prefix for the alternate CDN,
    for example: https://mirror.example.cn/edx
    :param original_video_url: The canonical source for this video, for example:
    https://cdn.example.com/edx-course-videos/VIDEO101/001.mp4
    :return: The re-written URL
    """

    if (not cdn_base_url) or (not original_video_url):
        return None

    parsed = urlparse(original_video_url)
    # Contruction of the rewrite url is intentionally very flexible of input.
    # For example, https://www.edx.org/ + /foo.html will be rewritten to
    # https://www.edx.org/foo.html.
    rewritten_url = cdn_base_url.rstrip("/") + "/" + parsed.path.lstrip("/")
    validator = URLValidator()

    try:
        validator(rewritten_url)
        return rewritten_url
    except ValidationError:
        log.warning("Invalid CDN rewrite URL encountered, %s", rewritten_url)

    # Mimic the behavior of removed get_video_from_cdn in this regard and
    # return None causing the caller to use the original URL.
    return None


def get_poster(video):
    """
    Generate poster metadata.

    youtube_streams is string that contains '1.00:youtube_id'

    Poster metadata is dict of youtube url for image thumbnail and edx logo
    """
    if not video.bumper.get("enabled"):
        return

    poster = OrderedDict({"url": "", "type": ""})

    if video.youtube_streams:
        youtube_id = video.youtube_streams.split('1.00:')[1].split(',')[0]
        poster["url"] = settings.YOUTUBE['IMAGE_API'].format(youtube_id=youtube_id)
        poster["type"] = "youtube"
    else:
        poster["url"] = "https://www.edx.org/sites/default/files/theme/edx-logo-header.png"
        poster["type"] = "html5"

    return poster


def format_xml_exception_message(location, key, value):
    """
    Generate exception message for VideoBlock class which will use for ValueError and UnicodeDecodeError
    when setting xml attributes.
    """
    exception_message = "Block-location:{location}, Key:{key}, Value:{value}".format(
        location=str(location),
        key=key,
        value=value
    )
    return exception_message


def set_query_parameter(url, param_name, param_value):
    """
    Given a URL, set or replace a query parameter and return the
    modified URL.
    """
    scheme, netloc, path, query_string, fragment = urlsplit(url)
    query_params = parse_qs(query_string)
    query_params[param_name] = [param_value]
    new_query_string = urlencode(query_params, doseq=True)

    return urlunsplit((scheme, netloc, path, new_query_string, fragment))
