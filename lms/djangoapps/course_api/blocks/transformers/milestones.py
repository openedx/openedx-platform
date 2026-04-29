"""
Milestones Transformer
"""


import logging

from django.conf import settings
from django.utils.translation import gettext as _
from edx_proctoring.api import get_attempt_status_summary
from edx_proctoring.exceptions import ProctoredExamNotFoundException

from common.djangoapps.student.models import EntranceExamConfiguration
from common.djangoapps.util import milestones_helpers
from openedx.core.djangoapps.content.block_structure.transformer import BlockStructureTransformer
from openedx.core.djangoapps.course_apps.toggles import exams_ida_enabled

log = logging.getLogger(__name__)


class MilestonesAndSpecialExamsTransformer(BlockStructureTransformer):
    """
    A transformer that handles both milestones and special (timed) exams.

    It includes or excludes all unfulfilled milestones from the student view based on the value of `include_gated_sections`.  # pylint: disable=line-too-long

    An entrance exam is considered a milestone, and is not considered a "special exam".

    It also includes or excludes all special (timed) exams (timed, proctored, practice proctored) in/from the
    student view, based on the value of `include_special_exams`.

    """
    WRITE_VERSION = 1
    READ_VERSION = 1

    @classmethod
    def name(cls):
        return "milestones"

    def __init__(self, include_special_exams=True, include_gated_sections=True):
        self.include_special_exams = include_special_exams
        self.include_gated_sections = include_gated_sections

    @classmethod
    def collect(cls, block_structure):
        """
        Computes any information for each XBlock that's necessary to execute
        this transformer's transform method.

        Arguments:
            block_structure (BlockStructureCollectedData)
        """
        block_structure.request_xblock_fields('is_proctored_enabled')
        block_structure.request_xblock_fields('is_practice_exam')
        block_structure.request_xblock_fields('is_timed_exam')
        block_structure.request_xblock_fields('entrance_exam_id')

    def transform(self, usage_info, block_structure):
        """
        Modify block structure according to the behavior of milestones and special exams.
        """
        required_content = self.get_required_content(usage_info, block_structure)

        def user_gated_from_block(block_key):
            """
            Checks whether the user is gated from accessing this block, first via special exams,
            then via a general milestones check.
            """

            if usage_info.has_staff_access:
                return False
            elif self.gated_by_required_content(block_key, block_structure, required_content):
                return True
            elif not self.include_gated_sections and self.has_pending_milestones_for_user(block_key, usage_info):
                return True
            elif (settings.FEATURES.get('ENABLE_SPECIAL_EXAMS', False) and
                  (self.is_special_exam(block_key, block_structure) and
                   not self.include_special_exams)):
                return True
            return False

        for block_key in block_structure.topological_traversal():
            if user_gated_from_block(block_key):
                block_structure.remove_block(block_key, False)
            elif self.is_special_exam(block_key, block_structure):
                self.add_special_exam_info(block_key, block_structure, usage_info)

    @staticmethod
    def is_special_exam(block_key, block_structure):
        """
        Test whether the block is a special exam.
        """
        return (
            block_structure.get_xblock_field(block_key, 'is_proctored_enabled') or
            block_structure.get_xblock_field(block_key, 'is_practice_exam') or
            block_structure.get_xblock_field(block_key, 'is_timed_exam')
        )

    @staticmethod
    def has_pending_milestones_for_user(block_key, usage_info):
        """
        Test whether the current user has any unfulfilled milestones preventing
        them from accessing this block.
        """
        return bool(milestones_helpers.get_course_content_milestones(
            str(block_key.course_key),
            str(block_key),
            'requires',
            usage_info.user.id
        ))

    # TODO: As part of a cleanup effort, this transformer should be split into
    # MilestonesTransformer and SpecialExamsTransformer, which are completely independent.
    def add_special_exam_info(self, block_key, block_structure, usage_info):
        """
        For special exams, add the special exam information to the course blocks.
        """
        special_exam_attempt_context = self._generate_special_exam_attempt_context(
            block_structure.get_xblock_field(block_key, 'is_practice_exam'),
            block_structure.get_xblock_field(block_key, 'is_proctored_enabled'),
            block_structure.get_xblock_field(block_key, 'is_timed_exam'),
            usage_info.user.id,
            block_key.course_key,
            str(block_key)
        )

        if special_exam_attempt_context:
            # This user has special exam context for this block so add it.
            block_structure.set_transformer_block_field(
                block_key,
                self,
                'special_exam_info',
                special_exam_attempt_context,
            )

    @staticmethod
    def get_required_content(usage_info, block_structure):
        """
        Get the required content for the course.

        This takes into account if the user can skip the entrance exam.

        """
        course_key = block_structure.root_block_usage_key.course_key
        user_can_skip_entrance_exam = False
        if usage_info.user.is_authenticated:
            user_can_skip_entrance_exam = EntranceExamConfiguration.user_can_skip_entrance_exam(
                usage_info.user, course_key)
        required_content = milestones_helpers.get_required_content(course_key, usage_info.user)

        if not required_content:
            return required_content

        if user_can_skip_entrance_exam:
            # remove the entrance exam from required content
            entrance_exam_id = block_structure.get_xblock_field(block_structure.root_block_usage_key, 'entrance_exam_id')  # pylint: disable=line-too-long
            required_content = [content for content in required_content if not content == entrance_exam_id]

        return required_content

    @staticmethod
    def gated_by_required_content(block_key, block_structure, required_content):  # pylint: disable=unused-argument
        """
        Returns True if the current block associated with the block_key should be gated by the given required_content.
        Returns False otherwise.
        """
        if not required_content:
            return False

        if block_key.block_type == 'chapter' and str(block_key) not in required_content:
            return True

        return False

    def _generate_special_exam_attempt_context(self, is_practice_exam, is_proctored_enabled,
                                               is_timed_exam, user_id, course_key, block_key):
        """
        Helper method which generates the special exam attempt context.
        Either calls into proctoring or, if exams ida waffle flag on, then get internally.
        Note: This method duplicates the method by the same name in:
        openedx/core/djangoapps/content/learning_sequences/api/processors/special_exams.py
        For now, both methods exist to avoid importing from different directories. In the future,
        we could potentially consolidate if there is a good common place to implement.
        """
        special_exam_attempt_context = None

        # if exams waffle flag enabled, get exam type internally
        if exams_ida_enabled(course_key):
            # add short description based on exam type
            if is_practice_exam:
                exam_type = _('Practice Exam')
            elif is_proctored_enabled:
                exam_type = _('Proctored Exam')
            elif is_timed_exam:
                exam_type = _('Timed Exam')
            else:  # sets a default, though considered impossible
                log.info('Using default Exam value for exam type.')
                exam_type = _('Exam')

            summary = {'short_description': exam_type, }
            special_exam_attempt_context = summary
        else:
            try:
                # Calls into edx_proctoring subsystem to get relevant special exam information.
                special_exam_attempt_context = get_attempt_status_summary(
                    user_id,
                    str(course_key),
                    block_key
                )
            except ProctoredExamNotFoundException as ex:
                log.exception(ex)

        return special_exam_attempt_context
