# lint-amnesty, pylint: disable=missing-module-docstring
import logging

from django.contrib.auth import get_user_model

from common.djangoapps.util import milestones_helpers

from .base import OutlineProcessor

User = get_user_model()
log = logging.getLogger(__name__)


class MilestonesOutlineProcessor(OutlineProcessor):
    """
    Responsible for applying all general course milestones outline processing.

    This does not include Entrance Exams (see `ContentGatingOutlineProcessor`),
    or Special Exams (see `SpecialExamsOutlineProcessor`)
    """
    def inaccessible_sequences(self, full_course_outline):
        """
        Returns the set of sequence usage keys for which the
        user has pending milestones
        """
        inaccessible = set()
        for section in full_course_outline.sections:
            inaccessible |= {
                seq.usage_key
                for seq in section.sequences
                if self.has_pending_milestones(seq.usage_key)
            }

        return inaccessible

    def has_pending_milestones(self, usage_key):
        return bool(milestones_helpers.get_course_content_milestones(
            str(self.course_key),
            str(usage_key),
            'requires',
            self.user.id
        ))
