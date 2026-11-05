"""
Tests for ContentLibraryTransformer.
"""

from unittest import mock

from ddt import data, ddt
from django.test import TestCase
from xblock.core import XBlock
from xblock.plugin import PluginMissingError

import openedx.core.djangoapps.content.block_structure.api as bs_api
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory
from openedx.core.djangoapps.content.block_structure.api import clear_course_from_cache
from openedx.core.djangoapps.content.block_structure.factory import BlockStructureFactory
from openedx.core.djangoapps.content.block_structure.transformers import BlockStructureTransformers
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from ...api import get_course_blocks
from ..library_content import (
    ContentLibraryOrderTransformer,
    ContentLibraryTransformer,
    _load_block_class,
)
from .helpers import CourseStructureTestCase


class MockedModule:
    """
    Object with mocked selected modules for user.
    """

    def __init__(self, state):
        """
        Set state attribute on initialize.
        """
        self.state = state


@ddt
class ContentLibraryTransformerTestCase(CourseStructureTestCase):
    """
    ContentLibraryTransformer Test
    """
    TRANSFORMER_CLASS_TO_TEST = ContentLibraryTransformer

    def setUp(self):
        """
        Setup course structure and create user for content library transformer test.
        """
        super().setUp()
        self._initialize_course_hierarchy()

    def _initialize_course_hierarchy(self, block_type='library_content'):
        """
        Initialize course hierarchy with the given block type.
        """
        # Build course.
        self.course_hierarchy = self.get_course_hierarchy(block_type)
        self.blocks = self.build_course(self.course_hierarchy)
        self.course = self.blocks['course']
        # Do this manually because publish signals are not fired by default in tests.
        bs_api.update_course_in_cache(self.course.id)
        clear_course_from_cache(self.course.id)

        # Enroll user in course.
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id, is_active=True)

    def get_course_hierarchy(self, block_type='library_content'):
        """
        Get a course hierarchy to test with.
        """
        return [{
            'org': 'ContentLibraryTransformer',
            'course': 'CL101F',
            'run': f'test_run_{block_type}',
            '#type': 'course',
            '#ref': 'course',
            '#children': [
                {
                    '#type': 'chapter',
                    '#ref': 'chapter1',
                    '#children': [
                        {
                            '#type': 'sequential',
                            '#ref': 'lesson1',
                            '#children': [
                                {
                                    '#type': 'vertical',
                                    '#ref': 'vertical1',
                                    '#children': [
                                        {
                                            '#type': block_type,
                                            '#ref': f'{block_type}1',
                                            '#children': [
                                                {
                                                    'metadata': {'display_name': "CL Vertical 2"},
                                                    '#type': 'vertical',
                                                    '#ref': 'vertical2',
                                                    '#children': [
                                                        {
                                                            'metadata': {'display_name': "HTML1"},
                                                            '#type': 'html',
                                                            '#ref': 'html1',
                                                        }
                                                    ]
                                                },
                                                {
                                                    'metadata': {'display_name': "CL Vertical 3"},
                                                    '#type': 'vertical',
                                                    '#ref': 'vertical3',
                                                    '#children': [
                                                        {
                                                            'metadata': {'display_name': "HTML2"},
                                                            '#type': 'html',
                                                            '#ref': 'html2',
                                                        }
                                                    ]
                                                }
                                            ]
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }]

    @data('library_content', 'itembank')
    def test_content_library(self, block_type):
        """
        Test when course has content library section.
        First test user can't see any content library section,
        and after that mock response from MySQL db.
        Check user can see mocked sections in content library.
        """
        # Re-initialize if testing with a different block type
        if block_type != 'library_content':
            self._initialize_course_hierarchy(block_type)

        raw_block_structure = get_course_blocks(
            self.user,
            self.course.location,
            transformers=BlockStructureTransformers(),
        )
        assert len(list(raw_block_structure.get_block_keys())) == len(self.blocks)

        bs_api.update_course_in_cache(self.course.id)
        clear_course_from_cache(self.course.id)
        trans_block_structure = get_course_blocks(
            self.user,
            self.course.location,
            self.transformers,
        )

        # Should dynamically assign a block to student
        trans_keys = set(trans_block_structure.get_block_keys())
        block_key_set = self.get_block_key_set(
            self.blocks, 'course', 'chapter1', 'lesson1', 'vertical1', f'{block_type}1'
        )
        for key in block_key_set:
            assert key in trans_keys

        vertical2_selected = self.get_block_key_set(self.blocks, 'vertical2').pop() in trans_keys
        vertical3_selected = self.get_block_key_set(self.blocks, 'vertical3').pop() in trans_keys

        assert vertical2_selected != vertical3_selected
        # only one of them should be selected
        selected_vertical = 'vertical2' if vertical2_selected else 'vertical3'
        selected_child = 'html1' if vertical2_selected else 'html2'

        # Check course structure again.
        clear_course_from_cache(self.course.id)
        for i in range(5):
            trans_block_structure = get_course_blocks(
                self.user,
                self.course.location,
                self.transformers,
            )
            assert set(trans_block_structure.get_block_keys()) == self.get_block_key_set(self.blocks, 'course',
                                                                                         'chapter1', 'lesson1',
                                                                                         'vertical1',
                                                                                         f'{block_type}1',
                                                                                         selected_vertical,
                                                                                         selected_child), f"Expected 'selected' equality failed in iteration {i}."  # pylint: disable=line-too-long


    def _collect_with_uninstalled(self, uninstalled_type):
        """
        Run collect() with uninstalled_type behaving as though its XBlock is missing.

        Only calls that pass no default are made to raise, which is what distinguishes
        collect() from the modulestore: the modulestore passes its default_class and so
        loads an unknown type as a HiddenBlock, while collect() passed none and raised.

        Arguments:
            uninstalled_type (str): block type to treat as having no installed XBlock.

        Returns:
            The collected BlockStructure.
        """
        block_structure = BlockStructureFactory.create_from_modulestore(
            self.course.location, modulestore()
        )
        real_load_class = XBlock.load_class

        def load_class(identifier, default=None, select=None):
            """Raise for the target type only when no default was supplied."""
            if identifier == uninstalled_type and default is None:
                raise PluginMissingError(identifier)
            return real_load_class(identifier, default, select)

        with mock.patch.object(XBlock, 'load_class', side_effect=load_class):
            ContentLibraryTransformer.collect(block_structure)

        return block_structure

    def test_collect_skips_block_with_uninstalled_type(self):
        """
        collect() completes instead of letting PluginMissingError escape.

        The item bank is skipped, so its children get no analytics summary, but the
        structure is still built and can be stored. Before this, the exception aborted
        the build for the entire course and nothing was ever cached, so every request
        repeated the failure and the course stayed inaccessible.
        """
        block_structure = self._collect_with_uninstalled('library_content')

        library_block_key = self.blocks['library_content1'].location
        children = block_structure.get_children(library_block_key)
        assert children
        for child_key in children:
            assert block_structure.get_transformer_block_field(
                child_key, ContentLibraryTransformer, 'block_analytics_summary'
            ) is None

    def test_collect_summarizes_when_type_is_installed(self):
        """
        Skipping is limited to the uninstalled type; normal collection is unchanged.
        """
        block_structure = BlockStructureFactory.create_from_modulestore(
            self.course.location, modulestore()
        )
        ContentLibraryTransformer.collect(block_structure)

        library_block_key = self.blocks['library_content1'].location
        children = block_structure.get_children(library_block_key)
        assert children
        for child_key in children:
            assert block_structure.get_transformer_block_field(
                child_key, ContentLibraryTransformer, 'block_analytics_summary'
            ) is not None


@ddt
class ContentLibraryOrderTransformerTestCase(CourseStructureTestCase):
    """
    ContentLibraryOrderTransformer Test
    """
    TRANSFORMER_CLASS_TO_TEST = ContentLibraryOrderTransformer

    def setUp(self):
        """
        Setup course structure and create user for content library order transformer test.
        """
        super().setUp()
        self._initialize_course_hierarchy()

    def _initialize_course_hierarchy(self, block_type='library_content'):
        """
        Initialize course hierarchy with the given block type.
        """
        self.course_hierarchy = self.get_course_hierarchy(block_type)
        self.blocks = self.build_course(self.course_hierarchy)
        self.course = self.blocks['course']
        bs_api.update_course_in_cache(self.course.id)
        clear_course_from_cache(self.course.id)

        # Enroll user in course.
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id, is_active=True)

    def get_course_hierarchy(self, block_type='library_content'):
        """
        Get a course hierarchy to test with.
        """
        return [{
            'org': 'ContentLibraryTransformer',
            'course': 'CL101F',
            'run': f'test_run_{block_type}',
            '#type': 'course',
            '#ref': 'course',
            '#children': [
                {
                    '#type': 'chapter',
                    '#ref': 'chapter1',
                    '#children': [
                        {
                            '#type': 'sequential',
                            '#ref': 'lesson1',
                            '#children': [
                                {
                                    '#type': 'vertical',
                                    '#ref': 'vertical1',
                                    '#children': [
                                        {
                                            '#type': block_type,
                                            '#ref': f'{block_type}1',
                                            '#children': [
                                                {
                                                    'metadata': {'display_name': "CL Vertical 2"},
                                                    '#type': 'vertical',
                                                    '#ref': 'vertical2',
                                                    '#children': [
                                                        {
                                                            'metadata': {'display_name': "HTML1"},
                                                            '#type': 'html',
                                                            '#ref': 'html1',
                                                        }
                                                    ]
                                                },
                                                {
                                                    'metadata': {'display_name': "CL Vertical 3"},
                                                    '#type': 'vertical',
                                                    '#ref': 'vertical3',
                                                    '#children': [
                                                        {
                                                            'metadata': {'display_name': "HTML2"},
                                                            '#type': 'html',
                                                            '#ref': 'html2',
                                                        }
                                                    ]
                                                },
                                                {
                                                    'metadata': {'display_name': "CL Vertical 4"},
                                                    '#type': 'vertical',
                                                    '#ref': 'vertical4',
                                                    '#children': [
                                                        {
                                                            'metadata': {'display_name': "HTML3"},
                                                            '#type': 'html',
                                                            '#ref': 'html3',
                                                        }
                                                    ]
                                                }
                                            ]
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }]

    @mock.patch('lms.djangoapps.course_blocks.transformers.library_content.get_student_module_as_dict')
    @data('library_content', 'itembank')
    def test_content_library_randomize(self, block_type, mocked):
        """
        Test whether the order of the children blocks matches the order of the selected blocks when
        course has content library section
        """
        # Re-initialize if testing with a different block type
        if block_type != 'library_content':
            self._initialize_course_hierarchy(block_type)
        mocked.return_value = {
            'selected': [
                ['vertical', 'vertical_vertical3'],
                ['vertical', 'vertical_vertical2'],
                ['vertical', 'vertical_vertical4'],
            ]
        }
        for i in range(5):
            trans_block_structure = get_course_blocks(
                self.user,
                self.course.location,
                self.transformers,
            )
            children = []
            for block_key in trans_block_structure.topological_traversal():
                if block_key.block_type == block_type:
                    children = trans_block_structure.get_children(block_key)
                    break

            expected_children = ['vertical_vertical3', 'vertical_vertical2', 'vertical_vertical4']
            assert expected_children == [child.block_id for child in children], \
                f"Expected 'selected' equality failed in iteration {i}."

    @mock.patch('lms.djangoapps.course_blocks.transformers.library_content.get_student_module_as_dict')
    def test_content_library_randomize_selected_blocks_mismatch(self, mocked):
        """
        Test and verify that the ContentLibraryOrderTransformer's order transformation doesn't
        happen when the current children blocks no longer match the selections made in the ContentLibraryTransformer
        and stored in the database.

        There are two types of block structure transformers - filtering and non-filtering. The filtering transformers
        can only filter blocks from the block structure but cannot perform any other transformations. The
        non-filtering transformers can transform the blocks in the block structure.

        The ContentLibraryTransformer is a filtering transformer that selects the children blocks of the randomized
        content blocks, saves the selection in the database and filters the blocks that are not selected.

        The ContentLibraryOrderTransformer is a non-filtering transformer which transforms the order of the children
        blocks of the randomized content block based on the order of the stored selection made by the
        ContentLibraryTransformer.

        The 'transform()' methods of all the filtering transformers are combined into a single transformation function
        and run in a single block structure traversal. The non-filtering block structure transformers are run
        after this.

        When some filtering transformers like those for content visibility, gating etc. run after the
        ContentLibraryTransformer and remove some/all of the selected children blocks before the
        ContentLibraryTransformer runs, there will be a mismatch between the stored selected children blocks and
        the current children blocks. When this happens, the ContentLibraryOrderTransformer shouldn't
        transform the order.
        """
        mocked.return_value = {
            'selected': [
                ['vertical', 'vertical_vertical3'],
            ]
        }

        expected_children_without_hiding_or_gating = ['vertical_vertical3', ]

        for _ in range(5):
            trans_block_structure = get_course_blocks(
                self.user,
                self.course.location,
                self.transformers,
            )
            children = []
            for block_key in trans_block_structure.topological_traversal():
                if block_key.block_type == 'library_content':
                    children = trans_block_structure.get_children(block_key)
                    break

            assert expected_children_without_hiding_or_gating != [child.block_id for child in children]

    @mock.patch('lms.djangoapps.course_blocks.transformers.library_content.get_student_module_as_dict')
    def test_content_library_randomize_selected_blocks_missing(self, mocked):
        """
        Test that no selected blocks is handled gracefully
        """
        mocked.return_value = {}

        expected_children_without_hiding_or_gating = ['vertical_vertical3', ]

        for _ in range(5):
            trans_block_structure = get_course_blocks(
                self.user,
                self.course.location,
                self.transformers,
            )
            children = []
            for block_key in trans_block_structure.topological_traversal():
                if block_key.block_type == 'library_content':
                    children = trans_block_structure.get_children(block_key)
                    break

            assert expected_children_without_hiding_or_gating != [child.block_id for child in children]


class LoadBlockClassTestCase(TestCase):
    """
    Tests for _load_block_class.
    """

    def test_installed_block_type_loads(self):
        """
        A block type with an installed XBlock resolves to its class.
        """
        assert _load_block_class('vertical') is not None

    def test_uninstalled_block_type_returns_none(self):
        """
        A block type with no installed XBlock yields None rather than raising.

        Courses can end up holding a block of type 'p' after a bad OLX import. Before
        this returned None, PluginMissingError escaped ContentLibraryTransformer.collect
        and aborted the block structure build for the whole course.
        """
        assert _load_block_class('p') is None
