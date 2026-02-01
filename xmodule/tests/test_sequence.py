"""
Tests for sequence block.
"""
# pylint: disable=no-member


import ast
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import ddt
from django.test import RequestFactory
from django.test.utils import override_settings
from django.utils.timezone import now
from freezegun import freeze_time
from web_fragments.fragment import Fragment

from edx_toggles.toggles.testutils import override_waffle_flag
from openedx.features.content_type_gating.models import ContentTypeGatingConfig
from xmodule.seq_block import TIMED_EXAM_GATING_WAFFLE_FLAG, SequenceBlock
from xmodule.tests import get_test_system, prepare_block_runtime
from xmodule.tests.helpers import StubUserService
from xmodule.tests.xml import XModuleXmlImportTest
from xmodule.tests.xml import factories as xml
from xmodule.x_module import PUBLIC_VIEW, STUDENT_VIEW
from lxml import etree

TODAY = now()
DUE_DATE = TODAY + timedelta(days=7)
PAST_DUE_BEFORE_END_DATE = TODAY + timedelta(days=14)
COURSE_END_DATE = TODAY + timedelta(days=21)


@ddt.ddt
class SequenceBlockTestCase(XModuleXmlImportTest):
    """
    Base class for tests of Sequence Block.
    """

    def setUp(self):
        super().setUp()

        course_xml = self._set_up_course_xml()
        self.course = self.process_xml(course_xml)
        self._set_up_module_system(self.course)

        for chapter_index in range(len(self.course.get_children())):
            chapter = self._set_up_block(self.course, chapter_index)
            setattr(self, f'chapter_{chapter_index + 1}', chapter)

            for sequence_index in range(len(chapter.get_children())):
                sequence = self._set_up_block(chapter, sequence_index)
                setattr(self, f'sequence_{chapter_index + 1}_{sequence_index + 1}', sequence)

    @staticmethod
    def _set_up_course_xml():
        """
        Sets up and returns XML course structure.
        """
        course = xml.CourseFactory.build(end=str(COURSE_END_DATE))

        chapter_1 = xml.ChapterFactory.build(parent=course)  # has 2 child sequences
        xml.ChapterFactory.build(parent=course)  # has 0 child sequences
        chapter_3 = xml.ChapterFactory.build(parent=course)  # has 1 child sequence
        chapter_4 = xml.ChapterFactory.build(parent=course)  # has 1 child sequence, with hide_after_due
        chapter_5 = xml.ChapterFactory.build(parent=course)  # has 1 child sequence, with a time limit

        xml.SequenceFactory.build(parent=chapter_1)
        xml.SequenceFactory.build(parent=chapter_1)
        sequence_3_1 = xml.SequenceFactory.build(parent=chapter_3)  # has 3 verticals
        xml.SequenceFactory.build(  # sequence_4_1
            parent=chapter_4,
            hide_after_due=str(True),
            due=str(DUE_DATE),
        )

        for _ in range(3):
            xml.VerticalFactory.build(parent=sequence_3_1)

        sequence_5_1 = xml.SequenceFactory.build(
            parent=chapter_5,
            is_time_limited=str(True)
        )
        vertical_5_1 = xml.VerticalFactory.build(parent=sequence_5_1)
        xml.ProblemFactory.build(parent=vertical_5_1)

        return course

    def _set_up_block(self, parent, index_in_parent):
        """
        Sets up the stub sequence block for testing.
        """
        block = parent.get_children()[index_in_parent]

        self._set_up_module_system(block)

        block.runtime._services['bookmarks'] = Mock()  # pylint: disable=protected-access
        block.runtime._services['user'] = StubUserService(user=Mock())  # pylint: disable=protected-access
        block.parent = parent.location
        return block

    def _set_up_module_system(self, block):
        """
        Sets up the test module system for the given block.
        """
        prepare_block_runtime(block.runtime)

        # The render operation will ask modulestore for the current course to get some data. As these tests were
        # originally not written to be compatible with a real modulestore, we've mocked out the relevant return values.
        block.runtime.modulestore = Mock()
        block.runtime.modulestore.get_course.return_value = self.course

    def _get_rendered_view(self,
                           sequence,
                           requested_child=None,
                           extra_context=None,
                           self_paced=False,
                           view=STUDENT_VIEW):
        """
        Returns the rendered student view for the given sequence and the
        requested_child parameter.
        """
        context = {'requested_child': requested_child}
        if extra_context:
            context.update(extra_context)

        self.course.self_paced = self_paced
        return sequence.runtime.render(sequence, view, context).content

    def _assert_view_at_position(self, rendered_html, expected_position):
        """
        Verifies that the rendered view contains the expected position.
        """
        assert f"'position': {expected_position}" in rendered_html

    def test_student_view_init(self):
        runtime = get_test_system()
        runtime.position = 2
        seq_block = SequenceBlock(runtime=runtime, scope_ids=Mock())
        seq_block.bind_for_student(34)
        assert seq_block.position == 2
        # matches position set in the runtime

    @ddt.unpack
    @ddt.data(
        {'view': STUDENT_VIEW},
        {'view': PUBLIC_VIEW},
    )
    def test_render_student_view(self, view):
        html = self._get_rendered_view(
            self.sequence_3_1,
            extra_context=dict(next_url='NextSequential', prev_url='PrevSequential'),
            view=view
        )
        self._assert_view_at_position(html, expected_position=1)
        assert str(self.sequence_3_1.location) in html
        assert "'gated': False" in html
        assert "'next_url': 'NextSequential'" in html
        assert "'prev_url': 'PrevSequential'" in html
        assert 'fa fa-check-circle check-circle is-hidden' not in html

    # pylint: disable=line-too-long
    @patch('xmodule.seq_block.SequenceBlock.gate_entire_sequence_if_it_is_a_timed_exam_and_contains_content_type_gated_problems')
    def test_timed_exam_gating_waffle_flag(self, mocked_function):  # pylint: disable=unused-argument
        """
        Verify the code inside the waffle flag is not executed with the flag off
        Verify the code inside the waffle flag is executed with the flag on
        """
        # the order of the overrides is important since the `assert_not_called` does
        # not appear to be limited to just the override_waffle_flag = False scope
        with override_waffle_flag(TIMED_EXAM_GATING_WAFFLE_FLAG, active=False):
            self._get_rendered_view(
                self.sequence_5_1,
                extra_context=dict(next_url='NextSequential', prev_url='PrevSequential'),
                view=STUDENT_VIEW
            )
            mocked_function.assert_not_called()

        with override_waffle_flag(TIMED_EXAM_GATING_WAFFLE_FLAG, active=True):
            self._get_rendered_view(
                self.sequence_5_1,
                extra_context=dict(next_url='NextSequential', prev_url='PrevSequential'),
                view=STUDENT_VIEW
            )
            mocked_function.assert_called_once()

    @override_waffle_flag(TIMED_EXAM_GATING_WAFFLE_FLAG, active=True)
    def test_that_timed_sequence_gating_respects_access_configurations(self):
        """
        Verify that if a time limited sequence contains content type gated problems, we gate the sequence
        """
        # the one problem in this sequence needs to have graded set to true in order to test content type gating
        self.sequence_5_1.get_children()[0].get_children()[0].graded = True
        gated_fragment = Fragment('i_am_gated')

        # When a time limited sequence contains content type gated problems, the sequence itself is gated
        self.sequence_5_1.runtime._services['content_type_gating'] = Mock(return_value=Mock(  # pylint: disable=protected-access
            check_children_for_content_type_gating_paywall=Mock(return_value=gated_fragment.content),
        ))
        view = self._get_rendered_view(
            self.sequence_5_1,
            extra_context=dict(next_url='NextSequential', prev_url='PrevSequential'),
            view=STUDENT_VIEW
        )
        assert 'i_am_gated' in view
        # check a few elements to ensure the correct page was loaded
        assert 'seq_block.html' in view
        assert 'NextSequential' in view
        assert 'PrevSequential' in view

    @ddt.unpack
    @ddt.data(
        {'view': STUDENT_VIEW},
        {'view': PUBLIC_VIEW},
    )
    def test_student_view_first_child(self, view):
        html = self._get_rendered_view(
            self.sequence_3_1, requested_child='first', view=view
        )
        self._assert_view_at_position(html, expected_position=1)

    @ddt.unpack
    @ddt.data(
        {'view': STUDENT_VIEW},
        {'view': PUBLIC_VIEW},
    )
    def test_student_view_last_child(self, view):
        html = self._get_rendered_view(self.sequence_3_1, requested_child='last', view=view)
        self._assert_view_at_position(html, expected_position=3)

    def test_tooltip(self):
        html = self._get_rendered_view(self.sequence_3_1, requested_child=None)
        for child in self.sequence_3_1.children:
            assert f"'page_title': '{child.block_id}'" in html

    def test_hidden_content_before_due(self):
        html = self._get_rendered_view(self.sequence_4_1)
        assert 'seq_block.html' in html
        assert "'banner_text': None" in html

    def test_hidden_content_past_due(self):
        with freeze_time(COURSE_END_DATE):
            progress_url = 'http://test_progress_link'
            html = self._get_rendered_view(
                self.sequence_4_1,
                extra_context=dict(progress_url=progress_url),
            )
            assert 'hidden_content.html' in html
            assert progress_url in html

    def test_masquerade_hidden_content_past_due(self):
        with freeze_time(COURSE_END_DATE):
            html = self._get_rendered_view(
                self.sequence_4_1,
                extra_context=dict(specific_masquerade=True),
            )
            assert 'seq_block.html' in html
            html = self.get_context_dict_from_string(html)
            assert 'Because the due date has passed, this assignment is hidden from the learner.' == html['banner_text']

    def test_hidden_content_self_paced_past_due_before_end(self):
        with freeze_time(PAST_DUE_BEFORE_END_DATE):
            html = self._get_rendered_view(self.sequence_4_1, self_paced=True)
            assert 'seq_block.html' in html
            assert "'banner_text': None" in html

    def test_hidden_content_self_paced_past_end(self):
        with freeze_time(COURSE_END_DATE + timedelta(days=7)):
            progress_url = 'http://test_progress_link'
            html = self._get_rendered_view(
                self.sequence_4_1,
                extra_context=dict(progress_url=progress_url),
                self_paced=True,
            )
            assert 'hidden_content.html' in html
            assert progress_url in html

    def _assert_gated(self, html, sequence):
        """
        Assert sequence content is gated
        """
        assert 'seq_block.html' in html
        html = self.get_context_dict_from_string(html)
        assert html['banner_text'] is None
        assert [] == html['items']
        assert html['gated_content']['gated']
        assert 'PrereqUrl' == html['gated_content']['prereq_url']
        assert 'PrereqSectionName' == html['gated_content']['prereq_section_name']
        assert str(sequence.display_name) in html['gated_content']['gated_section_name']
        assert 'NextSequential' == html['next_url']
        assert 'PrevSequential' == html['prev_url']

    def _assert_prereq(self, html, sequence):
        """
        Assert sequence is a prerequisite with unfulfilled gates
        """
        assert 'seq_block.html' in html
        html = self.get_context_dict_from_string(html)
        assert 'This section is a prerequisite. You must complete this section in order to unlock additional content.' == html['banner_text']
        assert not html['gated_content']['gated']
        assert str(sequence.location) == html['item_id']
        assert html['gated_content']['prereq_url'] is None
        assert html['gated_content']['prereq_section_name'] is None
        assert 'NextSequential' == html['next_url']
        assert 'PrevSequential' == html['prev_url']

    def _assert_ungated(self, html, sequence):
        """
        Assert sequence is not gated
        """
        assert 'seq_block.html' in html
        assert "'banner_text': None" in html
        assert "'gated': False" in html
        assert str(sequence.location) in html
        assert "'prereq_url': None" in html
        assert "'prereq_section_name': None" in html
        assert "'next_url': 'NextSequential'" in html
        assert "'prev_url': 'PrevSequential'" in html

    def test_gated_content(self):
        """
        Test when sequence is both a prerequisite for a sequence
        and gated on another prerequisite sequence
        """
        # setup seq_1_2 as a gate and gated
        gating_mock_1_2 = Mock()
        gating_mock_1_2.return_value.is_gate_fulfilled.return_value = False
        gating_mock_1_2.return_value.required_prereq.return_value = True
        gating_mock_1_2.return_value.compute_is_prereq_met.return_value = [
            False,
            {'url': 'PrereqUrl', 'display_name': 'PrereqSectionName', 'id': 'mockId'}
        ]
        self.sequence_1_2.runtime._services['gating'] = gating_mock_1_2  # pylint: disable=protected-access
        self.sequence_1_2.display_name = 'sequence_1_2'

        html = self._get_rendered_view(
            self.sequence_1_2,
            extra_context=dict(next_url='NextSequential', prev_url='PrevSequential'),
        )

        # expect content to be gated, with no banner
        self._assert_gated(html, self.sequence_1_2)

        # change seq_1_2 to be ungated, but still a gate (prequiste)
        gating_mock_1_2.return_value.is_gate_fulfilled.return_value = False
        gating_mock_1_2.return_value.required_prereq.return_value = True
        gating_mock_1_2.return_value.compute_is_prereq_met.return_value = [True, {}]

        html = self._get_rendered_view(
            self.sequence_1_2,
            extra_context=dict(next_url='NextSequential', prev_url='PrevSequential'),
        )
        # assert that content and preq banner is shown
        self._assert_prereq(html, self.sequence_1_2)

        # change seq_1_2 to have no unfulfilled gates
        gating_mock_1_2.return_value.is_gate_fulfilled.return_value = True
        gating_mock_1_2.return_value.required_prereq.return_value = True
        gating_mock_1_2.return_value.compute_is_prereq_met.return_value = [True, {}]

        html = self._get_rendered_view(
            self.sequence_1_2,
            extra_context=dict(next_url='NextSequential', prev_url='PrevSequential'),
        )

        # assert content shown as normal
        self._assert_ungated(html, self.sequence_1_2)

    def test_xblock_handler_get_completion_success(self):
        """Test that the completion data is returned successfully on targeted vertical through ajax call"""
        self.sequence_3_1.runtime._services['completion'] = Mock(  # pylint: disable=protected-access
            return_value=Mock(vertical_is_complete=Mock(return_value=True))
        )
        for child in self.sequence_3_1.get_children():
            usage_key = str(child.location)
            request = RequestFactory().post(
                '/',
                data=json.dumps({'usage_key': usage_key}),
                content_type='application/json',
            )
            completion_return = self.sequence_3_1.handle('get_completion', request)
            assert completion_return.json == {'complete': True}
        self.sequence_3_1.runtime._services['completion'] = None  # pylint: disable=protected-access

    def test_xblock_handler_get_completion_bad_key(self):
        """Test that the completion data is returned as False when usage key is None through ajax call"""
        request = RequestFactory().post(
            '/',
            data=json.dumps({'usage_key': None}),
            content_type='application/json',
        )
        completion_return = self.sequence_3_1.handle('get_completion', request)
        assert completion_return.json == {'complete': False}

    def test_handle_ajax_get_completion_success(self):
        """Test that the old-style ajax handler for completion still works"""
        self.sequence_3_1.runtime._services['completion'] = Mock(  # pylint: disable=protected-access
            return_value=Mock(vertical_is_complete=Mock(return_value=True))
        )
        for child in self.sequence_3_1.get_children():
            usage_key = str(child.location)
            completion_return = self.sequence_3_1.handle_ajax('get_completion', {'usage_key': usage_key})
            assert json.loads(completion_return) == {'complete': True}
        self.sequence_3_1.runtime._services['completion'] = None  # pylint: disable=protected-access

    def test_xblock_handler_goto_position_success(self):
        """Test that we can set position through ajax call"""
        assert self.sequence_3_1.position != 5
        request = RequestFactory().post(
            '/',
            data=json.dumps({'position': 5}),
            content_type='application/json',
        )
        goto_return = self.sequence_3_1.handle('goto_position', request)
        assert goto_return.json == {'success': True}
        assert self.sequence_3_1.position == 5

    def test_xblock_handler_goto_position_bad_position(self):
        """Test that we gracefully handle bad positions as position 1"""
        assert self.sequence_3_1.position != 1
        request = RequestFactory().post(
            '/',
            data=json.dumps({'position': -10}),
            content_type='application/json',
        )
        goto_return = self.sequence_3_1.handle('goto_position', request)
        assert goto_return.json == {'success': True}
        assert self.sequence_3_1.position == 1

    def test_handle_ajax_goto_position_success(self):
        """Test that the old-style ajax handler for setting position still works"""
        goto_return = self.sequence_3_1.handle_ajax('goto_position', {'position': 5})
        assert json.loads(goto_return) == {'success': True}
        assert self.sequence_3_1.position == 5

    def test_get_metadata(self):
        """Test that the sequence metadata is returned correctly"""
        # rather than dealing with json serialization of the Mock object,
        # let's just disable the bookmarks service
        self.sequence_3_1.runtime._services['bookmarks'] = None  # lint-amnesty, pylint: disable=protected-access
        metadata = self.sequence_3_1.get_metadata()
        assert len(metadata['items']) == 3
        assert metadata['tag'] == 'sequential'
        assert metadata['display_name'] == self.sequence_3_1.display_name_with_default

    def test_get_metadata_navigation_disabled(self):
        """Test that the sequence metadata is returned correctly when navigation is disabled"""
        self.sequence_3_1.hide_from_toc = True
        metadata = self.sequence_3_1.get_metadata()
        assert len(metadata['items']) == 3
        assert metadata['tag'] == 'sequential'
        assert metadata['display_name'] == self.sequence_3_1.display_name_with_default
        assert metadata['navigation_disabled'] is True

    @override_settings(FIELD_OVERRIDE_PROVIDERS=(
        'openedx.features.content_type_gating.field_override.ContentTypeGatingFieldOverride',
    ))
    def test_get_metadata_content_type_gated_content(self):
        """The contains_content_type_gated_content field tells whether the item contains content type gated content"""
        self.sequence_5_1.runtime._services['bookmarks'] = None  # pylint: disable=protected-access
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))
        metadata = self.sequence_5_1.get_metadata()
        assert metadata['items'][0]['contains_content_type_gated_content'] is False

        # When a block contains content type gated problems, set the contains_content_type_gated_content field
        self.sequence_5_1.get_children()[0].get_children()[0].graded = True
        self.sequence_5_1.runtime._services['content_type_gating'] = Mock(return_value=Mock(  # pylint: disable=protected-access
            enabled_for_enrollment=Mock(return_value=True),
            content_type_gate_for_block=Mock(return_value=Fragment('i_am_gated'))
        ))
        metadata = self.sequence_5_1.get_metadata()
        assert metadata['items'][0]['contains_content_type_gated_content'] is True

    def get_context_dict_from_string(self, data):
        """
        Retrieve dictionary from string.
        """
        # Replace tuple and un-necessary info from inside string and get the dictionary.
        cleaned_data = data.replace("(('seq_block.html',\n", '').replace("),\n {})", '').strip()
        return ast.literal_eval(cleaned_data)

    def test_not_gated_blocks_rendered_normally(self):
        """
        Test that non-gated blocks are rendered with full content when prerequisites are met.
        """
        # Mock child block
        child = Mock()
        child.scope_ids.usage_id = "block1"
        child.scope_ids.block_type = "vertical"
        child.display_name_with_default = "Test Block"
        children = [child]

        # Mock context
        context = {"next_url": "next_url", "prev_url": "prev_url"}
        fragment = Mock()

        # Mock `_render_student_view_for_blocks`
        self.sequence_3_1._render_student_view_for_blocks = Mock(return_value="rendered_blocks")  # pylint: disable=protected-access

        # Call `_get_render_metadata` with prerequisites met
        metadata = self.sequence_3_1._get_render_metadata(  # pylint: disable=protected-access
            context, children, prereq_met=True, prereq_meta_info={}, fragment=fragment
        )

        # Assert that blocks are rendered normally
        assert metadata["items"] == "rendered_blocks"
        assert metadata["next_url"] == "next_url"
        assert metadata["prev_url"] == "prev_url"

    def test_gated_blocks_rendered_with_basic_info(self):
        """
        Test that gated blocks are rendered with minimal metadata when prerequisites are not met.
        """
        # Mock child block
        child = Mock()
        child.scope_ids.usage_id = "block1"
        child.scope_ids.block_type = "vertical"
        child.display_name_with_default = "Test Block"
        children = [child]

        # Mock context
        context = {"next_url": "next_url", "prev_url": "prev_url"}

        # Mock prereq_meta_info with required keys
        prereq_meta_info = {
            "url": "http://example.com/prereq",
            "display_name": "Prerequisite Section",
            "id": "prereq_block_id",
        }

        # Call `_get_render_metadata` with prerequisites not met
        metadata = self.sequence_3_1._get_render_metadata(  # pylint: disable=protected-access
            context, children, prereq_met=False, prereq_meta_info=prereq_meta_info
        )

        # Assert that gated blocks are rendered with basic info
        assert len(metadata["items"]) == 1
        assert metadata["items"][0]["id"] == "block1"
        assert metadata["items"][0]["type"] == "vertical"
        assert metadata["items"][0]["display_name"] == "Test Block"
        assert metadata["items"][0]["is_gated"] is True
        assert metadata["items"][0]["content"] == ""

        # Assert that next and previous URLs are present
        assert metadata["next_url"] == "next_url"
        assert metadata["prev_url"] == "prev_url"

    def test_prereqs_met_content_rendered_normally(self):
        """
        Test that content is rendered normally when prerequisites are met.
        """
        # Mock child block
        child = Mock()
        child.scope_ids.usage_id = "block1"
        child.scope_ids.block_type = "vertical"
        child.display_name_with_default = "Test Block"
        children = [child]

        # Mock context
        context = {"next_url": "next_url", "prev_url": "prev_url"}
        fragment = Mock()

        # Mock `_render_student_view_for_blocks`
        self.sequence_3_1._render_student_view_for_blocks = Mock(return_value="rendered_blocks")  # pylint: disable=protected-access

        # Call `_get_render_metadata` with prerequisites met
        metadata = self.sequence_3_1._get_render_metadata(  # pylint: disable=protected-access
            context, children, prereq_met=True, prereq_meta_info={}, fragment=fragment
        )

        # Assert that content is rendered normally
        assert metadata["items"] == "rendered_blocks"
        assert metadata["next_url"] == "next_url"
        assert metadata["prev_url"] == "prev_url"

    @patch("openedx.core.lib.gating.api.get_required_content")
    def test_export_sequence_with_prerequisites(self, mock_get_required_content):
        """
        Test that add_prerequisite_to_xml adds prerequisite information correctly.
        """
        xml_object = etree.Element('sequential')

        mock_get_required_content.return_value = (
            "block-v1:TestX+TestCourse+1+type@sequential+block@required_sequential",
            80,
            90,
        )
        self.sequence_3_1.add_prerequisite_to_xml(xml_object)
        mock_get_required_content.assert_called_once_with(
            self.sequence_3_1.location.course_key,
            self.sequence_3_1.location
        )
        assert xml_object.get('required_content') == 'required_sequential'
        assert xml_object.get('min_score') == '80'
        assert xml_object.get('min_completion') == '90'

    @patch("openedx.core.lib.gating.api.get_required_content")
    def test_export_sequence_no_prerequisites(self, mock_get_required_content):
        """
        Test that add_prerequisite_to_xml adds No prerequisite information if
        current sequential is not gated.
        """
        xml_object = etree.Element('sequential')

        mock_get_required_content.return_value = (None, None, None)

        self.sequence_3_1.add_prerequisite_to_xml(xml_object)
        mock_get_required_content.assert_called_once_with(
            self.sequence_3_1.location.course_key,
            self.sequence_3_1.location
        )
        assert 'required_content' not in xml_object.attrib
        assert 'min_score' not in xml_object.attrib
        assert 'min_completion' not in xml_object.attrib
