# lint-amnesty, pylint: disable=missing-module-docstring
import logging
from datetime import datetime

from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import EntranceExamConfiguration
from common.djangoapps.util import milestones_helpers
from openedx.core import types

from .base import OutlineProcessor

log = logging.getLogger(__name__)


class ContentGatingOutlineProcessor(OutlineProcessor):
    """
    Responsible for applying all content gating outline processing.

    This includes:
    - Entrance Exams
    - Chapter gated content
    """

    def __init__(self, course_key: CourseKey, user: types.User, at_time: datetime):
        super().__init__(course_key, user, at_time)
        self.required_content = None
        self.can_skip_entrance_exam = False

    def load_data(self, full_course_outline):
        """
        Get the required content for the course, and whether
        or not the user can skip the entrance exam.
        """
        self.required_content = milestones_helpers.get_required_content(self.course_key, self.user)

        if self.user.is_authenticated:
            self.can_skip_entrance_exam = EntranceExamConfiguration.user_can_skip_entrance_exam(
                self.user, self.course_key
            )

    def inaccessible_sequences(self, full_course_outline):
        """
        Mark any section that is gated by required content as inaccessible
        """
        if full_course_outline.entrance_exam_id and self.can_skip_entrance_exam:
            self.required_content = [
                content
                for content in self.required_content
                if not content == full_course_outline.entrance_exam_id
            ]

        inaccessible = set()
        for section in full_course_outline.sections:
            if self.gated_by_required_content(section.usage_key):
                inaccessible |= {
                    seq.usage_key
                    for seq in section.sequences
                }

        return inaccessible

    def gated_by_required_content(self, section_usage_key):
        """
        Returns True if the current section associated with the usage_key should be gated by the given required_content.
        Returns False otherwise.
        """
        if not self.required_content:
            return False

        # This should always be a chapter block
        assert section_usage_key.block_type == 'chapter'
        if str(section_usage_key) not in self.required_content:
            return True

        return False
