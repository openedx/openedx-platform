"""
Video xmodule tests in mongo.
"""


from contextlib import contextmanager
import json
import shutil
from collections import OrderedDict
from tempfile import mkdtemp
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import ddt
import pytest
from django.conf import settings
from django.core.files import File
from django.core.files.base import ContentFile
from django.http import Http404
from django.test import TestCase
from django.test.utils import override_settings
from edx_toggles.toggles.testutils import override_waffle_flag
from edxval.api import (
    ValCannotCreateError,
    ValVideoNotFoundError,
    create_or_update_video_transcript,
    create_profile,
    create_video,
    create_video_transcript,
    get_video_info,
    get_video_transcript,
    get_video_transcript_data,
)
from edxval.utils import create_file_in_fs
from fs.osfs import OSFS
from fs.path import combine
from lxml import etree
from path import Path as path
from xmodule.contentstore.content import StaticContent
from openedx.core.djangoapps.video_config.sharing import (
    COURSE_VIDEO_SHARING_ALL_VIDEOS,
    COURSE_VIDEO_SHARING_NONE,
    COURSE_VIDEO_SHARING_PER_VIDEO
)
from xmodule.exceptions import NotFoundError
from xmodule.modulestore.inheritance import own_metadata
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE
# noinspection PyUnresolvedReferences
from xmodule.tests.helpers import mock_render_template, override_descriptor_system  # pylint: disable=unused-import
from xmodule.tests.test_import import DummyModuleStoreRuntime
from xmodule.tests.test_video import VideoBlockTestBase
from xmodule.video_block import VideoBlock, video_utils
from openedx.core.djangoapps.video_config.transcripts_utils import Transcript, save_to_store, subs_filename
from xmodule.video_block.video_block import EXPORT_IMPORT_COURSE_DIR, EXPORT_IMPORT_STATIC_DIR
from xmodule.x_module import PUBLIC_VIEW, STUDENT_VIEW

from common.djangoapps.xblock_django.constants import ATTR_KEY_REQUEST_COUNTRY_CODE
from lms.djangoapps.courseware.tests.helpers import get_context_from_dict
from openedx.core.djangoapps.video_config.toggles import PUBLIC_VIDEO_SHARE
from openedx.core.djangoapps.video_config import sharing
from openedx.core.djangoapps.video_pipeline.config.waffle import DEPRECATE_YOUTUBE
from openedx.core.djangoapps.waffle_utils.models import WaffleFlagCourseOverrideModel
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase

from .test_video_handlers import BaseTestVideoXBlock, TestVideo
from .test_video_xml import SOURCE_XML, PUBLIC_SOURCE_XML
from common.test.utils import assert_dict_contains_subset

if settings.USE_EXTRACTED_VIDEO_BLOCK:
    from xblocks_contrib.video import bumper_utils
    bumper_utils_path = 'xblocks_contrib.video.bumper_utils'
else:
    from xmodule.video_block import bumper_utils
    bumper_utils_path = 'xmodule.video_block.bumper_utils'

TRANSCRIPT_FILE_SRT_DATA = """
1
00:00:14,370 --> 00:00:16,530
I am overwatch.

2
00:00:16,500 --> 00:00:18,600
可以用“我不太懂艺术 但我知道我喜欢什么”做比喻.
"""

TRANSCRIPT_FILE_SJSON_DATA = """{\n   "start": [10],\n   "end": [100],\n   "text": ["Hi, welcome to edxval."]\n}"""


class TestVideoYouTube(TestVideo):  # lint-amnesty, pylint: disable=missing-class-docstring, test-inherits-tests
    METADATA = {}

    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    def test_video_constructor(self, mock_render_django_template):
        """Make sure that all parameters extracted correctly from xml"""
        self.block.student_view(None)
        sources = ['example.mp4', 'example.webm']

        expected_context = {
            'autoadvance_enabled': False,
            'license': None,
            'bumper_metadata': 'null',
            'block_id': str(self.block.location),
            'course_id': str(self.block.location.course_key),
            'cdn_eval': False,
            'cdn_exp_group': None,
            'display_name': 'A Name',
            'download_video_link': 'example.mp4',
            'is_video_from_same_origin': False,
            'handout': None,
            'hide_downloads': False,
            'id': self.block.location.html_id(),
            'is_embed': False,
            'metadata': json.dumps(OrderedDict({
                'autoAdvance': False,
                'saveStateEnabled': True,
                'saveStateUrl': self.block.ajax_url + '/save_user_state',
                'autoplay': False,
                'streams': '0.75:jNCf2gIqpeE,1.00:ZwkTiUPN0mg,1.25:rsq9auxASqI,1.50:kMyNdzVHHgg',
                'sources': sources,
                'duration': None,
                'poster': None,
                'captionDataDir': None,
                'showCaptions': 'true',
                'generalSpeed': 1.0,
                'speed': None,
                'savedVideoPosition': 0.0,
                'start': 3603.0,
                'end': 3610.0,
                'transcriptLanguage': 'en',
                'transcriptLanguages': OrderedDict({'en': 'English', 'uk': 'Українська'}),
                'ytMetadataEndpoint': '',
                'ytTestTimeout': 1500,
                'ytApiUrl': 'https://www.youtube.com/iframe_api',
                'lmsRootURL': settings.LMS_ROOT_URL,
                'transcriptTranslationUrl': self.get_handler_url('transcript', 'translation/__lang__'),
                'transcriptAvailableTranslationsUrl': self.get_handler_url('transcript', 'available_translations'),
                'aiTranslationsUrl': settings.AI_TRANSLATIONS_API_URL,
                'autohideHtml5': False,
                'recordedYoutubeIsAvailable': True,
                'completionEnabled': False,
                'completionPercentage': 0.95,
                'publishCompletionUrl': self.get_handler_url('publish_completion', ''),
                'prioritizeHls': False,
            })),
            'track': None,
            'transcript_download_format': 'srt',
            'transcript_download_formats_list': [
                {'display_name': 'SubRip (.srt) file', 'value': 'srt'},
                {'display_name': 'Text (.txt) file', 'value': 'txt'}
            ],
            'poster': 'null',
            'transcript_feedback_enabled': False,
            'video_id': '',
        }

        # Get the actual context that was passed to render_django_template
        actual_context = mock_render_django_template.call_args.args[1]

        # Validate and compare contexts
        assert get_context_from_dict(actual_context) == get_context_from_dict(expected_context)


class TestVideoNonYouTube(TestVideo):  # pylint: disable=test-inherits-tests
    """Integration tests: web client + mongo."""
    DATA = """
        <video show_captions="true"
        display_name="A Name"
        sub="a_sub_file.srt.sjson"
        download_video="true"
        start_time="3603.0" end_time="3610.0"
        >
            <source src="example.mp4"/>
            <source src="example.webm"/>
        </video>
    """
    MODEL_DATA = {
        'data': DATA,
    }
    METADATA = {}

    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    def test_video_constructor(self, mock_render_django_template):
        """Make sure that if the 'youtube' attribute is omitted in XML, then
            the template generates an empty string for the YouTube streams.
        """
        self.block.student_view(None)
        sources = ['example.mp4', 'example.webm']

        expected_context = {
            'autoadvance_enabled': False,
            'license': None,
            'bumper_metadata': 'null',
            'block_id': str(self.block.location),
            'course_id': str(self.block.location.course_key),
            'cdn_eval': False,
            'cdn_exp_group': None,
            'display_name': 'A Name',
            'download_video_link': 'example.mp4',
            'is_video_from_same_origin': False,
            'handout': None,
            'hide_downloads': False,
            'is_embed': False,
            'id': self.block.location.html_id(),
            'metadata': json.dumps(OrderedDict({
                'autoAdvance': False,
                'saveStateEnabled': True,
                'saveStateUrl': self.block.ajax_url + '/save_user_state',
                'autoplay': False,
                'streams': '1.00:3_yD_cEKoCk',
                'sources': sources,
                'duration': None,
                'poster': None,
                'captionDataDir': None,
                'showCaptions': 'true',
                'generalSpeed': 1.0,
                'speed': None,
                'savedVideoPosition': 0.0,
                'start': 3603.0,
                'end': 3610.0,
                'transcriptLanguage': 'en',
                'transcriptLanguages': OrderedDict({'en': 'English'}),
                'ytMetadataEndpoint': '',
                'ytTestTimeout': 1500,
                'ytApiUrl': 'https://www.youtube.com/iframe_api',
                'lmsRootURL': settings.LMS_ROOT_URL,
                'transcriptTranslationUrl': self.get_handler_url('transcript', 'translation/__lang__'),
                'transcriptAvailableTranslationsUrl': self.get_handler_url('transcript', 'available_translations'),
                'aiTranslationsUrl': settings.AI_TRANSLATIONS_API_URL,
                'autohideHtml5': False,
                'recordedYoutubeIsAvailable': True,
                'completionEnabled': False,
                'completionPercentage': 0.95,
                'publishCompletionUrl': self.get_handler_url('publish_completion', ''),
                'prioritizeHls': False,
            })),
            'track': None,
            'transcript_download_format': 'srt',
            'transcript_download_formats_list': [
                {'display_name': 'SubRip (.srt) file', 'value': 'srt'},
                {'display_name': 'Text (.txt) file', 'value': 'txt'}
            ],
            'poster': 'null',
            'transcript_feedback_enabled': False,
            'video_id': '',
        }

        # Get the actual context that was passed to render_django_template
        actual_context = mock_render_django_template.call_args.args[1]

        # Validate and compare contexts
        validated_actual = get_context_from_dict(actual_context)
        validated_expected = get_context_from_dict(expected_context)
        assert validated_actual == validated_expected

        # Verify specific fields
        assert validated_actual['download_video_link'] == 'example.mp4'
        assert validated_actual['display_name'] == 'A Name'


