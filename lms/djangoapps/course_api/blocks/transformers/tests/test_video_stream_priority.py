"""
Tests for VideoBlockStreamPriorityTransformer.
"""


from unittest import mock

from openedx.core.djangoapps.content.block_structure.factory import BlockStructureFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import ToyCourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..student_view import StudentViewTransformer
from ..video_stream_priority import VideoBlockStreamPriorityTransformer


class TestVideoBlockStreamPriorityTransformer(ModuleStoreTestCase):
    """
    Test the stream priority for videos using VideoBlockStreamPriorityTransformer.
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
        Relocate stream priority data in new dictionary for pre & post transformation
        data comparison.
        """
        stream_priorities = {}
        for video_format, video_data in encoded_videos.items():
            stream_priorities[video_format] = video_data['stream_priority']
        return stream_priorities

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
        VideoBlockStreamPriorityTransformer().transform(
            usage_info=self.course_usage_key,
            block_structure=self.block_structure,
        )

    @mock.patch('lms.djangoapps.course_blocks.usage_info.CourseUsageInfo')
    @mock.patch('openedx.core.djangoapps.waffle_utils.CourseWaffleFlag.is_enabled')
    @mock.patch('xmodule.video_block.VideoBlock.student_view_data')
    def test_write_for_deprecated_youtube_flag_on(self, mock_video_data, deprecate_youtube_flag, usage_info):
        """
        Test that video stream priority is written correctly with
        videos.deprecate_youtube flag on.
        """
        mock_video_data.return_value = {
            'encoded_videos': {
                'hls': {
                    'url': 'https://xyz123.cloudfront.net/XYZ123ABC.mp4',
                    'file_size': 0
                },
                'mobile_low': {
                    'url': 'https://1234a.cloudfront.net/A1234a.mp4',
                    'file_size': 0
                },
                'mobile_high': {
                    'url': 'https://1234ab.cloudfront.net/A1234ab.mp4',
                    'file_size': 0
                },
                'desktop_mp4': {
                    'url': 'https://1234abc.cloudfront.net/A1234abc.mp4',
                    'file_size': 0
                },
                'desktop_webm': {
                    'url': 'https://123abc.cloudfront.net/A123abc.mp4',
                    'file_size': 0
                },
                'fallback': {
                    'url': 'https://1234abcd.cloudfront.net/A1234abcd.mp4',
                    'file_size': 0
                },
                'youtube': {
                    'url': 'https://1234abcde.cloudfront.net/A1234abcde.mp4',
                    'file_size': 0
                },
                'new_video_format': {
                    'url': 'https://1234abcdef.cloudfront.net/A1234abcdef.mp4',
                    'file_size': 0
                }
            },
            'only_on_web': False
        }
        deprecate_youtube_flag.return_value = True
        usage_info.return_value = {'course_key': self.course_key}

        video_block_key = self.course_key.make_usage_key('video', 'sample_video')
        self.collect_and_transform()
        post_transform_data = self.get_post_transform_data(video_block_key)
        post_transform_data = self.change_encoded_videos_presentation(post_transform_data['encoded_videos'])

        for video_format, stream_priority in post_transform_data.items():
            fetched_stream_priority = VideoBlockStreamPriorityTransformer.\
                DEPRECATE_YOUTUBE_VIDEO_STREAM_PRIORITY.get(video_format)
            if fetched_stream_priority is None:
                assert post_transform_data[video_format] == -1
            else:
                assert post_transform_data[video_format] == fetched_stream_priority

    @mock.patch('lms.djangoapps.course_blocks.usage_info.CourseUsageInfo')
    @mock.patch('openedx.core.djangoapps.waffle_utils.CourseWaffleFlag.is_enabled')
    @mock.patch('xmodule.video_block.VideoBlock.student_view_data')
    def test_write_for_deprecated_youtube_flag_off(self, mock_video_data, deprecate_youtube_flag, usage_info):
        """
        Test that video stream priority is written correctly with
        videos.deprecate_youtube flag off.
        """
        mock_video_data.return_value = {
            'encoded_videos': {
                'hls': {
                    'url': 'https://xyz123.cloudfront.net/XYZ123ABC.mp4',
                    'file_size': 0
                },
                'mobile_low': {
                    'url': 'https://1234a.cloudfront.net/A1234a.mp4',
                    'file_size': 0
                },
                'mobile_high': {
                    'url': 'https://1234ab.cloudfront.net/A1234ab.mp4',
                    'file_size': 0
                },
                'desktop_mp4': {
                    'url': 'https://1234abc.cloudfront.net/A1234abc.mp4',
                    'file_size': 0
                },
                'desktop_webm': {
                    'url': 'https://123abc.cloudfront.net/A123abc.mp4',
                    'file_size': 0
                },
                'fallback': {
                    'url': 'https://1234abcd.cloudfront.net/A1234abcd.mp4',
                    'file_size': 0
                },
                'youtube': {
                    'url': 'https://1234abcde.cloudfront.net/A1234abcde.mp4',
                    'file_size': 0
                },
                'new_video_format': {
                    'url': 'https://1234abcdef.cloudfront.net/A1234abcdef.mp4',
                    'file_size': 0
                }
            },
            'only_on_web': False
        }
        deprecate_youtube_flag.return_value = False
        usage_info.return_value = {'course_key': self.course_key}

        video_block_key = self.course_key.make_usage_key('video', 'sample_video')
        self.collect_and_transform()
        post_transform_data = self.get_post_transform_data(video_block_key)
        post_transform_data = self.change_encoded_videos_presentation(post_transform_data['encoded_videos'])

        for video_format, stream_priority in post_transform_data.items():
            fetched_stream_priority = VideoBlockStreamPriorityTransformer.\
                DEFAULT_VIDEO_STREAM_PRIORITY.get(video_format)
            if fetched_stream_priority is None:
                assert post_transform_data[video_format] == -1
            else:
                assert post_transform_data[video_format] == fetched_stream_priority

    @mock.patch('xmodule.video_block.VideoBlock.student_view_data')
    def test_no_priority_for_web_only_videos(self, mock_video_data):
        """
        Verify no write attempt is made for the videos
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
