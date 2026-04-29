""" Tests for transcripts_utils. """

import copy
import json
import re
import tempfile
import textwrap
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

import ddt
import pytest
from django.conf import settings
from django.test.utils import override_settings
from django.utils import translation
from xblocks_contrib.video.exceptions import TranscriptsGenerationException

from cms.djangoapps.contentstore.tests.utils import setup_caption_responses
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.video_config import transcripts_utils  # pylint: disable=wrong-import-order
from xmodule.contentstore.content import StaticContent  # pylint: disable=wrong-import-order
from xmodule.contentstore.django import contentstore  # pylint: disable=wrong-import-order
from xmodule.exceptions import NotFoundError  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)

TEST_DATA_CONTENTSTORE = copy.deepcopy(settings.CONTENTSTORE)
TEST_DATA_CONTENTSTORE['DOC_STORE_CONFIG']['db'] = 'test_xcontent_%s' % uuid4().hex  # noqa: UP031


class TestGenerateSubs(unittest.TestCase):
    """Tests for `generate_subs` function."""
    def setUp(self):
        super().setUp()

        self.source_subs = {
            'start': [100, 200, 240, 390, 1000],
            'end': [200, 240, 380, 1000, 1500],
            'text': [
                'subs #1',
                'subs #2',
                'subs #3',
                'subs #4',
                'subs #5'
            ]
        }

    def test_generate_subs_increase_speed(self):
        subs = transcripts_utils.generate_subs(2, 1, self.source_subs)
        self.assertDictEqual(  # noqa: PT009
            subs,
            {
                'start': [200, 400, 480, 780, 2000],
                'end': [400, 480, 760, 2000, 3000],
                'text': ['subs #1', 'subs #2', 'subs #3', 'subs #4', 'subs #5']
            }
        )

    def test_generate_subs_decrease_speed_1(self):
        subs = transcripts_utils.generate_subs(0.5, 1, self.source_subs)
        self.assertDictEqual(  # noqa: PT009
            subs,
            {
                'start': [50, 100, 120, 195, 500],
                'end': [100, 120, 190, 500, 750],
                'text': ['subs #1', 'subs #2', 'subs #3', 'subs #4', 'subs #5']
            }
        )

    def test_generate_subs_decrease_speed_2(self):
        """Test for correct devision during `generate_subs` process."""
        subs = transcripts_utils.generate_subs(1, 2, self.source_subs)
        self.assertDictEqual(  # noqa: PT009
            subs,
            {
                'start': [50, 100, 120, 195, 500],
                'end': [100, 120, 190, 500, 750],
                'text': ['subs #1', 'subs #2', 'subs #3', 'subs #4', 'subs #5']
            }
        )


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class TestSaveSubsToStore(SharedModuleStoreTestCase):
    """Tests for `save_subs_to_store` function."""

    org = 'MITx'
    number = '999'
    display_name = 'Test course'

    def clear_subs_content(self):
        """Remove, if subtitles content exists."""
        for content_location in [self.content_location, self.content_copied_location]:
            try:
                content = contentstore().find(content_location)
                contentstore().delete(content.location)
            except NotFoundError:
                pass

    @classmethod
    def sub_id_to_location(cls, sub_id):
        """
        A helper to compute a static file location from a subtitle id.
        """
        return StaticContent.compute_location(cls.course.id, f'subs_{sub_id}.srt.sjson')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org=cls.org, number=cls.number, display_name=cls.display_name)

        cls.subs = {
            'start': [100, 200, 240, 390, 1000],
            'end': [200, 240, 380, 1000, 1500],
            'text': [
                'subs #1',
                'subs #2',
                'subs #3',
                'subs #4',
                'subs #5'
            ]
        }

        # Prefix it to ensure that unicode filenames are allowed
        cls.subs_id = f'uniçøde_{uuid4()}'
        cls.subs_copied_id = f'cøpy_{uuid4()}'

        cls.content_location = cls.sub_id_to_location(cls.subs_id)
        cls.content_copied_location = cls.sub_id_to_location(cls.subs_copied_id)

        # incorrect subs
        cls.unjsonable_subs = {1}  # set can't be serialized

        cls.unjsonable_subs_id = str(uuid4())
        cls.content_location_unjsonable = cls.sub_id_to_location(cls.unjsonable_subs_id)

    def setUp(self):
        super().setUp()
        self.addCleanup(self.clear_subs_content)
        self.clear_subs_content()

    def test_save_subs_to_store(self):
        with self.assertRaises(NotFoundError):  # noqa: PT027
            contentstore().find(self.content_location)

        result_location = transcripts_utils.save_subs_to_store(
            self.subs,
            self.subs_id,
            self.course)

        self.assertTrue(contentstore().find(self.content_location))  # noqa: PT009
        self.assertEqual(result_location, self.content_location)  # noqa: PT009

    def test_save_unjsonable_subs_to_store(self):
        """
        Ensures that subs, that can't be dumped, can't be found later.
        """
        with self.assertRaises(NotFoundError):  # noqa: PT027
            contentstore().find(self.content_location_unjsonable)

        with self.assertRaises(TypeError):  # noqa: PT027
            transcripts_utils.save_subs_to_store(
                self.unjsonable_subs,
                self.unjsonable_subs_id,
                self.course)

        with self.assertRaises(NotFoundError):  # noqa: PT027
            contentstore().find(self.content_location_unjsonable)


