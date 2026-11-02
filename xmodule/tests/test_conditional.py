# lint-amnesty, pylint: disable=missing-module-docstring

import json
import unittest
from unittest.mock import Mock, patch

from django.conf import settings
from fs.memoryfs import MemoryFS
from lxml import etree
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator
from web_fragments.fragment import Fragment
from xblock.field_data import DictFieldData
from xblock.fields import ScopeIds

from xmodule.conditional_block import ConditionalBlock
from xmodule.error_block import ErrorBlock
from xmodule.modulestore.xml import CourseLocationManager, XMLImportingModuleStoreRuntime, XMLModuleStore
from xmodule.tests import DATA_DIR, get_test_system, prepare_block_runtime
from xmodule.tests.xml import XModuleXmlImportTest
from xmodule.tests.xml import factories as xml
from xmodule.validation import StudioValidationMessage
from xmodule.x_module import AUTHOR_VIEW, STUDENT_VIEW

ORG = 'test_org'
COURSE = 'conditional'      # name of directory with course data


class DummyModuleStoreRuntime(XMLImportingModuleStoreRuntime):  # pylint: disable=abstract-method
    """
    Minimal modulestore runtime for tests
    """

    @patch('xmodule.modulestore.xml.OSFS', lambda directory: MemoryFS())
    def __init__(self, load_error_blocks):

        xmlstore = XMLModuleStore("data_dir", source_dirs=[], load_error_blocks=load_error_blocks)

        super().__init__(
            xmlstore=xmlstore,
            course_id=CourseKey.from_string('/'.join([ORG, COURSE, 'test_run'])),
            course_dir='test_dir',
            error_tracker=Mock(),
            load_error_blocks=load_error_blocks,
        )

    @property
    def render_template(self):  # lint-amnesty, pylint: disable=method-hidden
        raise Exception("Shouldn't be called")


class ConditionalBlockFactory(xml.XmlImportFactory):
    """
    Factory for generating ConditionalBlock for testing purposes
    """
    tag = 'conditional'


class ConditionalFactory:
    """
    A helper class to create a conditional block and associated source and child blocks
    to allow for testing.
    """
    @staticmethod
    def create(system, source_is_error_block=False, source_visible_to_staff_only=False):
        """
        return a dict of blocks: the conditional with a single source and a single child.
        Keys are 'cond_block', 'source_block', and 'child_block'.

        if the source_is_error_block flag is set, create a real ErrorBlock for the source.
        """

        # construct source block and module:
        source_location = BlockUsageLocator(CourseLocator("edX", "conditional_test", "test_run", deprecated=True),
                                            "problem", "SampleProblem", deprecated=True)
        if source_is_error_block:
            # Make an error block
            source_block = ErrorBlock.from_xml(
                'some random xml data',
                system,
                id_generator=CourseLocationManager(source_location.course_key),
                error_msg='random error message'
            )
        else:
            source_block = Mock(name='source_block')
            source_block.location = source_location

        source_block.visible_to_staff_only = source_visible_to_staff_only
        source_block.runtime = system
        source_block.render = lambda view, context=None: system.render(source_block, view, context)

        # construct other blocks:
        child_block = Mock(name='child_block')
        child_block.visible_to_staff_only = False
        child_block._xmodule.student_view.return_value = Fragment(content='<p>This is a secret</p>')  # lint-amnesty, pylint: disable=protected-access
        child_block.student_view = child_block._xmodule.student_view  # lint-amnesty, pylint: disable=protected-access
        child_block.runtime = system
        child_block.render = lambda view, context=None: system.render(child_block, view, context)
        child_block.location = source_location.replace(category='html', name='child')

        def visible_to_nonstaff_users(desc):
            """
            Returns if the object is visible to nonstaff users.
            """
            return not desc.visible_to_staff_only

        def load_item(usage_id, for_parent=None):  # pylint: disable=unused-argument
            """Test-only implementation of load_item that simply returns static xblocks."""
            return {
                child_block.location: child_block,
                source_location: source_block
            }.get(usage_id)

        system.load_item = load_item

        # construct conditional block:
        cond_location = BlockUsageLocator(CourseLocator("edX", "conditional_test", "test_run", deprecated=True),
                                          "conditional", "SampleConditional", deprecated=True)
        field_data = DictFieldData({
            'data': '<conditional/>',
            'conditional_attr': 'attempted',
            'conditional_value': 'true',
            'xml_attributes': {'attempted': 'true'},
            'children': [child_block.location],
        })

        cond_block = ConditionalBlock(
            system,
            field_data,
            ScopeIds(None, None, cond_location, cond_location)
        )
        system.get_block_for_descriptor = lambda desc: desc if visible_to_nonstaff_users(desc) else None
        cond_block.get_required_blocks = [
            system.get_block_for_descriptor(source_block),
        ]

        # return dict:
        return {'cond_block': cond_block,
                'source_block': source_block,
                'child_block': child_block}


