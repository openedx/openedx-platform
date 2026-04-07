"""
Tests for HiddenContentTransformer.
"""


from datetime import timedelta

import ddt
from django.utils.timezone import now
from edx_when.api import get_dates_for_course, set_date_for_block
from edx_when.field_data import DateOverrideTransformer

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.course_blocks.transformers.hidden_content import HiddenContentTransformer
from lms.djangoapps.course_blocks.transformers.tests.helpers import BlockParentsMapTestCase, update_block
from openedx.core.djangoapps.content.block_structure.tests.helpers import mock_registered_transformers
from openedx.core.djangoapps.content.block_structure.transformers import BlockStructureTransformers


@ddt.ddt
class HiddenContentTransformerTestCase(BlockParentsMapTestCase):
    """
    HiddenContentTransformer Test
    """
    TRANSFORMER_CLASS_TO_TEST = HiddenContentTransformer
    ALL_BLOCKS = {0, 1, 2, 3, 4, 5, 6}

    class DateType:
        """
        Use constant enum types for deterministic ddt test method names (rather than dynamically generated timestamps)
        """
        none = 1
        future = 2
        past = 3

        TODAY = now()
        PAST_DATE = TODAY - timedelta(days=30)
        FUTURE_DATE = TODAY + timedelta(days=30)

        @classmethod
        def get_date(cls, enum_value):
            """
            Returns a start date for the given enum value
            """
            if enum_value == cls.future:
                return cls.FUTURE_DATE
            elif enum_value == cls.past:
                return cls.PAST_DATE
            else:
                return None

    # Following test cases are based on BlockParentsMapTestCase.parents_map:
    #        0
    #     /     \
    #    1       2
    #   / \     / \
    #  3   4   /   5
    #       \ /
    #        6
    @ddt.data(
        ({}, ALL_BLOCKS),

        ({0: DateType.none}, ALL_BLOCKS),
        ({0: DateType.future}, ALL_BLOCKS),
        ({1: DateType.none}, ALL_BLOCKS),
        ({1: DateType.future}, ALL_BLOCKS),
        ({4: DateType.none}, ALL_BLOCKS),
        ({4: DateType.future}, ALL_BLOCKS),

        ({0: DateType.past}, {}),
        ({1: DateType.past}, ALL_BLOCKS - {1, 3, 4}),
        ({2: DateType.past}, ALL_BLOCKS - {2, 5}),
        ({4: DateType.past}, ALL_BLOCKS - {4}),

        ({1: DateType.past, 2: DateType.past}, {0}),
        ({1: DateType.none, 2: DateType.past}, ALL_BLOCKS - {2, 5}),
        ({1: DateType.past, 2: DateType.none}, ALL_BLOCKS - {1, 3, 4}),
    )
    @ddt.unpack
    def test_hidden_content(
            self,
            hide_due_values,
            expected_visible_blocks,
    ):
        """ Tests content is hidden if due date is in the past """

        for idx, due_date_type in hide_due_values.items():
            block = self.get_block(idx)
            block.due = self.DateType.get_date(due_date_type)
            block.hide_after_due = True
            update_block(block)

        self.assert_transform_results(
            self.student,
            expected_visible_blocks,
            blocks_with_differing_access=None,
            transformers=self.transformers,
        )

    @ddt.data(
        (DateType.none, {}, ALL_BLOCKS),

        (DateType.none, {0}, ALL_BLOCKS),
        (DateType.future, {0, 2}, ALL_BLOCKS),
        (DateType.none, {1}, ALL_BLOCKS),
        (DateType.future, {1}, ALL_BLOCKS),
        (DateType.none, {4}, ALL_BLOCKS),
        (DateType.future, {4}, ALL_BLOCKS),

        (DateType.past, {0}, {}),
        (DateType.past, {1}, ALL_BLOCKS - {1, 3, 4}),
        (DateType.past, {2}, ALL_BLOCKS - {2, 5}),
        (DateType.past, {4}, ALL_BLOCKS - {4}),
        (DateType.past, {1, 2}, {0}),
        (DateType.past, {2, 4}, ALL_BLOCKS - {2, 4, 5, 6}),
    )
    @ddt.unpack
    def test_hidden_content_self_paced_course(
            self,
            course_end_date_type,
            hide_after_due_blocks,
            expected_visible_blocks,
    ):
        """ Tests content is hidden if end date is in the past and course is self paced """
        course = self.get_block(0)
        course.self_paced = True
        course.end = self.DateType.get_date(course_end_date_type)
        update_block(course)

        for block_idx in hide_after_due_blocks:
            block = self.get_block(block_idx)
            block.hide_after_due = True
            update_block(block)

        self.assert_transform_results(
            self.student,
            expected_visible_blocks,
            blocks_with_differing_access=None,
            transformers=self.transformers,
        )

    def test_hidden_content_with_transformer_override(self):
        """
        Tests content is hidden if the date changes after collection and
        during the transform phase (for example, by the DateOverrideTransformer).
        """
        with mock_registered_transformers([DateOverrideTransformer, self.TRANSFORMER_CLASS_TO_TEST]):
            transformers = BlockStructureTransformers(
                [DateOverrideTransformer(self.student), self.TRANSFORMER_CLASS_TO_TEST()]
            )

        block = self.get_block(1)
        block.hide_after_due = True
        update_block(block)
        set_date_for_block(self.course.id, block.location, 'due', self.DateType.PAST_DATE)

        # Due date is in the past so some blocks are hidden
        self.assert_transform_results(
            self.student,
            self.ALL_BLOCKS - {1, 3, 4},
            blocks_with_differing_access=None,
            transformers=transformers,
        )

        # Set an override for the due date to be in the future
        set_date_for_block(self.course.id, block.location, 'due', self.DateType.FUTURE_DATE, user=self.student)
        # this line is just to bust the cache for the user so it returns the updated date.
        get_dates_for_course(self.course.id, user=self.student, use_cached=False)

        # Now all blocks are returned for the student
        self.assert_transform_results(
            self.student,
            self.ALL_BLOCKS,
            blocks_with_differing_access=None,
            transformers=transformers,
        )

        # But not for a different user
        different_user = UserFactory()
        with mock_registered_transformers([DateOverrideTransformer, self.TRANSFORMER_CLASS_TO_TEST]):
            transformers = BlockStructureTransformers(
                [DateOverrideTransformer(different_user), self.TRANSFORMER_CLASS_TO_TEST()]
            )
        self.assert_transform_results(
            different_user,
            self.ALL_BLOCKS - {1, 3, 4},
            blocks_with_differing_access=None,
            transformers=transformers,
        )
