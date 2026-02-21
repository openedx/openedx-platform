"""Video xmodule tests in mongo."""


import json
import os
import tempfile
import textwrap
from datetime import timedelta
from unittest.mock import MagicMock, Mock, patch
import pytest
import ddt
import freezegun
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils.timezone import now
from django.test import RequestFactory
from edxval import api
from xblock.django.request import DjangoWebobRequest
from webob import Request, Response

from common.djangoapps.student.tests.factories import UserFactory
from common.test.utils import normalize_repr
from openedx.core.djangoapps.contentserver.caching import del_cached_content
from xmodule.contentstore.content import StaticContent  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.contentstore.django import contentstore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.exceptions import NotFoundError  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
# noinspection PyUnresolvedReferences
from xmodule.tests.helpers import override_descriptor_system  # pylint: disable=unused-import
from xmodule.video_block import VideoBlock  # lint-amnesty, pylint: disable=wrong-import-order
from openedx.core.djangoapps.video_config.transcripts_utils import (  # lint-amnesty, pylint: disable=wrong-import-order
    Transcript,
    edxval_api,
    get_transcript,
    subs_filename,
)
from xmodule.x_module import STUDENT_VIEW

from .helpers import BaseTestXmodule
from .test_video_xml import SOURCE_XML

TEST_USERNAME = 'test-user'
TRANSCRIPT = {"start": [10], "end": [100], "text": ["Hi, welcome to Edx."]}
BUMPER_TRANSCRIPT = {"start": [1], "end": [10], "text": ["A bumper"]}
SRT_content = textwrap.dedent("""
        0
        00:00:00,12 --> 00:00:00,100
        Привіт, edX вітає вас.
    """)

if settings.USE_EXTRACTED_VIDEO_BLOCK:
    path_video_handlers = 'xblocks_contrib.video.video_handlers'
    path_transcripts_utils = 'xblocks_contrib.video.video_transcripts_utils'
else:
    path_video_handlers = 'xmodule.video_block.video_handlers'
    path_transcripts_utils = 'openedx.core.djangoapps.video_config.transcripts_utils'


def _create_srt_file(content=None):
    """
    Create srt file in filesystem.
    """
    content = content or SRT_content

    srt_file = tempfile.NamedTemporaryFile(suffix=".srt")  # lint-amnesty, pylint: disable=consider-using-with
    srt_file.content_type = 'application/x-subrip; charset=utf-8'
    srt_file.write(content.encode('utf-8'))
    srt_file.seek(0)
    return srt_file


def _check_asset(location, asset_name):
    """
    Check that asset with asset_name exists in assets.
    """
    content_location = StaticContent.compute_location(
        location.course_key, asset_name
    )
    try:
        contentstore().find(content_location)
    except NotFoundError:
        return False
    else:
        return True


def _clear_assets(location):
    """
    Clear all assets for location.
    """
    store = contentstore()

    assets, __ = store.get_all_content_for_course(location.course_key)
    for asset in assets:
        asset_location = asset['asset_key']
        del_cached_content(asset_location)
        store.delete(asset_location)


def _get_subs_id(filename):
    basename = os.path.splitext(os.path.basename(filename))[0]
    return basename.replace('subs_', '').replace('.srt', '')


def _create_file(content=''):
    """
    Create temporary subs_somevalue.srt.sjson file.
    """
    sjson_file = tempfile.NamedTemporaryFile(prefix="subs_", suffix=".srt.sjson")  # lint-amnesty, pylint: disable=consider-using-with
    sjson_file.content_type = 'application/json'
    sjson_file.write(textwrap.dedent(content).encode('utf-8'))
    sjson_file.seek(0)
    return sjson_file


def _upload_sjson_file(subs_file, location, default_filename='subs_{}.srt.sjson'):
    filename = default_filename.format(_get_subs_id(subs_file.name))
    _upload_file(subs_file, location, filename)


def _upload_file(subs_file, location, filename):  # lint-amnesty, pylint: disable=missing-function-docstring
    mime_type = subs_file.content_type
    content_location = StaticContent.compute_location(
        location.course_key, filename
    )
    content = StaticContent(content_location, filename, mime_type, subs_file.read())
    contentstore().save(content)
    del_cached_content(content.location)


@normalize_repr
def attach_sub(item, filename):
    """
    Attach `en` transcript.
    """
    item.sub = filename


@normalize_repr
def attach_bumper_transcript(item, filename, lang="en"):
    """
    Attach bumper transcript.
    """
    item.video_bumper["transcripts"][lang] = filename


@pytest.mark.usefixtures("override_descriptor_system")
class BaseTestVideoXBlock(BaseTestXmodule):
    """Base class for VideoXBlock tests."""

    CATEGORY = 'video'

    def initialize_block(self, data=None, **kwargs):
        """ Initialize an XBlock to run tests on. """
        if data:
            # VideoBlock data field is no longer used but to avoid needing to re-do
            # a lot of tests code, parse and set the values as fields.
            fields_data = VideoBlock.parse_video_xml(data)
            kwargs.update(fields_data)
        kwargs.pop('source', None)
        kwargs.get('metadata', {}).pop('source', None)
        super().initialize_module(**kwargs)

    def setUp(self):
        super().setUp()
        self.initialize_block(data=self.DATA, metadata=self.METADATA)


