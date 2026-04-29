"""
FieldOverride that forces Show Answer values that use Past Due logic to
new Show Answer values that remove the Past Due check (keeping the rest intact)
"""

from lms.djangoapps.courseware.field_overrides import FieldOverrideProvider
from openedx.features.course_experience import RELATIVE_DATES_FLAG
from xmodule.capa_block import SHOWANSWER  # pylint: disable=wrong-import-order


class ShowAnswerFieldOverride(FieldOverrideProvider):
    """
    A concrete implementation of
    :class:`~courseware.field_overrides.FieldOverrideProvider` which forces
    Show Answer values that use Past Due logic to new Show Answer values
    that remove the Past Due check (keeping the rest intact)

    Once Courseware is able to use BlockTransformers, this override should be
    converted to a BlockTransformer to set the showanswer field.
    """
    def get(self, block, name, default):
        """
        Overwrites the 'showanswer' field on blocks in self-paced courses to
        remove any checks about due dates being in the past.
        """
        if name != 'showanswer' or not self.fallback_field_data.has(block, 'showanswer'):
            return default

        mapping = {
            SHOWANSWER.ATTEMPTED: SHOWANSWER.ATTEMPTED_NO_PAST_DUE,
            SHOWANSWER.CLOSED: SHOWANSWER.AFTER_ALL_ATTEMPTS,
            SHOWANSWER.CORRECT_OR_PAST_DUE: SHOWANSWER.ANSWERED,
            SHOWANSWER.FINISHED: SHOWANSWER.AFTER_ALL_ATTEMPTS_OR_CORRECT,
            SHOWANSWER.PAST_DUE: SHOWANSWER.NEVER,
        }
        current_show_answer_value = self.fallback_field_data.get(block, 'showanswer')

        return mapping.get(current_show_answer_value, default)

    @classmethod
    def enabled_for(cls, course):  # pylint: disable=arguments-differ
        """ Enabled only for Self-Paced courses using Personalized User Schedules. """
        return course and course.self_paced and RELATIVE_DATES_FLAG.is_enabled(course.id)
