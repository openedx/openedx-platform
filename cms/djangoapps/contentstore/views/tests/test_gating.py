"""
Unit tests for the gating feature in Studio
"""


import json
from unittest.mock import patch

import ddt

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_usage_url
from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import VisibilityState
from openedx.core.lib.gating.api import GATING_NAMESPACE_QUALIFIER
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE
from xmodule.modulestore.tests.factories import BlockFactory


@ddt.ddt
class TestSubsectionGating(CourseTestCase):
    """
    Tests for the subsection gating feature
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE
    ENABLED_SIGNALS = ['item_deleted']

    def setUp(self):
        """
        Initial data setup
        """
        super().setUp()

        # Enable subsection gating for the test course
        self.course.enable_subsection_gating = True
        self.save_course()

        # create a chapter
        self.chapter = BlockFactory.create(
            parent_location=self.course.location,
            category='chapter',
            display_name='untitled chapter'
        )

        # create 2 sequentials
        self.seq1 = BlockFactory.create(
            parent_location=self.chapter.location,
            category='sequential',
            display_name='untitled sequential 1'
        )
        self.seq1_url = reverse_usage_url('xblock_handler', self.seq1.location)

        self.seq2 = BlockFactory.create(
            parent_location=self.chapter.location,
            category='sequential',
            display_name='untitled sequential 2'
        )
        self.seq2_url = reverse_usage_url('xblock_handler', self.seq2.location)

    @patch('cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers.gating_api.add_prerequisite')
    def test_add_prerequisite(self, mock_add_prereq):
        """
        Test adding a subsection as a prerequisite
        """

        self.client.ajax_post(
            self.seq1_url,
            data={'isPrereq': True}
        )
        mock_add_prereq.assert_called_with(self.course.id, self.seq1.location)

    @patch('cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers.gating_api.remove_prerequisite')
    def test_remove_prerequisite(self, mock_remove_prereq):
        """
        Test removing a subsection as a prerequisite
        """

        self.client.ajax_post(
            self.seq1_url,
            data={'isPrereq': False}
        )
        mock_remove_prereq.assert_called_with(self.seq1.location)

    @patch('cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers.gating_api.set_required_content')
    def test_add_gate(self, mock_set_required_content):
        """
        Test adding a gated subsection
        """

        self.client.ajax_post(
            self.seq2_url,
            data={'prereqUsageKey': str(self.seq1.location), 'prereqMinScore': '100',
                  'prereqMinCompletion': '100'}
        )
        mock_set_required_content.assert_called_with(
            self.course.id,
            self.seq2.location,
            str(self.seq1.location),
            '100',
            '100'
        )

    @patch('cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers.gating_api.set_required_content')
    def test_remove_gate(self, mock_set_required_content):
        """
        Test removing a gated subsection
        """

        self.client.ajax_post(
            self.seq2_url,
            data={'prereqUsageKey': '', 'prereqMinScore': '', 'prereqMinCompletion': ''}
        )
        mock_set_required_content.assert_called_with(
            self.course.id,
            self.seq2.location,
            '',
            '',
            ''
        )

    @patch('cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers.gating_api.get_prerequisites')
    @patch('cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers.gating_api.get_required_content')
    @patch('cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers.gating_api.is_prerequisite')
    @ddt.data(
        (90, None),
        (None, 90),
        (100, 100),
    )
    @ddt.unpack
    def test_get_prerequisite(
            self, min_score, min_completion,
            mock_is_prereq, mock_get_required_content, mock_get_prereqs
    ):
        mock_is_prereq.return_value = True
        mock_get_required_content.return_value = str(self.seq1.location), min_score, min_completion
        mock_get_prereqs.return_value = [
            {'namespace': f'{str(self.seq1.location)}{GATING_NAMESPACE_QUALIFIER}'},
            {'namespace': f'{str(self.seq2.location)}{GATING_NAMESPACE_QUALIFIER}'}
        ]
        resp = json.loads(self.client.get_json(self.seq2_url).content.decode('utf-8'))
        mock_is_prereq.assert_called_with(self.course.id, self.seq2.location)
        mock_get_required_content.assert_called_with(self.course.id, self.seq2.location)
        mock_get_prereqs.assert_called_with(self.course.id)
        self.assertTrue(resp['is_prereq'])
        self.assertEqual(resp['prereq'], str(self.seq1.location))
        self.assertEqual(resp['prereq_min_score'], min_score)
        self.assertEqual(resp['prereq_min_completion'], min_completion)
        self.assertEqual(resp['visibility_state'], VisibilityState.gated)

    @patch('cms.djangoapps.contentstore.signals.handlers.gating_api.set_required_content')
    @patch('cms.djangoapps.contentstore.signals.handlers.gating_api.remove_prerequisite')
    def test_delete_item_signal_handler_called(self, mock_remove_prereq, mock_set_required):
        seq3 = BlockFactory.create(
            parent_location=self.chapter.location,
            category='sequential',
            display_name='untitled sequential 3'
        )
        self.client.delete(reverse_usage_url('xblock_handler', seq3.location))
        mock_remove_prereq.assert_called_with(seq3.location)
        mock_set_required.assert_called_with(seq3.location.course_key, seq3.location, None, None, None)