class ConditionalBlockBasicTest(unittest.TestCase):
    """
    Make sure that conditional block works, using mocks for
    other blocks.
    """

    def setUp(self):
        super().setUp()
        self.test_system = get_test_system()

    def test_icon_class(self):
        '''verify that get_icon_class works independent of condition satisfaction'''
        blocks = ConditionalFactory.create(self.test_system)
        for attempted in ["false", "true"]:
            for icon_class in ['other', 'problem', 'video']:
                blocks['source_block'].is_attempted = attempted
                blocks['child_block'].get_icon_class = lambda: icon_class  # lint-amnesty, pylint: disable=cell-var-from-loop
                assert blocks['cond_block'].get_icon_class() == icon_class

    def test_get_html(self):
        blocks = ConditionalFactory.create(self.test_system)
        # because get_test_system returns the repr of the context dict passed to render_template,
        # we reverse it here
        html = blocks['cond_block'].render(STUDENT_VIEW).content
        mako_service = blocks['cond_block'].runtime.service(blocks['cond_block'], 'mako')
        expected = mako_service.render_lms_template('conditional_ajax.html', {
            'ajax_url': blocks['cond_block'].ajax_url,
            'element_id': 'i4x-edX-conditional_test-conditional-SampleConditional',
            'depends': 'i4x-edX-conditional_test-problem-SampleProblem',
        })
        assert expected == html

    def test_handle_ajax(self):
        blocks = ConditionalFactory.create(self.test_system)
        blocks['cond_block'].save()
        blocks['source_block'].is_attempted = "false"
        ajax = json.loads(blocks['cond_block'].handle_ajax('', ''))
        fragments = ajax['fragments']
        assert not any(('This is a secret' in item['content']) for item in fragments)

        # now change state of the capa problem to make it completed
        blocks['source_block'].is_attempted = "true"
        ajax = json.loads(blocks['cond_block'].handle_ajax('', ''))
        blocks['cond_block'].save()
        fragments = ajax['fragments']
        assert any(('This is a secret' in item['content']) for item in fragments)

    def test_error_as_source(self):
        '''
        Check that handle_ajax works properly if the source is really an ErrorBlock,
        and that the condition is not satisfied.
        '''
        blocks = ConditionalFactory.create(self.test_system, source_is_error_block=True)
        blocks['cond_block'].save()
        ajax = json.loads(blocks['cond_block'].handle_ajax('', ''))
        fragments = ajax['fragments']
        assert not any(('This is a secret' in item['content']) for item in fragments)

    @patch('xmodule.conditional_block.log')
    def test_conditional_with_staff_only_source_block(self, mock_log):
        blocks = ConditionalFactory.create(
            self.test_system,
            source_visible_to_staff_only=True,
        )
        cond_block = blocks['cond_block']
        cond_block.save()
        cond_block.is_attempted = "false"
        cond_block.handle_ajax('', '')
        assert not mock_log.warn.called
        assert None in cond_block.get_required_blocks


