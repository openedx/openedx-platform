"""
Tests for ContentLibraryTransformer.
"""

from unittest import mock

import openedx.core.djangoapps.content.block_structure.api as bs_api
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory
from openedx.core.djangoapps.content.block_structure.api import clear_course_from_cache
from openedx.core.djangoapps.content.block_structure.transformers import BlockStructureTransformers

from ...api import get_course_blocks
from ..library_content import ContentLibraryOrderTransformer, ContentLibraryTransformer
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

        # Build course.
        self.course_hierarchy = self.get_course_hierarchy()
        self.blocks = self.build_course(self.course_hierarchy)
        self.course = self.blocks['course']
        # Do this manually because publish signals are not fired by default in tests.
        bs_api.update_course_in_cache(self.course.id)
        clear_course_from_cache(self.course.id)

        # Enroll user in course.
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id, is_active=True)

    def get_course_hierarchy(self):
        """
        Get a course hierarchy to test with.
        """
        return [{
            'org': 'ContentLibraryTransformer',
            'course': 'CL101F',
            'run': 'test_run',
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
                                            '#type': 'library_content',
                                            '#ref': 'library_content1',
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

    def test_content_library(self):
        """
        Test when course has content library section.
        First test user can't see any content library section,
        and after that mock response from MySQL db.
        Check user can see mocked sections in content library.
        """
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
            self.blocks, 'course', 'chapter1', 'lesson1', 'vertical1', 'library_content1'
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
                                                                                         'library_content1',
                                                                                         selected_vertical,
                                                                                         selected_child), f"Expected 'selected' equality failed in iteration {i}."  # pylint: disable=line-too-long


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
        self.course_hierarchy = self.get_course_hierarchy()
        self.blocks = self.build_course(self.course_hierarchy)
        self.course = self.blocks['course']
        bs_api.update_course_in_cache(self.course.id)
        clear_course_from_cache(self.course.id)

        # Enroll user in course.
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id, is_active=True)

    def get_course_hierarchy(self):
        """
        Get a course hierarchy to test with.
        """
        return [{
            'org': 'ContentLibraryTransformer',
            'course': 'CL101F',
            'run': 'test_run',
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
                                            '#type': 'library_content',
                                            '#ref': 'library_content1',
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
    def test_content_library_randomize(self, mocked):
        """
        Test whether the order of the children blocks matches the order of the selected blocks when
        course has content library section
        """
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
                if block_key.block_type == 'library_content':
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
