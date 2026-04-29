"""
This test will run for annotator_mixin.py
"""


import unittest

from lxml import etree

from xmodule.annotator_mixin import get_extension, get_instructions, html_to_text


class HelperFunctionTest(unittest.TestCase):
    """
    Tests to ensure that the following helper functions work for the annotation tool
    """
    sample_xml = '''
        <annotatable>
            <instructions><p>Helper Test Instructions.</p></instructions>
        </annotatable>
    '''
    sample_sourceurl = "http://video-js.zencoder.com/oceans-clip.mp4"
    sample_youtubeurl = "http://www.youtube.com/watch?v=yxLIu-scR9Y"
    sample_html = '<p><b>Testing here</b> and not bolded here</p>'
    sample_html_with_image_alt = '''<p>Testing here with image: </p><p><img src="/static/image.jpg" alt="the alt text" width="560" height="315" /></p>'''  # noqa: E501
    sample_html_with_no_image_alt = '''<p>Testing here with image: </p><p><img src="/static/image.jpg" width="560" height="315" /></p>'''  # noqa: E501

    def test_get_instructions(self):
        """
        Function takes in an input of a specific xml string with surrounding instructions
        tags and returns a valid html string.
        """
        xmltree = etree.fromstring(self.sample_xml)

        expected_xml = "<div><p>Helper Test Instructions.</p></div>"
        actual_xml = get_instructions(xmltree)
        assert actual_xml is not None
        assert expected_xml.strip() == actual_xml.strip()

        xmltree = etree.fromstring('<annotatable>foo</annotatable>')
        actual = get_instructions(xmltree)
        assert actual is None

    def test_get_extension(self):
        """
        Tests whether given a url if the video will return a youtube source or extension
        """
        expectedyoutube = 'video/youtube'
        expectednotyoutube = 'video/mp4'
        result1 = get_extension(self.sample_sourceurl)
        result2 = get_extension(self.sample_youtubeurl)
        assert expectedyoutube == result2
        assert expectednotyoutube == result1

    def test_html_to_text(self):
        expectedtext = "Testing here and not bolded here"
        result = html_to_text(self.sample_html)
        assert expectedtext == result

    def test_html_image_with_alt_text(self):
        expectedtext = "Testing here with image: the alt text"
        result = html_to_text(self.sample_html_with_image_alt)
        assert expectedtext == result

    def test_html_image_with_no_alt_text(self):
        expectedtext = "Testing here with image: "
        result = html_to_text(self.sample_html_with_no_image_alt)
        assert expectedtext == result
