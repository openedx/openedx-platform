"""
XBlock implementation of a learning sequence
"""

# pylint: disable=abstract-method


import collections
import json
import logging
from datetime import datetime
from functools import reduce
from django.conf import settings
from zoneinfo import ZoneInfo

from edx_django_utils.monitoring import set_custom_attribute
from lxml import etree
from opaque_keys.edx.keys import UsageKey
from web_fragments.fragment import Fragment
from xblock.completable import XBlockCompletionMode
from xblock.core import XBlock
from xblock.exceptions import NoSuchServiceError
from xblock.fields import Boolean, Date, Integer, List, Scope, String
from xblock.progress import Progress

from edx_toggles.toggles import WaffleFlag, SettingToggle
from xmodule.util.builtin_assets import add_webpack_js_to_fragment, add_css_to_fragment
from xmodule.x_module import (
    ResourceTemplates,
    shim_xmodule_js,
    STUDENT_VIEW,
    XModuleMixin,
    XModuleToXBlockMixin,
)

from common.djangoapps.xblock_django.constants import ATTR_KEY_USER_ID, ATTR_KEY_USER_IS_STAFF

from .exceptions import NotFoundError
from .mako_block import MakoTemplateBlockBase
from .x_module import AUTHOR_VIEW, PUBLIC_VIEW
from .xml_block import XmlMixin

log = logging.getLogger(__name__)

# HACK: This shouldn't be hard-coded to two types
# OBSOLETE: This obsoletes 'type'
class_priority = ['video', 'problem']

# Make '_' a no-op so we can scrape strings. Using lambda instead of
#  `django.utils.translation.ugettext_noop` because Django cannot be imported in this file
_ = lambda text: text

TIMED_EXAM_GATING_WAFFLE_FLAG = WaffleFlag(  # lint-amnesty, pylint: disable=toggle-missing-annotation
    'xmodule.rev_1377_rollout', __name__
)

# .. toggle_name: SHOW_PROGRESS_BAR
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: Set to True to show progress bar.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2022-02-09
# .. toggle_target_removal_date: None
SHOW_PROGRESS_BAR = SettingToggle("SHOW_PROGRESS_BAR", default=False, module_name=__name__)


class SequenceFields:  # lint-amnesty, pylint: disable=missing-class-docstring
    has_children = True
    completion_mode = XBlockCompletionMode.AGGREGATOR

    # NOTE: Position is 1-indexed.  This is silly, but there are now student
    # positions saved on prod, so it's not easy to fix.
    position = Integer(help="Last tab viewed in this sequence", scope=Scope.user_state)

    due = Date(
        display_name=_("Due Date"),
        help=_("Enter the date by which problems are due."),
        scope=Scope.settings,
    )
    # This attribute is for custom pacing in self paced courses for Studio if CUSTOM_RELATIVE_DATES flag is active
    relative_weeks_due = Integer(
        display_name=_("Number of Relative Weeks Due By"),
        help=_("Enter the number of weeks the problems are due by relative to the learner's enrollment date"),
        scope=Scope.settings,
    )

    hide_after_due = Boolean(
        display_name=_("Hide sequence content After Due Date"),
        help=_(
            "If set, the sequence content is hidden for non-staff users after the due date has passed."
        ),
        default=False,
        scope=Scope.settings,
    )

    is_entrance_exam = Boolean(
        display_name=_("Is Entrance Exam"),
        help=_(
            "Tag this course block as an Entrance Exam. "
            "Note, you must enable Entrance Exams for this course setting to take effect."
        ),
        default=False,
        scope=Scope.settings,
    )


class SequenceMixin(SequenceFields):
    """
    A mixin of shared code between the SequenceBlock and other XBlocks.
    """
    @classmethod
    def definition_from_xml(cls, xml_object, system):  # pylint: disable=missing-function-docstring
        children = []
        for child in xml_object:
            try:
                child_block = system.process_xml(etree.tostring(child, encoding='unicode'))
                children.append(child_block.scope_ids.usage_id)
            except Exception as e:  # pylint: disable=broad-except
                log.exception("Unable to load child when parsing Sequence. Continuing...")
                if system.error_tracker is not None:
                    system.error_tracker(f"ERROR: {e}")
                continue
        return {}, children

    def index_dictionary(self):
        """
        Return dictionary prepared with module content and type for indexing.
        """
        # return key/value fields in a Python dict object
        # values may be numeric / string or dict
        # default implementation is an empty dict
        xblock_body = super().index_dictionary()
        html_body = {
            "display_name": self.display_name,
        }
        if "content" in xblock_body:
            xblock_body["content"].update(html_body)
        else:
            xblock_body["content"] = html_body
        xblock_body["content_type"] = "Sequence"

        return xblock_body


