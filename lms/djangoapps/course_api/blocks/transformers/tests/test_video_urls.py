"""
Tests for VideoBlockURLTransformer.
"""


from unittest import mock

from openedx.core.djangoapps.content.block_structure.factory import BlockStructureFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import ToyCourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..student_view import StudentViewTransformer
from ..video_urls import VideoBlockURLTransformer


class TestVideoBlockURLTransformer(ModuleStoreTestCase):
    """
    Test the URL re-write for video URLs using VideoBlockURLTransformer.
    """

    def setUp(self):
        super().setUp()
        self.course_key = ToyCourseFactory.create().id
        self.course_usage_key = self.store.make_course_usage_key(self.course_key)
        self.block_structure = BlockStructureFactory.create_from_modulestore(self.course_usage_key, self.store)

    def get_pre_transform_data(self, block_key):
        """
        Return the student view data before the transformation for given video block.
        """
        video_block = self.block_structure.get_xblock(block_key)
        return video_block.student_view_data()

    def change_encoded_videos_presentation(self, encoded_videos):
        """
        Relocate url data in new dictionary for pre & post transformation data comparison.
        """
        video_urls = {}
        for video_format, video_data in encoded_videos.items():
            video_urls[video_format] = video_data['url']
        return video_urls

    def get_post_transform_data(self, block_key):
        """
        Return the block's student view data after transformation.
        """
        return self.block_structure.get_transformer_block_field(
            block_key, StudentViewTransformer, StudentViewTransformer.STUDENT_VIEW_DATA
        )

    def collect_and_transform(self):
        """
        Perform transformer operations.
        """
        StudentViewTransformer.collect(self.block_structure)
        self.block_structure._collect_requested_xblock_fields()  # pylint: disable=protected-access
        StudentViewTransformer(['video']).transform(
            usage_info=None,
            block_structure=self.block_structure,
        )
        VideoBlockURLTransformer().transform(
            usage_info=None,
            block_structure=self.block_structure,
        )

    @mock.patch('xmodule.video_block.VideoBlock.student_view_data')
    def test_rewrite_for_encoded_videos(self, mock_video_data):
        """
        Test that video URLs for videos with available encodings
        are re-written successfully by VideoBlockURLTransformer.
        """
        mock_video_data.return_value = {
            'encoded_videos': {
                'hls': {
                    'url': 'https://xyz123.cloudfront.net/XYZ123ABC.mp4',
                    'file_size': 0
                },
                'mobile_low': {
                    'url': 'https://1234abcd.cloudfront.net/ABCD1234abcd.mp4',
                    'file_size': 0
                }
            },
            'only_on_web': False
        }
        video_block_key = self.course_key.make_usage_key('video', 'sample_video')
        pre_transform_data = self.get_pre_transform_data(video_block_key)
        pre_transform_data = self.change_encoded_videos_presentation(pre_transform_data['encoded_videos'])

        self.collect_and_transform()
        post_transform_data = self.get_post_transform_data(video_block_key)
        post_transform_data = self.change_encoded_videos_presentation(post_transform_data['encoded_videos'])

        for video_format, video_url in post_transform_data.items():
            assert pre_transform_data[video_format] != video_url

    @mock.patch('xmodule.video_block.VideoBlock.student_view_data')
    def test_no_rewrite_for_third_party_vendor(self, mock_video_data):
        """
        Test that video URLs aren't re-written for the videos
        being served from third party vendors or CDN.
        """
        mock_video_data.return_value = {
            'encoded_videos': {
                'youtube': {
                    'url': 'https://www.youtube.com/watch?v=abcd1234',
                    'file_size': 0
                },
                'fallback': {
                    'url': 'https://1234abcd.third_part_cdn.com/ABCD1234abcd.mp4',
                    'file_size': 0
                }
            },
            'only_on_web': False
        }
        video_block_key = self.course_key.make_usage_key('video', 'sample_video')
        pre_transform_data = self.get_pre_transform_data(video_block_key)
        pre_transform_data = self.change_encoded_videos_presentation(pre_transform_data['encoded_videos'])

        self.collect_and_transform()
        post_transform_data = self.get_post_transform_data(video_block_key)
        post_transform_data = self.change_encoded_videos_presentation(post_transform_data['encoded_videos'])

        for video_format, video_url in post_transform_data.items():
            assert pre_transform_data[video_format] == video_url

    @mock.patch('xmodule.video_block.VideoBlock.student_view_data')
    def test_no_rewrite_for_web_only_videos(self, mock_video_data):
        """
        Verify no rewrite attempt is made for the videos
        available on web only.
        """
        mock_video_data.return_value = {
            'only_on_web': True
        }
        video_block_key = self.course_key.make_usage_key('video', 'sample_video')
        pre_transform_data = self.get_pre_transform_data(video_block_key)
        self.collect_and_transform()
        post_transform_data = self.get_post_transform_data(video_block_key)
        self.assertDictEqual(pre_transform_data, post_transform_data)