class TestYoutubeSubsBase(SharedModuleStoreTestCase):
    """
    Base class for tests of Youtube subs.  Using override_settings and
    a setUpClass() override in a test class which is inherited by another
    test class doesn't work well with pytest-django.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org=cls.org, number=cls.number, display_name=cls.display_name)  # pylint: disable=no-member


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class TestDownloadYoutubeSubs(TestYoutubeSubsBase):
    """
    Tests for `download_youtube_subs` function.
    """

    org = 'MITx'
    number = '999'
    display_name = 'Test course'

    def clear_sub_content(self, subs_id):
        """
        Remove, if subtitle content exists.
        """
        filename = f'subs_{subs_id}.srt.sjson'
        content_location = StaticContent.compute_location(self.course.id, filename)
        try:
            content = contentstore().find(content_location)
            contentstore().delete(content.location)
        except NotFoundError:
            pass

    def clear_subs_content(self, youtube_subs):
        """
        Remove, if subtitles content exists.

        youtube_subs: dict of '{speed: youtube_id}' format for different speeds.
        """
        for subs_id in youtube_subs.values():
            self.clear_sub_content(subs_id)

    def test_success_downloading_subs(self):

        caption_response_string = textwrap.dedent("""<?xml version="1.0" encoding="utf-8" ?>
                <transcript>
                    <text start="0" dur="0.27"></text>
                    <text start="0.27" dur="2.45">Test text 1.</text>
                    <text start="2.72">Test text 2.</text>
                    <text start="5.43" dur="1.73">Test text 3.</text>
                </transcript>
        """)
        good_youtube_sub = 'good_id_2'
        self.clear_sub_content(good_youtube_sub)

        language_code = 'en'
        with patch('openedx.core.djangoapps.video_config.transcripts_utils.requests.get') as mock_get:
            setup_caption_responses(mock_get, language_code, caption_response_string)
            transcripts_utils.download_youtube_subs(good_youtube_sub, self.course, settings)

            self.assertEqual(2, len(mock_get.mock_calls))  # noqa: PT009
            args, kwargs = mock_get.call_args_list[0]
            self.assertEqual(args[0], 'https://www.youtube.com/watch?v=good_id_2')  # noqa: PT009
            args, kwargs = mock_get.call_args_list[1]
            self.assertTrue(re.match(r"^https://www\.youtube\.com/api/timedtext.*", args[0]))  # noqa: PT009

    def test_subs_for_html5_vid_with_periods(self):
        """
        This is to verify a fix whereby subtitle files uploaded against
        a HTML5 video that contains periods in the name causes
        incorrect subs name parsing
        """
        html5_ids = transcripts_utils.get_html5_ids(['foo.mp4', 'foo.1.bar.mp4', 'foo/bar/baz.1.4.mp4', 'foo'])
        self.assertEqual(4, len(html5_ids))  # noqa: PT009
        self.assertEqual(html5_ids[0], 'foo')  # noqa: PT009
        self.assertEqual(html5_ids[1], 'foo.1.bar')  # noqa: PT009
        self.assertEqual(html5_ids[2], 'baz.1.4')  # noqa: PT009
        self.assertEqual(html5_ids[3], 'foo')  # noqa: PT009

    @patch('openedx.core.djangoapps.video_config.transcripts_utils.requests.get')
    def test_fail_downloading_subs(self, mock_get):

        track_status_code = 404
        setup_caption_responses(mock_get, 'en', 'Error 404', track_status_code)

        bad_youtube_sub = 'BAD_YOUTUBE_ID2'
        self.clear_sub_content(bad_youtube_sub)

        with self.assertRaises(transcripts_utils.GetTranscriptsFromYouTubeException):  # noqa: PT027
            transcripts_utils.download_youtube_subs(bad_youtube_sub, self.course, settings)

    def test_success_downloading_chinese_transcripts(self):

        # Disabled 11/14/13
        # This test is flaky because it performs an HTTP request on an external service
        # Re-enable when `requests.get` is patched using `mock.patch`
        pytest.skip()

        good_youtube_sub = 'j_jEn79vS3g'  # Chinese, utf-8
        self.clear_sub_content(good_youtube_sub)

        # Check transcripts_utils.GetTranscriptsFromYouTubeException not thrown
        transcripts_utils.download_youtube_subs(good_youtube_sub, self.course, settings)

        # Check assets status after importing subtitles.
        for subs_id in good_youtube_subs.values():  # pylint: disable=undefined-variable  # noqa: F821
            filename = f'subs_{subs_id}.srt.sjson'
            content_location = StaticContent.compute_location(
                self.course.id, filename
            )
            self.assertTrue(contentstore().find(content_location))  # noqa: PT009

        self.clear_sub_content(good_youtube_sub)


class TestGenerateSubsFromSource(TestDownloadYoutubeSubs):  # pylint: disable=test-inherits-tests
    """Tests for `generate_subs_from_source` function."""

    def test_success_generating_subs(self):
        youtube_subs = {
            0.5: 'JMD_ifUUfsU',
            1.0: 'hI10vDNYz4M',
            2.0: 'AKqURZnYqpk'
        }
        srt_filedata = textwrap.dedent("""
            1
            00:00:10,500 --> 00:00:13,000
            Elephant's Dream

            2
            00:00:15,000 --> 00:00:18,000
            At the left we can see...
        """)
        self.clear_subs_content(youtube_subs)

        # Check TranscriptsGenerationException not thrown.
        # Also checks that uppercase file extensions are supported.
        transcripts_utils.generate_subs_from_source(youtube_subs, 'SRT', srt_filedata, self.course)

        # Check assets status after importing subtitles.
        for subs_id in youtube_subs.values():
            filename = f'subs_{subs_id}.srt.sjson'
            content_location = StaticContent.compute_location(
                self.course.id, filename
            )
            self.assertTrue(contentstore().find(content_location))  # noqa: PT009

        self.clear_subs_content(youtube_subs)

    def test_fail_bad_subs_type(self):
        youtube_subs = {
            0.5: 'JMD_ifUUfsU',
            1.0: 'hI10vDNYz4M',
            2.0: 'AKqURZnYqpk'
        }

        srt_filedata = textwrap.dedent("""
            1
            00:00:10,500 --> 00:00:13,000
            Elephant's Dream

            2
            00:00:15,000 --> 00:00:18,000
            At the left we can see...
        """)

        with self.assertRaises(TranscriptsGenerationException) as cm:  # noqa: PT027
            transcripts_utils.generate_subs_from_source(youtube_subs, 'BAD_FORMAT', srt_filedata, self.course)
        exception_message = str(cm.exception)
        self.assertEqual(exception_message, "We support only SubRip (*.srt) transcripts format.")  # noqa: PT009

    def test_fail_bad_subs_filedata(self):
        youtube_subs = {
            0.5: 'JMD_ifUUfsU',
            1.0: 'hI10vDNYz4M',
            2.0: 'AKqURZnYqpk'
        }

        srt_filedata = """BAD_DATA"""

        with self.assertRaises(TranscriptsGenerationException) as cm:  # noqa: PT027
            transcripts_utils.generate_subs_from_source(youtube_subs, 'srt', srt_filedata, self.course)
        exception_message = str(cm.exception)
        self.assertEqual(exception_message, "Something wrong with SubRip transcripts file during parsing.")  # noqa: PT009  # pylint: disable=line-too-long


class TestGenerateSrtFromSjson(TestDownloadYoutubeSubs):  # pylint: disable=test-inherits-tests
    """Tests for `generate_srt_from_sjson` function."""

    def test_success_generating_subs(self):
        sjson_subs = {
            'start': [100, 200, 240, 390, 54000],
            'end': [200, 240, 380, 1000, 78400],
            'text': [
                'subs #1',
                'subs #2',
                'subs #3',
                'subs #4',
                'subs #5'
            ]
        }
        srt_subs = transcripts_utils.generate_srt_from_sjson(sjson_subs, 1)
        self.assertTrue(srt_subs)  # noqa: PT009
        expected_subs = [
            '00:00:00,100 --> 00:00:00,200\nsubs #1',
            '00:00:00,200 --> 00:00:00,240\nsubs #2',
            '00:00:00,240 --> 00:00:00,380\nsubs #3',
            '00:00:00,390 --> 00:00:01,000\nsubs #4',
            '00:00:54,000 --> 00:01:18,400\nsubs #5',
        ]

        for sub in expected_subs:
            self.assertIn(sub, srt_subs)  # noqa: PT009

    def test_success_generating_subs_speed_up(self):
        sjson_subs = {
            'start': [100, 200, 240, 390, 54000],
            'end': [200, 240, 380, 1000, 78400],
            'text': [
                'subs #1',
                'subs #2',
                'subs #3',
                'subs #4',
                'subs #5'
            ]
        }
        srt_subs = transcripts_utils.generate_srt_from_sjson(sjson_subs, 0.5)
        self.assertTrue(srt_subs)  # noqa: PT009
        expected_subs = [
            '00:00:00,050 --> 00:00:00,100\nsubs #1',
            '00:00:00,100 --> 00:00:00,120\nsubs #2',
            '00:00:00,120 --> 00:00:00,190\nsubs #3',
            '00:00:00,195 --> 00:00:00,500\nsubs #4',
            '00:00:27,000 --> 00:00:39,200\nsubs #5',
        ]
        for sub in expected_subs:
            self.assertIn(sub, srt_subs)  # noqa: PT009

    def test_success_generating_subs_speed_down(self):
        sjson_subs = {
            'start': [100, 200, 240, 390, 54000],
            'end': [200, 240, 380, 1000, 78400],
            'text': [
                'subs #1',
                'subs #2',
                'subs #3',
                'subs #4',
                'subs #5'
            ]
        }
        srt_subs = transcripts_utils.generate_srt_from_sjson(sjson_subs, 2)
        self.assertTrue(srt_subs)  # noqa: PT009

        expected_subs = [
            '00:00:00,200 --> 00:00:00,400\nsubs #1',
            '00:00:00,400 --> 00:00:00,480\nsubs #2',
            '00:00:00,480 --> 00:00:00,760\nsubs #3',
            '00:00:00,780 --> 00:00:02,000\nsubs #4',
            '00:01:48,000 --> 00:02:36,800\nsubs #5',
        ]
        for sub in expected_subs:
            self.assertIn(sub, srt_subs)  # noqa: PT009

    def test_fail_generating_subs(self):
        sjson_subs = {
            'start': [100, 200],
            'end': [100],
            'text': [
                'subs #1',
                'subs #2'
            ]
        }
        srt_subs = transcripts_utils.generate_srt_from_sjson(sjson_subs, 1)
        self.assertFalse(srt_subs)  # noqa: PT009


class TestYoutubeTranscripts(unittest.TestCase):
    """
    Tests for checking right datastructure returning when using youtube api.
    """
    @patch('openedx.core.djangoapps.video_config.transcripts_utils.requests.get')
    def test_youtube_bad_status_code(self, mock_get):
        track_status_code = 404
        setup_caption_responses(mock_get, 'en', 'test', track_status_code)
        youtube_id = 'bad_youtube_id'
        with self.assertRaises(transcripts_utils.GetTranscriptsFromYouTubeException):  # noqa: PT027
            link = transcripts_utils.get_transcript_links_from_youtube(youtube_id, settings, translation)
            transcripts_utils.get_transcript_from_youtube(link, youtube_id, translation)

    @patch('openedx.core.djangoapps.video_config.transcripts_utils.requests.get')
    def test_youtube_empty_text(self, mock_get):
        setup_caption_responses(mock_get, 'en', '')
        youtube_id = 'bad_youtube_id'
        with self.assertRaises(transcripts_utils.GetTranscriptsFromYouTubeException):  # noqa: PT027
            link = transcripts_utils.get_transcript_links_from_youtube(youtube_id, settings, translation)
            transcripts_utils.get_transcript_from_youtube(link, youtube_id, translation)

    def test_youtube_good_result(self):
        caption_response_string = textwrap.dedent("""<?xml version="1.0" encoding="utf-8" ?>
                <transcript>
                    <text start="0" dur="0.27"></text>
                    <text start="0.27" dur="2.45">Test text 1.</text>
                    <text start="2.72">Test text 2.</text>
                    <text start="5.43" dur="1.73">Test text 3.</text>
                </transcript>
        """)
        expected_transcripts = {
            'start': [270, 2720, 5430],
            'end': [2720, 2720, 7160],
            'text': ['Test text 1.', 'Test text 2.', 'Test text 3.']
        }
        youtube_id = 'good_youtube_id'
        language_code = 'en'
        with patch('openedx.core.djangoapps.video_config.transcripts_utils.requests.get') as mock_get:
            setup_caption_responses(mock_get, language_code, caption_response_string)
            link = transcripts_utils.get_transcript_links_from_youtube(youtube_id, settings, translation)
            transcripts = transcripts_utils.get_transcript_from_youtube(link['en'], youtube_id, translation)

        self.assertEqual(transcripts, expected_transcripts)  # noqa: PT009
        self.assertEqual(2, len(mock_get.mock_calls))  # noqa: PT009
        args, kwargs = mock_get.call_args_list[0]
        self.assertEqual(args[0], f'https://www.youtube.com/watch?v={youtube_id}')  # noqa: PT009
        args, kwargs = mock_get.call_args_list[1]
        self.assertTrue(re.match(r"^https://www\.youtube\.com/api/timedtext.*", args[0]))  # noqa: PT009


class TestTranscript(unittest.TestCase):
    """
    Tests for Transcript class e.g. different transcript conversions.
    """
    def setUp(self):
        super().setUp()

        self.srt_transcript = textwrap.dedent("""\
            0
            00:00:10,500 --> 00:00:13,000
            Elephant&#39;s Dream

            1
            00:00:15,000 --> 00:00:18,000
            At the left we can see...

        """)

        self.sjson_transcript = textwrap.dedent("""\
            {
                "start": [
                    10500,
                    15000
                ],
                "end": [
                    13000,
                    18000
                ],
                "text": [
                    "Elephant&#39;s Dream",
                    "At the left we can see..."
                ]
            }
        """)

        self.txt_transcript = "Elephant's Dream\nAt the left we can see..."

    def test_convert_srt_to_txt(self):
        """
        Tests that the srt transcript is successfully converted into txt format.
        """
        expected = self.txt_transcript
        actual = transcripts_utils.Transcript.convert(self.srt_transcript, 'srt', 'txt')
        self.assertEqual(actual, expected)  # noqa: PT009

    def test_convert_srt_to_srt(self):
        """
        Tests that srt to srt conversion works as expected.
        """
        expected = self.srt_transcript
        actual = transcripts_utils.Transcript.convert(self.srt_transcript, 'srt', 'srt')
        self.assertEqual(actual, expected)  # noqa: PT009

    def test_convert_sjson_to_txt(self):
        """
        Tests that the sjson transcript is successfully converted into txt format.
        """
        expected = self.txt_transcript
        actual = transcripts_utils.Transcript.convert(self.sjson_transcript, 'sjson', 'txt')
        self.assertEqual(actual, expected)  # noqa: PT009

    def test_convert_sjson_to_srt(self):
        """
        Tests that the sjson transcript is successfully converted into srt format.
        """
        expected = self.srt_transcript
        actual = transcripts_utils.Transcript.convert(self.sjson_transcript, 'sjson', 'srt')
        self.assertEqual(actual, expected)  # noqa: PT009

    def test_convert_srt_to_sjson(self):
        """
        Tests that the srt transcript is successfully converted into sjson format.
        """
        expected = self.sjson_transcript
        actual = transcripts_utils.Transcript.convert(self.srt_transcript, 'srt', 'sjson')
        self.assertDictEqual(json.loads(actual), json.loads(expected))  # noqa: PT009

    def test_convert_invalid_srt_to_sjson(self):
        """
        Tests that TranscriptsGenerationException was raises on trying
        to convert invalid srt transcript to sjson.
        """
        invalid_srt_transcript = 'invalid SubRip file content'
        with self.assertRaises(TranscriptsGenerationException):  # noqa: PT027
            transcripts_utils.Transcript.convert(invalid_srt_transcript, 'srt', 'sjson')

    def test_convert_invalid_invalid_sjson_to_srt(self):
        invalid_content = "Text with special character /\"\'\b\f\t\r\n."
        error_transcript = {"start": [1], "end": [2], "text": ["An error occured obtaining the transcript."]}
        assert transcripts_utils.Transcript.convert(invalid_content, 'sjson', 'txt') == error_transcript['text'][0]
        assert error_transcript["text"][0] in transcripts_utils.Transcript.convert(invalid_content, 'sjson', 'srt')

    def test_dummy_non_existent_transcript(self):
        """
        Test `Transcript.asset` raises `NotFoundError` for dummy non-existent transcript.
        """
        with self.assertRaises(NotFoundError):  # noqa: PT027
            transcripts_utils.Transcript.asset(None, transcripts_utils.NON_EXISTENT_TRANSCRIPT)

        with self.assertRaises(NotFoundError):  # noqa: PT027
            transcripts_utils.Transcript.asset(None, None, filename=transcripts_utils.NON_EXISTENT_TRANSCRIPT)

    def test_latin1(self):
        """
        Test to make sure Latin-1 encoded transcripts work.
        """
        latin1_sjson_str = textwrap.dedent("""\
            {
                "start": [
                    10500,
                    15000
                ],
                "end": [
                    13000,
                    18000
                ],
                "text": [
                    "û",
                    "At the left we can see..."
                ]
            }
        """)
        latin1_sjson_bytes = latin1_sjson_str.encode('latin-1')

        expected_result = textwrap.dedent("""\
            0
            00:00:10,500 --> 00:00:13,000
            û

            1
            00:00:15,000 --> 00:00:18,000
            At the left we can see...

        """)
        result = transcripts_utils.Transcript.convert(latin1_sjson_bytes, 'sjson', 'srt')
        assert result == expected_result


class TestSubsFilename(unittest.TestCase):
    """
    Tests for subs_filename funtion.
    """

    def test_unicode(self):
        name = transcripts_utils.subs_filename("˙∆©ƒƒƒ")
        self.assertEqual(name, 'subs_˙∆©ƒƒƒ.srt.sjson')  # noqa: PT009
        name = transcripts_utils.subs_filename("˙∆©ƒƒƒ", 'uk')
        self.assertEqual(name, 'uk_subs_˙∆©ƒƒƒ.srt.sjson')  # noqa: PT009


@ddt.ddt
class TestVideoIdsInfo(unittest.TestCase):
    """
    Tests for `get_video_ids_info`.
    """
    @ddt.data(
        {
            'edx_video_id': '000-000-000',
            'youtube_id_1_0': '12as34',
            'html5_sources': [
                'www.abc.com/foo.mp4', 'www.abc.com/bar.webm', 'foo/bar/baz.m3u8'
            ],
            'expected_result': (False, ['000-000-000', '12as34', 'foo', 'bar', 'baz'])
        },
        {
            'edx_video_id': '',
            'youtube_id_1_0': '12as34',
            'html5_sources': [
                'www.abc.com/foo.mp4', 'www.abc.com/bar.webm', 'foo/bar/baz.m3u8'
            ],
            'expected_result': (True, ['12as34', 'foo', 'bar', 'baz'])
        },
        {
            'edx_video_id': '',
            'youtube_id_1_0': '',
            'html5_sources': [
                'www.abc.com/foo.mp4', 'www.abc.com/bar.webm',
            ],
            'expected_result': (True, ['foo', 'bar'])
        },
    )
    @ddt.unpack
    def test_get_video_ids_info(self, edx_video_id, youtube_id_1_0, html5_sources, expected_result):
        """
        Verify that `get_video_ids_info` works as expected.
        """
        actual_result = transcripts_utils.get_video_ids_info(edx_video_id, youtube_id_1_0, html5_sources)
        self.assertEqual(actual_result, expected_result)  # noqa: PT009


@ddt.ddt
class TestGetTranscript(SharedModuleStoreTestCase):
    """Tests for `get_transcript` function."""

    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create()

        self.subs_id = 'video_101'

        self.subs_sjson = {
            'start': [100, 200, 240, 390, 1000],
            'end': [200, 240, 380, 1000, 1500],
            'text': [
                'subs #1',
                'subs #2',
                'subs #3',
                'subs #4',
                'subs #5'
            ]
        }

        self.subs_srt = transcripts_utils.Transcript.convert(json.dumps(self.subs_sjson), 'sjson', 'srt')

        self.subs = {
            'en': self.subs_srt,
            'ur': transcripts_utils.Transcript.convert(json.dumps(self.subs_sjson), 'sjson', 'srt'),
        }

        self.srt_mime_type = transcripts_utils.Transcript.mime_types[transcripts_utils.Transcript.SRT]
        self.sjson_mime_type = transcripts_utils.Transcript.mime_types[transcripts_utils.Transcript.SJSON]

        self.user = UserFactory.create()
        self.vertical = BlockFactory.create(category='vertical', parent_location=self.course.location)
        self.video = BlockFactory.create(
            category='video',
            parent_location=self.vertical.location,
            edx_video_id='1234-5678-90'
        )

    def create_transcript(self, subs_id, language='en', filename='video.srt', youtube_id_1_0='', html5_sources=None):
        """
        create transcript.
        """
        transcripts = {}
        if language != 'en':
            transcripts = {language: filename}

        html5_sources = html5_sources or []
        self.video = BlockFactory.create(
            category='video',
            parent_location=self.vertical.location,
            sub=subs_id,
            youtube_id_1_0=youtube_id_1_0,
            transcripts=transcripts,
            edx_video_id='1234-5678-90',
            html5_sources=html5_sources
        )

        possible_subs = [subs_id, youtube_id_1_0] + transcripts_utils.get_html5_ids(html5_sources)
        for possible_sub in possible_subs:
            if possible_sub:
                transcripts_utils.save_subs_to_store(
                    self.subs_sjson,
                    possible_sub,
                    self.video,
                    language=language,
                )

    def create_srt_file(self, content):
        """
        Create srt file.
        """
        srt_file = tempfile.NamedTemporaryFile(suffix=".srt")  # pylint: disable=consider-using-with
        srt_file.content_type = transcripts_utils.Transcript.SRT
        srt_file.write(content)
        srt_file.seek(0)
        return srt_file

    def upload_file(self, subs_file, location, filename):
        """
        Upload a file in content store.

        Arguments:
            subs_file (File): pointer to file to be uploaded
            location (Locator): Item location
            filename (unicode): Name of file to be uploaded
        """
        mime_type = subs_file.content_type
        content_location = StaticContent.compute_location(
            location.course_key, filename
        )
        content = StaticContent(content_location, filename, mime_type, subs_file.read())
        contentstore().save(content)

    @ddt.data(
        # en lang does not exist so NotFoundError will be raised
        ('en',),
        # ur lang does not exist so KeyError and then NotFoundError will be raised
        ('ur',),
    )
    @ddt.unpack
    def test_get_transcript_not_found(self, lang):
        """
        Verify that `NotFoundError` exception is raised when transcript is not found in both the content store and val.
        """
        with self.assertRaises(NotFoundError):  # noqa: PT027
            transcripts_utils.get_transcript(
                self.video,
                lang=lang
            )

    @ddt.data(
        # video.sub transcript
        {
            'language': 'en',
            'subs_id': 'video_101',
            'youtube_id_1_0': '',
            'html5_sources': [],
            'expected_filename': 'en_video_101.srt',
        },
        # if video.sub is present, rest will be skipped.
        {
            'language': 'en',
            'subs_id': 'video_101',
            'youtube_id_1_0': 'test_yt_id',
            'html5_sources': ['www.abc.com/foo.mp4'],
            'expected_filename': 'en_video_101.srt',
        },
        # video.youtube_id_1_0 transcript
        {
            'language': 'en',
            'subs_id': '',
            'youtube_id_1_0': 'test_yt_id',
            'html5_sources': [],
            'expected_filename': 'en_test_yt_id.srt',
        },
        # video.html5_sources transcript
        {
            'language': 'en',
            'subs_id': '',
            'youtube_id_1_0': '',
            'html5_sources': ['www.abc.com/foo.mp4'],
            'expected_filename': 'en_foo.srt',
        },
        # non-english transcript
        {
            'language': 'ur',
            'subs_id': '',
            'youtube_id_1_0': '',
            'html5_sources': [],
            'expected_filename': 'ur_video_101.srt',
        },
    )
    @ddt.unpack
    def test_get_transcript_from_contentstore(
        self,
        language,
        subs_id,
        youtube_id_1_0,
        html5_sources,
        expected_filename
    ):
        """
        Verify that `get_transcript` function returns correct data when transcript is in content store.
        """
        base_filename = 'video_101.srt'
        self.upload_file(self.create_srt_file(self.subs_srt.encode('utf-8')), self.video.location, base_filename)
        self.create_transcript(subs_id, language, base_filename, youtube_id_1_0, html5_sources)
        content, file_name, mimetype = transcripts_utils.get_transcript(
            self.video,
            language
        )

        self.assertEqual(content, self.subs[language])  # noqa: PT009
        self.assertEqual(file_name, expected_filename)  # noqa: PT009
        self.assertEqual(mimetype, self.srt_mime_type)  # noqa: PT009

    def test_get_transcript_from_content_store_for_ur(self):
        """
        Verify that `get_transcript` function returns correct data for non-english when transcript is in content store.
        """
        language = 'ur'
        self.create_transcript(self.subs_id, language)
        content, filename, mimetype = transcripts_utils.get_transcript(
            self.video,
            language,
            output_format=transcripts_utils.Transcript.SJSON
        )

        self.assertEqual(json.loads(content), self.subs_sjson)  # noqa: PT009
        self.assertEqual(filename, 'ur_video_101.sjson')  # noqa: PT009
        self.assertEqual(mimetype, self.sjson_mime_type)  # noqa: PT009

    @patch('openedx.core.djangoapps.video_config.transcripts_utils.get_video_transcript_content')
    def test_get_transcript_from_val(self, mock_get_video_transcript_content):
        """
        Verify that `get_transcript` function returns correct data when transcript is in val.
        """
        mock_get_video_transcript_content.return_value = {
            'content': json.dumps(self.subs_sjson),
            'file_name': 'edx.sjson'
        }

        content, filename, mimetype = transcripts_utils.get_transcript(
            self.video,
        )
        self.assertEqual(content, self.subs_srt)  # noqa: PT009
        self.assertEqual(filename, 'edx.srt')  # noqa: PT009
        self.assertEqual(mimetype, self.srt_mime_type)  # noqa: PT009

    def test_get_transcript_invalid_format(self):
        """
        Verify that `get_transcript` raises correct exception if transcript format is invalid.
        """
        with self.assertRaises(NotFoundError) as invalid_format_exception:  # noqa: PT027
            transcripts_utils.get_transcript(
                self.video,
                'ur',
                output_format='mpeg'
            )

        exception_message = str(invalid_format_exception.exception)
        self.assertEqual(exception_message, 'Invalid transcript format `mpeg`')  # noqa: PT009

    def test_get_transcript_no_content(self):
        """
        Verify that `get_transcript` function returns correct exception when transcript content is empty.
        """
        self.upload_file(self.create_srt_file(b''), self.video.location, 'ur_video_101.srt')
        self.create_transcript('', 'ur', 'ur_video_101.srt')

        with self.assertRaises(NotFoundError) as no_content_exception:  # noqa: PT027
            transcripts_utils.get_transcript(
                self.video,
                'ur'
            )

        exception_message = str(no_content_exception.exception)
        self.assertEqual(exception_message, 'No transcript content')  # noqa: PT009

    def test_get_transcript_no_en_transcript(self):
        """
        Verify that `get_transcript` function returns correct exception when no transcript exists for `en`.
        """
        self.video.youtube_id_1_0 = ''
        self.store.update_item(self.video, self.user.id)
        with self.assertRaises(NotFoundError) as no_en_transcript_exception:  # noqa: PT027
            transcripts_utils.get_transcript(
                self.video,
                'en'
            )

        exception_message = str(no_en_transcript_exception.exception)
        self.assertEqual(exception_message, 'No transcript for `en` language')  # noqa: PT009

    @patch('openedx.core.djangoapps.video_config.transcripts_utils.edxval_api.get_video_transcript_data')
    def test_get_transcript_incorrect_json_(self, mock_get_video_transcript_data):
        """
        Verify that `get transcript` function returns a working json file if the original throws an error
        """
        error_transcript = {"start": [1], "end": [2], "text": ["An error occured obtaining the transcript."]}
        mock_get_video_transcript_data.side_effect = ValueError
        content, _, _ = transcripts_utils.get_transcript(self.video, 'zh')
        assert error_transcript["text"][0] in content

    @ddt.data(
        TranscriptsGenerationException,
        UnicodeDecodeError('aliencodec', b'\x02\x01', 1, 2, 'alien codec found!')
    )
    @patch('openedx.core.djangoapps.video_config.transcripts_utils.Transcript')
    def test_get_transcript_val_exceptions(self, exception_to_raise, mock_Transcript):
        """
        Verify that `get_transcript_from_val` function raises `NotFoundError` when specified exceptions raised.
        """
        mock_Transcript.convert.side_effect = exception_to_raise
        transcripts_info = self.video.get_transcripts_info()
        lang = self.video.get_default_transcript_language(transcripts_info)
        edx_video_id = transcripts_utils.clean_video_id(self.video.edx_video_id)
        with self.assertRaises(NotFoundError):  # noqa: PT027
            transcripts_utils.get_transcript_from_val(
                edx_video_id,
                lang=lang,
                output_format=transcripts_utils.Transcript.SRT
            )

    @ddt.data(
        TranscriptsGenerationException,
        UnicodeDecodeError('aliencodec', b'\x02\x01', 1, 2, 'alien codec found!')
    )
    @patch('openedx.core.djangoapps.video_config.transcripts_utils.Transcript')
    def test_get_transcript_content_store_exceptions(self, exception_to_raise, mock_Transcript):
        """
        Verify that `get_transcript_from_contentstore` function raises `NotFoundError` when specified exceptions raised.
        """
        mock_Transcript.asset.side_effect = exception_to_raise
        transcripts_info = self.video.get_transcripts_info()
        lang = self.video.get_default_transcript_language(transcripts_info)
        with self.assertRaises(NotFoundError):  # noqa: PT027
            transcripts_utils.get_transcript_from_contentstore(
                self.video,
                language=lang,
                output_format=transcripts_utils.Transcript.SRT,
                transcripts_info=transcripts_info
            )


@ddt.ddt
class TestResolveLanguageCodeToTranscriptCode(unittest.TestCase):
    """ Tests for resolve_language_code_to_transcript_code """
    TEST_OTHER_LANGS = {'ab': 1, 'ab-cd': 1, 'ab-EF': 1, 'cd': 1, 'cd-jk': 1}
    TEST_TRANSCRIPTS = {'transcripts': TEST_OTHER_LANGS, 'sub': False}

    @ddt.unpack
    @ddt.data(
        ('ab', 'ab'),
        ('ab-CD', 'ab-cd'),
        ('ab-ef', 'ab-EF'),
        ('zx', None),
        ('cd-lmao', 'cd'),
    )
    def test_resolve_lang(self, lang, expected):
        """
        Test that resolve_language_code_to_transcript_code will successfully match
        language codes of different cases, and return None if it isn't found
        """
        self.assertEqual(  # noqa: PT009
            transcripts_utils.resolve_language_code_to_transcript_code(self.TEST_TRANSCRIPTS, lang),
            expected
        )


class TestGetEndonymOrLabel(unittest.TestCase):
    """
    tests for the get_endonym_or_label function
    """
    LANG_CODE = 'ab-cd'
    GENERIC_CODE = 'ab'
    LANG_ENTONYM = 'ab language entonym (cd)'
    LANG_LABEL = 'ab-cd language english label'
    GENERIC_LABEL = 'ab language english label'

    TEST_LANGUAGE_DICT = {LANG_CODE: LANG_ENTONYM}
    TEST_ALL_LANGUAGES = (
        ["aa", "Afar"],
        [GENERIC_CODE, GENERIC_LABEL],
        [LANG_CODE, LANG_LABEL],
        ["ur", "Urdu"],
    )

    @contextmanager
    def mock_django_get_language_info(self, side_effect=None):
        """
        Helper for cleaner mocking
        """
        with patch('openedx.core.djangoapps.video_config.transcripts_utils.get_language_info') as mock_get:
            if side_effect:
                mock_get.side_effect = side_effect
            yield mock_get

    def test_language_in_languages(self):
        """ If language is found in LANGUAGE_DICT that value should be returned """
        with override_settings(LANGUAGE_DICT=self.TEST_LANGUAGE_DICT):
            self.assertEqual(  # noqa: PT009
                transcripts_utils.get_endonym_or_label(self.LANG_CODE),
                self.LANG_ENTONYM
            )

    def test_language_in_django_lang_info(self):
        """
        If language is not found in LANGUAGE_DICT, check get_language_info and return that
        local name if found
        """
        with override_settings(LANGUAGE_DICT={}):
            with self.mock_django_get_language_info() as mock_get_language_info:
                self.assertEqual(  # noqa: PT009
                    transcripts_utils.get_endonym_or_label(self.LANG_CODE),
                    mock_get_language_info.return_value['name_local']
                )

    def test_language_exact_in_all_languages(self):
        """
        If not found in LANGUAGE_DICT or get_language_info, check in
        ALL_LANGUAGES for the English language name
        """
        with override_settings(LANGUAGE_DICT={}):
            with self.mock_django_get_language_info(side_effect=KeyError):
                with override_settings(ALL_LANGUAGES=self.TEST_ALL_LANGUAGES):
                    label = transcripts_utils.get_endonym_or_label(self.LANG_CODE)
        self.assertEqual(label, self.LANG_LABEL)  # noqa: PT009

    def test_language_generic_in_all_languages(self):
        """
        If not found in LANGUAGE_DICT or get_language_info, and the exact code
        wasn't found in ALL_LANGUAGES, use the generic code if it is found in ALL_LANGUAGES.
        """
        all_languages = (
            self.TEST_ALL_LANGUAGES[0],
            self.TEST_ALL_LANGUAGES[1],
            self.TEST_ALL_LANGUAGES[3]
        )

        with override_settings(LANGUAGE_DICT={}):
            with self.mock_django_get_language_info(side_effect=KeyError):
                with override_settings(ALL_LANGUAGES=all_languages):
                    label = transcripts_utils.get_endonym_or_label(self.LANG_CODE)
        self.assertEqual(label, self.GENERIC_LABEL)  # noqa: PT009

    def test_language_not_found_anywhere(self):
        """
        Raise a NotFoundError if the language isn't found anywhere
        """
        all_languages = (self.TEST_ALL_LANGUAGES[0], self.TEST_ALL_LANGUAGES[3])
        with override_settings(LANGUAGE_DICT={}):
            with self.mock_django_get_language_info(side_effect=KeyError):
                with override_settings(ALL_LANGUAGES=all_languages):
                    with self.assertRaises(NotFoundError):  # noqa: PT027
                        transcripts_utils.get_endonym_or_label(self.LANG_CODE)
