# pylint: disable=missing-module-docstring

import unittest
from unittest.mock import Mock

import ddt
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.test.utils import override_settings
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator
from xblock.field_data import DictFieldData
from xblock.fields import ScopeIds

from xmodule import html_block
from xmodule.html_block import CourseInfoBlock

from ..x_module import PUBLIC_VIEW, STUDENT_VIEW
from . import get_test_descriptor_system, get_test_system


@ddt.ddt
class _HtmlBlockCourseApiTestCaseBase(TestCase):
    """
    Test the HTML XModule's student_view_data method.
    """

    __test__ = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html_class = html_block.reset_class()

    @ddt.data(
        {},
        dict(FEATURES={}),
        dict(FEATURES=dict(ENABLE_HTML_XBLOCK_STUDENT_VIEW_DATA=False))
    )
    def test_disabled(self, settings):
        """
        Ensure that student_view_data does not return html if the ENABLE_HTML_XBLOCK_STUDENT_VIEW_DATA feature flag
        is not set.
        """
        field_data = DictFieldData({'data': '<h1>Some HTML</h1>'})
        module_system = get_test_system()
        block = self.html_class(module_system, field_data, Mock())

        with override_settings(**settings):
            assert block.student_view_data() ==\
                   dict(enabled=False, message='To enable, set FEATURES["ENABLE_HTML_XBLOCK_STUDENT_VIEW_DATA"]')

    @ddt.data(
        '<h1>Some content</h1>',  # Valid HTML
        '',
        None,
        '<h1>Some content</h',  # Invalid HTML
        '<script>alert()</script>',  # Does not escape tags
        '<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7">',  # Images allowed
        'short string ' * 100,  # May contain long strings
    )
    @override_settings(FEATURES=dict(ENABLE_HTML_XBLOCK_STUDENT_VIEW_DATA=True))
    def test_common_values(self, html):
        """
        Ensure that student_view_data will return HTML data when enabled,
        can handle likely input,
        and doesn't modify the HTML in any way.

        This means that it does NOT protect against XSS, escape HTML tags, etc.

        Note that the %%USER_ID%% substitution is tested below.
        """
        field_data = DictFieldData({'data': html})
        module_system = get_test_system()
        block = self.html_class(module_system, field_data, Mock())
        assert block.student_view_data() == dict(enabled=True, html=html)

    @ddt.data(
        STUDENT_VIEW,
        PUBLIC_VIEW,
    )
    def test_student_preview_view(self, view):
        """
        Ensure that student_view and public_view renders correctly.
        """
        html = '<p>This is a test</p>'
        field_data = DictFieldData({'data': html})
        module_system = get_test_system()
        block = self.html_class(module_system, field_data, Mock())
        rendered = module_system.render(block, view, {}).content
        assert html in rendered


class _HtmlBlockSubstitutionTestCaseBase(TestCase):  # pylint: disable=missing-class-docstring

    __test__ = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html_class = html_block.reset_class()

    def test_substitution_user_id(self):
        sample_xml = '''%%USER_ID%%'''
        field_data = DictFieldData({'data': sample_xml})
        module_system = get_test_system()
        block = self.html_class(module_system, field_data, Mock())
        assert block.get_html() == str(module_system.anonymous_student_id)

    def test_substitution_course_id(self):
        sample_xml = '''%%COURSE_ID%%'''
        field_data = DictFieldData({'data': sample_xml})
        module_system = get_test_system()
        block = self.html_class(module_system, field_data, Mock())
        course_key = CourseLocator(
            org='some_org',
            course='some_course',
            run='some_run'
        )
        usage_key = BlockUsageLocator(
            course_key=course_key,
            block_type='problem',
            block_id='block_id'
        )
        block.scope_ids.usage_id = usage_key
        assert block.get_html() == str(course_key)

    def test_substitution_without_magic_string(self):
        sample_xml = '''
            <html>
                <p>Hi USER_ID!11!</p>
            </html>
        '''
        field_data = DictFieldData({'data': sample_xml})
        module_system = get_test_system()
        block = self.html_class(module_system, field_data, Mock())
        assert block.get_html() == sample_xml

    def test_substitution_without_anonymous_student_id(self):
        sample_xml = '''%%USER_ID%%'''
        field_data = DictFieldData({'data': sample_xml})
        module_system = get_test_system(user=AnonymousUser())
        block = self.html_class(module_system, field_data, Mock())
        block.runtime.service(block, 'user')._deprecated_anonymous_user_id = ''  # pylint: disable=protected-access
        assert block.get_html() == sample_xml