class ConditionalBlockXmlTest(unittest.TestCase):
    """
    Make sure ConditionalBlock works, by loading data in from an XML-defined course.
    """

    def setUp(self):
        super().setUp()

        def add_block_as_child_node(block, node):
            child = etree.SubElement(node, "unknown")
            block.add_xml_to_node(child)
        self.test_system = get_test_system(add_get_block_overrides=True)
        self.test_system.add_block_as_child_node = add_block_as_child_node
        self.modulestore = XMLModuleStore(DATA_DIR, source_dirs=['conditional_and_poll'])
        courses = self.modulestore.get_courses()
        assert len(courses) == 1
        self.course = courses[0]

    def get_block_for_location(self, location):
        block = self.modulestore.get_item(location, depth=None)
        return self.test_system.get_block_for_descriptor(block)

    @patch('xmodule.x_module.block_global_local_resource_url')
    @patch.dict(settings.FEATURES, {'ENABLE_EDXNOTES': False})
    def test_conditional_block(self, _):
        """Make sure that conditional block works"""
        # edx - HarvardX
        # cond_test - ER22x
        location = BlockUsageLocator(CourseLocator("HarvardX", "ER22x", "2013_Spring", deprecated=True),
                                     "conditional", "condone", deprecated=True)

        block = self.get_block_for_location(location)
        html = block.render(STUDENT_VIEW).content
        mako_service = block.runtime.service(block, 'mako')
        html_expect = mako_service.render_lms_template(
            'conditional_ajax.html',
            {
                # Test ajax url is just usage-id / handler_name
                'ajax_url': f'{str(location)}/xmodule_handler',
                'element_id': 'i4x-HarvardX-ER22x-conditional-condone',
                'depends': 'i4x-HarvardX-ER22x-problem-choiceprob'
            }
        )
        assert html == html_expect

        ajax = json.loads(block.handle_ajax('', ''))
        fragments = ajax['fragments']
        assert not any(('This is a secret' in item['content']) for item in fragments)

        # Now change state of the capa problem to make it completed
        inner_block = self.get_block_for_location(location.replace(category="problem", name='choiceprob'))
        inner_block.attempts = 1
        # Save our modifications to the underlying KeyValueStore so they can be persisted
        inner_block.save()

        ajax = json.loads(block.handle_ajax('', ''))
        fragments = ajax['fragments']
        assert any(('This is a secret' in item['content']) for item in fragments)

    def test_conditional_block_with_empty_sources_list(self):
        """
        If a ConditionalBlock is initialized with an empty sources_list, we assert that the sources_list is set
        via generating UsageKeys from the values in xml_attributes['sources']
        """
        dummy_system = Mock()
        dummy_location = BlockUsageLocator(CourseLocator("edX", "conditional_test", "test_run"),
                                           "conditional", "SampleConditional")
        dummy_scope_ids = ScopeIds(None, None, dummy_location, dummy_location)
        dummy_field_data = DictFieldData({
            'data': '<conditional/>',
            'xml_attributes': {'sources': 'i4x://HarvardX/ER22x/poll_question/T15_poll'},
            'children': None,
        })
        conditional = ConditionalBlock(
            dummy_system,
            dummy_field_data,
            dummy_scope_ids,
        )

        new_run = conditional.location.course_key.run  # lint-amnesty, pylint: disable=unused-variable
        assert conditional.sources_list[0] == BlockUsageLocator.from_string(conditional.xml_attributes['sources'])\
            .replace(run=dummy_location.course_key.run)

    def test_conditional_block_parse_sources(self):
        dummy_system = Mock()
        dummy_location = BlockUsageLocator(CourseLocator("edX", "conditional_test", "test_run"),
                                           "conditional", "SampleConditional")
        dummy_scope_ids = ScopeIds(None, None, dummy_location, dummy_location)
        dummy_field_data = DictFieldData({
            'data': '<conditional/>',
            'xml_attributes': {'sources': 'i4x://HarvardX/ER22x/poll_question/T15_poll;i4x://HarvardX/ER22x/poll_question/T16_poll'},  # lint-amnesty, pylint: disable=line-too-long
            'children': None,
        })
        conditional = ConditionalBlock(
            dummy_system,
            dummy_field_data,
            dummy_scope_ids,
        )
        assert conditional.parse_sources(conditional.xml_attributes) == ['i4x://HarvardX/ER22x/poll_question/T15_poll',
                                                                         'i4x://HarvardX/ER22x/poll_question/T16_poll']

    def test_conditional_block_parse_attr_values(self):
        root = '<conditional attempted="false"></conditional>'
        xml_object = etree.XML(root)
        definition = ConditionalBlock.definition_from_xml(xml_object, Mock())[0]
        expected_definition = {
            'show_tag_list': [],
            'conditional_attr': 'attempted',
            'conditional_value': 'false',
            'conditional_message': ''
        }

        assert definition == expected_definition

    def test_presence_attributes_in_xml_attributes(self):
        blocks = ConditionalFactory.create(self.test_system)
        blocks['cond_block'].save()
        blocks['cond_block'].definition_to_xml(Mock())
        expected_xml_attributes = {
            'attempted': 'true',
            'message': 'You must complete {link} before you can access this unit.',
            'sources': ''
        }
        self.assertDictEqual(blocks['cond_block'].xml_attributes, expected_xml_attributes)