class ProctoringFields:
    """
    Fields that are specific to Proctored or Timed Exams
    """
    is_time_limited = Boolean(
        display_name=_("Is Time Limited"),
        help=_(
            "This setting indicates whether students have a limited time"
            " to view or interact with this courseware component."
        ),
        default=False,
        scope=Scope.settings,
    )

    default_time_limit_minutes = Integer(
        display_name=_("Time Limit in Minutes"),
        help=_(
            "The number of minutes available to students for viewing or interacting with this courseware component."
        ),
        default=None,
        scope=Scope.settings,
    )

    is_proctored_enabled = Boolean(
        display_name=_("Is Proctoring Enabled"),
        help=_(
            "This setting indicates whether this exam is a proctored exam."
        ),
        default=False,
        scope=Scope.settings,
    )

    exam_review_rules = String(
        display_name=_("Software Secure Review Rules"),
        help=_(
            "This setting indicates what rules the proctoring team should follow when viewing the videos."
        ),
        default='',
        scope=Scope.settings,
    )

    is_practice_exam = Boolean(
        display_name=_("Is Practice Exam"),
        help=_(
            "This setting indicates whether this exam is for testing purposes only. Practice exams are not verified."
        ),
        default=False,
        scope=Scope.settings,
    )

    is_onboarding_exam = Boolean(
        display_name=_("Is Onboarding Exam"),
        help=_(
            "This setting indicates whether this exam is an onboarding exam."
        ),
        default=False,
        scope=Scope.settings,
    )

    def _get_course(self):
        """
        Return course by course id.
        """
        return self.runtime.modulestore.get_course(self.scope_ids.usage_id.context_key)  # pylint: disable=no-member

    @property
    def is_timed_exam(self):
        """
        Alias the permutation of above fields that corresponds to un-proctored timed exams
        to the more clearly-named is_timed_exam
        """
        return not self.is_proctored_enabled and not self.is_practice_exam and self.is_time_limited

    @property
    def is_proctored_exam(self):
        """ Alias the is_proctored_enabled field to the more legible is_proctored_exam """
        return self.is_proctored_enabled

    @property
    def allow_proctoring_opt_out(self):
        """
        Returns true if the learner should be given the option to choose between
        taking a proctored exam, or opting out to take the exam without proctoring.
        """
        return self._get_course().allow_proctoring_opt_out

    @is_proctored_exam.setter
    def is_proctored_exam(self, value):
        """ Alias the is_proctored_enabled field to the more legible is_proctored_exam """
        self.is_proctored_enabled = value


