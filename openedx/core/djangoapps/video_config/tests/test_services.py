"""
Tests for VideoConfigService.
"""

import unittest
from unittest.mock import patch

from django.conf import settings
from opaque_keys.edx.locator import CourseLocator
from xblock.field_data import DictFieldData
from xblock.fields import ScopeIds

from openedx.core.djangoapps.video_config.services import VideoConfigService
from xmodule.tests import get_test_descriptor_system
from xmodule.video_block import VideoBlock


class VideoConfigServiceTestCase(unittest.TestCase):
    """
    Unit tests for the VideoConfigService class.
    """

    def instantiate_video_block(self, **field_data):
        """Instantiate a video block with the given field data (e.g. data=xml_string)."""
        if field_data.get('data', None):
            field_data = VideoBlock.parse_video_xml(field_data['data'])
        system = get_test_descriptor_system()
        course_key = CourseLocator('org', 'course', 'run')
        usage_key = course_key.make_usage_key('video', 'SampleProblem')
        return system.construct_xblock_from_class(
            VideoBlock,
            scope_ids=ScopeIds(None, None, usage_key, usage_key),
            field_data=DictFieldData(field_data),
        )

    def test_video_with_multiple_transcripts_translation_retrieval(self):
        """
        Test translation retrieval of a video block with
        multiple transcripts uploaded by a user.
        """
        xml_data_transcripts = '''
            <video display_name="Test Video"
                youtube="1.0:p2Q6BrNhdh8,0.75:izygArpw-Qo,1.25:1EeWXzPdhSA,1.5:rABDYkeK0x8"
                show_captions="false"
                download_track="false"
                start_time="00:00:01"
                download_video="false"
                end_time="00:01:00">
            <source src="http://www.example.com/source.mp4"/>
            <track src="http://www.example.com/track"/>
            <handout src="http://www.example.com/handout"/>
            <transcript language="ge" src="subs_grmtran1.srt" />
            <transcript language="hr" src="subs_croatian1.srt" />
            </video>
        '''

        block = self.instantiate_video_block(data=xml_data_transcripts)
        video_config_service = block.runtime.service(block, 'video_config')
        assert isinstance(video_config_service, VideoConfigService)
        translations = video_config_service.available_translations(
            block, block.get_transcripts_info()
        )
        assert sorted(translations) == sorted(['hr', 'ge'])

    def test_video_with_no_transcripts_translation_retrieval(self):
        """
        Test translation retrieval of a video block with
        no transcripts uploaded by a user- ie, that retrieval
        does not throw an exception.
        """
        block = self.instantiate_video_block(data=None)
        video_config_service = block.runtime.service(block, 'video_config')
        assert isinstance(video_config_service, VideoConfigService)
        translations_with_fallback = video_config_service.available_translations(
            block, block.get_transcripts_info()
        )
        assert translations_with_fallback == ['en']

        with patch.dict(settings.FEATURES, FALLBACK_TO_ENGLISH_TRANSCRIPTS=False):
            # Some organizations don't have English transcripts for all videos
            # This feature makes it configurable
            translations_no_fallback = video_config_service.available_translations(
                block, block.get_transcripts_info()
            )
            assert translations_no_fallback == []
