"""
As currently designed, this processor ignores the course specific
`Enable Timed Exams` setting when determining whether or not it should
remove keys and/or supplement exam data. This matches the exact behavior
of `MilestonesAndSpecialExamsTransformer`. It is not entirely clear if
the behavior should be modified, so it has been decided to consider any
necessary fixes in a new ticket.

Please see the PR and discussion linked below for further context
https://github.com/openedx/edx-platform/pull/24545#discussion_r501738511
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _
from edx_proctoring.api import get_attempt_status_summary
from edx_proctoring.exceptions import ProctoredExamNotFoundException

from openedx.core.djangoapps.course_apps.toggles import exams_ida_enabled

from ...data import SpecialExamAttemptData, UserCourseOutlineData
from .base import OutlineProcessor

User = get_user_model()
log = logging.getLogger(__name__)


class SpecialExamsOutlineProcessor(OutlineProcessor):
    """
    Responsible for applying all outline processing related to special exams.
    """
    def load_data(self, full_course_outline):
        """
        Check if special exams are enabled
        """
        self.special_exams_enabled = settings.FEATURES.get('ENABLE_SPECIAL_EXAMS', False)  # lint-amnesty, pylint: disable=attribute-defined-outside-init

    def exam_data(self, pruned_course_outline: UserCourseOutlineData) -> SpecialExamAttemptData:
        """
        Return supplementary special exam information for this outline.

        Be careful to pass in a UserCourseOutlineData - i.e. an outline that has
        already been pruned to what a user is allowed to see. That way, we can
        use this to make sure that we're not returning data about
        LearningSequences that the user can't see because it was hidden by a
        different OutlineProcessor.
        """
        sequences = {}
        if self.special_exams_enabled:
            for section in pruned_course_outline.sections:
                for sequence in section.sequences:
                    # Don't bother checking for information
                    # on non-exam sequences
                    if not bool(sequence.exam):
                        continue

                    special_exam_attempt_context = self._generate_special_exam_attempt_context(
                        sequence.exam.is_practice_exam,
                        sequence.exam.is_proctored_enabled,
                        sequence.exam.is_time_limited,
                        self.user.id,
                        self.course_key,
                        str(sequence.usage_key)
                    )

                    if special_exam_attempt_context:
                        # Return exactly the same format as the edx_proctoring API response
                        sequences[sequence.usage_key] = special_exam_attempt_context

        return SpecialExamAttemptData(
            sequences=sequences,
        )

    def _generate_special_exam_attempt_context(self, is_practice_exam, is_proctored_enabled,
                                               is_timed_exam, user_id, course_key, block_key):
        """
        Helper method which generates the special exam attempt context.
        Either calls into proctoring or, if exams ida waffle flag on, then get internally.
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
