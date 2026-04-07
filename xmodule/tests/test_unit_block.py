"""
Tests for the Unit XBlock
"""

import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from web_fragments.fragment import Fragment
from xblock.completable import XBlockCompletionMode
from xblock.core import XBlock
from xblock.test.test_parsing import XmlTest

from xmodule.unit_block import UnitBlock


class FakeHTMLBlock(XBlock):
    """ An HTML block for use in tests """
    def student_view(self, context=None):  # pylint: disable=unused-argument
        """Provide simple HTML student view."""
        return Fragment("This is some HTML.")


class FakeVideoBlock(XBlock):
    """ A video block for use in tests """
    def student_view(self, context=None):  # pylint: disable=unused-argument
        """Provide simple Video student view."""
        return Fragment(
            '<iframe width="560" height="315" src="https://www.youtube.com/embed/B-EFayAA5_0"'
            ' frameborder="0" allow="autoplay; encrypted-media"></iframe>'
        )


class UnitBlockTests(XmlTest, unittest.TestCase):
    """
    Tests of the Unit XBlock.

    There's not much to this block, so we keep it simple.
    """
    maxDiff = None

    @XBlock.register_temp_plugin(FakeHTMLBlock, identifier='fake-html')
    @XBlock.register_temp_plugin(FakeVideoBlock, identifier='fake-video')
    def test_unit_html(self):
        block = self.parse_xml_to_block("""\
            <unit>
                <fake-html/>
                <fake-video/>
            </unit>
        """)

        with patch.object(block.runtime, 'applicable_aside_types', return_value=[]):  # Disable problematic Acid aside
            html = block.runtime.render(block, 'student_view').content

        self.assertXmlEqual(html, (
            '<div class="xblock-v1 xblock-v1-student_view" data-usage="u_1" data-block-type="unit">'
            '<div class="unit-xblock vertical">'
            '<div class="xblock-v1 xblock-v1-student_view" data-usage="u_3" data-block-type="fake-html">'
            'This is some HTML.'
            '</div>'
            '<div class="xblock-v1 xblock-v1-student_view" data-usage="u_5" data-block-type="fake-video">'
            '<iframe width="560" height="315" src="https://www.youtube.com/embed/B-EFayAA5_0"'
            ' frameborder="0" allow="autoplay; encrypted-media"></iframe>'
            '</div>'
            '</div>'
            '</div>'
        ))

    def test_is_aggregator(self):
        """
        The unit XBlock is designed to hold other XBlocks, so check that its
        completion status is defined as the aggregation of its child blocks.
        """
        assert XBlockCompletionMode.get_mode(UnitBlock) == XBlockCompletionMode.AGGREGATOR

    def assertXmlEqual(self, xml_str_a: str, xml_str_b: str) -> bool:
        """ Assert that the given XML strings are equal, ignoring attribute order and some whitespace variations. """
        self.assertEqual(
            ElementTree.canonicalize(xml_str_a, strip_text=True),
            ElementTree.canonicalize(xml_str_b, strip_text=True),
        )