@ddt.ddt
class TestVideoPublicAccess(BaseTestVideoXBlock):
    """Test video public access."""
    DATA = PUBLIC_SOURCE_XML
    MODEL_DATA = {
        'data': DATA,
    }
    METADATA = {}

    @contextmanager
    def mock_feature_toggle(self, enabled=True):
        with patch.object(PUBLIC_VIDEO_SHARE, 'is_enabled', return_value=enabled):
            yield

    @ddt.data(True, False)
    def test_is_public_sharing_enabled(self, feature_enabled):
        """Test public video url."""
        assert self.block.public_access is True
        with self.mock_feature_toggle(enabled=feature_enabled):
            assert sharing.is_public_sharing_enabled(self.block.location, self.block.public_access) == feature_enabled

    def test_is_public_sharing_enabled__not_public(self):
        self.block.public_access = False
        with self.mock_feature_toggle():
            assert not sharing.is_public_sharing_enabled(self.block.location, self.block.public_access)

    @patch('openedx.core.djangoapps.video_config.sharing.get_course_video_sharing_override')
    def test_is_public_sharing_enabled_by_course_override(self, mock_course_sharing_override):

        # Given a course overrides all videos to be shared
        mock_course_sharing_override.return_value = COURSE_VIDEO_SHARING_ALL_VIDEOS
        self.block.public_access = 'some-arbitrary-value'

        # When I try to determine if public sharing is enabled
        with self.mock_feature_toggle():
            is_public_sharing_enabled = sharing.is_public_sharing_enabled(self.block.location, self.block.public_access)

        # Then I will get that course value
        self.assertTrue(is_public_sharing_enabled)

    @patch('openedx.core.djangoapps.video_config.sharing.get_course_video_sharing_override')
    def test_is_public_sharing_disabled_by_course_override(self, mock_course_sharing_override):
        # Given a course overrides no videos to be shared
        mock_course_sharing_override.return_value = COURSE_VIDEO_SHARING_NONE
        self.block.public_access = 'some-arbitrary-value'

        # When I try to determine if public sharing is enabled
        with self.mock_feature_toggle():
            is_public_sharing_enabled = sharing.is_public_sharing_enabled(self.block.location, self.block.public_access)

        # Then I will get that course value
        self.assertFalse(is_public_sharing_enabled)

    @ddt.data(COURSE_VIDEO_SHARING_PER_VIDEO, None)
    @patch('openedx.core.djangoapps.video_config.sharing.get_course_video_sharing_override')
    def test_is_public_sharing_enabled_per_video(self, mock_override_value, mock_course_sharing_override):
        # Given a course does not override per-video settings
        mock_course_sharing_override.return_value = mock_override_value
        self.block.public_access = 'some-arbitrary-value'

        # When I try to determine if public sharing is enabled
        with self.mock_feature_toggle():
            is_public_sharing_enabled = sharing.is_public_sharing_enabled(self.block.location, self.block.public_access)

        # I will get the per-video value
        self.assertEqual(self.block.public_access, is_public_sharing_enabled)

    @patch('openedx.core.lib.courses.get_course_by_id')
    def test_is_public_sharing_course_not_found(self, mock_get_course):
        # Given a course does not override per-video settings
        mock_get_course.side_effect = Http404()
        self.block.public_access = 'some-arbitrary-value'

        # When I try to determine if public sharing is enabled
        with self.mock_feature_toggle():
            is_public_sharing_enabled = sharing.is_public_sharing_enabled(self.block.location, self.block.public_access)

        # I will fall-back to per-video values
        self.assertEqual(self.block.public_access, is_public_sharing_enabled)

    @ddt.data(False, True)
    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    def test_context(self, is_public_sharing_enabled, mock_render_django_template):
        with self.mock_feature_toggle():
            with patch.object(
                sharing,
                'is_public_sharing_enabled',
                return_value=is_public_sharing_enabled
            ):
                self.block.student_view(None)

        # Get the actual context that was passed to render_django_template
        context = mock_render_django_template.call_args.args[1]

        assert ('public_sharing_enabled' in context) == is_public_sharing_enabled
        assert ('public_video_url' in context) == is_public_sharing_enabled


