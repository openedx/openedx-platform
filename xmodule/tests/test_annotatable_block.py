"""Annotatable block tests"""


from django.test import TestCase
from django.test.utils import override_settings
from lxml import etree
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator
from xblock.field_data import DictFieldData
from xblock.fields import ScopeIds

from xmodule import annotatable_block

from . import get_test_system


class _AnnotatableBlockTestCaseBase(TestCase):  # pylint: disable=missing-class-docstring
    sample_xml = '''
        <annotatable display_name="Iliad">
            <instructions>Read the text.</instructions>
            <p>
                <annotation body="first">Sing</annotation>,
                <annotation title="goddess" body="second">O goddess</annotation>,
                <annotation title="anger" body="third" highlight="blue">the anger of Achilles son of Peleus</annotation>,
                that brought <i>countless</i> ills upon the Achaeans. Many a brave soul did it send
                hurrying down to Hades, and many a hero did it yield a prey to dogs and
                <div style="font-weight:bold"><annotation body="fourth" problem="4">vultures</annotation>, for so were the counsels
                of Jove fulfilled from the day on which the son of Atreus, king of men, and great
                Achilles, first fell out with one another.</div>
            </p>
            <annotation title="footnote" body="the end">The Iliad of Homer by Samuel Butler</annotation>
        </annotatable>
    '''

    __test__ = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.annotatable_class = annotatable_block.reset_class()

    def setUp(self):
        super().setUp()
        self.annotatable = self.annotatable_class(
            get_test_system(),
            DictFieldData({'data': self.sample_xml}),
            ScopeIds(None, None, None, BlockUsageLocator(CourseLocator('org', 'course', 'run'), 'category', 'name'))
        )

    def test_annotation_data_attr(self):
        el = etree.fromstring('<annotation title="bar" body="foo" problem="0">test</annotation>')

        expected_attr = {
            'data-comment-body': {'value': 'foo', '_delete': 'body'},
            'data-comment-title': {'value': 'bar', '_delete': 'title'},
            'data-problem-id': {'value': '0', '_delete': 'problem'}
        }

        actual_attr = self.annotatable._get_annotation_data_attr(0, el)  # pylint: disable=protected-access

        assert isinstance(actual_attr, dict)
        self.assertDictEqual(expected_attr, actual_attr)  # noqa: PT009

    def test_annotation_class_attr_default(self):
        xml = '<annotation title="x" body="y" problem="0">test</annotation>'
        el = etree.fromstring(xml)

        expected_attr = {'class': {'value': 'annotatable-span highlight'}}
        actual_attr = self.annotatable._get_annotation_class_attr(0, el)  # pylint: disable=protected-access

        assert isinstance(actual_attr, dict)
        self.assertDictEqual(expected_attr, actual_attr)  # noqa: PT009

    def test_annotation_class_attr_with_valid_highlight(self):
        xml = '<annotation title="x" body="y" problem="0" highlight="{highlight}">test</annotation>'

        for color in self.annotatable.HIGHLIGHT_COLORS:
            el = etree.fromstring(xml.format(highlight=color))
            value = f'annotatable-span highlight highlight-{color}'

            expected_attr = {
                'class': {
                    'value': value,
                    '_delete': 'highlight'
                }
            }
            actual_attr = self.annotatable._get_annotation_class_attr(0, el)  # pylint: disable=protected-access

            assert isinstance(actual_attr, dict)
            self.assertDictEqual(expected_attr, actual_attr)  # noqa: PT009

    def test_annotation_class_attr_with_invalid_highlight(self):
        xml = '<annotation title="x" body="y" problem="0" highlight="{highlight}">test</annotation>'

        for invalid_color in ['rainbow', 'blink', 'invisible', '', None]:
            el = etree.fromstring(xml.format(highlight=invalid_color))
            expected_attr = {
                'class': {
                    'value': 'annotatable-span highlight',
                    '_delete': 'highlight'
                }
            }
            actual_attr = self.annotatable._get_annotation_class_attr(0, el)  # pylint: disable=protected-access

            assert isinstance(actual_attr, dict)
            self.assertDictEqual(expected_attr, actual_attr)  # noqa: PT009

    def test_render_annotation(self):
        expected_html = '<span class="annotatable-span highlight highlight-yellow" data-comment-title="x" data-comment-body="y" data-problem-id="0">z</span>'  # pylint: disable=line-too-long
        expected_el = etree.fromstring(expected_html)

        actual_el = etree.fromstring('<annotation title="x" body="y" problem="0" highlight="yellow">z</annotation>')
        self.annotatable._render_annotation(0, actual_el)  # pylint: disable=protected-access

        assert expected_el.tag == actual_el.tag
        assert expected_el.text == actual_el.text
        self.assertDictEqual(dict(expected_el.attrib), dict(actual_el.attrib))  # noqa: PT009

    def test_render_content(self):
        content = self.annotatable._render_content()  # pylint: disable=protected-access
        el = etree.fromstring(content)

        assert 'div' == el.tag, 'root tag is a div'

        expected_num_annotations = 5
        actual_num_annotations = el.xpath('count(//span[contains(@class,"annotatable-span")])')
        assert expected_num_annotations == actual_num_annotations, 'check number of annotations'

    def test_get_html(self):
        context = self.annotatable.get_html()
        for key in ['display_name', 'element_id', 'content_html', 'instructions_html']:
            assert key in context

    def test_extract_instructions(self):
        xmltree = etree.fromstring(self.sample_xml)

        expected_xml = "<div>Read the text.</div>"
        actual_xml = self.annotatable._extract_instructions(xmltree)  # pylint: disable=protected-access
        assert actual_xml is not None
        assert expected_xml.strip() == actual_xml.strip()

        xmltree = etree.fromstring('<annotatable>foo</annotatable>')
        actual = self.annotatable._extract_instructions(xmltree)  # pylint: disable=protected-access
        assert actual is None

    def test_instruction_removal(self):
        xmltree = etree.fromstring(self.sample_xml)
        instructions = self.annotatable._extract_instructions(xmltree)  # pylint: disable=protected-access

        assert instructions is not None
        assert "Read the text." in instructions
        assert xmltree.find("instructions") is None


@override_settings(USE_EXTRACTED_ANNOTATABLE_BLOCK=True)
class ExtractedAnnotatableBlockTestCase(_AnnotatableBlockTestCaseBase):
    __test__ = True


@override_settings(USE_EXTRACTED_ANNOTATABLE_BLOCK=False)
class BuiltInAnnotatableBlockTestCase(_AnnotatableBlockTestCaseBase):
    __test__ = True
