"""
Unit tests for the gating feature in Studio
"""


import json
from unittest.mock import patch

import ddt
from django.db import connection
from django.test.utils import CaptureQueriesContext
from milestones.tests.utils import MilestonesTestCaseMixin

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_usage_url
from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import VisibilityState, create_xblock_info
from openedx.core.lib.gating import api as gating_api
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE
from xmodule.modulestore.tests.factories import BlockFactory


@ddt.ddt
class TestSubsectionGating(CourseTestCase, MilestonesTestCaseMixin):
    """
    Tests for the subsection gating feature
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE
    ENABLED_SIGNALS = ['pre_item_delete']

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

    @ddt.data(
        (90, None),
        (None, 90),
        (100, 100),
    )
    @ddt.unpack
    def test_get_prerequisite(self, min_score, min_completion):
        gating_api.add_prerequisite(self.course.id, self.seq1.location)
        gating_api.set_required_content(
            self.course.id, self.seq2.location, self.seq1.location, min_score, min_completion
        )
        resp = json.loads(self.client.get_json(self.seq2_url).content.decode('utf-8'))
        self.assertFalse(resp['is_prereq'])  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            [p['block_usage_key'] for p in resp['prereqs']],
            [str(self.seq1.location)],
        )
        self.assertEqual(resp['prereq'], str(self.seq1.location))  # noqa: PT009
        self.assertEqual(resp['prereq_min_score'], min_score)  # noqa: PT009
        self.assertEqual(resp['prereq_min_completion'], min_completion)  # noqa: PT009
        self.assertEqual(resp['visibility_state'], VisibilityState.gated)  # noqa: PT009

        resp = json.loads(self.client.get_json(self.seq1_url).content.decode('utf-8'))
        self.assertTrue(resp['is_prereq'])  # noqa: PT009
        # A subsection isn't listed as a possible prerequisite of itself.
        self.assertEqual(resp['prereqs'], [])  # noqa: PT009
        self.assertIsNone(resp['prereq'])  # noqa: PT009
        self.assertIsNone(resp['prereq_min_score'])  # noqa: PT009
        self.assertIsNone(resp['prereq_min_completion'])  # noqa: PT009

    def test_outline_gating_queries_dont_scale_with_subsections(self):
        """
        The course outline looks up gating milestones once for the course, not
        once per subsection.
        """
        gating_api.add_prerequisite(self.course.id, self.seq1.location)
        gating_api.set_required_content(self.course.id, self.seq2.location, self.seq1.location, 100, 100)

        def milestone_queries():
            with CaptureQueriesContext(connection) as queries:
                outline = create_xblock_info(
                    modulestore().get_course(self.course.id),
                    include_child_info=True,
                    course_outline=True,
                    include_children_predicate=lambda xblock: xblock.category != 'vertical',
                    user=self.user,
                )
            return outline, [q for q in queries if 'milestones_coursecontentmilestone' in q['sql']]

        outline, queries_for_two = milestone_queries()
        seqs = outline['child_info']['children'][0]['child_info']['children']
        self.assertEqual(  # noqa: PT009
            [(s['is_prereq'], s['prereq']) for s in seqs],
            [(True, None), (False, str(self.seq1.location))],
        )

        for i in range(3):
            BlockFactory.create(parent_location=self.chapter.location, category='sequential', display_name=f'seq {i}')
        _, queries_for_five = milestone_queries()
        self.assertEqual(len(queries_for_two), len(queries_for_five))  # noqa: PT009

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
