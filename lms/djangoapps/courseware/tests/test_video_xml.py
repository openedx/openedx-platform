# pylint: disable=protected-access

"""Test for Video Xmodule functional logic.
These test data read from xml, not from mongo.

We have a ModuleStoreTestCase class defined in
xmodule/modulestore/tests/django_utils.py.
You can search for usages of this in the cms and lms tests for examples.
You use this so that it will do things like point the modulestore
setting to mongo, flush the contentstore before and after, load the
templates, etc.
You can then use the CourseFactory and BlockFactory as defined in
xmodule/modulestore/tests/factories.py to create the
course, section, subsection, unit, etc.
"""


from django.test import TestCase

from xmodule.video_block import VideoBlock

SOURCE_XML = """
    <video show_captions="true"
    display_name="A Name"
    youtube="0.75:jNCf2gIqpeE,1.0:ZwkTiUPN0mg,1.25:rsq9auxASqI,1.50:kMyNdzVHHgg"
    sub="a_sub_file.srt.sjson"
    download_video="true"
    start_time="3603.0" end_time="3610.0"
    >
        <source src="example.mp4"/>
        <source src="example.webm"/>
        <transcript language="uk" src="ukrainian_translation.srt" />
    </video>
"""

PUBLIC_SOURCE_XML = """
    <video show_captions="true"
    display_name="A Name"
    youtube="0.75:jNCf2gIqpeE,1.0:ZwkTiUPN0mg,1.25:rsq9auxASqI,1.50:kMyNdzVHHgg"
    sub="a_sub_file.srt.sjson"
    download_video="true"
    public_access="true"
    start_time="3603.0" end_time="3610.0"
    >
        <source src="example.mp4"/>
        <source src="example.webm"/>
        <transcript language="uk" src="ukrainian_translation.srt" />
    </video>
"""


class VideoBlockLogicTest(TestCase):
    """Tests for logic of VideoBlock."""

    raw_field_data = {
        'data': '<video />'
    }

    def test_parse_youtube(self):
        """Test parsing old-style Youtube ID strings into a dict."""
        youtube_str = '0.75:jNCf2gIqpeE,1.00:ZwkTiUPN0mg,1.25:rsq9auxASqI,1.50:kMyNdzVHHgg'
        output = VideoBlock._parse_youtube(youtube_str)
        assert output == {'0.75': 'jNCf2gIqpeE', '1.00': 'ZwkTiUPN0mg', '1.25': 'rsq9auxASqI', '1.50': 'kMyNdzVHHgg'}

    def test_parse_youtube_one_video(self):
        """
        Ensure that all keys are present and missing speeds map to the
        empty string.
        """
        youtube_str = '0.75:jNCf2gIqpeE'
        output = VideoBlock._parse_youtube(youtube_str)
        assert output == {'0.75': 'jNCf2gIqpeE', '1.00': '', '1.25': '', '1.50': ''}

    def test_parse_youtube_key_format(self):
        """
        Make sure that inconsistent speed keys are parsed correctly.
        """
        youtube_str = '1.00:p2Q6BrNhdh8'
        youtube_str_hack = '1.0:p2Q6BrNhdh8'
        assert VideoBlock._parse_youtube(youtube_str) == VideoBlock._parse_youtube(youtube_str_hack)

    def test_parse_youtube_empty(self):
        """
        Some courses have empty youtube attributes, so we should handle
        that well.
        """
        assert VideoBlock._parse_youtube('') == {'0.75': '', '1.00': '', '1.25': '', '1.50': ''}