@ddt.ddt
class TestGetHtmlMethod(BaseTestVideoXBlock):
    '''
    Make sure that `get_html` works correctly.
    '''
    maxDiff = None
    CATEGORY = "video"
    DATA = SOURCE_XML
    METADATA = {}

    def setUp(self):
        super().setUp()
        self.setup_course()
        self.default_metadata_dict = OrderedDict({
            'autoAdvance': False,
            'saveStateEnabled': True,
            'saveStateUrl': '',
            'autoplay': settings.FEATURES.get('AUTOPLAY_VIDEOS', True),
            'streams': '1.00:3_yD_cEKoCk',
            'sources': '[]',
            'duration': 111.0,
            'poster': None,
            'captionDataDir': None,
            'showCaptions': 'true',
            'generalSpeed': 1.0,
            'speed': None,
            'savedVideoPosition': 0.0,
            'start': 3603.0,
            'end': 3610.0,
            'transcriptLanguage': 'en',
            'transcriptLanguages': OrderedDict({'en': 'English'}),
            'ytMetadataEndpoint': '',
            'ytTestTimeout': 1500,
            'ytApiUrl': 'https://www.youtube.com/iframe_api',
            'lmsRootURL': settings.LMS_ROOT_URL,
            'transcriptTranslationUrl': self.get_handler_url('transcript', 'translation/__lang__'),
            'transcriptAvailableTranslationsUrl': self.get_handler_url('transcript', 'available_translations'),
            'aiTranslationsUrl': settings.AI_TRANSLATIONS_API_URL,
            'autohideHtml5': False,
            'recordedYoutubeIsAvailable': True,
            'completionEnabled': False,
            'completionPercentage': 0.95,
            'publishCompletionUrl': self.get_handler_url('publish_completion', ''),
            'prioritizeHls': False,
        })

    def get_handler_url(self, handler, suffix):
        """
        Return the URL for the specified handler on the block represented by
        self.block.
        """
        return self.block.runtime.handler_url(
            self.block, handler, suffix
        ).rstrip('/?')

    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    def test_get_html_track(self, mock_render_django_template):
        # pylint: disable=invalid-name
        # lint-amnesty, pylint: disable=redefined-outer-name
        SOURCE_XML = """
            <video show_captions="true"
            display_name="{name}"
                sub="{sub}" download_track="{download_track}"
            start_time="3603.0" end_time="3610.0" download_video="true"
            >
                <source src="example.mp4"/>
                <source src="example.webm"/>
                {track}
                {transcripts}
            </video>
        """

        cases = [
            {
                'name': 'video 1',
                'download_track': 'true',
                'track': '<track src="http://www.example.com/track"/>',
                'sub': 'a_sub_file.srt.sjson',
                'expected_track_url': 'http://www.example.com/track',
                'transcripts': '',
            },
            {
                'name': 'video 2',
                'download_track': 'true',
                'track': '',
                'sub': 'a_sub_file.srt.sjson',
                'expected_track_url': 'a_sub_file.srt.sjson',
                'transcripts': '',
            },
            {
                'name': 'video 3',
                'download_track': 'true',
                'track': '',
                'sub': '',
                'expected_track_url': None,
                'transcripts': '',
            },
            {
                'name': 'video 4',
                'download_track': 'false',
                'track': '<track src="http://www.example.com/track"/>',
                'sub': 'a_sub_file.srt.sjson',
                'expected_track_url': None,
                'transcripts': '',
            },
            {
                'name': 'video 5',
                'download_track': 'true',
                'track': '',
                'sub': '',
                'expected_track_url': 'a_sub_file.srt.sjson',
                'transcripts': '<transcript language="uk" src="ukrainian.srt" />',
            },
        ]
        sources = ['example.mp4', 'example.webm']

        expected_context = {
            'autoadvance_enabled': False,
            'license': None,
            'bumper_metadata': 'null',
            'block_id': str(self.block.location),
            'course_id': str(self.block.location.course_key),
            'cdn_eval': False,
            'cdn_exp_group': None,
            'display_name': 'A Name',
            'download_video_link': 'example.mp4',
            'is_video_from_same_origin': False,
            'handout': None,
            'hide_downloads': False,
            'id': self.block.location.html_id(),
            'is_embed': False,
            'metadata': '',
            'track': None,
            'transcript_download_format': 'srt',
            'transcript_download_formats_list': [
                {'display_name': 'SubRip (.srt) file', 'value': 'srt'},
                {'display_name': 'Text (.txt) file', 'value': 'txt'}
            ],
            'poster': 'null',
            'transcript_feedback_enabled': False,
            'video_id': '',
        }

        for data in cases:
            metadata = self.default_metadata_dict
            metadata['sources'] = sources
            metadata['duration'] = None
            DATA = SOURCE_XML.format(
                download_track=data['download_track'],
                track=data['track'],
                sub=data['sub'],
                transcripts=data['transcripts'],
                name=data['name'],
            )

            self.initialize_block(data=DATA)
            track_url = self.get_handler_url('transcript', 'download')

            self.block.student_view(None)
            metadata.update({
                'transcriptLanguages': {"en": "English"} if not data['transcripts'] else {"uk": 'Українська'},
                'transcriptLanguage': 'en' if not data['transcripts'] or data.get('sub') else 'uk',
                'transcriptTranslationUrl': self.get_handler_url('transcript', 'translation/__lang__'),
                'transcriptAvailableTranslationsUrl': self.get_handler_url('transcript', 'available_translations'),
                'publishCompletionUrl': self.get_handler_url('publish_completion', ''),
                'saveStateUrl': self.block.ajax_url + '/save_user_state',
            })
            expected_context.update({
                'display_name': data['name'],
                'transcript_download_format': (
                    None if self.block.track and self.block.download_track else 'srt'
                ),
                'track': (
                    track_url if data['expected_track_url'] == 'a_sub_file.srt.sjson' else data['expected_track_url']
                ),
                'id': self.block.location.html_id(),
                'block_id': str(self.block.location),
                'course_id': str(self.block.location.course_key),
                'metadata': json.dumps(metadata)
            })

        # Get the actual context that was passed to render_django_template
        actual_context = mock_render_django_template.call_args.args[1]

        # Validate and compare contexts
        assert get_context_from_dict(actual_context) == get_context_from_dict(expected_context)

    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    def test_get_html_source(self, mock_render_django_template):
        # lint-amnesty, pylint: disable=invalid-name, redefined-outer-name
        SOURCE_XML = """
            <video show_captions="true"
            display_name="{name}"
            sub="a_sub_file.srt.sjson" source="{source}"
            download_video="{download_video}"
            start_time="3603.0" end_time="3610.0"
            >
                {sources}
            </video>
        """
        cases = [
            # self.download_video == True
            {
                'name': 'video 1',
                'download_video': 'true',
                'source': 'example_source.mp4',
                'sources': """
                    <source src="example.mp4"/>
                    <source src="example.webm"/>
                """,
                'result': {
                    'download_video_link': 'example.mp4',
                    'sources': ['example.mp4', 'example.webm'],
                },
            },
            {
                'name': 'video 2',
                'download_video': 'true',
                'source': '',
                'sources': """
                    <source src="example.mp4"/>
                    <source src="example.webm"/>
                """,
                'result': {
                    'download_video_link': 'example.mp4',
                    'sources': ['example.mp4', 'example.webm'],
                },
            },
            {
                'name': 'video 3',
                'download_video': 'true',
                'source': '',
                'sources': [],
                'result': {},
            },

            # self.download_video == False
            {
                'name': 'video 4',
                'download_video': 'false',
                'source': 'example_source.mp4',
                'sources': """
                    <source src="example.mp4"/>
                    <source src="example.webm"/>
                """,
                'result': {
                    'sources': ['example.mp4', 'example.webm'],
                },
            },
        ]

        initial_context = {
            'autoadvance_enabled': False,
            'license': None,
            'bumper_metadata': 'null',
            'block_id': str(self.block.location),
            'course_id': str(self.block.location.course_key),
            'cdn_eval': False,
            'cdn_exp_group': None,
            'display_name': 'A Name',
            'download_video_link': 'example.mp4',
            'is_video_from_same_origin': False,
            'handout': None,
            'hide_downloads': False,
            'id': self.block.location.html_id(),
            'is_embed': False,
            'metadata': self.default_metadata_dict,
            'track': None,
            'transcript_download_format': 'srt',
            'transcript_download_formats_list': [
                {'display_name': 'SubRip (.srt) file', 'value': 'srt'},
                {'display_name': 'Text (.txt) file', 'value': 'txt'}
            ],
            'poster': 'null',
            'transcript_feedback_enabled': False,
            'video_id': '',
        }
        initial_context['metadata']['duration'] = None

        for data in cases:
            DATA = SOURCE_XML.format(  # lint-amnesty, pylint: disable=invalid-name
                download_video=data['download_video'],
                source=data['source'],
                sources=data['sources'],
                name=data['name'],
            )
            self.initialize_block(data=DATA)
            self.block.student_view(None)

            expected_context = dict(initial_context)
            expected_context['metadata'].update({
                'transcriptTranslationUrl': self.get_handler_url('transcript', 'translation/__lang__'),
                'transcriptAvailableTranslationsUrl': self.get_handler_url('transcript', 'available_translations'),
                'publishCompletionUrl': self.get_handler_url('publish_completion', ''),
                'saveStateUrl': self.block.ajax_url + '/save_user_state',
                'sources': data['result'].get('sources', []),
            })
            expected_context.update({
                'display_name': data['name'],
                'id': self.block.location.html_id(),
                'block_id': str(self.block.location),
                'course_id': str(self.block.location.course_key),
                'download_video_link': data['result'].get('download_video_link'),
                'metadata': json.dumps(expected_context['metadata'])
            })

        # Get the actual context that was passed to render_django_template
        actual_context = mock_render_django_template.call_args.args[1]

        # Validate and compare contexts
        assert get_context_from_dict(actual_context) == get_context_from_dict(expected_context)

    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    def test_get_html_with_non_existent_edx_video_id(self, mock_render_django_template):
        """
        Tests the VideoBlock get_html where a edx_video_id is given but a video is not found
        """
        # pylint: disable=invalid-name
        # lint-amnesty, pylint: disable=redefined-outer-name
        SOURCE_XML = """
            <video show_captions="true"
            display_name="{name}"
            sub="a_sub_file.srt.sjson" source="{source}"
            download_video="{download_video}"
            start_time="3603.0" end_time="3610.0"
            edx_video_id="{edx_video_id}"
            >
                {sources}
            </video>
        """
        no_video_data = {
            'name': 'video 1',
            'download_video': 'true',
            'source': 'example_source.mp4',
            'sources': """
            <source src="example.mp4"/>
            <source src="example.webm"/>
            """,
            'edx_video_id': "meow",
            'result': {
                'download_video_link': 'example.mp4',
                'sources': ['example.mp4', 'example.webm'],
            }
        }
        DATA = SOURCE_XML.format(
            download_video=no_video_data['download_video'],
            source=no_video_data['source'],
            sources=no_video_data['sources'],
            edx_video_id=no_video_data['edx_video_id'],
            name=no_video_data['name'],
        )
        self.initialize_block(data=DATA)

        # Referencing a non-existent VAL ID in courseware won't cause an error --
        # it'll just fall back to the values in the VideoBlock.
        self.block.student_view(None)

        # Get the actual context that was passed to render_django_template
        actual_context = mock_render_django_template.call_args.args[1]

        # Verify it falls back to the sources defined in the VideoBlock XML
        assert actual_context['download_video_link'] == 'example.mp4'
        metadata_dict = json.loads(actual_context['metadata'])
        assert sorted(metadata_dict['sources']) == sorted(['example.mp4', 'example.webm'])

    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    def test_get_html_with_mocked_edx_video_id(self, mock_render_django_template):
        # lint-amnesty, pylint: disable=invalid-name, redefined-outer-name
        SOURCE_XML = """
            <video show_captions="true"
            display_name="A Name"
            sub="a_sub_file.srt.sjson" source="{source}"
            download_video="{download_video}"
            start_time="3603.0" end_time="3610.0"
            edx_video_id="{edx_video_id}"
            >
                {sources}
            </video>
        """

        data = {
            # test with download_video set to false and make sure download_video_link is not set (is None)
            'download_video': 'false',
            'source': 'example_source.mp4',
            'sources': """
                <source src="example.mp4"/>
                <source src="example.webm"/>
            """,
            'edx_video_id': "mock item",
            'result': {
                'download_video_link': None,
                # make sure the desktop_mp4 url is included as part of the alternative sources.
                'sources': ['example.mp4', 'example.webm', 'http://www.meowmix.com'],
            }
        }

        # Video found for edx_video_id
        metadata = self.default_metadata_dict
        metadata['autoplay'] = False
        metadata['sources'] = ""

        initial_context = {
            'autoadvance_enabled': False,
            'license': None,
            'bumper_metadata': 'null',
            'block_id': str(self.block.location),
            'course_id': str(self.block.location.course_key),
            'cdn_eval': False,
            'cdn_exp_group': None,
            'display_name': 'A Name',
            'download_video_link': 'example.mp4',
            'is_video_from_same_origin': False,
            'handout': None,
            'hide_downloads': False,
            'is_embed': False,
            'id': self.block.location.html_id(),
            'track': None,
            'transcript_download_format': 'srt',
            'transcript_download_formats_list': [
                {'display_name': 'SubRip (.srt) file', 'value': 'srt'},
                {'display_name': 'Text (.txt) file', 'value': 'txt'}
            ],
            'poster': 'null',
            'metadata': metadata,
            'transcript_feedback_enabled': False,
            'video_id': 'mock item',
        }

        DATA = SOURCE_XML.format(  # lint-amnesty, pylint: disable=invalid-name
            download_video=data['download_video'],
            source=data['source'],
            sources=data['sources'],
            edx_video_id=data['edx_video_id']
        )
        self.initialize_block(data=DATA)

        with patch('edxval.api.get_video_info') as mock_get_video_info:
            mock_get_video_info.return_value = {
                'url': '/edxval/video/example',
                'edx_video_id': 'example',
                'duration': 111.0,
                'client_video_id': 'The example video',
                'encoded_videos': [
                    {
                        'url': 'http://www.meowmix.com',
                        'file_size': 25556,
                        'bitrate': 9600,
                        'profile': 'desktop_mp4'
                    }
                ]
            }
            self.block.student_view(None)
            expected_context = dict(initial_context)
            expected_context['metadata'].update({
                'transcriptTranslationUrl': self.get_handler_url('transcript', 'translation/__lang__'),
                'transcriptAvailableTranslationsUrl': self.get_handler_url('transcript', 'available_translations'),
                'publishCompletionUrl': self.get_handler_url('publish_completion', ''),
                'saveStateUrl': self.block.ajax_url + '/save_user_state',
                'sources': data['result']['sources'],
            })
            expected_context.update({
                'id': self.block.location.html_id(),
                'block_id': str(self.block.location),
                'course_id': str(self.block.location.course_key),
                'download_video_link': data['result']['download_video_link'],
                'metadata': json.dumps(expected_context['metadata'])
            })

            # Get the actual context that was passed to render_django_template
            actual_context = mock_render_django_template.call_args.args[1]

            # Validate and compare contexts
            assert get_context_from_dict(actual_context) == get_context_from_dict(expected_context)

    def encode_and_create_video(self, edx_video_id):
        """
        Create and encode video to be used for tests
        """
        encoded_videos = []
        for profile, extension in [("desktop_webm", "webm"), ("desktop_mp4", "mp4")]:
            create_profile(profile)
            encoded_videos.append(
                dict(
                    url=f"http://fake-video.edx.org/{edx_video_id}.{extension}",
                    file_size=9000,
                    bitrate=42,
                    profile=profile,
                )
            )
        result = create_video(
            dict(
                client_video_id='A Client Video id',
                duration=111.0,
                edx_video_id=edx_video_id,
                status='test',
                encoded_videos=encoded_videos,
            )
        )
        assert result == edx_video_id
        return encoded_videos

    def helper_get_html_with_edx_video_id(self, data):
        """
        Create expected context and get actual context returned by `get_html` method.
        """
        # make sure the urls for the various encodings are included as part of the alternative sources.
        # lint-amnesty, pylint: disable=invalid-name, redefined-outer-name
        SOURCE_XML = """
            <video show_captions="true"
            display_name="A Name"
            sub="a_sub_file.srt.sjson" source="{source}"
            download_video="{download_video}"
            start_time="3603.0" end_time="3610.0"
            edx_video_id="{edx_video_id}"
            >
                {sources}
            </video>
        """

        # Video found for edx_video_id
        metadata = self.default_metadata_dict
        metadata['sources'] = ""

        initial_context = {
            'autoadvance_enabled': False,
            'license': None,
            'bumper_metadata': 'null',
            'block_id': str(self.block.location),
            'course_id': str(self.block.location.course_key),
            'cdn_eval': False,
            'cdn_exp_group': None,
            'display_name': 'A Name',
            'download_video_link': 'example.mp4',
            'is_video_from_same_origin': False,
            'handout': None,
            'hide_downloads': False,
            'is_embed': False,
            'id': self.block.location.html_id(),
            'track': None,
            'transcript_download_format': 'srt',
            'transcript_download_formats_list': [
                {'display_name': 'SubRip (.srt) file', 'value': 'srt'},
                {'display_name': 'Text (.txt) file', 'value': 'txt'}
            ],
            'poster': 'null',
            'metadata': metadata,
            'transcript_feedback_enabled': False,
            'video_id': data['edx_video_id'].replace('\t', ' '),
        }

        # pylint: disable=invalid-name
        DATA = SOURCE_XML.format(
            download_video=data['download_video'],
            source=data['source'],
            sources=data['sources'],
            edx_video_id=data['edx_video_id']
        )
        self.initialize_block(data=DATA)
        # context returned by get_html
        context = self.block.student_view(None).content

        # expected_context, expected context to be returned by get_html
        expected_context = dict(initial_context)
        expected_context['metadata'].update({
            'transcriptTranslationUrl': self.get_handler_url('transcript', 'translation/__lang__'),
            'transcriptAvailableTranslationsUrl': self.get_handler_url('transcript', 'available_translations'),
            'publishCompletionUrl': self.get_handler_url('publish_completion', ''),
            'saveStateUrl': self.block.ajax_url + '/save_user_state',
            'sources': data['result']['sources'],
        })
        expected_context.update({
            'id': self.block.location.html_id(),
            'block_id': str(self.block.location),
            'course_id': str(self.block.location.course_key),
            'download_video_link': data['result']['download_video_link'],
            'is_video_from_same_origin': data['result']['is_video_from_same_origin'],
            'metadata': json.dumps(expected_context['metadata'])
        })
        return context, expected_context

    # pylint: disable=invalid-name
    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    @patch(f'{VideoBlock.__module__}.rewrite_video_url')
    def test_get_html_cdn_source(self, mocked_get_video, mock_render_django_template):
        """
        Test if sources got from CDN
        """

        def side_effect(*args, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
            cdn = {
                'http://example.com/example.mp4': 'http://cdn-example.com/example.mp4',
                'http://example.com/example.webm': 'http://cdn-example.com/example.webm',
            }
            return cdn.get(args[1])

        mocked_get_video.side_effect = side_effect

        source_xml = """
            <video show_captions="true"
            display_name="A Name"
            sub="a_sub_file.srt.sjson" source="{source}"
            download_video="{download_video}"
            edx_video_id="{edx_video_id}"
            start_time="3603.0" end_time="3610.0"
            >
                {sources}
            </video>
        """

        case_data = {
            'download_video': 'true',
            'source': 'example_source.mp4',
            'sources': """
                <source src="http://example.com/example.mp4"/>
                <source src="http://example.com/example.webm"/>
            """,
            'result': {
                'download_video_link': 'http://example.com/example.mp4',
                'sources': [
                    'http://cdn-example.com/example.mp4',
                    'http://cdn-example.com/example.webm'
                ],
            },
        }

        # Only videos with a video id should have their URLs rewritten
        # based on CDN settings
        cases = [
            dict(case_data, edx_video_id="vid-v1:12345"),
        ]

        initial_context = {
            'autoadvance_enabled': False,
            'license': None,
            'bumper_metadata': 'null',
            'block_id': str(self.block.location),
            'course_id': str(self.block.location.course_key),
            'cdn_eval': False,
            'cdn_exp_group': None,
            'display_name': 'A Name',
            'download_video_link': None,
            'is_video_from_same_origin': False,
            'handout': None,
            'hide_downloads': False,
            'is_embed': False,
            'id': None,
            'metadata': self.default_metadata_dict,
            'track': None,
            'transcript_download_format': 'srt',
            'transcript_download_formats_list': [
                {'display_name': 'SubRip (.srt) file', 'value': 'srt'},
                {'display_name': 'Text (.txt) file', 'value': 'txt'}
            ],
            'poster': 'null',
            'transcript_feedback_enabled': False,
            'video_id': 'vid-v1:12345',
        }
        initial_context['metadata']['duration'] = None

        for data in cases:
            DATA = source_xml.format(
                download_video=data['download_video'],
                source=data['source'],
                sources=data['sources'],
                edx_video_id=data['edx_video_id'],
            )
            self.initialize_block(data=DATA, runtime_kwargs={
                'user_location': 'CN',
            })
            user_service = self.block.runtime.service(self.block, 'user')
            user_location = user_service.get_current_user().opt_attrs[ATTR_KEY_REQUEST_COUNTRY_CODE]
            assert user_location == 'CN'
            self.block.student_view(None)
            expected_context = dict(initial_context)
            expected_context['metadata'].update({
                'transcriptTranslationUrl': self.get_handler_url('transcript', 'translation/__lang__'),
                'transcriptAvailableTranslationsUrl': self.get_handler_url('transcript', 'available_translations'),
                'publishCompletionUrl': self.get_handler_url('publish_completion', ''),
                'saveStateUrl': self.block.ajax_url + '/save_user_state',
                'sources': data['result'].get('sources', []),
            })
            expected_context.update({
                'id': self.block.location.html_id(),
                'block_id': str(self.block.location),
                'course_id': str(self.block.location.course_key),
                'download_video_link': data['result'].get('download_video_link'),
                'metadata': json.dumps(expected_context['metadata'])
            })

        # Get the actual context that was passed to render_django_template
        actual_context = mock_render_django_template.call_args.args[1]

        # Validate and compare contexts
        assert get_context_from_dict(actual_context) == get_context_from_dict(expected_context)

    # pylint: disable=invalid-name
    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    def test_get_html_cdn_source_external_video(self, mock_render_django_template):
        """
        Test that video from an external source loads successfully.

        For a video from a third part, which has 'external' status
        in the VAL, the url-rewrite will not happen and URL will
        remain unchanged in the get_html() method.
        """

        source_xml = """
                    <video show_captions="true"
                    display_name="A Name"
                    sub="a_sub_file.srt.sjson" source="{source}"
                    download_video="{download_video}"
                    edx_video_id="{edx_video_id}"
                    start_time="3603.0" end_time="3610.0"
                    >
                        {sources}
                    </video>
                """

        case_data = {
            'download_video': 'true',
            'source': 'example_source.mp4',
            'sources': """
                        <source src="http://example.com/example.mp4"/>
                    """,
            'result': {
                'download_video_link': 'http://example.com/example.mp4',
                'sources': [
                    'http://example.com/example.mp4',
                ],
            },
        }

        cases = [
            dict(case_data, edx_video_id="vid-v1:12345"),
        ]

        initial_context = {
            'autoadvance_enabled': False,
            'license': None,
            'bumper_metadata': 'null',
            'cdn_eval': False,
            'cdn_exp_group': None,
            'display_name': 'A Name',
            'download_video_link': None,
            'is_video_from_same_origin': False,
            'handout': None,
            'hide_downloads': False,
            'id': None,
            'is_embed': False,
            'metadata': self.default_metadata_dict,
            'track': None,
            'transcript_download_format': 'srt',
            'transcript_download_formats_list': [
                {'display_name': 'SubRip (.srt) file', 'value': 'srt'},
                {'display_name': 'Text (.txt) file', 'value': 'txt'}
            ],
            'poster': 'null',
            'transcript_feedback_enabled': False,
            'video_id': 'vid-v1:12345',
        }
        initial_context['metadata']['duration'] = None

        for data in cases:
            DATA = source_xml.format(
                download_video=data['download_video'],
                source=data['source'],
                sources=data['sources'],
                edx_video_id=data['edx_video_id'],
            )
            self.initialize_block(data=DATA)

            # Mocking the edxval API call because if not done,
            # the method throws exception as no VAL entry is found
            # for the corresponding edx-video-id
            with patch('edxval.api.get_video_info') as mock_get_video_info:
                mock_get_video_info.return_value = {
                    'url': 'http://example.com/example.mp4',
                    'edx_video_id': 'vid-v1:12345',
                    'status': 'external',
                    'duration': None,
                    'client_video_id': 'external video',
                    'encoded_videos': {}
                }
            self.block.student_view(None)
            expected_context = dict(initial_context)
            expected_context['metadata'].update({
                'transcriptTranslationUrl': self.get_handler_url('transcript', 'translation/__lang__'),
                'transcriptAvailableTranslationsUrl': self.get_handler_url('transcript', 'available_translations'),
                'publishCompletionUrl': self.get_handler_url('publish_completion', ''),
                'saveStateUrl': self.block.ajax_url + '/save_user_state',
                'sources': data['result'].get('sources', []),
            })
            expected_context.update({
                'id': self.block.location.html_id(),
                'block_id': str(self.block.location),
                'course_id': str(self.block.location.course_key),
                'download_video_link': data['result'].get('download_video_link'),
                'metadata': json.dumps(expected_context['metadata'])
            })

        # Get the actual context that was passed to render_django_template
        actual_context = mock_render_django_template.call_args.args[1]

        # Validate and compare contexts (handles user_id and metadata ordering)
        assert get_context_from_dict(actual_context) == get_context_from_dict(expected_context)

    @ddt.data(
        (True, ['youtube', 'desktop_webm', 'desktop_mp4', 'hls']),
        (False, ['youtube', 'desktop_webm', 'desktop_mp4'])
    )
    @ddt.unpack
    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    def test_get_html_on_toggling_hls_feature(self, hls_feature_enabled, expected_val_profiles, _):
        """
        Verify val profiles on toggling HLS Playback feature.
        """
        with patch('xmodule.video_block.video_block.edxval_api.get_urls_for_profiles') as get_urls_for_profiles:
            get_urls_for_profiles.return_value = {
                'desktop_webm': 'https://webm.com/dw.webm',
                'hls': 'https://hls.com/hls.m3u8',
                'youtube': 'https://yt.com/?v=v0TFmdO4ZP0',
                'desktop_mp4': 'https://mp4.com/dm.mp4'
            }
            with patch(
                'openedx.core.djangoapps.video_config.services.VideoConfigService.is_hls_playback_enabled'
            ) as feature_enabled:
                feature_enabled.return_value = hls_feature_enabled
                video_xml = '<video display_name="Video" download_video="true" edx_video_id="12345-67890">[]</video>'
                self.initialize_block(data=video_xml)
                self.block.render(STUDENT_VIEW)
                get_urls_for_profiles.assert_called_with(
                    self.block.edx_video_id,
                    expected_val_profiles,
                )

    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    @patch(
        'openedx.core.djangoapps.video_config.services.VideoConfigService.is_hls_playback_enabled',
        Mock(return_value=True)
    )
    @patch('xmodule.video_block.video_block.edxval_api.get_urls_for_profiles')
    def test_get_html_hls(self, get_urls_for_profiles, mock_render_django_template):
        """
        Verify that hls profile functionality works as expected.

        * HLS source should be added into list of available sources
        * HLS source should not be used for download URL If available from edxval
        """
        video_xml = '<video display_name="Video" download_video="true" edx_video_id="12345-67890">[]</video>'

        get_urls_for_profiles.return_value = {
            'desktop_webm': 'https://webm.com/dw.webm',
            'hls': 'https://hls.com/hls.m3u8',
            'youtube': 'https://yt.com/?v=v0TFmdO4ZP0',
            'desktop_mp4': 'https://mp4.com/dm.mp4'
        }

        self.initialize_block(data=video_xml)
        self.block.student_view(None)

        # Get the actual context that was passed to render_django_template
        actual_context = mock_render_django_template.call_args.args[1]

        metadata_dict = json.loads(actual_context['metadata'])

        assert actual_context['download_video_link'] == 'https://mp4.com/dm.mp4'
        assert metadata_dict['streams'] == '1.00:https://yt.com/?v=v0TFmdO4ZP0'
        assert sorted(metadata_dict['sources']) == sorted([
            'https://webm.com/dw.webm',
            'https://mp4.com/dm.mp4',
            'https://hls.com/hls.m3u8',
        ])

    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    def test_get_html_hls_no_video_id(self, mock_render_django_template):
        """
        Verify that `download_video_link` is set to None for HLS videos if no video id
        """
        video_xml = """
        <video display_name="Video" download_video="true" source="https://hls.com/hls.m3u8">
        ["https://hls.com/hls2.m3u8", "https://hls.com/hls3.m3u8"]
        </video>
        """

        self.initialize_block(data=video_xml)
        self.block.student_view(None)

        # Get the actual context that was passed to render_django_template
        actual_context = mock_render_django_template.call_args.args[1]

        assert actual_context['download_video_link'] is None

    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    def test_get_html_non_hls_video_download(self, _):
        """
        Verify that `download_video_link` is available if a non HLS videos is available
        """
        video_xml = """
        <video display_name="Video" download_video="true">
            <source src="http://example.com/example.m3u8"/>
            <source src="http://example.com/example.mp4"/>
            <source src="http://example.com/example.webm"/>
        </video>
        """

        self.initialize_block(data=video_xml)
        context = self.block.student_view(None).content
        assert "'download_video_link': 'http://example.com/example.mp4'" in context

    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    def test_html_student_public_view(self, _):
        """
        Test the student and public views
        """
        video_xml = """
        <video display_name="Video" download_video="true" source="https://hls.com/hls.m3u8">
        ["https://hls.com/hls2.m3u8", "https://hls.com/hls3.m3u8"]
        </video>
        """

        self.initialize_block(data=video_xml)
        context = self.block.student_view(None).content
        assert '"saveStateEnabled": true' in context
        context = self.block.render(PUBLIC_VIEW).content
        assert '"saveStateEnabled": false' in context

    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    @patch('xmodule.video_block.video_block.edxval_api.get_course_video_image_url')
    def test_poster_image(self, get_course_video_image_url, _):
        """
        Verify that poster image functionality works as expected.
        """
        video_xml = '<video display_name="Video" download_video="true" edx_video_id="12345-67890">[]</video>'
        get_course_video_image_url.return_value = '/media/video-images/poster.png'

        self.initialize_block(data=video_xml)
        context = self.block.student_view(None).content

        assert '"poster": "/media/video-images/poster.png"' in context

    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    @patch('xmodule.video_block.video_block.edxval_api.get_course_video_image_url')
    def test_poster_image_without_edx_video_id(self, get_course_video_image_url, _):
        """
        Verify that poster image is set to None and there is no crash when no edx_video_id.
        """
        video_xml = '<video display_name="Video" download_video="true" edx_video_id="null">[]</video>'
        get_course_video_image_url.return_value = '/media/video-images/poster.png'

        self.initialize_block(data=video_xml)
        context = self.block.student_view(None).content

        assert "'poster': 'null'" in context

    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    @patch(
        'openedx.core.djangoapps.video_config.services.VideoConfigService.is_hls_playback_enabled',
        Mock(return_value=False)
    )
    def test_hls_primary_playback_on_toggling_hls_feature(self, _):
        """
        Verify that `prioritize_hls` is set to `False` if `HLSPlaybackEnabledFlag` is disabled.
        """
        video_xml = '<video display_name="Video" download_video="true" edx_video_id="12345-67890">[]</video>'
        self.initialize_block(data=video_xml)
        context = self.block.student_view(None).content
        assert '"prioritizeHls": false' in context

    @ddt.data(
        {
            'course_override': WaffleFlagCourseOverrideModel.ALL_CHOICES.on,
            'waffle_enabled': False,
            'youtube': '3_yD_cEKoCk',
            'hls': ['https://hls.com/hls.m3u8'],
            'result': 'true'
        },
        {
            'course_override': WaffleFlagCourseOverrideModel.ALL_CHOICES.on,
            'waffle_enabled': False,
            'youtube': '',
            'hls': ['https://hls.com/hls.m3u8'],
            'result': 'false'
        },
        {
            'course_override': WaffleFlagCourseOverrideModel.ALL_CHOICES.on,
            'waffle_enabled': False,
            'youtube': '',
            'hls': [],
            'result': 'false'
        },
        {
            'course_override': WaffleFlagCourseOverrideModel.ALL_CHOICES.on,
            'waffle_enabled': False,
            'youtube': '3_yD_cEKoCk',
            'hls': [],
            'result': 'true'
        },
        {
            'course_override': WaffleFlagCourseOverrideModel.ALL_CHOICES.off,
            'waffle_enabled': True,
            'youtube': '3_yD_cEKoCk',
            'hls': ['https://hls.com/hls.m3u8'],
            'result': 'false'
        },
    )
    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    @patch(
        'openedx.core.djangoapps.video_config.services.VideoConfigService.is_hls_playback_enabled',
        Mock(return_value=True)
    )
    def test_deprecate_youtube_course_waffle_flag(self, data, mock_render_django_template):
        """
        Tests various combinations of a `prioritize_hls` flag being set in waffle and overridden for a course.
        """
        metadata = {
            'html5_sources': ['http://youtu.be/3_yD_cEKoCk.mp4'] + data['hls'],
        }
        video_xml = '<video display_name="Video" edx_video_id="12345-67890" youtube_id_1_0="{}">[]</video>'.format(
            data['youtube']
        )
        with patch.object(WaffleFlagCourseOverrideModel, 'override_value', return_value=data['course_override']):
            with override_waffle_flag(DEPRECATE_YOUTUBE, active=data['waffle_enabled']):
                self.initialize_block(data=video_xml, metadata=metadata)
                self.block.student_view(None)
                context = mock_render_django_template.call_args.args[1]
                metadata_dict = json.loads(context['metadata'])
                assert metadata_dict['prioritizeHls'] == (data['result'] == 'true')


@ddt.ddt
class TestVideoBlockInitialization(BaseTestVideoXBlock):
    """
    Make sure that block initialization works correctly.
    """
    CATEGORY = "video"
    DATA = SOURCE_XML
    METADATA = {}

    def setUp(self):
        super().setUp()
        self.setup_course()

    @ddt.data(
        (
            {
                'youtube': 'v0TFmdO4ZP0',
                'hls': 'https://hls.com/hls.m3u8',
                'desktop_mp4': 'https://mp4.com/dm.mp4',
                'desktop_webm': 'https://webm.com/dw.webm',
            },
            ['https://www.youtube.com/watch?v=v0TFmdO4ZP0']
        ),
        (
            {
                'youtube': None,
                'hls': 'https://hls.com/hls.m3u8',
                'desktop_mp4': 'https://mp4.com/dm.mp4',
                'desktop_webm': 'https://webm.com/dw.webm',
            },
            ['https://www.youtube.com/watch?v=3_yD_cEKoCk']
        ),
        (
            {
                'youtube': None,
                'hls': None,
                'desktop_mp4': None,
                'desktop_webm': None,
            },
            ['https://www.youtube.com/watch?v=3_yD_cEKoCk']
        ),
    )
    @ddt.unpack
    @patch(
        'openedx.core.djangoapps.video_config.services.VideoConfigService.is_hls_playback_enabled',
        Mock(return_value=True)
    )
    def test_val_encoding_in_context(self, val_video_encodings, video_url):
        """
        Tests that the val encodings correctly override the video url when the edx video id is set and
        one or more encodings are present.
        Accepted order of source priority is:
            VAL's youtube source > external youtube source > hls > mp4 > webm.

        Note that `https://www.youtube.com/watch?v=3_yD_cEKoCk` is the default youtube source with which
        a video component is initialized. Current implementation considers this youtube source as a valid
        external youtube source.
        """
        with patch('xmodule.video_block.video_block.edxval_api.get_urls_for_profiles') as get_urls_for_profiles:
            get_urls_for_profiles.return_value = val_video_encodings
            self.initialize_block(
                data='<video display_name="Video" download_video="true" edx_video_id="12345-67890">[]</video>'
            )
            context = self.block.get_context()
        assert context['transcripts_basic_tab_metadata']['video_url']['value'] == video_url

    @ddt.data(
        (
            {
                'youtube': None,
                'hls': 'https://hls.com/hls.m3u8',
                'desktop_mp4': 'https://mp4.com/dm.mp4',
                'desktop_webm': 'https://webm.com/dw.webm',
            },
            ['https://hls.com/hls.m3u8']
        ),
        (
            {
                'youtube': 'v0TFmdO4ZP0',
                'hls': 'https://hls.com/hls.m3u8',
                'desktop_mp4': None,
                'desktop_webm': 'https://webm.com/dw.webm',
            },
            ['https://www.youtube.com/watch?v=v0TFmdO4ZP0']
        ),
    )
    @ddt.unpack
    @patch(
        'openedx.core.djangoapps.video_config.services.VideoConfigService.is_hls_playback_enabled',
        Mock(return_value=True)
    )
    def test_val_encoding_in_context_without_external_youtube_source(self, val_video_encodings, video_url):
        """
        Tests that the val encodings correctly override the video url when the edx video id is set and
        one or more encodings are present. In this scenerio no external youtube source is provided.
        Accepted order of source priority is:
            VAL's youtube source > external youtube source > hls > mp4 > webm.
        """
        with patch('xmodule.video_block.video_block.edxval_api.get_urls_for_profiles') as get_urls_for_profiles:
            get_urls_for_profiles.return_value = val_video_encodings
            # pylint: disable=line-too-long
            self.initialize_block(
                data='<video display_name="Video" youtube_id_1_0="" download_video="true" edx_video_id="12345-67890">[]</video>'
            )
            context = self.block.get_context()
        assert context['transcripts_basic_tab_metadata']['video_url']['value'] == video_url


@ddt.ddt
class TestEditorSavedMethod(BaseTestVideoXBlock):
    """
    Make sure that `editor_saved` method works correctly.
    """
    CATEGORY = "video"
    DATA = SOURCE_XML
    METADATA = {}
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super().setUp()
        self.setup_course()
        self.metadata = {
            'source': 'http://youtu.be/3_yD_cEKoCk',
            'html5_sources': ['http://example.org/video.mp4'],
        }
        # path to subs_3_yD_cEKoCk.srt.sjson file
        self.file_name = 'subs_3_yD_cEKoCk.srt.sjson'
        self.test_dir = path(__file__).abspath().dirname().dirname().dirname().dirname().dirname()
        self.file_path = self.test_dir + '/common/test/data/uploads/' + self.file_name

    def test_editor_saved_when_html5_sub_not_exist(self):
        """
        When there is youtube_sub exist but no html5_sub present for
        html5_sources, editor_saved function will generate new html5_sub
        for video.
        """
        self.initialize_block(metadata=self.metadata)
        item = self.store.get_item(self.block.location)
        with open(self.file_path, "rb") as myfile:  # lint-amnesty, pylint: disable=bad-option-value, open-builtin
            save_to_store(myfile.read(), self.file_name, 'text/sjson', item.location)
        item.sub = "3_yD_cEKoCk"
        # subs_video.srt.sjson does not exist before calling editor_saved function
        with pytest.raises(NotFoundError):
            Transcript.get_asset(item.location, 'subs_video.srt.sjson')
        old_metadata = own_metadata(item)
        # calling editor_saved will generate new file subs_video.srt.sjson for html5_sources
        item.editor_saved(self.user, old_metadata, None)
        assert isinstance(Transcript.get_asset(item.location, 'subs_3_yD_cEKoCk.srt.sjson'), StaticContent)

    def test_editor_saved_when_youtube_and_html5_subs_exist(self):
        """
        When both youtube_sub and html5_sub already exist then no new
        sub will be generated by editor_saved function.
        """
        self.initialize_block(metadata=self.metadata)
        item = self.store.get_item(self.block.location)
        with open(self.file_path, "rb") as myfile:  # lint-amnesty, pylint: disable=bad-option-value, open-builtin
            save_to_store(myfile.read(), self.file_name, 'text/sjson', item.location)
            save_to_store(myfile.read(), 'subs_video.srt.sjson', 'text/sjson', item.location)
        item.sub = "3_yD_cEKoCk"
        # subs_3_yD_cEKoCk.srt.sjson and subs_video.srt.sjson already exist
        assert isinstance(Transcript.get_asset(item.location, self.file_name), StaticContent)
        assert isinstance(Transcript.get_asset(item.location, 'subs_video.srt.sjson'), StaticContent)
        old_metadata = own_metadata(item)
        with patch(
            'openedx.core.djangoapps.video_config.services.manage_video_subtitles_save'
        ) as manage_video_subtitles_save:
            item.editor_saved(self.user, old_metadata, None)
            assert not manage_video_subtitles_save.called

    def test_editor_saved_with_unstripped_video_id(self):
        """
        Verify editor saved when video id contains spaces/tabs.
        """
        stripped_video_id = str(uuid4())
        unstripped_video_id = '{video_id}{tabs}'.format(video_id=stripped_video_id, tabs='\t\t\t')
        self.metadata.update({
            'edx_video_id': unstripped_video_id
        })
        self.initialize_block(metadata=self.metadata)
        item = self.store.get_item(self.block.location)
        assert item.edx_video_id == unstripped_video_id

        # Now, modifying and saving the video block should strip the video id.
        old_metadata = own_metadata(item)
        item.display_name = 'New display name'
        item.editor_saved(self.user, old_metadata, None)
        assert item.edx_video_id == stripped_video_id

    @patch('xmodule.video_block.video_block.edxval_api.get_url_for_profile', Mock(return_value='test_yt_id'))
    def test_editor_saved_with_yt_val_profile(self):
        """
        Verify editor saved overrides `youtube_id_1_0` when a youtube val profile is there
        for a given `edx_video_id`.
        """
        self.initialize_block(metadata=self.metadata)
        item = self.store.get_item(self.block.location)
        assert item.youtube_id_1_0 == '3_yD_cEKoCk'

        # Now, modify `edx_video_id` and save should override `youtube_id_1_0`.
        old_metadata = own_metadata(item)
        item.edx_video_id = str(uuid4())
        item.editor_saved(self.user, old_metadata, None)
        assert item.youtube_id_1_0 == 'test_yt_id'


@ddt.ddt
class TestVideoBlockStudentViewJson(BaseTestVideoXBlock, CacheIsolationTestCase):
    """
    Tests for the student_view_data method on VideoBlock.
    """
    TEST_DURATION = 111.0
    TEST_PROFILE = "mobile"
    TEST_SOURCE_URL = "http://www.example.com/source.mp4"
    TEST_LANGUAGE = "ge"
    TEST_ENCODED_VIDEO = {
        'profile': TEST_PROFILE,
        'bitrate': 333,
        'url': 'http://example.com/video',
        'file_size': 222,
    }
    TEST_EDX_VIDEO_ID = 'test_edx_video_id'
    TEST_YOUTUBE_ID = 'test_youtube_id'
    TEST_YOUTUBE_EXPECTED_URL = 'https://www.youtube.com/watch?v=test_youtube_id'

    def setUp(self):
        super().setUp()
        video_declaration = (
            "<video display_name='Test Video' edx_video_id='123' youtube_id_1_0=\'" + self.TEST_YOUTUBE_ID + "\'>"
        )
        sample_xml = ''.join([
            video_declaration,
            "<source src='", self.TEST_SOURCE_URL, "'/> ",
            "<transcript language='", self.TEST_LANGUAGE, "' src='german_translation.srt' /> ",
            "</video>"]
        )
        self.transcript_url = "transcript_url"
        self.initialize_block(data=sample_xml)
        self.video = self.block
        self.video.runtime.handler_url = Mock(return_value=self.transcript_url)

    def setup_val_video(self, associate_course_in_val=False):
        """
        Creates a video entry in VAL.
        Arguments:
            associate_course - If True, associates the test course with the video in VAL.
        """
        create_profile('mobile')
        create_video({
            'edx_video_id': self.TEST_EDX_VIDEO_ID,
            'client_video_id': 'test_client_video_id',
            'duration': self.TEST_DURATION,
            'status': 'dummy',
            'encoded_videos': [self.TEST_ENCODED_VIDEO],
            'courses': [str(self.video.location.course_key)] if associate_course_in_val else [],
        })
        self.val_video = get_video_info(self.TEST_EDX_VIDEO_ID)  # pylint: disable=attribute-defined-outside-init

    def get_result(self, allow_cache_miss=True):
        """
        Returns the result from calling the video's student_view_data method.
        Arguments:
            allow_cache_miss is passed in the context to the student_view_data method.
        """
        context = {
            "profiles": [self.TEST_PROFILE],
            "allow_cache_miss": "True" if allow_cache_miss else "False"
        }
        return self.video.student_view_data(context)

    def verify_result_with_fallback_and_youtube(self, result):
        """
        Verifies the result is as expected when returning "fallback" video data (not from VAL).
        """
        self.assertDictEqual(
            result,
            {
                "only_on_web": False,
                "duration": None,
                "transcripts": {self.TEST_LANGUAGE: self.transcript_url},
                "encoded_videos": {
                    "fallback": {"url": self.TEST_SOURCE_URL, "file_size": 0},
                    "youtube": {"url": self.TEST_YOUTUBE_EXPECTED_URL, "file_size": 0}
                },
                "all_sources": [self.TEST_SOURCE_URL],
            }
        )

    def verify_result_with_youtube_url(self, result):
        """
        Verifies the result is as expected when returning "fallback" video data (not from VAL).
        """
        self.assertDictEqual(
            result,
            {
                "only_on_web": False,
                "duration": None,
                "transcripts": {self.TEST_LANGUAGE: self.transcript_url},
                "encoded_videos": {"youtube": {"url": self.TEST_YOUTUBE_EXPECTED_URL, "file_size": 0}},
                "all_sources": [],
            }
        )

    def verify_result_with_val_profile(self, result):
        """
        Verifies the result is as expected when returning video data from VAL.
        """
        assert_dict_contains_subset(
            self,
            result.pop("encoded_videos")[self.TEST_PROFILE],
            self.TEST_ENCODED_VIDEO,
        )
        self.assertDictEqual(
            result,
            {
                "only_on_web": False,
                "duration": self.TEST_DURATION,
                "transcripts": {self.TEST_LANGUAGE: self.transcript_url},
                'all_sources': [self.TEST_SOURCE_URL],
            }
        )

    def test_only_on_web(self):
        self.video.only_on_web = True
        result = self.get_result()
        self.assertDictEqual(result, {"only_on_web": True})

    def test_no_edx_video_id(self):
        result = self.get_result()
        self.verify_result_with_fallback_and_youtube(result)

    def test_no_edx_video_id_and_no_fallback(self):
        video_declaration = f"<video display_name='Test Video 2' youtube_id_1_0=\'{self.TEST_YOUTUBE_ID}\'>"
        # the video has no source listed, only a youtube link, so no fallback url will be provided
        sample_xml = ''.join([
            video_declaration,
            "<transcript language='", self.TEST_LANGUAGE, "' src='german_translation.srt' /> ",
            "</video>"
        ])
        self.transcript_url = "transcript_url"
        self.initialize_block(data=sample_xml)
        self.video = self.block
        self.video.runtime.handler_url = Mock(return_value=self.transcript_url)
        result = self.get_result()
        self.verify_result_with_youtube_url(result)

    @ddt.data(True, False)
    def test_with_edx_video_id_video_associated_in_val(self, allow_cache_miss):
        """
        Tests retrieving a video that is stored in VAL and associated with a course in VAL.
        """
        self.video.edx_video_id = self.TEST_EDX_VIDEO_ID
        self.setup_val_video(associate_course_in_val=True)
        # the video is associated in VAL so no cache miss should ever happen but test retrieval in both contexts
        result = self.get_result(allow_cache_miss)
        self.verify_result_with_val_profile(result)

    @ddt.data(True, False)
    def test_with_edx_video_id_video_unassociated_in_val(self, allow_cache_miss):
        """
        Tests retrieving a video that is stored in VAL but not associated with a course in VAL.
        """
        self.video.edx_video_id = self.TEST_EDX_VIDEO_ID
        self.setup_val_video(associate_course_in_val=False)
        result = self.get_result(allow_cache_miss)
        if allow_cache_miss:
            self.verify_result_with_val_profile(result)
        else:
            self.verify_result_with_fallback_and_youtube(result)

    @ddt.data(True, False)
    def test_with_edx_video_id_video_not_in_val(self, allow_cache_miss):
        """
        Tests retrieving a video that is not stored in VAL.
        """
        self.video.edx_video_id = self.TEST_EDX_VIDEO_ID
        # The video is not in VAL so in contexts that do and don't allow cache misses we should always get a fallback
        result = self.get_result(allow_cache_miss)
        self.verify_result_with_fallback_and_youtube(result)

    @ddt.data(
        ({}, '', [], ['en']),
        ({}, '', ['de'], ['de']),
        ({}, '', ['en', 'de'], ['en', 'de']),
        ({}, 'en-subs', ['de'], ['en', 'de']),
        ({'uk': 1}, 'en-subs', ['de'], ['en', 'uk', 'de']),
        ({'uk': 1, 'de': 1}, 'en-subs', ['de', 'en'], ['en', 'uk', 'de']),
    )
    @ddt.unpack
    @patch('openedx.core.djangoapps.video_config.transcripts_utils.edxval_api.get_available_transcript_languages')
    def test_student_view_with_val_transcripts_enabled(self, transcripts, english_sub, val_transcripts,
                                                       expected_transcripts, mock_get_transcript_languages):
        """
        Test `student_view_data` with edx-val transcripts enabled.
        """
        mock_get_transcript_languages.return_value = val_transcripts
        self.video.transcripts = transcripts
        self.video.sub = english_sub
        student_view_response = self.get_result()
        self.assertCountEqual(list(student_view_response['transcripts'].keys()), expected_transcripts)


@ddt.ddt
class VideoBlockTest(TestCase, VideoBlockTestBase):
    """
    Tests for video block that requires access to django settings.
    """
    def setUp(self):
        super().setUp()
        self.block.runtime.handler_url = MagicMock()
        self.temp_dir = mkdtemp()
        file_system = OSFS(self.temp_dir)
        self.file_system = file_system.makedir(EXPORT_IMPORT_COURSE_DIR, recreate=True)
        self.addCleanup(shutil.rmtree, self.temp_dir)

    def get_video_transcript_data(self, video_id, language_code='en', file_format='srt', provider='Custom'):
        return dict(
            video_id=video_id,
            language_code=language_code,
            provider=provider,
            file_format=file_format,
        )

    def test_get_context(self):
        """"
        Test get_context.

        This test is located here and not in xmodule.tests because get_context calls editable_metadata_fields.
        Which, in turn, uses settings.LANGUAGES from django setttings.
        """
        correct_tabs = [
            {
                'name': "Basic",
                'template': "video/transcripts.html",
                'current': True
            },
            {
                'name': 'Advanced',
                'template': 'tabs/metadata-edit-tab.html'
            }
        ]
        rendered_context = self.block.get_context()
        self.assertListEqual(rendered_context['tabs'], correct_tabs)

        # Assert that the Video ID field is present in basic tab metadata context.
        assert rendered_context['transcripts_basic_tab_metadata']['edx_video_id'] ==\
               self.block.editable_metadata_fields['edx_video_id']

    def test_export_val_data_with_internal(self):
        """
        Tests that exported VAL videos are working as expected.
        """
        language_code = 'ar'
        transcript_file_name = 'test_edx_video_id-ar.srt'
        expected_transcript_path = combine(
            combine(self.temp_dir, EXPORT_IMPORT_COURSE_DIR),
            combine(EXPORT_IMPORT_STATIC_DIR, transcript_file_name)
        )
        self.block.edx_video_id = 'test_edx_video_id'

        create_profile('mobile')
        create_video({
            'edx_video_id': self.block.edx_video_id,
            'client_video_id': 'test_client_video_id',
            'duration': 111.0,
            'status': 'dummy',
            'encoded_videos': [{
                'profile': 'mobile',
                'url': 'http://example.com/video',
                'file_size': 222,
                'bitrate': 333,
            }],
        })
        create_or_update_video_transcript(
            video_id=self.block.edx_video_id,
            language_code=language_code,
            metadata={
                'provider': 'Cielo24',
                'file_format': 'srt'
            },
            file_data=ContentFile(TRANSCRIPT_FILE_SRT_DATA)
        )

        actual = self.block.definition_to_xml(resource_fs=self.file_system)
        expected_str = """
            <video youtube="1.00:3_yD_cEKoCk" url_name="SampleProblem" transcripts='{transcripts}'>
                <video_asset client_video_id="test_client_video_id" duration="111.0" image="">
                    <encoded_video profile="mobile" url="http://example.com/video" file_size="222" bitrate="333"/>
                    <transcripts>
                        <transcript file_format="srt" language_code="{language_code}" provider="Cielo24"/>
                    </transcripts>
                </video_asset>
                <transcript language="{language_code}" src="{transcript_file}"/>
            </video>
        """.format(
            language_code=language_code,
            transcript_file=transcript_file_name,
            transcripts=json.dumps({language_code: transcript_file_name})
        )
        parser = etree.XMLParser(remove_blank_text=True)
        expected = etree.XML(expected_str, parser=parser)
        self.assertXmlEqual(expected, actual)

        # Verify transcript file is created.
        assert [transcript_file_name] == self.file_system.listdir(EXPORT_IMPORT_STATIC_DIR)

        # Also verify the content of created transcript file.
        with open(expected_transcript_path) as transcript_path:
            expected_transcript_content = File(transcript_path).read()
            transcript = get_video_transcript_data(video_id=self.block.edx_video_id, language_code=language_code)
            assert transcript['content'].decode('utf-8') == expected_transcript_content

    @ddt.data(
        (['en', 'da'], 'test_sub', ''),
        (['da'], 'test_sub', 'test_sub')
    )
    @ddt.unpack
    def test_export_val_transcripts_backward_compatibility(self, languages, sub, expected_sub):
        """
        Tests new transcripts export for backward compatibility.
        """
        self.block.edx_video_id = 'test_video_id'
        self.block.sub = sub

        # Setup VAL encode profile, video and transcripts
        create_profile('mobile')
        create_video({
            'edx_video_id': self.block.edx_video_id,
            'client_video_id': 'test_client_video_id',
            'duration': 111.0,
            'status': 'dummy',
            'encoded_videos': [{
                'profile': 'mobile',
                'url': 'http://example.com/video',
                'file_size': 222,
                'bitrate': 333,
            }],
        })

        for language in languages:
            create_video_transcript(
                video_id=self.block.edx_video_id,
                language_code=language,
                file_format=Transcript.SRT,
                content=ContentFile(TRANSCRIPT_FILE_SRT_DATA)
            )

        # Export the video block into xml
        video_xml = self.block.definition_to_xml(resource_fs=self.file_system)

        # Assert `sub` and `transcripts` attribute in the xml
        assert video_xml.get('sub') == expected_sub

        expected_transcripts = {
            language: "{edx_video_id}-{language}.srt".format(
                edx_video_id=self.block.edx_video_id,
                language=language
            )
            for language in languages
        }
        self.assertDictEqual(json.loads(video_xml.get('transcripts')), expected_transcripts)

        # Assert transcript content from course OLX
        for language in languages:
            expected_transcript_path = combine(
                combine(self.temp_dir, EXPORT_IMPORT_COURSE_DIR),
                combine(EXPORT_IMPORT_STATIC_DIR, expected_transcripts[language])
            )
            with open(expected_transcript_path) as transcript_path:
                expected_transcript_content = File(transcript_path).read()
                transcript = get_video_transcript_data(video_id=self.block.edx_video_id, language_code=language)
                assert transcript['content'].decode('utf-8') == expected_transcript_content

    def test_export_val_data_not_found(self):
        """
        Tests that external video export works as expected.
        """
        self.block.edx_video_id = 'nonexistent'
        actual = self.block.definition_to_xml(resource_fs=self.file_system)
        expected_str = """<video youtube="1.00:3_yD_cEKoCk" url_name="SampleProblem"/>"""
        parser = etree.XMLParser(remove_blank_text=True)
        expected = etree.XML(expected_str, parser=parser)
        self.assertXmlEqual(expected, actual)

    @patch('openedx.core.djangoapps.video_config.transcripts_utils.get_video_ids_info')
    def test_export_no_video_ids(self, mock_get_video_ids_info):
        """
        Tests export when there is no video id. `export_to_xml` only works in case of video id.
        """
        mock_get_video_ids_info.return_value = True, []

        actual = self.block.definition_to_xml(resource_fs=self.file_system)
        expected_str = '<video youtube="1.00:3_yD_cEKoCk" url_name="SampleProblem"></video>'

        parser = etree.XMLParser(remove_blank_text=True)
        expected = etree.XML(expected_str, parser=parser)
        self.assertXmlEqual(expected, actual)

    def test_import_val_data_internal(self):
        """
        Test that import val data internal works as expected.
        """
        create_profile('mobile')
        module_system = DummyModuleStoreRuntime(load_error_blocks=True)

        edx_video_id = 'test_edx_video_id'
        sub_id = '0CzPOIIdUsA'
        external_transcript_name = 'The_Flash.srt'
        external_transcript_language_code = 'ur'
        val_transcript_language_code = 'ar'
        val_transcript_provider = 'Cielo24'
        external_transcripts = {
            external_transcript_language_code: external_transcript_name
        }

        # Create static directory in import file system and place transcript files inside it.
        module_system.resources_fs.makedirs(EXPORT_IMPORT_STATIC_DIR, recreate=True)

        # Create VAL transcript.
        create_file_in_fs(
            TRANSCRIPT_FILE_SRT_DATA,
            'test_edx_video_id-ar.srt',
            module_system.resources_fs,
            EXPORT_IMPORT_STATIC_DIR
        )

        # Create self.sub and self.transcripts transcript.
        create_file_in_fs(
            TRANSCRIPT_FILE_SRT_DATA,
            subs_filename(sub_id, self.block.transcript_language),
            module_system.resources_fs,
            EXPORT_IMPORT_STATIC_DIR
        )
        create_file_in_fs(
            TRANSCRIPT_FILE_SRT_DATA,
            external_transcript_name,
            module_system.resources_fs,
            EXPORT_IMPORT_STATIC_DIR
        )

        xml_data = """
            <video edx_video_id='{edx_video_id}' sub='{sub_id}' transcripts='{transcripts}'>
                <video_asset client_video_id="test_client_video_id" duration="111.0">
                    <encoded_video profile="mobile" url="http://example.com/video" file_size="222" bitrate="333"/>
                    <transcripts>
                        <transcript file_format="srt" language_code="{val_transcript_language_code}" provider="{val_transcript_provider}"/>
                    </transcripts>
                </video_asset>
            </video>
        """.format(
            edx_video_id=edx_video_id,
            sub_id=sub_id,
            transcripts=json.dumps(external_transcripts),
            val_transcript_language_code=val_transcript_language_code,
            val_transcript_provider=val_transcript_provider
        )
        xml_object = etree.fromstring(xml_data)
        module_system.id_generator.target_course_id = "test_course_id"

        video = self.block.parse_xml(xml_object, module_system, None)

        assert video.edx_video_id == 'test_edx_video_id'
        video_data = get_video_info(video.edx_video_id)
        assert video_data['client_video_id'] == 'test_client_video_id'
        assert video_data['duration'] == 111.0
        assert video_data['status'] == 'imported'
        assert video_data['courses'] == [{module_system.id_generator.target_course_id: None}]
        assert video_data['encoded_videos'][0]['profile'] == 'mobile'
        assert video_data['encoded_videos'][0]['url'] == 'http://example.com/video'
        assert video_data['encoded_videos'][0]['file_size'] == 222
        assert video_data['encoded_videos'][0]['bitrate'] == 333

        # Verify that VAL transcript is imported.
        assert_dict_contains_subset(
            self,
            self.get_video_transcript_data(
                edx_video_id,
                language_code=val_transcript_language_code,
                provider=val_transcript_provider
            ),
            get_video_transcript(video.edx_video_id, val_transcript_language_code),
        )

        # Verify that transcript from sub field is imported.
        assert_dict_contains_subset(
            self,
            self.get_video_transcript_data(
                edx_video_id,
                language_code=self.block.transcript_language
            ),
            get_video_transcript(video.edx_video_id, self.block.transcript_language),
        )

        # Verify that transcript from transcript field is imported.
        assert_dict_contains_subset(
            self,
            self.get_video_transcript_data(
                edx_video_id,
                language_code=external_transcript_language_code
            ),
            get_video_transcript(video.edx_video_id, external_transcript_language_code),
        )

    def test_import_no_video_id(self):
        """
        Test that importing a video with no video id, creates a new external video.
        """
        xml_data = """<video><video_asset></video_asset></video>"""
        xml_object = etree.fromstring(xml_data)
        module_system = DummyModuleStoreRuntime(load_error_blocks=True)

        # Verify edx_video_id is empty before.
        assert self.block.edx_video_id == ''

        video = self.block.parse_xml(xml_object, module_system, None)

        # Verify edx_video_id is populated after the import.
        assert video.edx_video_id != ''

        video_data = get_video_info(video.edx_video_id)
        assert video_data['client_video_id'] == 'External Video'
        assert video_data['duration'] == 0.0
        assert video_data['status'] == 'external'

    def test_import_val_transcript(self):
        """
        Test that importing a video with val transcript, creates a new transcript record.
        """
        edx_video_id = 'test_edx_video_id'
        val_transcript_language_code = 'es'
        val_transcript_provider = 'Cielo24'
        xml_data = """
        <video edx_video_id='{edx_video_id}'>
            <video_asset client_video_id="test_client_video_id" duration="111.0">
                <transcripts>
                    <transcript file_format="srt" language_code="{val_transcript_language_code}" provider="{val_transcript_provider}"/>
                </transcripts>
            </video_asset>
        </video>
        """.format(
            edx_video_id=edx_video_id,
            val_transcript_language_code=val_transcript_language_code,
            val_transcript_provider=val_transcript_provider
        )
        xml_object = etree.fromstring(xml_data)
        module_system = DummyModuleStoreRuntime(load_error_blocks=True)

        # Create static directory in import file system and place transcript files inside it.
        module_system.resources_fs.makedirs(EXPORT_IMPORT_STATIC_DIR, recreate=True)

        # Create VAL transcript.
        create_file_in_fs(
            TRANSCRIPT_FILE_SRT_DATA,
            'test_edx_video_id-es.srt',
            module_system.resources_fs,
            EXPORT_IMPORT_STATIC_DIR
        )

        # Verify edx_video_id is empty before.
        assert self.block.edx_video_id == ''

        video = self.block.parse_xml(xml_object, module_system, None)

        # Verify edx_video_id is populated after the import.
        assert video.edx_video_id != ''

        video_data = get_video_info(video.edx_video_id)
        assert video_data['status'] == 'external'

        # Verify that VAL transcript is imported.
        assert_dict_contains_subset(
            self,
            self.get_video_transcript_data(
                edx_video_id,
                language_code=val_transcript_language_code,
                provider=val_transcript_provider
            ),
            get_video_transcript(video.edx_video_id, val_transcript_language_code),
        )

    @ddt.data(
        (
            'test_sub_id',
            {'en': 'The_Flash.srt'},
            '<transcripts><transcript file_format="srt" language_code="en" provider="Cielo24"/></transcripts>',
            # VAL transcript takes priority
            {
                'video_id': 'test_edx_video_id',
                'language_code': 'en',
                'file_format': 'srt',
                'provider': 'Cielo24'
            }
        ),
        (
            '',
            {'en': 'The_Flash.srt'},
            '<transcripts><transcript file_format="srt" language_code="en" provider="Cielo24"/></transcripts>',
            # VAL transcript takes priority
            {
                'video_id': 'test_edx_video_id',
                'language_code': 'en',
                'file_format': 'srt',
                'provider': 'Cielo24'
            }
        ),
        (
            'test_sub_id',
            {},
            '<transcripts><transcript file_format="srt" language_code="en" provider="Cielo24"/></transcripts>',
            # VAL transcript takes priority
            {
                'video_id': 'test_edx_video_id',
                'language_code': 'en',
                'file_format': 'srt',
                'provider': 'Cielo24'
            }
        ),
        (
            'test_sub_id',
            {'en': 'The_Flash.srt'},
            '',
            # self.sub transcript takes priority
            {
                'video_id': 'test_edx_video_id',
                'language_code': 'en',
                'file_format': 'sjson',
                'provider': 'Custom'
            }
        ),
        (
            '',
            {'en': 'The_Flash.srt'},
            '',
            # self.transcripts would be saved.
            {
                'video_id': 'test_edx_video_id',
                'language_code': 'en',
                'file_format': 'srt',
                'provider': 'Custom'
            }
        )
    )
    @ddt.unpack
    def test_import_val_transcript_priority(self, sub_id, external_transcripts, val_transcripts, expected_transcript):
        """
        Test that importing a video with different type of transcripts for same language,
        creates expected transcript record.
        """
        edx_video_id = 'test_edx_video_id'
        language_code = 'en'

        module_system = DummyModuleStoreRuntime(load_error_blocks=True)

        # Create static directory in import file system and place transcript files inside it.
        module_system.resources_fs.makedirs(EXPORT_IMPORT_STATIC_DIR, recreate=True)

        xml_data = "<video edx_video_id='test_edx_video_id'"

        # Prepare self.sub transcript data.
        if sub_id:
            create_file_in_fs(
                TRANSCRIPT_FILE_SJSON_DATA,
                subs_filename(sub_id, language_code),
                module_system.resources_fs,
                EXPORT_IMPORT_STATIC_DIR
            )
            xml_data += " sub='{sub_id}'".format(
                sub_id=sub_id
            )

        # Prepare self.transcripts transcripts data.
        if external_transcripts:
            create_file_in_fs(
                TRANSCRIPT_FILE_SRT_DATA,
                external_transcripts['en'],
                module_system.resources_fs,
                EXPORT_IMPORT_STATIC_DIR
            )
            xml_data += " transcripts='{transcripts}'".format(
                transcripts=json.dumps(external_transcripts),
            )

        xml_data += '><video_asset client_video_id="test_client_video_id" duration="111.0">'

        # Prepare VAL transcripts data.
        if val_transcripts:
            create_file_in_fs(
                TRANSCRIPT_FILE_SRT_DATA,
                '{edx_video_id}-{language_code}.srt'.format(
                    edx_video_id=edx_video_id,
                    language_code=language_code
                ),
                module_system.resources_fs,
                EXPORT_IMPORT_STATIC_DIR
            )
            xml_data += val_transcripts

        xml_data += '</video_asset></video>'
        xml_object = etree.fromstring(xml_data)

        # Verify edx_video_id is empty before import.
        assert self.block.edx_video_id == ''

        video = self.block.parse_xml(xml_object, module_system, None)

        # Verify edx_video_id is not empty after import.
        assert video.edx_video_id != ''

        video_data = get_video_info(video.edx_video_id)
        assert video_data['status'] == 'external'

        # Verify that correct transcripts are imported.
        assert_dict_contains_subset(
            self,
            expected_transcript,
            get_video_transcript(video.edx_video_id, language_code),
        )

    def test_import_val_data_invalid(self):
        create_profile('mobile')
        module_system = DummyModuleStoreRuntime(load_error_blocks=True)

        # Negative file_size is invalid
        xml_data = """
            <video edx_video_id="test_edx_video_id">
                <video_asset client_video_id="test_client_video_id" duration="111.0">
                    <encoded_video profile="mobile" url="http://example.com/video" file_size="-222" bitrate="333"/>
                </video_asset>
            </video>
        """
        xml_object = etree.fromstring(xml_data)
        with pytest.raises(ValCannotCreateError):
            VideoBlock.parse_xml(xml_object, module_system, None)
        with pytest.raises(ValVideoNotFoundError):
            get_video_info("test_edx_video_id")


class TestVideoWithBumper(TestVideo):  # pylint: disable=test-inherits-tests
    """
    Tests rendered content in presence of video bumper.
    """
    CATEGORY = "video"
    METADATA = {}
    # Use temporary FEATURES in this test without affecting the original
    FEATURES = dict(settings.FEATURES)

    @patch(f'{bumper_utils_path}.get_bumper_settings')
    def test_is_bumper_enabled(self, get_bumper_settings):
        """
        Check that bumper is (not)shown if ENABLE_VIDEO_BUMPER is (False)True

        Assume that bumper settings are correct.
        """
        self.FEATURES.update({
            "SHOW_BUMPER_PERIODICITY": 1,
            "ENABLE_VIDEO_BUMPER": True,
        })

        get_bumper_settings.return_value = {
            "video_id": "edx_video_id",
            "transcripts": {},
        }
        with override_settings(FEATURES=self.FEATURES):
            assert bumper_utils.is_bumper_enabled(self.block)

        self.FEATURES.update({"ENABLE_VIDEO_BUMPER": False})

        with override_settings(FEATURES=self.FEATURES):
            assert not bumper_utils.is_bumper_enabled(self.block)

    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    @patch(f'{bumper_utils_path}.is_bumper_enabled')
    @patch(f'{bumper_utils_path}.get_bumper_settings')
    @patch('edxval.api.get_urls_for_profiles')
    def test_bumper_metadata(
        self, get_url_for_profiles, get_bumper_settings, is_bumper_enabled, mock_render_django_template
    ):
        """
        Test content with rendered bumper metadata.
        """
        get_url_for_profiles.return_value = {
            'desktop_mp4': 'http://test_bumper.mp4',
            'desktop_webm': '',
        }

        get_bumper_settings.return_value = {
            'video_id': 'edx_video_id',
            'transcripts': {},
        }

        is_bumper_enabled.return_value = True

        self.block.student_view(None)
        sources = ['example.mp4', 'example.webm']

        expected_context = {
            'autoadvance_enabled': False,
            'license': None,
            'bumper_metadata': json.dumps(OrderedDict({
                'saveStateUrl': self.block.ajax_url + '/save_user_state',
                'showCaptions': 'true',
                'sources': ['http://test_bumper.mp4'],
                'streams': '',
                'transcriptLanguage': 'en',
                'transcriptLanguages': {'en': 'English'},
                'transcriptTranslationUrl': video_utils.set_query_parameter(
                    self.get_handler_url('transcript', 'translation/__lang__'), 'is_bumper', 1
                ),
                'transcriptAvailableTranslationsUrl': video_utils.set_query_parameter(
                    self.get_handler_url('transcript', 'available_translations'), 'is_bumper', 1
                ),
                "publishCompletionUrl": video_utils.set_query_parameter(
                    self.get_handler_url('publish_completion', ''), 'is_bumper', 1
                ),
            })),
            'block_id': str(self.block.location),
            'course_id': str(self.block.location.course_key),
            'cdn_eval': False,
            'cdn_exp_group': None,
            'display_name': 'A Name',
            'download_video_link': 'example.mp4',
            'is_video_from_same_origin': False,
            'handout': None,
            'hide_downloads': False,
            'is_embed': False,
            'id': self.block.location.html_id(),
            'metadata': json.dumps(OrderedDict({
                'autoAdvance': False,
                'saveStateEnabled': True,
                'saveStateUrl': self.block.ajax_url + '/save_user_state',
                'autoplay': False,
                'streams': '0.75:jNCf2gIqpeE,1.00:ZwkTiUPN0mg,1.25:rsq9auxASqI,1.50:kMyNdzVHHgg',
                'sources': sources,
                'poster': None,
                'duration': None,
                'captionDataDir': None,
                'showCaptions': 'true',
                'generalSpeed': 1.0,
                'speed': None,
                'savedVideoPosition': 0.0,
                'start': 3603.0,
                'end': 3610.0,
                'transcriptLanguage': 'en',
                'transcriptLanguages': OrderedDict({'en': 'English', 'uk': 'Українська'}),
                'ytMetadataEndpoint': '',
                'ytTestTimeout': 1500,
                'ytApiUrl': 'https://www.youtube.com/iframe_api',
                'lmsRootURL': settings.LMS_ROOT_URL,
                'transcriptTranslationUrl': self.get_handler_url('transcript', 'translation/__lang__'),
                'transcriptAvailableTranslationsUrl': self.get_handler_url('transcript', 'available_translations'),
                'aiTranslationsUrl': settings.AI_TRANSLATIONS_API_URL,
                'autohideHtml5': False,
                'recordedYoutubeIsAvailable': True,
                'completionEnabled': False,
                'completionPercentage': 0.95,
                'publishCompletionUrl': self.get_handler_url('publish_completion', ''),
                'prioritizeHls': False,
            })),
            'track': None,
            'transcript_download_format': 'srt',
            'transcript_download_formats_list': [
                {'display_name': 'SubRip (.srt) file', 'value': 'srt'},
                {'display_name': 'Text (.txt) file', 'value': 'txt'}
            ],
            'poster': json.dumps(OrderedDict({
                'url': 'http://img.youtube.com/vi/ZwkTiUPN0mg/0.jpg',
                'type': 'youtube'
            })),
            'transcript_feedback_enabled': False,
            'video_id': '',
        }

        # Get the actual context that was passed to render_django_template
        actual_context = mock_render_django_template.call_args.args[1]

        # Validate and compare contexts
        assert get_context_from_dict(actual_context) == get_context_from_dict(expected_context)


@ddt.ddt
class TestAutoAdvanceVideo(TestVideo):  # lint-amnesty, pylint: disable=test-inherits-tests
    """
    Tests the server side of video auto-advance.
    """
    maxDiff = None
    CATEGORY = "video"
    METADATA = {}
    # Use temporary FEATURES in this test without affecting the original
    FEATURES = dict(settings.FEATURES)

    def prepare_expected_context(self, autoadvanceenabled_flag, autoadvance_flag):
        """
        Build a dictionary with data expected by some operations in this test.
        Only parameters related to auto-advance are variable, rest is fixed.
        """

        context = {
            'autoadvance_enabled': autoadvanceenabled_flag,
            'block_id': str(self.block.location),
            'course_id': str(self.block.location.course_key),
            'license': None,
            'cdn_eval': False,
            'cdn_exp_group': None,
            'display_name': 'A Name',
            'download_video_link': 'example.mp4',
            'is_video_from_same_origin': False,
            'handout': None,
            'hide_downloads': False,
            'is_embed': False,
            'id': self.block.location.html_id(),
            'bumper_metadata': 'null',
            'metadata': json.dumps(OrderedDict({
                'autoAdvance': autoadvance_flag,
                'saveStateEnabled': True,
                'saveStateUrl': self.block.ajax_url + '/save_user_state',
                'autoplay': False,
                'streams': '0.75:jNCf2gIqpeE,1.00:ZwkTiUPN0mg,1.25:rsq9auxASqI,1.50:kMyNdzVHHgg',
                'sources': ['example.mp4', 'example.webm'],
                'duration': None,
                'poster': None,
                'captionDataDir': None,
                'showCaptions': 'true',
                'generalSpeed': 1.0,
                'speed': None,
                'savedVideoPosition': 0.0,
                'start': 3603.0,
                'end': 3610.0,
                'transcriptLanguage': 'en',
                'transcriptLanguages': OrderedDict({'en': 'English', 'uk': 'Українська'}),
                'ytMetadataEndpoint': '',
                'ytTestTimeout': 1500,
                'ytApiUrl': 'https://www.youtube.com/iframe_api',
                'lmsRootURL': settings.LMS_ROOT_URL,
                'transcriptTranslationUrl': self.block.runtime.handler_url(
                    self.block, 'transcript', 'translation/__lang__'
                ).rstrip('/?'),
                'transcriptAvailableTranslationsUrl': self.block.runtime.handler_url(
                    self.block, 'transcript', 'available_translations'
                ).rstrip('/?'),
                'aiTranslationsUrl': settings.AI_TRANSLATIONS_API_URL,
                'autohideHtml5': False,
                'recordedYoutubeIsAvailable': True,
                'completionEnabled': False,
                'completionPercentage': 0.95,
                'publishCompletionUrl': self.get_handler_url('publish_completion', ''),
                'prioritizeHls': False,
            })),
            'track': None,
            'transcript_download_format': 'srt',
            'transcript_download_formats_list': [
                {'display_name': 'SubRip (.srt) file', 'value': 'srt'},
                {'display_name': 'Text (.txt) file', 'value': 'txt'}
            ],
            'poster': 'null',
            'transcript_feedback_enabled': False,
            'video_id': '',
        }
        return context

    def assert_content_matches_expectations(
        self, autoadvanceenabled_must_be, autoadvance_must_be, mock_render_django_template
    ):
        """
        Check (assert) that loading video.html produces content that corresponds
        to the passed context.
        Helper function to avoid code repetition.
        """

        with override_settings(FEATURES=self.FEATURES):
            self.block.student_view(None)

        expected_context = self.prepare_expected_context(
            autoadvanceenabled_flag=autoadvanceenabled_must_be,
            autoadvance_flag=autoadvance_must_be,
        )

        # Get the actual context that was passed to render_django_template
        actual_context = mock_render_django_template.call_args.args[1]

        # Validate and compare contexts
        assert get_context_from_dict(actual_context) == get_context_from_dict(expected_context)

    def change_course_setting_autoadvance(self, new_value):
        """
        Change the .video_auto_advance course setting (a.k.a. advanced setting).
        This avoids doing .save(), and instead modifies the instance directly.
        Based on test code for video_bumper setting.
        """
        # This first render is done to initialize the instance
        self.block.render(STUDENT_VIEW)
        self.block.video_auto_advance = new_value
        self.block._reset_dirty_field(self.block.fields['video_auto_advance'])  # pylint: disable=protected-access
        # After this step, render() should see the new value
        # e.g. use self.block.student_view(None).content

    @ddt.data(
        (False, False),
        (False, True),
        (True, False),
        (True, True),
    )
    @ddt.unpack
    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    def test_is_autoadvance_available_and_enabled(self, global_setting, course_setting, mock_render_django_template):
        """
        Check that the autoadvance is not available when it is disabled via feature flag
        (ENABLE_AUTOADVANCE_VIDEOS set to False) or by the course setting.
        It checks that:
        - only when the feature flag and the course setting are True (at the same time)
          the controls are visible
        - in that case (when the controls are visible) the video will autoadvance
          (because that's the default), in other cases it won't
        """
        self.FEATURES.update({"ENABLE_AUTOADVANCE_VIDEOS": global_setting})
        self.change_course_setting_autoadvance(new_value=course_setting)

        self.assert_content_matches_expectations(
            autoadvanceenabled_must_be=(global_setting and course_setting),
            autoadvance_must_be=(global_setting and course_setting),
            mock_render_django_template=mock_render_django_template,
        )
