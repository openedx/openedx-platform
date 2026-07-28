"""
Video block URL Transformer
"""


from django.conf import settings

from openedx.core.djangoapps.content.block_structure.transformer import BlockStructureTransformer
from xmodule.video_block.video_utils import rewrite_video_url  # pylint: disable=wrong-import-order

from .student_view import StudentViewTransformer


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
