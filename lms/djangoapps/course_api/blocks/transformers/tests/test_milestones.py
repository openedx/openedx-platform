"""
Tests for ProctoredExamTransformer.
"""


from unittest.mock import Mock, patch

import ddt
from milestones.tests.utils import MilestonesTestCaseMixin

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory
from edx_toggles.toggles.testutils import override_waffle_flag
from lms.djangoapps.course_blocks.api import get_course_blocks
from lms.djangoapps.course_blocks.transformers.tests.helpers import CourseStructureTestCase
from lms.djangoapps.gating import api as lms_gating_api
import openedx.core.djangoapps.content.block_structure.api as bs_api
from openedx.core.djangoapps.content.block_structure.transformers import BlockStructureTransformers
from openedx.core.djangoapps.course_apps.toggles import EXAMS_IDA
from openedx.core.lib.gating import api as gating_api

from ..milestones import MilestonesAndSpecialExamsTransformer


@ddt.ddt
@patch.dict('django.conf.settings.FEATURES', {'ENABLE_SPECIAL_EXAMS': True})
class MilestonesTransformerTestCase(CourseStructureTestCase, MilestonesTestCaseMixin):
    """
    Test behavior of ProctoredExamTransformer
    """
    TRANSFORMER_CLASS_TO_TEST = MilestonesAndSpecialExamsTransformer

    def setUp(self):
        """
        Setup course structure and create user for split test transformer test.
        """
        super().setUp()

        # Build course.
        self.course_hierarchy = self.get_course_hierarchy()
        self.blocks = self.build_course(self.course_hierarchy)
        self.course = self.blocks['course']

        # Enroll user in course.
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id, is_active=True)

        self.transformers = BlockStructureTransformers([self.TRANSFORMER_CLASS_TO_TEST(False)])

    def setup_gated_section(self, gated_block, gating_block):
        """
        Test helper to create a gating requirement.
        Args:
            gated_block: The block that should be inaccessible until gating_block is completed
            gating_block: The block that must be completed before access is granted
        """
        gating_api.add_prerequisite(self.course.id, str(gating_block.location))
        gating_api.set_required_content(self.course.id, gated_block.location, gating_block.location, 100, 0)

    ALL_BLOCKS = (
        'course', 'A', 'B', 'C', 'ProctoredExam', 'D', 'E', 'PracticeExam', 'F', 'G', 'H', 'I', 'TimedExam', 'J', 'K'
    )

    # The special exams (proctored, practice, timed) are not visible to
    # students via the Courses API.
    ALL_BLOCKS_EXCEPT_SPECIAL = ('course', 'A', 'B', 'C', 'H', 'I')

    def get_course_hierarchy(self):
        """
        Get a course hierarchy to test with.
        """

        #                                course
        #               /       /             \         \   \
        #              /       /               \         \   \
        #            A     ProctoredExam   PracticeExam   H  TimedExam
        #          /  \     / \            / \            |     /  \
        #         /   \    /   \          /   \           |    /    \
        #        B    C   D     E        F    G           I    J     K
        #
        return [
            {
                'org': 'MilestonesTransformer',
                'course': 'PE101F',
                'run': 'test_run',
                '#type': 'course',
                '#ref': 'course',
            },
            {
                '#type': 'sequential',
                '#ref': 'A',
                '#children': [
                    {'#type': 'vertical', '#ref': 'B'},
                    {'#type': 'vertical', '#ref': 'C'},
                ],
            },
            {
                '#type': 'sequential',
                '#ref': 'ProctoredExam',
                'is_time_limited': True,
                'is_proctored_enabled': True,
                'is_practice_exam': False,
                '#children': [
                    {'#type': 'vertical', '#ref': 'D'},
                    {'#type': 'vertical', '#ref': 'E'},
                ],
            },
            {
                '#type': 'sequential',
                '#ref': 'PracticeExam',
                'is_time_limited': True,
                'is_proctored_enabled': True,
                'is_practice_exam': True,
                '#children': [
                    {'#type': 'vertical', '#ref': 'F'},
                    {'#type': 'vertical', '#ref': 'G'},
                ],
            },
            {
                '#type': 'sequential',
                '#ref': 'H',
                '#children': [
                    {'#type': 'vertical', '#ref': 'I'},
                ],
            },
            {
                '#type': 'sequential',
                '#ref': 'TimedExam',
                'is_time_limited': True,
                'is_proctored_enabled': False,
                'is_practice_exam': False,
                '#children': [
                    {'#type': 'vertical', '#ref': 'J'},
                    {'#type': 'vertical', '#ref': 'K'},
                ],
            },
        ]

    def test_special_exams_not_visible_to_non_staff(self):
        self.get_blocks_and_check_against_expected(self.user, self.ALL_BLOCKS_EXCEPT_SPECIAL)

    @ddt.data(
        (
            'H',
            'A',
            ('course', 'A', 'B', 'C', 'H', 'I')
        ),
        (
            'H',
            'ProctoredExam',
            ('course', 'A', 'B', 'C', 'H', 'I'),
        ),
    )
    @ddt.unpack
    def test_gated(self, gated_block_ref, gating_block_ref, expected_blocks_before_completion):
        """
        Students should be able to see gated content blocks before and after they have completed the
        prerequisite for it.

        First, checks that a student can see the gated block when it is gated by the gating block and no
        attempt has been made to complete the gating block.
        Then, checks that the student can see the gated block after the gating block has been completed.

        expected_blocks_before_completion is the set of blocks we expect to be visible to the student
        before the student has completed the gating block.

        The test data includes one special exam and one non-special block as the gating blocks.
        """
        self.course.enable_subsection_gating = True
        self.setup_gated_section(self.blocks[gated_block_ref], self.blocks[gating_block_ref])

        # Cache the course blocks so that they don't need to be generated when we're trying to
        # get data back.  This would happen as a part of publishing in a production system.
        bs_api.update_course_in_cache(self.course.id)

        with self.assertNumQueries(4):
            self.get_blocks_and_check_against_expected(self.user, expected_blocks_before_completion)

        # clear the request cache to simulate a new request
        self.clear_caches()

        # this call triggers reevaluation of prerequisites fulfilled by the gating block.
        lms_gating_api.evaluate_prerequisite(
            self.course,
            Mock(location=self.blocks[gating_block_ref].location, percent_graded=1.0),
            self.user,
        )

        with self.assertNumQueries(4):
            self.get_blocks_and_check_against_expected(self.user, self.ALL_BLOCKS_EXCEPT_SPECIAL)

    def test_staff_access(self):
        """
        Ensures that staff can always access all blocks in the course,
        regardless of gating or proctoring.
        """
        expected_blocks = self.ALL_BLOCKS
        self.setup_gated_section(self.blocks['H'], self.blocks['A'])
        self.get_blocks_and_check_against_expected(self.staff, expected_blocks)

    def test_special_exams(self):
        """
        When the block structure transformers are set to allow users to view special exams,
        ensure that we can see the special exams and not any of the otherwise gated blocks.
        """
        self.transformers = BlockStructureTransformers([self.TRANSFORMER_CLASS_TO_TEST(True)])
        self.course.enable_subsection_gating = True
        self.setup_gated_section(self.blocks['H'], self.blocks['A'])
        expected_blocks = (
            'course', 'A', 'B', 'C', 'ProctoredExam', 'D', 'E', 'PracticeExam', 'F', 'G', 'TimedExam', 'J', 'K', 'H',
            'I')  # lint-amnesty, pylint: disable=line-too-long
        self.get_blocks_and_check_against_expected(self.user, expected_blocks)
        # clear the request cache to simulate a new request
        self.clear_caches()

        # this call triggers reevaluation of prerequisites fulfilled by the gating block.
        lms_gating_api.evaluate_prerequisite(
            self.course,
            Mock(location=self.blocks['A'].location, percent_graded=1.0),
            self.user,
        )
        self.get_blocks_and_check_against_expected(self.user, self.ALL_BLOCKS)

    def get_blocks_and_check_against_expected(self, user, expected_blocks):
        """
        Calls the course API as the specified user and checks the
        output against a specified set of expected blocks.
        """
        block_structure = get_course_blocks(
            user,
            self.course.location,
            self.transformers,
        )
        assert set(block_structure.get_block_keys()) == set(self.get_block_key_set(self.blocks, *expected_blocks))

    @override_waffle_flag(EXAMS_IDA, active=False)
    @patch('lms.djangoapps.course_api.blocks.transformers.milestones.get_attempt_status_summary')
    def test_exams_ida_flag_off(self, mock_get_attempt_status_summary):
        self.transformers = BlockStructureTransformers([self.TRANSFORMER_CLASS_TO_TEST(True)])
        self.course.enable_subsection_gating = True
        self.setup_gated_section(self.blocks['H'], self.blocks['A'])
        expected_blocks = (
            'course', 'A', 'B', 'C', 'ProctoredExam', 'D', 'E', 'PracticeExam', 'F', 'G', 'TimedExam', 'J', 'K', 'H',
            'I')  # lint-amnesty, pylint: disable=line-too-long
        self.get_blocks_and_check_against_expected(self.user, expected_blocks)

        # Ensure that call is made to get_attempt_status_summary
        assert mock_get_attempt_status_summary.call_count > 0

    @override_waffle_flag(EXAMS_IDA, active=True)
    @patch('lms.djangoapps.course_api.blocks.transformers.milestones.get_attempt_status_summary')
    def test_exams_ida_flag_on(self, mock_get_attempt_status_summary):
        self.transformers = BlockStructureTransformers([self.TRANSFORMER_CLASS_TO_TEST(True)])
        self.course.enable_subsection_gating = True
        self.setup_gated_section(self.blocks['H'], self.blocks['A'])
        expected_blocks = (
            'course', 'A', 'B', 'C', 'ProctoredExam', 'D', 'E', 'PracticeExam', 'F', 'G', 'TimedExam', 'J', 'K', 'H',
            'I')  # lint-amnesty, pylint: disable=line-too-long
        self.get_blocks_and_check_against_expected(self.user, expected_blocks)

        # Ensure that no calls are made to get_attempt_status_summary
        assert mock_get_attempt_status_summary.call_count == 0