@XBlock.wants('proctoring')
@XBlock.wants('gating')
@XBlock.wants('credit')
@XBlock.wants('completion')
@XBlock.needs('user')
@XBlock.needs('bookmarks')
@XBlock.needs('i18n')
@XBlock.needs('mako')
@XBlock.wants('content_type_gating')
class SequenceBlock(
    SequenceMixin,
    SequenceFields,
    ProctoringFields,
    MakoTemplateBlockBase,
    XmlMixin,
    XModuleToXBlockMixin,
    ResourceTemplates,
    XModuleMixin,
):
    """
    Layout module which lays out content in a temporal sequence
    """
    resources_dir = None
    has_author_view = True

    show_in_read_only_mode = True
    uses_xmodule_styles_setup = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.gated_sequence_paywall = None

    def bind_for_student(self, user_id, wrappers=None):
        # The position of the child XBlock to select can also be passed in via the URL.
        # In such cases the value is set in the runtime inside prepare_runtime_for_user()
        # and needs to be read here after the value has been set.
        super().bind_for_student(user_id, wrappers)
        # If position is specified in system, then use that instead.
        position = getattr(self.runtime, 'position', None)
        if position is not None:
            assert isinstance(position, int)
            self.position = self.runtime.position

    def get_progress(self):
        ''' Return the total progress, adding total done and total available.
        (assumes that each submodule uses the same "units" for progress.)
        '''
        # TODO: Cache progress or children array?
        children = self.get_children()
        progresses = [child.get_progress() for child in children]
        progress = reduce(Progress.add_counts, progresses, None)
        return progress

    @XBlock.json_handler
    def get_completion(self, data, _suffix=''):
        """Returns whether the provided vertical is complete based off the 'usage_key' value in the incoming dict"""
        return self._get_completion(data)
    # This 'will_recheck_access' attribute is checked by the upper-level handler code, where it will avoid stripping
    # inaccessible blocks from our tree. We don't want them stripped because 'get_completion' needs to know about FBE
    # blocks even if the user can't complete them, otherwise it might accidentally say a vertical is complete when
    # there are still incomplete but access-locked blocks left.
    get_completion.will_recheck_access = True

    def _get_completion(self, data):
        """Returns whether the provided vertical is complete based off the 'usage_key' value in the incoming dict"""
        complete = False
        usage_key = data.get('usage_key', None)
        if usage_key:
            item = self.get_child(UsageKey.from_string(usage_key))
            if item:
                completion_service = self.runtime.service(self, 'completion')
                complete = completion_service.vertical_is_complete(item)
        return {'complete': complete}

    @XBlock.json_handler
    def goto_position(self, data, _suffix=''):
        """Sets the xblock position based off the 'position' value in the incoming dict"""
        return self._goto_position(data)

    def _goto_position(self, data):
        """Sets the xblock position based off the 'position' value in the incoming dict"""
        # set position to default value if either 'position' argument not
        # found in request or it is a non-positive integer
        position = data.get('position', 1)
        if isinstance(position, int) and position > 0:
            self.position = position
        else:
            self.position = 1
        return {'success': True}

    # If you are reading this and it's past the 'Maple' Open edX release, you can delete this handle_ajax method, as
    # these are now individual xblock-style handler methods. We want to keep these around for a single release, simply
    # to handle learners that haven't refreshed their courseware page when the server gets updated and their old
    # javascript calls these old handlers.
    # If you do clean this up, you can also move the internal private versions just directly into the handler methods,
    # as nothing else calls them (at time of writing).
    def handle_ajax(self, dispatch, data):
        """Old xmodule-style ajax handler"""
        if dispatch == 'goto_position':
            return json.dumps(self._goto_position(data))
        elif dispatch == 'get_completion':
            return json.dumps(self._get_completion(data))
        raise NotFoundError('Unexpected dispatch type')

    def get_metadata(self, view=STUDENT_VIEW, context=None):
        """Returns a dict of some common block properties"""
        context = context or {}
        context['exclude_units'] = True
        prereq_met = True
        prereq_meta_info = {}
        banner_text = None
        children = self.get_children()
        course = self._get_course()
        is_hidden_after_due = False

        if self._required_prereq():
            if self.runtime.service(self, 'user').get_current_user().opt_attrs.get(ATTR_KEY_USER_IS_STAFF):
                banner_text = _(
                    'This subsection is unlocked for learners when they meet the prerequisite requirements.'
                )
            else:
                # check if prerequisite has been met
                prereq_met, prereq_meta_info = self._compute_is_prereq_met(True)

        if prereq_met and view == STUDENT_VIEW and not self._can_user_view_content(course):
            if context.get('specific_masquerade', False):
                # Still show the content, but flag to the staff user that the learner wouldn't be able to see it
                banner_text = self._hidden_content_banner_text(course)
            else:
                is_hidden_after_due = True

        meta = self._get_render_metadata(context, children, prereq_met, prereq_meta_info, banner_text, view)
        meta['display_name'] = self.display_name_with_default
        meta['format'] = getattr(self, 'format', '')
        meta['is_hidden_after_due'] = is_hidden_after_due
        meta['navigation_disabled'] = self.is_sequence_navigation_disabled()
        return meta

    def is_sequence_navigation_disabled(self):
        """
        Returns whether the navigation to other sequences is disabled.

        As of today, this is used to disable the navigation to other sequences when the
        current sequence is configured as Hide from Table of Contents. But it can be
        extended to other use cases in the future.
        """
        return getattr(self, "hide_from_toc", False)

    @classmethod
    def verify_current_content_visibility(cls, date, hide_after_date):
        """
        Returns whether the content visibility policy passes
        for the given date and hide_after_date values and
        the current date-time.
        """
        return (
            not date or
            not hide_after_date or
            datetime.now(ZoneInfo("UTC")) < date
        )

    def gate_entire_sequence_if_it_is_a_timed_exam_and_contains_content_type_gated_problems(self):
        """
        Problem:
        Content type gating for FBE (Feature Based Enrollments) previously only gated individual blocks.
        This was an issue because audit learners could start a timed exam
        and then be unable to complete it because the graded content would be gated.
        Even if they later upgraded, they could still be unable to complete the exam
        because the timer could have expired.

        Solution:
        Gate the entire sequence when we think the above problem can occur.

        If:
        1. This sequence is a timed exam (this is currently being checked before calling)
        2. And this sequence contains problems which this user cannot load due to content type gating
        Then:
        We will gate access to the entire sequence.
        Otherwise, learners would have the ability to start their timer for an exam,
        but then not have the ability to complete it.

        We are displaying the gating fragment within the sequence, as is done for gating for prereqs,
        rather than content type gating the entire sequence because that would remove the next/previous navigation.

        When gated_sequence_paywall is not set to None, the sequence will be gated.

        This functionality still needs to be replicated in the frontend-app-learning courseware MFE
        The ticket to track this is https://openedx.atlassian.net/browse/REV-1220
        Note that this will break compatability with using sequences outside of edx-platform
        but we are ok with this for now
        """
        content_type_gating_service = self.runtime.service(self, 'content_type_gating')
        if content_type_gating_service:
            self.gated_sequence_paywall = content_type_gating_service.check_children_for_content_type_gating_paywall(
                self, self.scope_ids.usage_id.context_key
            )

    def student_view(self, context):
        """
        Renders the normal student view of the block in the LMS.
        """
        _ = self.runtime.service(self, "i18n").ugettext
        context = context or {}
        self._capture_basic_metrics()
        banner_text = None
        prereq_met = True
        prereq_meta_info = {}
        if self._required_prereq():
            if self.runtime.service(self, 'user').get_current_user().opt_attrs.get(ATTR_KEY_USER_IS_STAFF):
                banner_text = _(
                    'This subsection is unlocked for learners when they meet the prerequisite requirements.'
                )
            else:
                # check if prerequisite has been met
                prereq_met, prereq_meta_info = self._compute_is_prereq_met(True)
        if prereq_met:
            special_html_view = self._hidden_content_student_view(context) or self._special_exam_student_view()
            if special_html_view:
                masquerading_as_specific_student = context.get('specific_masquerade', False)
                banner_text, special_html = special_html_view
                if special_html and not masquerading_as_specific_student:
                    fragment = Fragment(special_html)
                    add_css_to_fragment(fragment, 'SequenceBlockDisplay.css')
                    add_webpack_js_to_fragment(fragment, 'SequenceBlockDisplay')
                    shim_xmodule_js(fragment, 'Sequence')
                    return fragment

        return self._student_or_public_view(context, prereq_met, prereq_meta_info, banner_text)

    def public_view(self, context):
        """
        Renders the preview view of the block in the LMS.
        """
        prereq_met = True
        prereq_meta_info = {}

        if self._required_prereq():
            prereq_met, prereq_meta_info = self._compute_is_prereq_met(True)
        return self._student_or_public_view(context or {}, prereq_met, prereq_meta_info, None, PUBLIC_VIEW)

    def author_view(self, context):  # lint-amnesty, pylint: disable=missing-function-docstring
        context = context or {}
        context['exclude_units'] = True
        if 'position' in context:
            context['position'] = int(context['position'])
        return self._student_or_public_view(context, True, {}, view=AUTHOR_VIEW)

    def _special_exam_student_view(self):
        """
        Checks whether this sequential is a special exam.  If so, returns
        a banner_text or the fragment to display depending on whether
        staff is masquerading.
        """
        _ = self.runtime.service(self, "i18n").ugettext

        if self.is_time_limited:
            if TIMED_EXAM_GATING_WAFFLE_FLAG.is_enabled():
                # set the self.gated_sequence_paywall variable
                self.gate_entire_sequence_if_it_is_a_timed_exam_and_contains_content_type_gated_problems()
            if self.gated_sequence_paywall is None:
                special_exam_html = self._time_limited_student_view()
                if special_exam_html:
                    banner_text = _("This exam is hidden from the learner.")
                    return banner_text, special_exam_html

    def _hidden_content_banner_text(self, course):
        """
        Chooses a banner message to show for hidden content
        """
        _ = self.runtime.service(self, 'i18n').gettext
        if course.self_paced:
            return _('Because the course has ended, this assignment is hidden from the learner.')
        else:
            return _('Because the due date has passed, this assignment is hidden from the learner.')

    def _hidden_content_student_view(self, context):
        """
        Checks whether the content of this sequential is hidden from the
        runtime user. If so, returns a banner_text or the fragment to
        display depending on whether staff is masquerading.
        """
        _ = self.runtime.service(self, "i18n").ugettext
        course = self._get_course()
        if not self._can_user_view_content(course):
            banner_text = self._hidden_content_banner_text(course)

            hidden_content_html = self.runtime.service(self, 'mako').render_lms_template(
                'hidden_content.html',
                {
                    'self_paced': course.self_paced,
                    'progress_url': context.get('progress_url'),
                }
            )

            return banner_text, hidden_content_html

    def _can_user_view_content(self, course):
        """
        Returns whether the runtime user can view the content
        of this sequential.
        """
        hidden_date = course.end if course.self_paced else self.due
        return (
            self.runtime.service(self, 'user').get_current_user().opt_attrs.get(ATTR_KEY_USER_IS_STAFF) or
            self.verify_current_content_visibility(hidden_date, self.hide_after_due)
        )

    def is_user_authenticated(self, context):
        # NOTE (CCB): We default to true to maintain the behavior in place prior to allowing anonymous access access.
        return context.get('user_authenticated', True)

    def _get_render_metadata(self, context, children, prereq_met, prereq_meta_info, banner_text=None,
                             view=STUDENT_VIEW, fragment=None):
        """Returns a dictionary of sequence metadata, used by render methods and for the courseware API"""
        if prereq_met and not self._is_gate_fulfilled():
            _ = self.runtime.service(self, "i18n").ugettext
            banner_text = _(
                'This section is a prerequisite. You must complete this section in order to unlock additional content.'
            )

        if prereq_met:
            blocks = self._render_student_view_for_blocks(context, children, fragment, view)
        else:
            blocks = []
            for child in children:
                usage_id = child.scope_ids.usage_id
                blocks.append({
                    'id': str(usage_id),
                    'type': child.scope_ids.block_type,
                    'display_name': child.display_name_with_default,
                    'is_gated': True,  # Mark as blocked
                    'content': '',  # Real content not included
                })

        params = {
            'items': blocks,
            'element_id': self.location.html_id(),
            'item_id': str(self.location),
            'is_time_limited': self.is_time_limited,
            'is_proctored': self.is_proctored_enabled,
            'position': self.position,
            'tag': self.location.block_type,
            'next_url': context.get('next_url'),
            'prev_url': context.get('prev_url'),
            'banner_text': banner_text,
            'save_position': view != PUBLIC_VIEW,
            'show_completion': view != PUBLIC_VIEW,
            'gated_content': self._get_gated_content_info(prereq_met, prereq_meta_info),
            'sequence_name': self.display_name,
            'exclude_units': context.get('exclude_units', False),
            'gated_sequence_paywall': self.gated_sequence_paywall
        }

        return params

    def _student_or_public_view(self, context, prereq_met, prereq_meta_info, banner_text=None, view=STUDENT_VIEW):
        """
        Returns the rendered student view of the content of this
        sequential.  If banner_text is given, it is added to the
        content.
        """
        _ = self.runtime.service(self, "i18n").ugettext
        children = self.get_children()
        self._update_position(context, len(children))

        fragment = Fragment()
        params = self._get_render_metadata(context, children, prereq_met, prereq_meta_info, banner_text, view, fragment)  # lint-amnesty, pylint: disable=line-too-long
        if SHOW_PROGRESS_BAR.is_enabled() and getattr(settings, 'COMPLETION_AGGREGATOR_URL', ''):
            parent_block_id = self.get_parent().scope_ids.usage_id.block_id
            params['chapter_completion_aggregator_url'] = '/'.join(
                [settings.COMPLETION_AGGREGATOR_URL, str(self.scope_ids.usage_id.context_key), parent_block_id]) + '/'
        fragment.add_content(self.runtime.service(self, 'mako').render_lms_template("seq_block.html", params))

        self._capture_full_seq_item_metrics(children)
        self._capture_current_unit_metrics(children)

        add_css_to_fragment(fragment, 'SequenceBlockDisplay.css')
        add_webpack_js_to_fragment(fragment, 'SequenceBlockDisplay')
        shim_xmodule_js(fragment, 'Sequence')
        return fragment

    def _get_gated_content_info(self, prereq_met, prereq_meta_info):
        """
        Returns a dict of information about gated_content context
        """
        gated_content = {
            'prereq_id': None,
            'prereq_url': None,
            'prereq_section_name': None,
            'gated': False,
            'gated_section_name': self.display_name,
        }
        if not prereq_met:
            gated_content['gated'] = True
            gated_content['prereq_url'] = prereq_meta_info['url']
            gated_content['prereq_section_name'] = prereq_meta_info['display_name']
            gated_content['prereq_id'] = prereq_meta_info['id']

        return gated_content

    def _is_gate_fulfilled(self):
        """
        Determines if this section is a prereq and has any unfulfilled milestones.

        Returns:
            True if section has no unfufilled milestones or is not a prerequisite.
            False otherwise
        """
        gating_service = self.runtime.service(self, 'gating')
        if gating_service:
            user_id = self.runtime.service(self, 'user').get_current_user().opt_attrs.get(ATTR_KEY_USER_ID)
            fulfilled = gating_service.is_gate_fulfilled(
                self.scope_ids.usage_id.context_key, self.location, user_id
            )
            return fulfilled

        return True

    def _required_prereq(self):
        """
        Checks whether a prerequisite is required for this Section

        Returns:
            milestone if a prereq is required, None otherwise
        """
        gating_service = self.runtime.service(self, 'gating')
        if gating_service:
            milestone = gating_service.required_prereq(
                self.scope_ids.usage_id.context_key, self.location, 'requires'
            )
            return milestone

        return None

    def descendants_are_gated(self, context):
        """
        Sequences do their own access gating logic as to whether their content
        should be viewable, based on things like pre-reqs and time exam starts.
        Ideally, this information would be passed down to all descendants so
        that they would know if it's safe to render themselves, but the least
        invasive patch to this is to make a method that rendering Django views
        can use to verify before rendering descendants.

        This does _NOT_ check for the content types of children because the
        performing that traversal undoes a lot of the performance gains made in
        large sequences when hitting the render_xblock endpoint directly. This
        method is here mostly to help render_xblock figure out if it's okay to
        render a descendant of a sequence to guard against malicious actors. So
        the "let's check all descendants to not let people start an exam they
        can't finish" reasoning of doing the full traversal does not apply.

        Returns:
            True if this sequence and its descendants are gated by what are
                currently sequence-level checks.
            False if the sequence is and its decendants are not gated.

            Note that this gating logic is only a part of the equation when it
            comes to determining whether a student is allowed to access this,
            with other checks being done in has_access calls.
        """
        user_is_staff = self.runtime.service(self, 'user').get_current_user().opt_attrs.get(ATTR_KEY_USER_IS_STAFF)
        if user_is_staff or context.get('specific_masquerade', False):
            return False

        # We're not allowed to see it because of pre-reqs that haven't been
        # fullfilled.
        if self._required_prereq():
            prereq_met, _prereq_meta_info = self._compute_is_prereq_met(True)
            if not prereq_met:
                return True

        # Are we a time limited test that hasn't started yet?
        if self.is_time_limited:
            if self._time_limited_student_view() or self._hidden_content_student_view({}):
                return True

        # Otherwise, nothing is blocking us.
        return False

    def _compute_is_prereq_met(self, recalc_on_unmet):
        """
        Evaluate if the user has completed the prerequisite

        Arguments:
            recalc_on_unmet: Recalculate the subsection grade if prereq has not yet been met

        Returns:
            tuple: True|False,
            prereq_meta_info = { 'url': prereq_url, 'display_name': prereq_name}
        """
        gating_service = self.runtime.service(self, 'gating')
        if gating_service:
            user_id = self.runtime.service(self, 'user').get_current_user().opt_attrs.get(ATTR_KEY_USER_ID)
            return gating_service.compute_is_prereq_met(self.location, user_id, recalc_on_unmet)

        return True, {}

    def _update_position(self, context, number_of_children):
        """
        Update the user's sequential position given the context and the
        number_of_children
        """

        position = context.get('position')
        if position:
            self.position = position

        # If we're rendering this sequence, but no position is set yet,
        # or exceeds the length of the displayable blocks,
        # default the position to the first element
        if context.get('requested_child') == 'first':
            self.position = 1
        elif context.get('requested_child') == 'last':
            self.position = number_of_children or 1
        elif self.position is None or self.position > number_of_children:
            self.position = 1

    def _render_student_view_for_blocks(self, context, children, fragment, view=STUDENT_VIEW):
        """
        Updates the given fragment with rendered student views of the given
        children.  Returns a list of dict objects with information about
        the given children.
        """
        # Avoid circular imports.
        from openedx.core.lib.xblock_utils import get_icon

        render_blocks = not context.get('exclude_units', False)
        is_user_authenticated = self.is_user_authenticated(context)
        completion_service = self.runtime.service(self, 'completion')
        try:
            bookmarks_service = self.runtime.service(self, 'bookmarks')
        except NoSuchServiceError:
            bookmarks_service = None
        user = self.runtime.service(self, 'user').get_current_user()
        context['username'] = user.opt_attrs.get(
            'edx-platform.username')
        display_names = [
            self.get_parent().display_name_with_default,
            self.display_name_with_default
        ]
        contents = []
        for block in children:
            item_type = get_icon(block)
            usage_id = block.scope_ids.usage_id

            show_bookmark_button = False
            is_bookmarked = False

            if is_user_authenticated and bookmarks_service:
                show_bookmark_button = True
                is_bookmarked = bookmarks_service.is_bookmarked(usage_key=usage_id)

            context['show_bookmark_button'] = show_bookmark_button
            context['bookmarked'] = is_bookmarked
            context['format'] = getattr(self, 'format', '')

            if render_blocks:
                rendered_block = block.render(view, context)
                fragment.add_fragment_resources(rendered_block)
                content = rendered_block.content
            else:
                content = ''

            content_type_gating_service = self.runtime.service(self, 'content_type_gating')
            contains_content_type_gated_content = False
            if content_type_gating_service:
                contains_content_type_gated_content = content_type_gating_service.check_children_for_content_type_gating_paywall(  # pylint:disable=line-too-long
                    block, self.scope_ids.usage_id.context_key
                ) is not None
            block_info = {
                'content': content,
                'page_title': getattr(block, 'tooltip_title', ''),
                'type': item_type,
                'id': str(usage_id),
                'bookmarked': is_bookmarked,
                'path': " > ".join(display_names + [block.display_name_with_default]),
                'graded': block.graded,
                'contains_content_type_gated_content': contains_content_type_gated_content,
            }
            if not render_blocks:
                # The item url format can be defined in the template context like so:
                # context['item_url'] = '/my/item/path/{usage_key}/whatever'
                block_info['href'] = context.get('item_url', '').format(usage_key=usage_id)
            if is_user_authenticated:
                if block.location.block_type == 'vertical' and completion_service:
                    block_info['complete'] = completion_service.vertical_is_complete(block)

            contents.append(block_info)

        return contents

    def _locations_in_subtree(self, node):
        """
        The usage keys for all descendants of an XBlock/XModule as a flat list.

        Includes the location of the node passed in.
        """
        stack = [node]
        locations = []

        while stack:
            curr = stack.pop()
            locations.append(curr.location)
            if curr.has_children:
                stack.extend(curr.get_children())

        return locations

    def _capture_basic_metrics(self):
        """
        Capture basic information about this sequence in telemetry.
        """
        set_custom_attribute('seq.block_id', str(self.location))
        set_custom_attribute('seq.display_name', self.display_name or '')
        set_custom_attribute('seq.position', self.position)
        set_custom_attribute('seq.is_time_limited', self.is_time_limited)

    def _capture_full_seq_item_metrics(self, children):
        """
        Capture information about the number and types of XBlock content in
        the sequence as a whole. We record this information in telemetry so that
        we can do better performance analysis of courseware.
        """
        # Basic count of the number of Units (a.k.a. VerticalBlocks) we have in
        # this learning sequence
        set_custom_attribute('seq.num_units', len(children))

        # Count of all modules (leaf nodes) in this sequence (e.g. videos,
        # problems, etc.) The units (verticals) themselves are not counted.
        all_item_keys = self._locations_in_subtree(self)
        set_custom_attribute('seq.num_items', len(all_item_keys))

        # Count of all modules by block_type (e.g. "video": 2, "discussion": 4)
        block_counts = collections.Counter(usage_key.block_type for usage_key in all_item_keys)
        for block_type, count in block_counts.items():
            set_custom_attribute(f'seq.block_counts.{block_type}', count)

    def _capture_current_unit_metrics(self, children):
        """
        Capture information about the current selected Unit within the Sequence.
        """
        # Positions are stored with indexing starting at 1. If we get into a
        # weird state where the saved position is out of bounds (e.g. the
        # content was changed), avoid going into any details about this unit.
        if 1 <= self.position <= len(children):
            # Basic info about the Unit...
            current = children[self.position - 1]
            set_custom_attribute('seq.current.block_id', str(current.location))
            set_custom_attribute('seq.current.display_name', current.display_name or '')

            # Examining all blocks inside the Unit (or split_test, conditional, etc.)
            child_locs = self._locations_in_subtree(current)
            set_custom_attribute('seq.current.num_items', len(child_locs))
            curr_block_counts = collections.Counter(usage_key.block_type for usage_key in child_locs)
            for block_type, count in curr_block_counts.items():
                set_custom_attribute(f'seq.current.block_counts.{block_type}', count)

    def _time_limited_student_view(self):
        """
        Delegated rendering of a student view when in a time
        limited view. This ultimately calls down into edx_proctoring
        pip installed djangoapp
        """

        # None = no overridden view rendering
        view_html = None

        proctoring_service = self.runtime.service(self, 'proctoring')
        credit_service = self.runtime.service(self, 'credit')

        # Is this sequence designated as a Timed Examination, which includes
        # Proctored Exams
        feature_enabled = (
            proctoring_service and
            credit_service and
            self.is_time_limited
        )
        if feature_enabled:
            current_user = self.runtime.service(self, 'user').get_current_user()
            user_id = current_user.opt_attrs.get(ATTR_KEY_USER_ID)
            user_is_staff = current_user.opt_attrs.get(ATTR_KEY_USER_IS_STAFF)
            user_role_in_course = 'staff' if user_is_staff else 'student'
            course_id = self.scope_ids.usage_id.context_key
            content_id = self.location
            course = self._get_course()

            # LTI exam tools are not managed by the edx-proctoring library
            # Return None rather than reaching into the edx-proctoring subsystem
            if course.proctoring_provider == 'lti_external':
                return None

            context = {
                'display_name': self.display_name,
                'default_time_limit_mins': (
                    self.default_time_limit_minutes if
                    self.default_time_limit_minutes else 0
                ),
                'is_practice_exam': self.is_practice_exam,
                'allow_proctoring_opt_out': self.allow_proctoring_opt_out,
                'due_date': self.due,
                'grace_period': self.graceperiod,  # lint-amnesty, pylint: disable=no-member
            }

            # inject the user's credit requirements and fulfillments
            if credit_service:
                credit_state = credit_service.get_credit_state(user_id, course_id)
                if credit_state:
                    context.update({
                        'credit_state': credit_state
                    })

            # See if the edx-proctoring subsystem wants to present
            # a special view to the student rather
            # than the actual sequence content
            #
            # This will return None if there is no
            # overridden view to display given the
            # current state of the user
            view_html = proctoring_service.get_student_view(
                user_id=user_id,
                course_id=course_id,
                content_id=content_id,
                context=context,
                user_role=user_role_in_course
            )

        return view_html

    def get_icon_class(self):
        child_classes = {child.get_icon_class()
                         for child in self.get_children()}
        new_class = 'other'
        for c in class_priority:
            if c in child_classes:
                new_class = c
        return new_class

    def definition_to_xml(self, resource_fs):
        xml_object = etree.Element('sequential')
        for child in self.get_children():
            self.runtime.add_block_as_child_node(child, xml_object)
        self.add_prerequisite_to_xml(xml_object)
        return xml_object

    def add_prerequisite_to_xml(self, xml_object):
        """
        Add prerequisite information to sequential XML for export.
        """
        from openedx.core.lib.gating import api as gating_api

        prereq_info = gating_api.get_required_content(
            self.location.course_key,
            self.location
        )
        prereq_usage_key_str, min_score, min_completion = prereq_info
        if not isinstance(prereq_usage_key_str, str):
            return
        prereq_usage_key = UsageKey.from_string(prereq_usage_key_str)
        xml_object.set('required_content', prereq_usage_key.block_id)
        xml_object.set('min_score', str(min_score))
        xml_object.set('min_completion', str(min_completion))

    @property
    def non_editable_metadata_fields(self):
        """
        `is_entrance_exam` should not be editable in the Studio settings editor.
        """
        non_editable_fields = super().non_editable_metadata_fields
        non_editable_fields.append(self.fields['is_entrance_exam'])  # pylint:disable=unsubscriptable-object
        return non_editable_fields


class HighlightsFields:
    """Only Sections have summaries now, but we may expand that later."""
    highlights = List(
        help=_("A list summarizing what students should look forward to in this section."),
        scope=Scope.settings
    )


class SectionBlock(HighlightsFields, SequenceBlock):
    """
    XBlock for a Section/Chapter.
    """
