"""
Creates Call to Actions for resetting a Personalized Learner Schedule for use inside of Courseware.
"""

import logging

from crum import get_current_request
from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from openedx.core.djangolib.markup import HTML, Text
from openedx.core.lib.mobile_utils import is_request_from_mobile_app
from openedx.features.course_experience import RELATIVE_DATES_DISABLE_RESET_FLAG
from openedx.features.course_experience.url_helpers import is_request_from_learning_mfe
from openedx.features.course_experience.utils import dates_banner_should_display
from xmodule.util.misc import is_xblock_an_assignment

log = logging.getLogger(__name__)


class PersonalizedLearnerScheduleCallToAction:
    """
    Creates Call to Actions for resetting a Personalized Learner Schedule for use inside of Courseware.
    """
    CAPA_SUBMIT_DISABLED = 'capa_submit_disabled'
    VERTICAL_BANNER = 'vertical_banner'
    past_due_class_warnings = set()

    def get_ctas(self, xblock, category, completed):
        """
        Return the calls to action associated with the specified category for the given xblock.

        Look at CallToActionService docstring to see what will be returned.
        """
        ctas = []
        request = get_current_request()

        course_key = xblock.scope_ids.usage_id.context_key
        missed_deadlines, missed_gated_content = dates_banner_should_display(
            course_key, request.user, allow_warning=True
        )

        # Not showing in the missed_gated_content case because those learners are not eligible
        # to shift due dates.
        if missed_gated_content:
            return []

        # Some checks to disable PLS calls to action until these environments (mobile and MFE) support them natively
        if request and is_request_from_mobile_app(request):
            return []

        is_learning_mfe = request and is_request_from_learning_mfe(request)
        if category == self.CAPA_SUBMIT_DISABLED:
            # xblock is a capa problem, and the submit button is disabled. Check if it's because of a personalized
            # schedule due date being missed, and if so, we can offer to shift it.
            if self._is_block_shiftable(xblock, category):
                ctas.append(self._make_deadlines_cta(course_key, xblock, category, is_learning_mfe))

        elif category == self.VERTICAL_BANNER and not completed and missed_deadlines:
            # xblock is a vertical, so we'll check all the problems inside it. If there are any that will show a
            # a "shift dates" CTA under CAPA_SUBMIT_DISABLED, then we'll also show the same CTA as a vertical banner.
            if any(self._is_block_shiftable(item, category) for item in xblock.get_children()):
                ctas.append(self._make_deadlines_cta(course_key, xblock, category, is_learning_mfe))

        return ctas

    @classmethod
    def _is_block_shiftable(cls, xblock, category):
        """
        Test if the xblock would be solvable if we were to shift dates.

        Only xblocks with an is_past_due method (e.g. capa and LTI) will be considered possibly shiftable.
        """
        if not hasattr(xblock, 'is_past_due'):
            return False

        if hasattr(xblock, 'attempts') and hasattr(xblock, 'max_attempts'):
            can_attempt = xblock.max_attempts is None or xblock.attempts < xblock.max_attempts
        else:
            can_attempt = True

        if callable(xblock.is_past_due):
            is_past_due = xblock.is_past_due()
        else:
            PersonalizedLearnerScheduleCallToAction._log_past_due_warning(type(xblock).__name__)
            is_past_due = xblock.is_past_due

        can_shift = xblock.self_paced and can_attempt and is_past_due

        # Note: we will still show the CTA at the xblock level (next to the submit button) regardless
        # of if the xblock is an assignment (meaning graded *and* scored)
        if category == cls.VERTICAL_BANNER:
            can_shift = can_shift and is_xblock_an_assignment(xblock)

        return can_shift

    @staticmethod
    def _log_past_due_warning(name):
        """
        Logs out if an xblock has is_past_due defined as a property
        (since we want to move to using it as a function everywhere)
        """
        if name in PersonalizedLearnerScheduleCallToAction.past_due_class_warnings:
            return

        log.warning('PersonalizedLearnerScheduleCallToAction has encountered an xblock that defines is_past_due '
                    'as a property. This is supported for now, but may not be in the future. Please change '
                    '%s.is_past_due into a method.', name)
        PersonalizedLearnerScheduleCallToAction.past_due_class_warnings.add(name)

    @classmethod
    def _make_deadlines_cta(cls, course_key, xblock, category, is_learning_mfe=False):
        """
        Constructs a call to action object containing the necessary information for the view
        """
        from lms.urls import RESET_COURSE_DEADLINES_NAME

        if RELATIVE_DATES_DISABLE_RESET_FLAG.is_enabled(course_key):
            return {"description": _("The deadline to complete this assignment has passed.")}

        course_key = xblock.scope_ids.usage_id.context_key

        cta_data = {
            'link': reverse(RESET_COURSE_DEADLINES_NAME),
            'link_name': _('Shift due dates'),
            'form_values': {
                'course_id': course_key,
            },
            'description': Text('{b_open}{header}{b_close} {explanation}').format(
                b_open=HTML('<b>'),
                b_close=HTML('</b>'),
                header=_('It looks like you missed some important deadlines based on our suggested schedule.'),
                explanation=_('To keep yourself on track, you can update this schedule and shift the past due '
                              'assignments into the future. Don’t worry—you won’t lose any of the progress you’ve '
                              'made when you shift your due dates.'),
            ),
        }

        has_attempts = hasattr(xblock, 'attempts') and hasattr(xblock, 'max_attempts')

        if category == cls.CAPA_SUBMIT_DISABLED and has_attempts and xblock.attempts:
            if xblock.max_attempts:
                cta_data['link_name'] = ngettext('Try again ({attempts} attempt remaining)',
                                                 'Try again ({attempts} attempts remaining)',
                                                 (xblock.max_attempts - xblock.attempts)).format(
                    attempts=(xblock.max_attempts - xblock.attempts)
                )
                cta_data['description'] = (_('You have used {attempts} of {max_attempts} attempts for this '
                                             'problem.').format(
                    attempts=xblock.attempts, max_attempts=xblock.max_attempts
                ) + ' ' + cta_data['description'])
            else:
                cta_data['link_name'] = _('Try again (unlimited attempts)')
                cta_data['description'] = _('You have used {attempts} of unlimited attempts for this '
                                            'problem.').format(attempts=xblock.attempts) + ' ' + cta_data['description']

        if is_learning_mfe:
            cta_data['event_data'] = {
                'event_name': 'post_event',
                'post_data': {
                    'body_params': {
                        'course_id': str(course_key),
                    },
                    'url': '{}{}'.format(settings.LMS_ROOT_URL, reverse('course-experience-reset-course-deadlines')),
                },
                'research_event_data': {
                    'block_id': str(xblock.location),
                    'location': f'{xblock.category}-view',
                },
            }

        return cta_data
