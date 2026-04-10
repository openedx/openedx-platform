''' Tests mechanism for obtaining language-specific transcript links from youtube video pages
Note that tests that work with these links are located elsewhere (test_video.py)

These tests follow the following nomenclature:
 - a youtube video page supports one or more caption languages (aka 'tracks')
 - embedded in the page's HTML are track descriptors
 - among the fields found in a track descriptor is a caption URL (aka caption link)
        - use this link to obtain the track's caption data
'''
from unittest import TestCase, mock

import ddt

from openedx.core.djangoapps.video_config.transcripts_utils import get_transcript_link_from_youtube

YOUTUBE_VIDEO_ID = "z-LoKnweV6w"

# Use CAPTION_URL_TEMPLATE.format(<youtube_video_ID>, <ampersand_encoding>, <language_code>)
# to get either the HTML rendition or the json rendition of the link used to obtain a youtube video's subtitles
#
# Parameterized with
# {0} - The youtube video ID whose captions you want
# {1} - Either \u0026 for use with UTF-8 encoded HTML, or '&' for use with json
# {2} - Language code (e.g., "en")
CAPTION_URL_TEMPLATE = "https://www.youtube.com/api/timedtext?v = {0}{1}caps = asr{1}xoaf = 5{1}hl = {2}{1}\
ip = 0.0.0.0{1}ipbits = 0{1}expire = 1667281544{1}sparams = ip, ipbits, expire, v, caps, xoaf{1}\
signature = 3A2A34F0A1FB11B3825FF54D4238B6CC415877E8.058892{1}key = yt8{1}kind = asr{1}lang = {2}"

UTF8_AMPERSAND = '\\u0026'

# These caption link templates have tailored uses and parameterize on the language code only
#
# Parameterized with
# {0} - Language code (e.g., "en")
CAPTION_URL_UTF8_ENCODED_TEMPLATE = CAPTION_URL_TEMPLATE.format(YOUTUBE_VIDEO_ID, UTF8_AMPERSAND, "{0}")
CAPTION_URL_UTF8_DECODED_TEMPLATE = CAPTION_URL_TEMPLATE.format(YOUTUBE_VIDEO_ID, "&", "{0}")

# Macro providing the HTML returned by our mock GET operation on the youtube video page
#
# This template is hard-wired for a video with a single language track
#
# This is not valid HTML, but that's OK, as we'll only be using it to confirm the regex
# search on the 'playerCaptionsTrackListRenderer' subtree.
#
# Packed quality (i.e., no spaces) is essential for these tests to work! Introduction
# of spaces before or after the colon signs causes the regex matching to stop working.
#
# Parameterized with
# {0} - the URL that obtains the selected video's caption
# {1} - Language code (e.g., "en")
YOUTUBE_HTML_TEMPLATE = "<b>HTML content that comes before the caption tracks...</b>" \
                        "\"captions\":{{\"playerCaptionsTracklistRenderer\":" \
                        "{{\"captionTracks\":[{{\"baseUrl\":\"{0}\"," \
                        "\"name\":{{\"simpleText\":\"(Caption language name in local language)\"}}," \
                        "\"vssId\":\".{1}\",\"languageCode\":\"{1}\"," \
                        "\"isTranslatable\":true}}]}}}}<b>HTML content that comes after the caption tracks ...</b>"


class YoutubeVideoHTMLResponse:
    """
    Generates substitute HTTP GET responses used when mocking the GET operation to a youtube video page
    """

    @classmethod
    def with_caption_track(cls, language_code):
        """
        Generates a GET response of HTML with a single caption track for the specified
        language code language_code = "en" for english
        """
        caption_link = CAPTION_URL_UTF8_ENCODED_TEMPLATE.format(language_code)
        html_with_single_caption_track = YOUTUBE_HTML_TEMPLATE.format(caption_link, language_code)
        return cls.MockResponse(html_with_single_caption_track)

    @classmethod
    def with_no_caption_tracks(cls):
        """
        Generates a GET response of (invalid) HTML lacking any caption tracks within it.
        This fake HTML is nevered rendered; it's only intended as a source for a regex
        search
        """
        html_with_no_caption_tracks = "<b>No caption URL info for regex to find here</b>"
        return cls.MockResponse(html_with_no_caption_tracks)

    @classmethod
    def with_malformed_caption_track(cls, language_code):
        """
        Generates a GET response of HTML with a single caption of the specified
        language code language_code = "en" for english
        """
        caption_link = CAPTION_URL_UTF8_ENCODED_TEMPLATE.format(language_code)
        html_with_single_valid_caption_track = YOUTUBE_HTML_TEMPLATE.format(caption_link, language_code)
        html_with_single_malformed_caption_track = \
            html_with_single_valid_caption_track.replace('languageCode', 'bogus_key')
        return cls.MockResponse(html_with_single_malformed_caption_track)

    class MockResponse:
        """
        An object fit to be returned from a an HTTP GET operation, exposing
        a UTF-8 encoded version of the youtube_html input string in its content attribute
        """
        def __init__(self, youtube_html):
            self.status_code = 200
            self.content = bytearray(youtube_html, 'UTF-8')


@ddt.ddt
class TranscriptsUtilsTest(TestCase):
    """
    Tests utility fucntions for transcripts (in video_block)
    """

    @mock.patch('requests.get')
    @ddt.data("en", "en-US", "en-GB", 'fr')
    def test_get_transcript_link_from_youtube(self, language_code, mock_get):
        """
        Happy path test: dict of caption links returned when video page HTML has at least one caption
        """
        mock_get.return_value = YoutubeVideoHTMLResponse.with_caption_track(language_code)

        language_specific_caption_link = get_transcript_link_from_youtube(YOUTUBE_VIDEO_ID)
        self.assertEqual(  # noqa: PT009
            language_specific_caption_link,
            {language_code: CAPTION_URL_UTF8_DECODED_TEMPLATE.format(language_code)}
        )

    @ mock.patch('requests.get')
    @ddt.data(None)
    def test_get_caption_no_caption(self, language_code, mock_get):
        """
        No caption link returned when video page HTML contains no caption
        """
        mock_get.return_value = YoutubeVideoHTMLResponse.with_caption_track(language_code)

        english_language_caption_link = get_transcript_link_from_youtube(YOUTUBE_VIDEO_ID)
        self.assertIsNone(english_language_caption_link)  # noqa: PT009

    @ mock.patch('requests.get')
    def test_get_caption_malformed_caption_locator(self, mock_get):
        """
        Caption track provided on video page for the selected language, but with broken syntax
        """
        language_code = 'en'
        mock_get.return_value = YoutubeVideoHTMLResponse.with_malformed_caption_track(language_code)

        english_language_caption_link = get_transcript_link_from_youtube(YOUTUBE_VIDEO_ID)
        self.assertIsNone(english_language_caption_link)  # noqa: PT009