class _HtmlBlockIndexingTestCaseBase(TestCase):
    """
    Make sure that HtmlBlock can format data for indexing as expected.
    """

    __test__ = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html_class = html_block.reset_class()

    def instantiate_block(self, **field_data):
        """
        Instantiate HtmlBlock with field data.
        """
        system = get_test_descriptor_system()
        course_key = CourseLocator('org', 'course', 'run')
        usage_key = course_key.make_usage_key('html', 'SampleHtml')
        return system.construct_xblock_from_class(
            self.html_class,
            scope_ids=ScopeIds(None, None, usage_key, usage_key),
            field_data=DictFieldData(field_data),
        )

    def test_index_dictionary_simple_html_block(self):
        sample_xml = '''
            <html>
                <p>Hello World!</p>
            </html>
        '''
        block = self.instantiate_block(data=sample_xml)
        assert block.index_dictionary() ==\
               {'content': {'html_content': ' Hello World! ', 'display_name': 'Text'}, 'content_type': 'Text'}

    def test_index_dictionary_cdata_html_block(self):
        sample_xml_cdata = '''
            <html>
                <p>This has CDATA in it.</p>
                <![CDATA[This is just a CDATA!]]>
            </html>
        '''
        block = self.instantiate_block(data=sample_xml_cdata)
        assert block.index_dictionary() ==\
               {'content': {'html_content': ' This has CDATA in it. ', 'display_name': 'Text'}, 'content_type': 'Text'}

    def test_index_dictionary_multiple_spaces_html_block(self):
        sample_xml_tab_spaces = '''
            <html>
                <p>     Text has spaces :)  </p>
            </html>
        '''
        block = self.instantiate_block(data=sample_xml_tab_spaces)
        assert block.index_dictionary() ==\
               {'content': {'html_content': ' Text has spaces :) ', 'display_name': 'Text'}, 'content_type': 'Text'}

    def test_index_dictionary_html_block_with_comment(self):
        sample_xml_comment = '''
            <html>
                <p>This has HTML comment in it.</p>
                <!-- Html Comment -->
            </html>
        '''
        block = self.instantiate_block(data=sample_xml_comment)
        assert block.index_dictionary() == {'content': {'html_content': ' This has HTML comment in it. ', 'display_name': 'Text'}, 'content_type': 'Text'}  # pylint: disable=line-too-long

    def test_index_dictionary_html_block_with_both_comments_and_cdata(self):
        sample_xml_mix_comment_cdata = '''
            <html>
                <!-- Beginning of the html -->
                <p>This has HTML comment in it.<!-- Commenting Content --></p>
                <!-- Here comes CDATA -->
                <![CDATA[This is just a CDATA!]]>
                <p>HTML end.</p>
            </html>
        '''
        block = self.instantiate_block(data=sample_xml_mix_comment_cdata)
        assert block.index_dictionary() ==\
               {'content': {'html_content': ' This has HTML comment in it. HTML end. ',
                            'display_name': 'Text'}, 'content_type': 'Text'}

    def test_index_dictionary_html_block_with_script_and_style_tags(self):
        sample_xml_style_script_tags = '''
            <html>
                <style>p {color: green;}</style>
                <!-- Beginning of the html -->
                <p>This has HTML comment in it.<!-- Commenting Content --></p>
                <!-- Here comes CDATA -->
                <![CDATA[This is just a CDATA!]]>
                <p>HTML end.</p>
                <script>
                    var message = "Hello world!"
                </script>
            </html>
        '''
        block = self.instantiate_block(data=sample_xml_style_script_tags)
        assert block.index_dictionary() ==\
               {'content': {'html_content': ' This has HTML comment in it. HTML end. ',
                            'display_name': 'Text'}, 'content_type': 'Text'}