class ConditionalBlockStudioTest(XModuleXmlImportTest):
    """
    Unit tests for how conditional test interacts with Studio.
    """

    def setUp(self):
        super().setUp()
        course = xml.CourseFactory.build()
        sequence = xml.SequenceFactory.build(parent=course)
        conditional = ConditionalBlockFactory(
            parent=sequence,
            attribs={
                'group_id_to_child': '{"0": "i4x://edX/xml_test_course/html/conditional_0"}'
            }
        )
        xml.HtmlFactory(parent=conditional, url_name='conditional_0', text='This is a secret HTML')

        self.course = self.process_xml(course)
        self.sequence = self.course.get_children()[0]
        self.conditional = self.sequence.get_children()[0]

        user = Mock(username='ma', email='ma@edx.org', is_staff=False, is_active=True)
        self.conditional.runtime = prepare_block_runtime(self.course.runtime)
        self.conditional.bind_for_student(
            user.id
        )

    def test_render_author_view(self,):
        """
        Test the rendering of the Studio author view.
        """

        def create_studio_context(root_xblock, is_unit_page):
            """
            Context for rendering the studio "author_view".
            """
            return {
                'reorderable_items': set(),
                'root_xblock': root_xblock,
                'is_unit_page': is_unit_page
            }

        context = create_studio_context(self.conditional, False)
        html = self.course.runtime.render(self.conditional, AUTHOR_VIEW, context).content
        assert 'This is a secret HTML' in html

        context = create_studio_context(self.sequence, True)
        html = self.course.runtime.render(self.conditional, AUTHOR_VIEW, context).content
        assert 'This is a secret HTML' not in html

    def test_non_editable_settings(self):
        """
        Test the settings that are marked as "non-editable".
        """
        non_editable_metadata_fields = self.conditional.non_editable_metadata_fields
        assert ConditionalBlock.due in non_editable_metadata_fields

    def test_validation_messages(self):
        """
        Test the validation message for a correctly configured conditional.
        """
        self.conditional.sources_list = None
        validation = self.conditional.validate()
        assert validation.summary.text == 'This component has no source components configured yet.'
        assert validation.summary.type == StudioValidationMessage.NOT_CONFIGURED
        assert validation.summary.action_class == 'edit-button'
        assert validation.summary.action_label == 'Configure list of sources'
