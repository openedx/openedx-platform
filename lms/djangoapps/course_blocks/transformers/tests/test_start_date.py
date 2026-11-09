"""
Tests for StartDateTransformer.
"""


from datetime import timedelta

import ddt
from django.test.utils import override_settings
from django.utils.timezone import now

from common.djangoapps.student.tests.factories import BetaTesterFactory

from ..start_date import DEFAULT_START_DATE, StartDateTransformer
from .helpers import BlockParentsMapTestCase, update_block


@ddt.ddt
class StartDateTransformerTestCase(BlockParentsMapTestCase):
    """
    StartDateTransformer Test
    """
    STUDENT = 1
    BETA_USER = 2
    TRANSFORMER_CLASS_TO_TEST = StartDateTransformer

    class StartDateType:
        """
        Use constant enum types for deterministic ddt test method names (rather than dynamically generated timestamps)
        """
        released = 1
        future = 2
        default = 3

        TODAY = now()
        LAST_MONTH = TODAY - timedelta(days=30)
        NEXT_MONTH = TODAY + timedelta(days=30)

        @classmethod
        def start(cls, enum_value):
            """
            Returns a start date for the given enum value
            """
            if enum_value == cls.released:
                return cls.LAST_MONTH
            elif enum_value == cls.future:
                return cls.NEXT_MONTH
            else:
                return DEFAULT_START_DATE

    def setUp(self):
        super().setUp()
        self.beta_user = BetaTesterFactory(course_key=self.course.id, username='beta_tester', password=self.password)
        course = self.get_block(0)
        course.days_early_for_beta = 33
        update_block(course)

    # Following test cases are based on BlockParentsMapTestCase.parents_map:
    #        0
    #     /     \
    #    1       2
    #   / \     / \
    #  3   4   /   5
    #       \ /
    #        6
    @override_settings(DISABLE_START_DATES=False)
    @ddt.data(
        (STUDENT, {}, {}, {}),
        (STUDENT, {0: StartDateType.default}, {}, {}),
        (STUDENT, {0: StartDateType.future}, {}, {}),
        (STUDENT, {0: StartDateType.released}, {0, 1, 2, 3, 4, 5, 6}, {}),

        # has_access checks on block directly and doesn't follow negative access set on parent/ancestor (i.e., 0)
        (STUDENT, {1: StartDateType.released}, {}, {1, 3, 4, 6}),
        (STUDENT, {2: StartDateType.released}, {}, {2, 5}),
        (STUDENT, {1: StartDateType.released, 2: StartDateType.released}, {}, {1, 2, 3, 4, 5, 6}),

        # DAG conflicts: has_access relies on field inheritance so it takes only the value from the first parent-chain
        (STUDENT, {0: StartDateType.released, 4: StartDateType.future}, {0, 1, 2, 3, 5, 6}, {6}),
        (
            STUDENT,
            {0: StartDateType.released, 2: StartDateType.released, 4: StartDateType.future},
            {0, 1, 2, 3, 5, 6},
            {6},
        ),
        (STUDENT, {0: StartDateType.released, 2: StartDateType.future, 4: StartDateType.released}, {0, 1, 3, 4, 6}, {}),

        # beta user cases
        (BETA_USER, {}, {}, {}),
        (BETA_USER, {0: StartDateType.default}, {}, {}),
        (BETA_USER, {0: StartDateType.future}, {0, 1, 2, 3, 4, 5, 6}, {}),
        (BETA_USER, {0: StartDateType.released}, {0, 1, 2, 3, 4, 5, 6}, {}),

        (
            BETA_USER,
            {0: StartDateType.released, 2: StartDateType.default, 5: StartDateType.future},
            {0, 1, 3, 4, 6},
            {5},
        ),
        (BETA_USER, {1: StartDateType.released, 2: StartDateType.default}, {}, {1, 3, 4, 6}),
        (BETA_USER, {0: StartDateType.released, 4: StartDateType.future}, {0, 1, 2, 3, 4, 5, 6}, {}),
        (BETA_USER, {0: StartDateType.released, 4: StartDateType.default}, {0, 1, 2, 3, 5, 6}, {6}),
    )
    @ddt.unpack
    def test_block_start_date(
            self,
            user_type,
            start_date_type_values,
            expected_student_visible_blocks,
            blocks_with_differing_student_access
    ):
        for idx, start_date_type in start_date_type_values.items():
            block = self.get_block(idx)
            block.start = self.StartDateType.start(start_date_type)
            update_block(block)

        self.assert_transform_results(
            self.beta_user if user_type == self.BETA_USER else self.student,
            expected_student_visible_blocks,
            blocks_with_differing_student_access,
            self.transformers,
        )