class CourseInfoBlockTestCase(unittest.TestCase):
    """
    Make sure that CourseInfoBlock renders updates properly.
    """

    def test_updates_render(self):
        """
        Tests that a course info block will render its updates, even if they are malformed.
        """
        sample_update_data = [
            {
                "id": i,
                "date": data,
                "content": "This is a very important update!",
                "status": CourseInfoBlock.STATUS_VISIBLE,
            } for i, data in enumerate(
                [
                    'January 1, 1970',
                    'Marchtober 45, -1963',
                    'Welcome!',
                    'Date means "title", right?'
                ]
            )
        ]
        info_block = CourseInfoBlock(
            get_test_system(),
            DictFieldData({'items': sample_update_data, 'data': ""}),
            Mock()
        )

        # Prior to TNL-4115, an exception would be raised when trying to parse invalid dates in this method
        try:
            info_block.get_html()
        except ValueError:
            self.fail("CourseInfoBlock could not parse an invalid date!")

    def test_updates_order(self):
        """
        Tests that a course info block will render its updates in the correct order.
        """
        sample_update_data = [
            {
                "id": 3,
                "date": "March 18, 1982",
                "content": "This is a very important update that was inserted last with an older date!",
                "status": CourseInfoBlock.STATUS_VISIBLE,
            },
            {
                "id": 1,
                "date": "January 1, 2012",
                "content": "This is a very important update that was inserted first!",
                "status": CourseInfoBlock.STATUS_VISIBLE,
            },
            {
                "id": 2,
                "date": "January 1, 2012",
                "content": "This is a very important update that was inserted second!",
                "status": CourseInfoBlock.STATUS_VISIBLE,
            }
        ]
        info_block = CourseInfoBlock(
            Mock(),
            DictFieldData({'items': sample_update_data, 'data': ""}),
            Mock()
        )

        # This is the expected context that should be used by the render function
        expected_context = {
            'visible_updates': [
                {
                    "id": 2,
                    "date": "January 1, 2012",
                    "content": "This is a very important update that was inserted second!",
                    "status": CourseInfoBlock.STATUS_VISIBLE,
                },
                {
                    "id": 1,
                    "date": "January 1, 2012",
                    "content": "This is a very important update that was inserted first!",
                    "status": CourseInfoBlock.STATUS_VISIBLE,
                },
                {
                    "id": 3,
                    "date": "March 18, 1982",
                    "content": "This is a very important update that was inserted last with an older date!",
                    "status": CourseInfoBlock.STATUS_VISIBLE,
                }
            ],
            'hidden_updates': [],
        }
        template_name = f"{info_block.TEMPLATE_DIR}/course_updates.html"
        info_block.get_html()
        # Assertion to validate that render function is called with the expected context
        info_block.runtime.service(info_block, 'mako').render_lms_template.assert_called_once_with(
            template_name,
            expected_context
        )


@override_settings(USE_EXTRACTED_HTML_BLOCK=True)
class ExtractedHtmlBlockCourseApiTestCase(_HtmlBlockCourseApiTestCaseBase):
    __test__ = True


@override_settings(USE_EXTRACTED_HTML_BLOCK=False)
class BuiltInHtmlBlockCourseApiTestCase(_HtmlBlockCourseApiTestCaseBase):
    __test__ = True


@override_settings(USE_EXTRACTED_HTML_BLOCK=True)
class ExtractedHtmlBlockSubstitutionTestCase(_HtmlBlockSubstitutionTestCaseBase):
    __test__ = True


@override_settings(USE_EXTRACTED_HTML_BLOCK=False)
class BuiltInHtmlBlockSubstitutionTestCase(_HtmlBlockSubstitutionTestCaseBase):
    __test__ = True


@override_settings(USE_EXTRACTED_HTML_BLOCK=True)
class ExtractedHtmlBlockIndexingTestCase(_HtmlBlockIndexingTestCaseBase):
    __test__ = True


@override_settings(USE_EXTRACTED_HTML_BLOCK=False)
class BuiltInHtmlBlockIndexingTestCase(_HtmlBlockIndexingTestCaseBase):
    __test__ = True