class TestVideo(BaseTestVideoXBlock):
    """Integration tests: web client + mongo."""
    CATEGORY = "video"
    DATA = SOURCE_XML
    METADATA = {}

    def test_handle_ajax_wrong_dispatch(self):
        responses = {
            user.username: self.clients[user.username].post(
                self.get_url('whatever'),
                {},
                HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            for user in self.users
        }

        status_codes = {response.status_code for response in responses.values()}
        assert status_codes.pop() == 404

    def test_handle_ajax_for_speed_with_nan(self):
        self.block.handle_ajax('save_user_state', {'speed': json.dumps(1.0)})
        assert self.block.speed == 1.0
        assert self.block.global_speed == 1.0

        # try to set NaN value for speed.
        response = self.block.handle_ajax(
            'save_user_state', {'speed': json.dumps(float('NaN'))}
        )

        assert not json.loads(response)['success']
        expected_error = "Invalid speed value nan, must be a float."
        assert json.loads(response)['error'] == expected_error

        # verify that the speed and global speed are still 1.0
        assert self.block.speed == 1.0
        assert self.block.global_speed == 1.0

    def test_handle_ajax(self):

        data = [
            {'speed': 2.0},
            {'saved_video_position': "00:00:10"},
            {'transcript_language': 'uk'},
            {'bumper_do_not_show_again': True},
            {'bumper_last_view_date': True},
            {'demoo�': 'sample'}
        ]
        for sample in data:
            if settings.USE_EXTRACTED_VIDEO_BLOCK:
                handler_url = self.get_url('save_user_state', handler_name='ajax_handler')
                response = self.clients[self.users[0].username].post(
                    handler_url,
                    sample,
                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            else:
                response = self.clients[self.users[0].username].post(
                    self.get_url('save_user_state'),
                    sample,
                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            assert response.status_code == 200

        assert self.block.speed is None
        self.block.handle_ajax('save_user_state', {'speed': json.dumps(2.0)})
        assert self.block.speed == 2.0
        assert self.block.global_speed == 2.0

        assert self.block.saved_video_position == timedelta(0)
        self.block.handle_ajax('save_user_state', {'saved_video_position': "00:00:10"})
        assert self.block.saved_video_position == timedelta(0, 10)

        assert self.block.transcript_language == 'en'
        self.block.handle_ajax('save_user_state', {'transcript_language': "uk"})
        assert self.block.transcript_language == 'uk'

        assert self.block.bumper_do_not_show_again is False
        self.block.handle_ajax('save_user_state', {'bumper_do_not_show_again': True})
        assert self.block.bumper_do_not_show_again is True

        with freezegun.freeze_time(now()):
            assert self.block.bumper_last_view_date is None
            self.block.handle_ajax('save_user_state', {'bumper_last_view_date': True})
            assert self.block.bumper_last_view_date == now()

        response = self.block.handle_ajax('save_user_state', {'demoo�': "sample"})
        assert json.loads(response)['success'] is True

    def get_handler_url(self, handler, suffix):
        """
        Return the URL for the specified handler on self.block.
        """
        return self.block.runtime.handler_url(
            self.block, handler, suffix
        ).rstrip('/?')

    def tearDown(self):
        _clear_assets(self.block.location)
        super().tearDown()


@ddt.ddt
class TestTranscriptAvailableTranslationsDispatch(TestVideo):  # lint-amnesty, pylint: disable=test-inherits-tests
    """
    Test video handler that provide available translations info.

    Tests for `available_translations` dispatch.
    """
    srt_file = _create_srt_file()
    DATA = """
        <video show_captions="true"
        display_name="A Name"
        >
            <source src="example.mp4"/>
            <source src="example.webm"/>
            <transcript language="uk" src="{}"/>
        </video>
    """.format(os.path.split(srt_file.name)[1])

    MODEL_DATA = {
        'data': DATA
    }

    def setUp(self):
        super().setUp()
        self.block.render(STUDENT_VIEW)
        self.subs = {"start": [10], "end": [100], "text": ["Hi, welcome to Edx."]}

    def test_available_translation_en(self):
        good_sjson = _create_file(json.dumps(self.subs))
        _upload_sjson_file(good_sjson, self.block.location)
        self.block.sub = _get_subs_id(good_sjson.name)

        request = Request.blank('/available_translations')
        response = self.block.transcript(request=request, dispatch='available_translations')
        assert json.loads(response.body.decode('utf-8')) == ['en']

    def test_available_translation_non_en(self):
        _upload_file(_create_srt_file(), self.block.location, os.path.split(self.srt_file.name)[1])

        request = Request.blank('/available_translations')
        response = self.block.transcript(request=request, dispatch='available_translations')
        assert json.loads(response.body.decode('utf-8')) == ['uk']

    @patch('openedx.core.djangoapps.video_config.transcripts_utils.get_video_transcript_content')
    def test_multiple_available_translations(self, mock_get_video_transcript_content):
        mock_get_video_transcript_content.return_value = {
            'content': json.dumps({
                "start": [10],
                "end": [100],
                "text": ["Hi, welcome to Edx."],
            }),
            'file_name': 'edx.sjson'
        }

        good_sjson = _create_file(json.dumps(self.subs))

        # Upload english transcript.
        _upload_sjson_file(good_sjson, self.block.location)

        # Upload non-english transcript.
        _upload_file(self.srt_file, self.block.location, os.path.split(self.srt_file.name)[1])

        self.block.sub = _get_subs_id(good_sjson.name)
        self.block.edx_video_id = 'an-edx-video-id'

        request = Request.blank('/available_translations')
        response = self.block.transcript(request=request, dispatch='available_translations')
        assert sorted(json.loads(response.body.decode('utf-8'))) == sorted(['en', 'uk'])

    @patch('openedx.core.djangoapps.video_config.transcripts_utils.get_video_transcript_content')
    @patch(f'{path_transcripts_utils}.get_available_transcript_languages')
    @ddt.data(
        (
            ['en', 'uk', 'ro'],
            '',
            {},
            ['en', 'uk', 'ro']
        ),
        (
            ['uk', 'ro'],
            True,
            {},
            ['en', 'uk', 'ro']
        ),
        (
            ['de', 'ro'],
            True,
            {
                'uk': True,
                'ro': False,
            },
            ['en', 'uk', 'de', 'ro']
        ),
        (
            ['de'],
            True,
            {
                'uk': True,
                'ro': False,
            },
            ['en', 'uk', 'de', 'ro']
        ),
    )
    @ddt.unpack
    def test_val_available_translations(
        self,
        val_transcripts,
        sub,
        transcripts,
        result,
        mock_get_transcript_languages,
        mock_get_video_transcript_content
    ):
        """
        Tests available translations with video component's and val's transcript languages
        while the feature is enabled.
        """
        for lang_code, in_content_store in dict(transcripts).items():
            if in_content_store:
                file_name, __ = os.path.split(self.srt_file.name)
                _upload_file(self.srt_file, self.block.location, file_name)
                transcripts[lang_code] = file_name
            else:
                transcripts[lang_code] = 'non_existent.srt.sjson'
        if sub:
            sjson_transcript = _create_file(json.dumps(self.subs))
            _upload_sjson_file(sjson_transcript, self.block.location)
            sub = _get_subs_id(sjson_transcript.name)

        mock_get_video_transcript_content.return_value = {
            'content': json.dumps({
                "start": [10],
                "end": [100],
                "text": ["Hi, welcome to Edx."],
            }),
            'file_name': 'edx.sjson'
        }
        mock_get_transcript_languages.return_value = val_transcripts
        self.block.transcripts = transcripts
        self.block.sub = sub
        self.block.edx_video_id = 'an-edx-video-id'
        # Make request to available translations dispatch.
        request = Request.blank('/available_translations')
        response = self.block.transcript(request=request, dispatch='available_translations')
        self.assertCountEqual(json.loads(response.body.decode('utf-8')), result)

    @patch('openedx.core.djangoapps.video_config.transcripts_utils.edxval_api.get_available_transcript_languages')
    def test_val_available_translations_feature_disabled(self, mock_get_available_transcript_languages):
        """
        Tests available translations with val transcript languages when feature is disabled.
        """
        mock_get_available_transcript_languages.return_value = ['en', 'de', 'ro']
        request = Request.blank('/available_translations')
        response = self.block.transcript(request=request, dispatch='available_translations')
        assert response.status_code == 404


@ddt.ddt
class TestTranscriptAvailableTranslationsBumperDispatch(TestVideo):  # lint-amnesty, pylint: disable=test-inherits-tests
    """
    Test video handler that provide available translations info.

    Tests for `available_translations_bumper` dispatch.
    """
    srt_file = _create_srt_file()
    DATA = """
        <video show_captions="true"
        display_name="A Name"
        >
            <source src="example.mp4"/>
            <source src="example.webm"/>
            <transcript language="uk" src="{}"/>
        </video>
    """.format(os.path.split(srt_file.name)[1])

    MODEL_DATA = {
        'data': DATA
    }

    def setUp(self):
        super().setUp()
        self.block.render(STUDENT_VIEW)
        self.dispatch = "available_translations/?is_bumper=1"
        self.block.video_bumper = {"transcripts": {"en": ""}}

    @ddt.data("en", "uk")
    def test_available_translation_en_and_non_en(self, lang):
        filename = os.path.split(self.srt_file.name)[1]
        _upload_file(self.srt_file, self.block.location, filename)
        self.block.video_bumper["transcripts"][lang] = filename

        request = Request.blank('/' + self.dispatch)
        response = self.block.transcript(request=request, dispatch=self.dispatch)
        assert json.loads(response.body.decode('utf-8')) == [lang]

    @patch('openedx.core.djangoapps.video_config.transcripts_utils.get_available_transcript_languages')
    def test_multiple_available_translations(self, mock_get_transcript_languages):
        """
        Verify that available translations dispatch works as expected for multiple
        translations and returns both content store and edxval translations.
        """
        # Assuming that edx-val has German translation available for this video component.
        mock_get_transcript_languages.return_value = ['de']
        en_translation = _create_srt_file()
        en_translation_filename = os.path.split(en_translation.name)[1]
        uk_translation_filename = os.path.split(self.srt_file.name)[1]
        # Upload english transcript.
        _upload_file(en_translation, self.block.location, en_translation_filename)

        # Upload non-english transcript.
        _upload_file(self.srt_file, self.block.location, uk_translation_filename)

        self.block.video_bumper["transcripts"]["en"] = en_translation_filename
        self.block.video_bumper["transcripts"]["uk"] = uk_translation_filename

        request = Request.blank('/' + self.dispatch)
        response = self.block.transcript(request=request, dispatch=self.dispatch)
        # Assert that bumper only get its own translations.
        assert sorted(json.loads(response.body.decode('utf-8'))) == sorted(['en', 'uk'])


@ddt.ddt
class TestTranscriptDownloadDispatch(TestVideo):  # lint-amnesty, pylint: disable=test-inherits-tests
    """
    Test video handler that provide translation transcripts.

    Tests for `download` dispatch.
    """

    DATA = """
        <video show_captions="true"
        display_name="A Name"
        sub="OEoXaMPEzfM"
        edx_video_id="123"
        >
            <source src="example.mp4"/>
            <source src="example.webm"/>
        </video>
    """

    MODEL_DATA = {
        'data': DATA
    }

    def setUp(self):
        super().setUp()
        self.block.render(STUDENT_VIEW)

    def test_download_transcript_not_exist(self):
        request = Request.blank('/download')
        response = self.block.transcript(request=request, dispatch='download')
        assert response.status == '404 Not Found'

    @patch(
        f'{path_video_handlers}.get_transcript',
        return_value=('Subs!', 'test_filename.srt', 'application/x-subrip; charset=utf-8')
    )
    def test_download_srt_exist(self, __):
        request = Request.blank('/download')
        response = self.block.transcript(request=request, dispatch='download')
        assert response.body.decode('utf-8') == 'Subs!'
        assert response.headers['Content-Type'] == 'application/x-subrip; charset=utf-8'
        assert response.headers['Content-Language'] == 'en'

    @patch(
        f'{path_video_handlers}.get_transcript',
        return_value=('Subs!', 'txt', 'text/plain; charset=utf-8')
    )
    def test_download_txt_exist(self, __):
        self.block.transcript_format = 'txt'
        request = Request.blank('/download')
        response = self.block.transcript(request=request, dispatch='download')
        assert response.body.decode('utf-8') == 'Subs!'
        assert response.headers['Content-Type'] == 'text/plain; charset=utf-8'
        assert response.headers['Content-Language'] == 'en'

    def test_download_en_no_sub(self):
        request = Request.blank('/download')
        response = self.block.transcript(request=request, dispatch='download')
        assert response.status == '404 Not Found'
        with pytest.raises(NotFoundError):
            get_transcript(self.block)

    @patch(
        'openedx.core.djangoapps.video_config.transcripts_utils.get_transcript_for_video',
        return_value=(Transcript.SRT, "塞", 'Subs!')
    )
    def test_download_non_en_non_ascii_filename(self, __):
        request = Request.blank('/download')
        response = self.block.transcript(request=request, dispatch='download')
        assert response.body.decode('utf-8') == 'Subs!'
        assert response.headers['Content-Type'] == 'application/x-subrip; charset=utf-8'
        assert response.headers['Content-Disposition'] == 'attachment; filename="en_塞.srt"'

    @patch('openedx.core.djangoapps.video_config.transcripts_utils.edxval_api.get_video_transcript_data')
    @patch('xmodule.video_block.get_transcript', Mock(side_effect=NotFoundError))
    def test_download_fallback_transcript(self, mock_get_video_transcript_data):
        """
        Verify val transcript is returned as a fallback if it is not found in the content store.
        """
        mock_get_video_transcript_data.return_value = {
            'content': json.dumps({
                "start": [10],
                "end": [100],
                "text": ["Hi, welcome to Edx."],
            }),
            'file_name': 'edx.sjson'
        }

        # Make request to XModule transcript handler
        request = Request.blank('/download')
        response = self.block.transcript(request=request, dispatch='download')

        # Expected response
        expected_content = '0\n00:00:00,010 --> 00:00:00,100\nHi, welcome to Edx.\n\n'
        expected_headers = {
            'Content-Disposition': 'attachment; filename="edx.srt"',
            'Content-Language': 'en',
            'Content-Type': 'application/x-subrip; charset=utf-8'
        }

        # Assert the actual response
        assert response.status_code == 200
        assert response.text == expected_content
        for attribute, value in expected_headers.items():
            assert response.headers[attribute] == value


def _create_djangowebobrequest_object_for_url(url: str) -> DjangoWebobRequest:
    """
    create a DjangoWebobRequest (The type of requests used by xblocks)
    with a relevant user,
    """
    django_request = RequestFactory().get(url)
    django_request.user = UserFactory.create(username=TEST_USERNAME)
    return DjangoWebobRequest(django_request)


@ddt.ddt
class TestTranscriptTranslationGetDispatch(TestVideo):  # lint-amnesty, pylint: disable=test-inherits-tests
    """
    Test video handler that provide translation transcripts.

    Tests for `translation` and `translation_bumper` dispatches.
    """

    srt_file = _create_srt_file()
    DATA = """
        <video
            show_captions="true"
            display_name="A Name"
            edx_video_id="123"
        >
            <source src="example.mp4"/>
            <source src="example.webm"/>
            <transcript language="uk" src="{}"/>
        </video>
    """.format(os.path.split(srt_file.name)[1])

    MODEL_DATA = {
        'data': DATA
    }

    def setUp(self):
        super().setUp()
        self.block.render(STUDENT_VIEW)
        self.block.video_bumper = {"transcripts": {"en": ""}}

    @ddt.data(
        # No language
        ('/translation', 'translation', '400 Bad Request'),
        # No videoId - HTML5 video with language that is not in available languages
        ('/translation/ru', 'translation/ru', '404 Not Found'),
        # Language is not in available languages
        ('/translation/ru?videoId=12345', 'translation/ru', '404 Not Found'),
        # Youtube_id is invalid or does not exist
        ('/translation/uk?videoId=9855256955511225', 'translation/uk', '404 Not Found'),
        ('/translation?is_bumper=1', 'translation', '400 Bad Request'),
        ('/translation/ru?is_bumper=1', 'translation/ru', '404 Not Found'),
        ('/translation/ru?videoId=12345&is_bumper=1', 'translation/ru', '404 Not Found'),
        ('/translation/uk?videoId=9855256955511225&is_bumper=1', 'translation/uk', '404 Not Found'),
    )
    @ddt.unpack
    def test_translation_fails(self, url, dispatch, status_code):
        request = _create_djangowebobrequest_object_for_url(url)
        response = self.block.transcript(request=request, dispatch=dispatch)
        assert response.status == status_code

    @ddt.data(
        ('translation/en?videoId={}', 'translation/en', attach_sub),
        ('translation/en?videoId={}&is_bumper=1', 'translation/en', attach_bumper_transcript))
    @ddt.unpack
    def test_translaton_en_youtube_success(self, url, dispatch, attach):
        subs = {"start": [10], "end": [100], "text": ["Hi, welcome to Edx."]}
        good_sjson = _create_file(json.dumps(subs))
        _upload_sjson_file(good_sjson, self.block.location)
        subs_id = _get_subs_id(good_sjson.name)

        attach(self.block, subs_id)
        request = _create_djangowebobrequest_object_for_url(url.format(subs_id))
        response = self.block.transcript(request=request, dispatch=dispatch)
        self.assertDictEqual(json.loads(response.body.decode('utf-8')), subs)

    def test_translation_non_en_youtube_success(self):
        subs = {
            'end': [100],
            'start': [12],
            'text': [
                '\u041f\u0440\u0438\u0432\u0456\u0442, edX \u0432\u0456\u0442\u0430\u0454 \u0432\u0430\u0441.'
            ]
        }
        self.srt_file.seek(0)
        _upload_file(self.srt_file, self.block.location, os.path.split(self.srt_file.name)[1])
        subs_id = _get_subs_id(self.srt_file.name)

        # youtube 1_0 request, will generate for all speeds for existing ids
        self.block.youtube_id_1_0 = subs_id
        self.block.youtube_id_0_75 = '0_75'
        self.store.update_item(self.block, self.user.id)
        request = _create_djangowebobrequest_object_for_url(f'/translation/uk?videoId={subs_id}')
        response = self.block.transcript(request=request, dispatch='translation/uk')
        self.assertDictEqual(json.loads(response.body.decode('utf-8')), subs)

        # 0_75 subs are exist
        request = _create_djangowebobrequest_object_for_url('/translation/uk?videoId={}'.format('0_75'))
        response = self.block.transcript(request=request, dispatch='translation/uk')
        calculated_0_75 = {
            'end': [75],
            'start': [9],
            'text': [
                '\u041f\u0440\u0438\u0432\u0456\u0442, edX \u0432\u0456\u0442\u0430\u0454 \u0432\u0430\u0441.'
            ]
        }

        self.assertDictEqual(json.loads(response.body.decode('utf-8')), calculated_0_75)
        # 1_5 will be generated from 1_0
        self.block.youtube_id_1_5 = '1_5'
        self.store.update_item(self.block, self.user.id)
        request = _create_djangowebobrequest_object_for_url('/translation/uk?videoId={}'.format('1_5'))
        response = self.block.transcript(request=request, dispatch='translation/uk')
        calculated_1_5 = {
            'end': [150],
            'start': [18],
            'text': [
                '\u041f\u0440\u0438\u0432\u0456\u0442, edX \u0432\u0456\u0442\u0430\u0454 \u0432\u0430\u0441.'
            ]
        }
        self.assertDictEqual(json.loads(response.body.decode('utf-8')), calculated_1_5)

    @ddt.data(
        ('translation/en', 'translation/en', attach_sub),
        ('translation/en?is_bumper=1', 'translation/en', attach_bumper_transcript))
    @ddt.unpack
    def test_translaton_en_html5_success(self, url, dispatch, attach):
        good_sjson = _create_file(json.dumps(TRANSCRIPT))
        _upload_sjson_file(good_sjson, self.block.location)
        subs_id = _get_subs_id(good_sjson.name)

        attach(self.block, subs_id)
        self.store.update_item(self.block, self.user.id)
        request = _create_djangowebobrequest_object_for_url(url)
        response = self.block.transcript(request=request, dispatch=dispatch)
        self.assertDictEqual(json.loads(response.body.decode('utf-8')), TRANSCRIPT)

    def test_translaton_non_en_html5_success(self):
        subs = {
            'end': [100],
            'start': [12],
            'text': [
                '\u041f\u0440\u0438\u0432\u0456\u0442, edX \u0432\u0456\u0442\u0430\u0454 \u0432\u0430\u0441.'
            ]
        }
        self.srt_file.seek(0)
        _upload_file(self.srt_file, self.block.location, os.path.split(self.srt_file.name)[1])

        # manually clean youtube_id_1_0, as it has default value
        self.block.youtube_id_1_0 = ""
        request = _create_djangowebobrequest_object_for_url('/translation/uk')
        response = self.block.transcript(request=request, dispatch='translation/uk')
        self.assertDictEqual(json.loads(response.body.decode('utf-8')), subs)

    def test_translation_static_transcript_xml_with_data_dirc(self):
        """
        Test id data_dir is set in XML course.

        Set course data_dir and ensure we get redirected to that path
        if it isn't found in the contentstore.
        """
        # Simulate data_dir set in course.
        test_modulestore = MagicMock()
        attrs = {'get_course.return_value': Mock(data_dir='dummy/static', static_asset_path='')}
        test_modulestore.configure_mock(**attrs)
        self.block.runtime.modulestore = test_modulestore

        # Test youtube style en
        request = _create_djangowebobrequest_object_for_url('/translation/en?videoId=12345')
        response = self.block.transcript(request=request, dispatch='translation/en')
        assert response.status == '307 Temporary Redirect'
        assert ('Location', '/static/dummy/static/subs_12345.srt.sjson') in response.headerlist

        # Test HTML5 video style
        self.block.sub = 'OEoXaMPEzfM'
        request = _create_djangowebobrequest_object_for_url('/translation/en')
        response = self.block.transcript(request=request, dispatch='translation/en')
        assert response.status == '307 Temporary Redirect'
        assert ('Location', '/static/dummy/static/subs_OEoXaMPEzfM.srt.sjson') in response.headerlist

        # Test different language to ensure we are just ignoring it since we can't
        # translate with static fallback
        request = _create_djangowebobrequest_object_for_url('/translation/uk')
        response = self.block.transcript(request=request, dispatch='translation/uk')
        assert response.status == '404 Not Found'

    @ddt.data(
        # Test youtube style en
        ('/translation/en?videoId=12345', 'translation/en', '307 Temporary Redirect', '12345'),
        # Test html5 style en
        ('/translation/en', 'translation/en', '307 Temporary Redirect', 'OEoXaMPEzfM', attach_sub),
        # Test different language to ensure we are just ignoring it since we can't
        # translate with static fallback
        ('/translation/uk', 'translation/uk', '404 Not Found'),
        (
            '/translation/en?is_bumper=1', 'translation/en', '307 Temporary Redirect', 'OEoXaMPEzfM',
            attach_bumper_transcript
        ),
        ('/translation/uk?is_bumper=1', 'translation/uk', '404 Not Found'),
    )
    @ddt.unpack
    def test_translation_static_transcript(self, url, dispatch, status_code, sub=None, attach=None):
        """
        Set course static_asset_path and ensure we get redirected to that path
        if it isn't found in the contentstore
        """
        self._set_static_asset_path()

        if attach:
            attach(self.block, sub)
        request = _create_djangowebobrequest_object_for_url(url)
        response = self.block.transcript(request=request, dispatch=dispatch)
        assert response.status == status_code
        if sub:
            assert ('Location', f'/static/dummy/static/subs_{sub}.srt.sjson') in response.headerlist

    @patch('xmodule.video_block.VideoBlock.course_id', return_value='not_a_course_locator')
    def test_translation_static_non_course(self, __):
        """
        Test that get_static_transcript short-circuits in the case of a non-CourseLocator.
        This fixes a bug for videos inside of content libraries.
        """
        self._set_static_asset_path()

        # When course_id is not mocked out, these values would result in 307, as tested above.
        request = _create_djangowebobrequest_object_for_url('/translation/en?videoId=12345')
        response = self.block.transcript(request=request, dispatch='translation/en')
        assert response.status == '404 Not Found'

    def _set_static_asset_path(self):
        """ Helper method for setting up the static_asset_path information """
        self.course.static_asset_path = 'dummy/static'
        self.course.save()
        store = modulestore()
        with store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, self.course.id):
            store.update_item(self.course, self.user.id)

    @patch('openedx.core.djangoapps.video_config.transcripts_utils.edxval_api.get_video_transcript_data')
    @patch('xmodule.video_block.VideoBlock.get_static_transcript', Mock(return_value=Response(status=404)))
    def test_translation_fallback_transcript(self, mock_get_video_transcript_data):
        """
        Verify that the val transcript is returned as a fallback,
        if it is not found in the content store.
        """
        transcript = {
            'content': json.dumps({
                "start": [10],
                "end": [100],
                "text": ["Hi, welcome to Edx."],
            }),
            'file_name': 'edx.sjson'
        }
        mock_get_video_transcript_data.return_value = transcript

        # Make request to XModule transcript handler
        request = _create_djangowebobrequest_object_for_url('/translation/en')
        response = self.block.transcript(request=request, dispatch='translation/en')

        # Expected headers
        expected_headers = {
            'Content-Language': 'en',
            'Content-Type': 'application/json'
        }

        # Assert the actual response
        assert response.status_code == 200
        assert response.text == transcript['content']
        for attribute, value in expected_headers.items():
            assert response.headers[attribute] == value

    @patch('xmodule.video_block.VideoBlock.get_static_transcript', Mock(return_value=Response(status=404)))
    def test_translation_fallback_transcript_feature_disabled(self):
        """
        Verify that val transcript is not returned when its feature is disabled.
        """
        # Make request to XModule transcript handler
        response = self.block.transcript(
            request=_create_djangowebobrequest_object_for_url('/translation/en'),
            dispatch='translation/en'
        )
        # Assert the actual response
        assert response.status_code == 404


class TestStudioTranscriptTranslationGetDispatch(TestVideo):  # lint-amnesty, pylint: disable=test-inherits-tests
    """
    Test Studio video handler that provide translation transcripts.

    Tests for `translation` dispatch GET HTTP method.
    """
    srt_file = _create_srt_file()
    DATA = """
        <video show_captions="true"
        display_name="A Name"
        >
            <source src="example.mp4"/>
            <source src="example.webm"/>
            <transcript language="uk" src="{}"/>
            <transcript language="zh" src="{}"/>
        </video>
    """.format(os.path.split(srt_file.name)[1], "塞.srt")

    MODEL_DATA = {'data': DATA}

    def test_translation_fails(self):
        # No language
        request = Request.blank("")
        response = self.block.studio_transcript(request=request, dispatch="translation")
        assert response.status == '400 Bad Request'

        # No language_code param in request.GET
        request = Request.blank("")
        response = self.block.studio_transcript(request=request, dispatch="translation")
        assert response.status == '400 Bad Request'
        assert response.json['error'] == 'Language is required.'

        # Correct case:
        filename = os.path.split(self.srt_file.name)[1]
        _upload_file(self.srt_file, self.block.location, filename)
        request = Request.blank("translation?language_code=uk")
        response = self.block.studio_transcript(request=request, dispatch="translation?language_code=uk")
        self.srt_file.seek(0)
        assert response.body == self.srt_file.read()
        assert response.headers['Content-Type'] == 'application/x-subrip; charset=utf-8'
        assert response.headers['Content-Disposition'] == f'attachment; filename="uk_{filename}"'
        assert response.headers['Content-Language'] == 'uk'

        # Non ascii file name download:
        self.srt_file.seek(0)
        _upload_file(self.srt_file, self.block.location, "塞.srt")
        request = Request.blank("translation?language_code=zh")
        response = self.block.studio_transcript(request=request, dispatch="translation?language_code=zh")
        self.srt_file.seek(0)
        assert response.body == self.srt_file.read()
        assert response.headers['Content-Type'] == 'application/x-subrip; charset=utf-8'
        assert response.headers['Content-Disposition'] == 'attachment; filename="zh_塞.srt"'
        assert response.headers['Content-Language'] == 'zh'


@ddt.ddt
class TestStudioTranscriptTranslationPostDispatch(TestVideo):  # lint-amnesty, pylint: disable=test-inherits-tests
    """
    Test Studio video handler that provide translation transcripts.

    Tests for `translation` dispatch with HTTP POST method.
    """
    DATA = """
        <video show_captions="true"
        display_name="A Name"
        >
            <source src="example.mp4"/>
            <source src="example.webm"/>

        </video>
    """

    MODEL_DATA = {
        'data': DATA
    }

    METADATA = {}

    @ddt.data(
        {
            "post_data": {},
            "error_message": "The following parameters are required: edx_video_id, language_code, new_language_code."
        },
        {
            "post_data": {"edx_video_id": "111", "language_code": "ar", "new_language_code": "ur"},
            "error_message": 'A transcript with the "ur" language code already exists.'
        },
        {
            "post_data": {"edx_video_id": "111", "language_code": "ur", "new_language_code": "ur"},
            "error_message": "A transcript file is required."
        },
    )
    @patch('openedx.core.djangoapps.video_config.services.VideoConfigService.available_translations')
    @ddt.unpack
    def test_studio_transcript_post_validations(self, mock_available_translations, post_data, error_message):
        """
        Verify that POST request validations works as expected.
        """
        # mock available_translations method to return ['ur']
        mock_available_translations.return_value = ['ur']
        request = Request.blank('/translation', POST=post_data)
        response = self.block.studio_transcript(request=request, dispatch='translation')
        assert response.json['error'] == error_message

    @ddt.data(
        {
            "edx_video_id": "",
        },
        {
            "edx_video_id": "1234-5678-90",
        },
    )
    @ddt.unpack
    def test_studio_transcript_post_w_no_edx_video_id(self, edx_video_id):
        """
        Verify that POST request works as expected
        """
        post_data = {
            "edx_video_id": edx_video_id,
            "language_code": "ar",
            "new_language_code": "uk",
            "file": ("filename.srt", SRT_content)
        }

        if edx_video_id:
            edxval_api.create_video({
                "edx_video_id": edx_video_id,
                "status": "uploaded",
                "client_video_id": "a video",
                "duration": 0,
                "encoded_videos": [],
                "courses": []
            })

        request = Request.blank('/translation', POST=post_data)
        response = self.block.studio_transcript(request=request, dispatch='translation')
        assert response.status == '201 Created'
        response = json.loads(response.text)
        assert response['language_code'], 'uk'
        self.assertDictEqual(self.block.transcripts, {'uk': f'{response["edx_video_id"]}-uk.srt'})
        assert edxval_api.get_video_transcript_data(video_id=response['edx_video_id'], language_code='uk')

    def test_studio_transcript_post_bad_content(self):
        """
        Verify that transcript content encode/decode errors handled as expected
        """
        post_data = {
            "edx_video_id": "",
            "language_code": "ar",
            "new_language_code": "uk",
            "file": ("filename.srt", SRT_content.encode("cp1251"))
        }

        request = Request.blank("/translation", POST=post_data)
        response = self.block.studio_transcript(request=request, dispatch="translation")
        assert response.status_code == 400
        assert response.json['error'] == 'There is a problem with this transcript file. Try to upload a different file.'
        # transcripts fields should not be updated
        self.assertDictEqual(self.block.transcripts, {})


@ddt.ddt
class TestStudioTranscriptTranslationDeleteDispatch(TestVideo):  # lint-amnesty, pylint: disable=test-inherits-tests
    """
    Test studio video handler that provide translation transcripts.

    Tests for `translation` dispatch DELETE HTTP method.
    """
    EDX_VIDEO_ID, LANGUAGE_CODE_UK, LANGUAGE_CODE_EN = 'an_edx_video_id', 'uk', 'en'
    REQUEST_META = {'wsgi.url_scheme': 'http', 'REQUEST_METHOD': 'DELETE'}
    SRT_FILE = _create_srt_file()

    @ddt.data(
        {
            'params': {'lang': 'uk'}
        },
        {
            'params': {'edx_video_id': '12345'}
        },
        {
            'params': {}
        },
    )
    @ddt.unpack
    def test_translation_missing_required_params(self, params):
        """
        Verify that DELETE dispatch works as expected when required args are missing from request
        """
        request = Request(self.REQUEST_META, body=json.dumps(params).encode('utf-8'))
        response = self.block.studio_transcript(request=request, dispatch='translation')
        assert response.status_code == 400

    def test_translation_delete_w_edx_video_id(self):
        """
        Verify that DELETE dispatch works as expected when video has edx_video_id
        """
        request_body = json.dumps({'lang': self.LANGUAGE_CODE_UK, 'edx_video_id': self.EDX_VIDEO_ID})
        api.create_video({
            'edx_video_id': self.EDX_VIDEO_ID,
            'status': 'upload',
            'client_video_id': 'awesome.mp4',
            'duration': 0,
            'encoded_videos': [],
            'courses': [str(self.course.id)]
        })
        api.create_video_transcript(
            video_id=self.EDX_VIDEO_ID,
            language_code=self.LANGUAGE_CODE_UK,
            file_format='srt',
            content=ContentFile(SRT_content)
        )

        # verify that a video transcript exists for expected data
        assert api.get_video_transcript_data(video_id=self.EDX_VIDEO_ID, language_code=self.LANGUAGE_CODE_UK)

        request = Request(self.REQUEST_META, body=request_body.encode('utf-8'))
        self.block.edx_video_id = self.EDX_VIDEO_ID
        response = self.block.studio_transcript(request=request, dispatch='translation')
        assert response.status_code == 200

        # verify that a video transcript dose not exist for expected data
        assert not api.get_video_transcript_data(video_id=self.EDX_VIDEO_ID, language_code=self.LANGUAGE_CODE_UK)

    def test_translation_delete_wo_edx_video_id(self):
        """
        Verify that DELETE dispatch works as expected when video has no edx_video_id
        """
        request_body = json.dumps({'lang': self.LANGUAGE_CODE_UK, 'edx_video_id': ''})
        srt_file_name_uk = subs_filename('ukrainian_translation.srt', lang=self.LANGUAGE_CODE_UK)
        request = Request(self.REQUEST_META, body=request_body.encode('utf-8'))

        # upload and verify that srt file exists in assets
        _upload_file(self.SRT_FILE, self.block.location, srt_file_name_uk)
        assert _check_asset(self.block.location, srt_file_name_uk)

        # verify transcripts field
        assert self.block.transcripts != {}
        assert self.LANGUAGE_CODE_UK in self.block.transcripts

        # make request and verify response
        response = self.block.studio_transcript(request=request, dispatch='translation')
        assert response.status_code == 200

        # verify that srt file is deleted
        assert self.block.transcripts == {}
        assert not _check_asset(self.block.location, srt_file_name_uk)

    def test_translation_delete_w_english_lang(self):
        """
        Verify that DELETE dispatch works as expected for english language translation
        """
        request_body = json.dumps({'lang': self.LANGUAGE_CODE_EN, 'edx_video_id': ''})
        srt_file_name_en = subs_filename('english_translation.srt', lang=self.LANGUAGE_CODE_EN)
        self.block.transcripts['en'] = 'english_translation.srt'
        request = Request(self.REQUEST_META, body=request_body.encode('utf-8'))

        # upload and verify that srt file exists in assets
        _upload_file(self.SRT_FILE, self.block.location, srt_file_name_en)
        assert _check_asset(self.block.location, srt_file_name_en)

        # make request and verify response
        response = self.block.studio_transcript(request=request, dispatch='translation')
        assert response.status_code == 200

        # verify that srt file is deleted
        assert self.LANGUAGE_CODE_EN not in self.block.transcripts
        assert not _check_asset(self.block.location, srt_file_name_en)

    def test_translation_delete_w_sub(self):
        """
        Verify that DELETE dispatch works as expected when translation is present against `sub` field
        """
        request_body = json.dumps({'lang': self.LANGUAGE_CODE_EN, 'edx_video_id': ''})
        sub_file_name = subs_filename(self.block.sub, lang=self.LANGUAGE_CODE_EN)
        request = Request(self.REQUEST_META, body=request_body.encode('utf-8'))

        # sub should not be empy
        assert not self.block.sub == ''
        # lint-amnesty, pylint: disable=wrong-assert-type

        # upload and verify that srt file exists in assets
        _upload_file(self.SRT_FILE, self.block.location, sub_file_name)
        assert _check_asset(self.block.location, sub_file_name)

        # make request and verify response
        response = self.block.studio_transcript(request=request, dispatch='translation')
        assert response.status_code == 200

        # verify that sub is empty and transcript is deleted also
        assert self.block.sub == ''
        # lint-amnesty, pylint: disable=wrong-assert-type
        assert not _check_asset(self.block.location, sub_file_name)


class TestGetTranscript(TestVideo):  # lint-amnesty, pylint: disable=test-inherits-tests
    """
    Make sure that `get_transcript` method works correctly
    """
    srt_file = _create_srt_file()
    DATA = """
        <video show_captions="true"
        display_name="A Name"
        >
            <source src="example.mp4"/>
            <source src="example.webm"/>
            <transcript language="uk" src="{}"/>
            <transcript language="zh" src="{}"/>
        </video>
    """.format(os.path.split(srt_file.name)[1], "塞.srt")

    MODEL_DATA = {
        'data': DATA
    }
    METADATA = {}

    def setUp(self):
        super().setUp()
        self.block.render(STUDENT_VIEW)

    def test_good_transcript(self):
        """
        Test for download 'en' sub with html5 video and self.sub has correct non-empty value.
        """
        good_sjson = _create_file(content=textwrap.dedent("""\
                {
                  "start": [
                    270,
                    2720
                  ],
                  "end": [
                    2720,
                    5430
                  ],
                  "text": [
                    "Hi, welcome to Edx.",
                    "Let&#39;s start with what is on your screen right now."
                  ]
                }
            """))

        _upload_sjson_file(good_sjson, self.block.location)
        self.block.sub = _get_subs_id(good_sjson.name)

        text, filename, mime_type = get_transcript(self.block)

        expected_text = textwrap.dedent("""\
            0
            00:00:00,270 --> 00:00:02,720
            Hi, welcome to Edx.

            1
            00:00:02,720 --> 00:00:05,430
            Let&#39;s start with what is on your screen right now.

            """)

        assert text == expected_text
        assert filename[:(- 4)] == ('en_' + self.block.sub)
        assert mime_type == 'application/x-subrip; charset=utf-8'

    def test_good_txt_transcript(self):
        good_sjson = _create_file(content=textwrap.dedent("""\
                {
                  "start": [
                    270,
                    2720
                  ],
                  "end": [
                    2720,
                    5430
                  ],
                  "text": [
                    "Hi, welcome to Edx.",
                    "Let&#39;s start with what is on your screen right now."
                  ]
                }
            """))

        _upload_sjson_file(good_sjson, self.block.location)
        self.block.sub = _get_subs_id(good_sjson.name)
        text, filename, mime_type = get_transcript(self.block, output_format=Transcript.TXT)
        expected_text = textwrap.dedent("""\
            Hi, welcome to Edx.
            Let's start with what is on your screen right now.""")

        assert text == expected_text
        assert filename == (('en_' + self.block.sub) + '.txt')
        assert mime_type == 'text/plain; charset=utf-8'

    def test_en_with_empty_sub(self):

        self.block.sub = ""
        self.block.transcripts = None
        # no self.sub, self.youttube_1_0 exist, but no file in assets
        with pytest.raises(NotFoundError):
            get_transcript(self.block)

        # no self.sub and no self.youtube_1_0, no non-en transcritps
        self.block.youtube_id_1_0 = None
        with pytest.raises(NotFoundError):
            get_transcript(self.block)

        # no self.sub but youtube_1_0 exists with file in assets
        good_sjson = _create_file(content=textwrap.dedent("""\
                {
                  "start": [
                    270,
                    2720
                  ],
                  "end": [
                    2720,
                    5430
                  ],
                  "text": [
                    "Hi, welcome to Edx.",
                    "Let&#39;s start with what is on your screen right now."
                  ]
                }
            """))
        _upload_sjson_file(good_sjson, self.block.location)
        self.block.youtube_id_1_0 = _get_subs_id(good_sjson.name)

        text, filename, mime_type = get_transcript(self.block)
        expected_text = textwrap.dedent("""\
            0
            00:00:00,270 --> 00:00:02,720
            Hi, welcome to Edx.

            1
            00:00:02,720 --> 00:00:05,430
            Let&#39;s start with what is on your screen right now.

            """)

        assert text == expected_text
        assert filename == (('en_' + self.block.youtube_id_1_0) + '.srt')
        assert mime_type == 'application/x-subrip; charset=utf-8'

    def test_non_en_with_non_ascii_filename(self):
        self.block.transcript_language = 'zh'
        self.srt_file.seek(0)
        _upload_file(self.srt_file, self.block.location, "塞.srt")

        transcripts = self.block.get_transcripts_info()  # lint-amnesty, pylint: disable=unused-variable
        text, filename, mime_type = get_transcript(self.block)
        expected_text = textwrap.dedent("""
        0
        00:00:00,12 --> 00:00:00,100
        Привіт, edX вітає вас.
        """)
        assert text == expected_text
        assert filename == 'zh_塞.srt'
        assert mime_type == 'application/x-subrip; charset=utf-8'

    def test_value_error_handled(self):
        good_sjson = _create_file(content='bad content')

        _upload_sjson_file(good_sjson, self.block.location)
        self.block.sub = _get_subs_id(good_sjson.name)

        transcripts = self.block.get_transcripts_info()  # lint-amnesty, pylint: disable=unused-variable
        error_transcript = {"start": [], "end": [], "text": ["An error occured obtaining the transcript."]}
        content, _, _ = get_transcript(self.block)
        assert error_transcript["text"][0] in content

    def test_key_error(self):
        good_sjson = _create_file(content="""
                {
                  "start": [
                    270,
                    2720
                  ],
                  "end": [
                    2720,
                    5430
                  ]
                }
            """)

        _upload_sjson_file(good_sjson, self.block.location)
        self.block.sub = _get_subs_id(good_sjson.name)

        transcripts = self.block.get_transcripts_info()  # lint-amnesty, pylint: disable=unused-variable
        with pytest.raises(KeyError):
            get_transcript(self.block)
