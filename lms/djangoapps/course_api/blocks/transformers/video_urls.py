"""
Video block URL Transformer
"""

import logging
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from openedx.core.djangoapps.content.block_structure.transformer import BlockStructureTransformer

from .student_view import StudentViewTransformer

log = logging.getLogger(__name__)


def rewrite_video_url(cdn_base_url, original_video_url):
    """
    Returns a re-written video URL for cases when an alternate source
    has been configured and is selected using factors like user location.

    :param cdn_base_url: The scheme, hostname, port and any relevant path prefix for the alternate CDN.
    :param original_video_url: The canonical source for this video.
    :return: The re-written URL, or None if the result is not a valid URL.
    """
    if (not cdn_base_url) or (not original_video_url):
        return None

    parsed = urlparse(original_video_url)
    rewritten_url = cdn_base_url.rstrip("/") + "/" + parsed.path.lstrip("/")
    validator = URLValidator()

    try:
        validator(rewritten_url)
        return rewritten_url
    except ValidationError:
        log.warning("Invalid CDN rewrite URL encountered, %s", rewritten_url)

    return None


class VideoBlockURLTransformer(BlockStructureTransformer):
    """
    Transformer to re-write video urls for the encoded videos
    to server content from edx-video.
    """

    @classmethod
    def name(cls):
        return "video_url"

    WRITE_VERSION = 1
    READ_VERSION = 1
    CDN_URL = getattr(settings, 'VIDEO_CDN_URL', {}).get('default', 'https://edx-video.net')
    VIDEO_FORMAT_EXCEPTIONS = ['youtube', 'fallback']

    def transform(self, usage_info, block_structure):
        """
        Re-write all the video blocks' encoded videos URLs.

        For the encoded_videos dictionary, all the available video format URLs
        will be re-written to serve the videos from edx-video.net
        with YouTube and fallback URL as an exception. Fallback URL is an exception
        because when there is no video profile data in VAL, the user specified
        data from all_sources is taken, which can be URL from any CDN.
        """
        for block_key in block_structure.topological_traversal(
            filter_func=lambda block_key: block_key.block_type == 'video',
            yield_descendants_of_unyielded=True,
        ):
            student_view_data = block_structure.get_transformer_block_field(
                block_key, StudentViewTransformer, StudentViewTransformer.STUDENT_VIEW_DATA
            )
            if not student_view_data:
                return

            # web-only videos don't contain any video information for native clients
            only_on_web = student_view_data.get('only_on_web')
            if only_on_web:
                continue
            encoded_videos = student_view_data.get('encoded_videos')
            for video_format, video_data in encoded_videos.items():
                if video_format in self.VIDEO_FORMAT_EXCEPTIONS:
                    continue
                video_data['url'] = rewrite_video_url(self.CDN_URL, video_data['url'])
