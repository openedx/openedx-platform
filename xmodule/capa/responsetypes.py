"""
Problem response evaluation.  Handles checking of student responses,
of a variety of types.

Used by capa_problem.py
"""
# standard library imports
import abc
import inspect
import json
import logging
import numbers
import re
import sys
import textwrap
import traceback
from cmath import isnan
from collections import namedtuple
from datetime import datetime
from sys import float_info

import html5lib
import numpy
import random2 as random
import requests
import six
# specific library imports
from calc import UndefinedVariable, UnmatchedParenthesis, evaluator
from django.utils import html

from lxml import etree
from lxml.html.soupparser import fromstring as fromstring_bs  # uses Beautiful Soup!!! FIXME?
from pyparsing import ParseException
from pytz import UTC
from shapely.geometry import MultiPoint, Point
from six.moves import map, range, zip

import xmodule.capa.safe_exec as safe_exec
import xmodule.capa.xqueue_interface as xqueue_interface
from openedx.core.djangolib.markup import HTML, Text
from openedx.core.lib.grade_utils import round_away_from_zero

from . import correctmap
from .registry import TagRegistry
from .util import (
    compare_with_tolerance,
    contextualize_text,
    convert_files_to_filenames,
    default_tolerance,
    find_with_default,
    get_course_id_from_capa_block,
    get_inner_html_from_xpath,
    is_list_of_files
)

log = logging.getLogger(__name__)

registry = TagRegistry()

CorrectMap = correctmap.CorrectMap
CORRECTMAP_PY = None

# Make '_' a no-op so we can scrape strings. Using lambda instead of
#  `django.utils.translation.ugettext_noop` because Django cannot be imported in this file
_ = lambda text: text

QUESTION_HINT_CORRECT_STYLE = 'feedback-hint-correct'
QUESTION_HINT_INCORRECT_STYLE = 'feedback-hint-incorrect'
QUESTION_HINT_LABEL_STYLE = 'hint-label'
QUESTION_HINT_TEXT_STYLE = 'hint-text'
QUESTION_HINT_MULTILINE = 'feedback-hint-multi'

#-----------------------------------------------------------------------------
# Exceptions


class LoncapaProblemError(Exception):
    """
    Error in specification of a problem
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


class ResponseError(Exception):
    """
    Error for failure in processing a response, including
    exceptions that occur when executing a custom script.
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


class StudentInputError(Exception):
    """
    Error for an invalid student input.
    For example, submitting a string when the problem expects a number
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass

#-----------------------------------------------------------------------------
#
# Main base class for CAPA responsetypes


class LoncapaResponse(six.with_metaclass(abc.ABCMeta, object)):
    """
    Base class for CAPA responsetypes.  Each response type (ie a capa question,
    which is part of a capa problem) is represented as a subclass,
    which should provide the following methods:

      - get_score           : evaluate the given student answers, and return a CorrectMap
      - get_answers         : provide a dict of the expected answers for this problem

    Each subclass must also define the following attributes:

      - tags                : xhtml tags identifying this response (used in auto-registering)

    In addition, these methods are optional:

      - setup_response : find and note the answer input field IDs for the response; called
                         by __init__

      - check_hint_condition : check to see if the student's answers satisfy a particular
                               condition for a hint to be displayed

      - render_html          : render this Response as HTML (must return XHTML-compliant string)
      - __str__              : unicode representation of this Response

    Each response type may also specify the following attributes:

      - max_inputfields      : (int) maximum number of answer input fields (checked in __init__
                               if not None)

      - allowed_inputfields  : list of allowed input fields (each a string) for this Response

      - required_attributes  : list of required attributes (each a string) on the main
                               response XML stanza

      - hint_tag             : xhtml tag identifying hint associated with this response inside
                               hintgroup
    """

    tags = None
    hint_tag = None
    has_partial_credit = False
    credit_type = []

    max_inputfields = None
    allowed_inputfields = []
    required_attributes = []

    # Overridable field that specifies whether this capa response type has support for
    # for rendering on devices of different sizes and shapes.
    # By default, we set this to False, allowing subclasses to override as appropriate.
    multi_device_support = False

    def __init__(self, xml, inputfields, context, system, capa_block, minimal_init):
        """
        Init is passed the following arguments:

          - xml         : ElementTree of this Response
          - inputfields : ordered list of ElementTrees for each input entry field in this Response
          - context     : script processor context
          - system      : LoncapaSystem instance which provides OS, rendering, and user context
          - capa_block : Capa block, to access runtime
        """
        self.xml = xml
        self.inputfields = inputfields
        self.context = context
        self.capa_system = system
        self.capa_block = capa_block  # njp, note None

        self.id = xml.get('id')

        # The LoncapaProblemError messages here do not need to be translated as they are
        # only displayed to the user when settings.DEBUG is True
        for abox in inputfields:
            if abox.tag not in self.allowed_inputfields:
                msg = "%s: cannot have input field %s" % (
                    str(self), abox.tag)
                msg += "\nSee XML source line %s" % getattr(
                    xml, 'sourceline', '[unavailable]')
                raise LoncapaProblemError(msg)

        if self.max_inputfields and len(inputfields) > self.max_inputfields:
            msg = "%s: cannot have more than %s input fields" % (
                str(self), self.max_inputfields)
            msg += "\nSee XML source line %s" % getattr(
                xml, 'sourceline', '[unavailable]')
            raise LoncapaProblemError(msg)

        for prop in self.required_attributes:
            if not xml.get(prop):
                msg = "Error in problem specification: %s missing required attribute %s" % (
                    str(self), prop)
                msg += "\nSee XML source line %s" % getattr(
                    xml, 'sourceline', '[unavailable]')
                raise LoncapaProblemError(msg)

        # ordered list of answer_id values for this response
        self.answer_ids = [x.get('id') for x in self.inputfields]
        if self.max_inputfields == 1:
            # for convenience
            self.answer_id = self.answer_ids[0]

        # map input_id -> maxpoints
        self.maxpoints = {}
        for inputfield in self.inputfields:
            # By default, each answerfield is worth 1 point
            maxpoints = inputfield.get('points', '1')
            self.maxpoints.update({inputfield.get('id'): int(maxpoints)})

        if not minimal_init:
            # dict for default answer map (provided in input elements)
            self.default_answer_map = {}
            for entry in self.inputfields:
                answer = entry.get('correct_answer')
                if answer:
                    self.default_answer_map[entry.get(
                        'id')] = contextualize_text(answer, self.context)

            # Does this problem have partial credit?
            # If so, what kind? Get it as a list of strings.
            partial_credit = xml.xpath('.')[0].get('partial_credit', default=False)

            if str(partial_credit).lower().strip() == 'false':
                self.has_partial_credit = False
                self.credit_type = []
            else:
                self.has_partial_credit = True
                self.credit_type = partial_credit.split(',')
                self.credit_type = [word.strip().lower() for word in self.credit_type]

            if hasattr(self, 'setup_response'):
                self.setup_response()

    def get_max_score(self):
        """
        Return the total maximum points of all answer fields under this Response
        """
        return sum(self.maxpoints.values())

    def render_html(self, renderer, response_msg=''):
        """
        Return XHTML Element tree representation of this Response.

        Arguments:

          - renderer : procedure which produces HTML given an ElementTree
          - response_msg: a message displayed at the end of the Response
        """
        _ = self.capa_system.i18n.gettext

        # response_id = problem_id + response index
        response_id = self.xml.attrib['id']

        response_index = response_id.split('_')[-1]
        # Translators: index here could be 1,2,3 and so on
        response_label = _('Question {index}').format(index=response_index)

        # wrap the content inside a section
        tree = etree.Element('div')
        tree.set('class', 'wrapper-problem-response')
        tree.set('tabindex', '-1')
        tree.set('aria-label', response_label)
        tree.set('role', 'group')

        if self.xml.get('multiple_inputtypes'):
            # add <div> to wrap all inputtypes
            content = etree.SubElement(tree, 'div')
            content.set('class', 'multi-inputs-group')
            content.set('role', 'group')

            if self.xml.get('multiinput-group-label-id'):
                content.set('aria-labelledby', self.xml.get('multiinput-group-label-id'))

            if self.xml.get('multiinput-group_description_ids'):
                content.set('aria-describedby', self.xml.get('multiinput-group_description_ids'))
        else:
            content = tree

        # problem author can make this span display:inline
        if self.xml.get('inline', ''):
            tree.set('class', 'inline')

        for item in self.xml:
            # call provided procedure to do the rendering
            item_xhtml = renderer(item)
            if item_xhtml is not None:
                content.append(item_xhtml)
        tree.tail = self.xml.tail

        # Add a <div> for the message at the end of the response
        if response_msg:
            content.append(self._render_response_msg_html(response_msg))

        return tree

    def evaluate_answers(self, student_answers, old_cmap):
        """
        Called by capa_problem.LoncapaProblem to evaluate student answers, and to
        generate hints (if any).

        Returns the new CorrectMap, with (correctness,msg,hint,hintmode) for each answer_id.
        """
        new_cmap = self.get_score(student_answers)
        self.get_hints(convert_files_to_filenames(
            student_answers), new_cmap, old_cmap)
        return new_cmap

    def make_hint_div(self, hint_node, correct, student_answer, question_tag,
                      label=None, hint_log=None, multiline_mode=False, log_extra=None):
        """
        Returns the extended hint div based on the student_answer
        or the empty string if, after processing all the arguments, there is no hint.
        As a side effect, logs a tracking log event detailing the hint.

        Keyword args:
            * hint_node: xml node such as <optionhint>, holding extended hint text. May be passed in as None.
            * correct: bool indication if the student answer is correct
            * student_answer: list length 1 or more of string answers
              (only checkboxes make multiple answers)
            * question_tag: string name of enclosing question, e.g. 'choiceresponse'
            * label: (optional) if None (the default), extracts the label from the node,
              otherwise using this value. The value '' inhibits labeling of the hint.
            * hint_log: (optional) hints to be used, passed in as list-of-dict format (below)
            * multiline_mode: (optional) bool, default False, hints should be shown one-per line
            * log_extra: (optional) dict items to be injected in the tracking log

        There are many parameters to this method because a variety of extended hint contexts
        all bottleneck through here. In addition, the caller must provide detailed background
        information about the hint-trigger to go in the tracking log.

        hint_log format: list of dicts with each hint as a 'text' key. Each dict has extra
        information for logging, essentially recording the logic which triggered the feedback.
        Case 1: records which choices triggered
        e.g. [{'text': 'feedback 1', 'trigger': [{'choice': 'choice_0', 'selected': True}]},...
        Case 2: a compound hint, the trigger list has 1 or more choices
        e.g. [{'text': 'a hint', 'trigger':[{'choice': 'choice_0', 'selected': True},
              {'choice': 'choice_1', 'selected':True}]}]
        """
        _ = self.capa_system.i18n.gettext
        # 1. Establish the hint_texts
        # This can lead to early-exit if the hint is blank.
        if not hint_log:
            # .text can be None when node has immediate children nodes
            if hint_node is None or (hint_node.text is None and len(hint_node.getchildren()) == 0):
                return ''
            hint_text = get_inner_html_from_xpath(hint_node)
            if not hint_text:
                return ''
            hint_log = [{'text': hint_text}]
        # invariant: xxxx

        # 2. Establish the label:
        # Passed in, or from the node, or the default
        if not label and hint_node is not None:
            label = hint_node.get('label', None)
        # Tricky: label None means output defaults, while '' means output empty label
        if label is None:
            if correct:
                label = _('Correct:')
            else:
                label = _('Incorrect:')

        # This is the "feedback hint" event
        event_info = {}
        event_info['module_id'] = str(self.capa_block.location)
        event_info['problem_part_id'] = self.id
        event_info['trigger_type'] = 'single'  # maybe be overwritten by log_extra
        event_info['hint_label'] = label
        event_info['hints'] = hint_log
        event_info['correctness'] = correct
        event_info['student_answer'] = student_answer
        event_info['question_type'] = question_tag
        if log_extra:
            event_info.update(log_extra)
        self.capa_block.runtime.publish(self.capa_block, 'edx.problem.hint.feedback_displayed', event_info)

        # Form the div-wrapped hint texts
        hints_wrap = HTML('').join(
            [HTML('<div class="{question_hint_text_style}">{hint_content}</div>').format(
                question_hint_text_style=QUESTION_HINT_TEXT_STYLE,
                hint_content=HTML(dct.get('text'))
            ) for dct in hint_log]
        )
        if multiline_mode:
            hints_wrap = HTML('<div class="{question_hint_multiline}">{hints_wrap}</div>').format(
                question_hint_multiline=QUESTION_HINT_MULTILINE,
                hints_wrap=hints_wrap
            )
        label_wrap = ''
        if label:
            label_wrap = HTML('<span class="{question_hint_label_style}">{label} </span>').format(
                question_hint_label_style=QUESTION_HINT_LABEL_STYLE,
                label=Text(label)
            )

        # Establish the outer style
        if correct:
            style = QUESTION_HINT_CORRECT_STYLE
        else:
            style = QUESTION_HINT_INCORRECT_STYLE

        # Ready to go
        return HTML('<div class="{st}"><div class="explanation-title">{text}</div>{lwrp}{hintswrap}</div>').format(
            st=style,
            text=Text(_("Answer")),
            lwrp=label_wrap,
            hintswrap=hints_wrap
        )

    def get_extended_hints(self, student_answers, new_cmap):
        """
        Pull "extended hint" information out the xml based on the student answers,
        installing it in the new_map for display.
        Implemented by subclasses that have extended hints.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    def get_hints(self, student_answers, new_cmap, old_cmap):
        """
        Generate adaptive hints for this problem based on student answers, the old CorrectMap,
        and the new CorrectMap produced by get_score.

        Does not return anything.

        Modifies new_cmap, by adding hints to answer_id entries as appropriate.
        """

        hintfn = None
        hint_function_provided = False
        hintgroup = self.xml.find('hintgroup')
        if hintgroup is not None:
            hintfn = hintgroup.get('hintfn')
            if hintfn is not None:
                hint_function_provided = True

        if hint_function_provided:
            # if a hint function has been supplied, it will take precedence
            # Hint is determined by a function defined in the <script> context; evaluate
            # that function to obtain list of hint, hintmode for each answer_id.

            # The function should take arguments (answer_ids, student_answers, new_cmap, old_cmap)
            # and it should modify new_cmap as appropriate.

            # We may extend this in the future to add another argument which provides a
            # callback procedure to a social hint generation system.

            global CORRECTMAP_PY
            if CORRECTMAP_PY is None:
                # We need the CorrectMap code for hint functions. No, this is not great.
                CORRECTMAP_PY = inspect.getsource(correctmap)

            code = (
                CORRECTMAP_PY + "\n" +
                self.context['script_code'] + "\n" +
                textwrap.dedent("""
                    new_cmap = CorrectMap()
                    new_cmap.set_dict(new_cmap_dict)
                    old_cmap = CorrectMap()
                    old_cmap.set_dict(old_cmap_dict)
                    {hintfn}(answer_ids, student_answers, new_cmap, old_cmap)
                    new_cmap_dict.update(new_cmap.get_dict())
                    old_cmap_dict.update(old_cmap.get_dict())
                    """).format(hintfn=hintfn)
            )
            globals_dict = {
                'answer_ids': self.answer_ids,
                'student_answers': student_answers,
                'new_cmap_dict': new_cmap.get_dict(),
                'old_cmap_dict': old_cmap.get_dict(),
            }

            try:
                safe_exec.safe_exec(
                    code,
                    globals_dict,
                    python_path=self.context['python_path'],
                    extra_files=self.context['extra_files'],
                    limit_overrides_context=get_course_id_from_capa_block(
                        self.capa_block
                    ),
                    slug=self.id,
                    random_seed=self.context['seed'],
                    unsafely=self.capa_system.can_execute_unsafe_code(),
                )
            except Exception as err:
                _ = self.capa_system.i18n.gettext
                msg = _('Error {err} in evaluating hint function {hintfn}.').format(err=err, hintfn=hintfn)
                sourcenum = getattr(self.xml, 'sourceline', _('(Source code line unavailable)'))
                msg += "\n" + _("See XML source line {sourcenum}.").format(sourcenum=sourcenum)
                raise ResponseError(msg)  # lint-amnesty, pylint: disable=raise-missing-from

            new_cmap.set_dict(globals_dict['new_cmap_dict'])
            return

        # no hint function provided
        # hint specified by conditions and text dependent on conditions (a-la Loncapa design)
        # see http://help.loncapa.org/cgi-bin/fom?file=291
        #
        # Example:
        #
        # <formularesponse samples="x@-5:5#11" id="11" answer="$answer">
        #   <textline size="25" />
        #   <hintgroup>
        #     <formulahint samples="x@-5:5#11" answer="$wrongans" name="inversegrad"></formulahint>
        #     <hintpart on="inversegrad">
        #       <text>You have inverted the slope in the question.  The slope is
        #             (y2-y1)/(x2 - x1) you have the slope as (x2-x1)/(y2-y1).</text>
        #     </hintpart>
        #   </hintgroup>
        # </formularesponse>

        if (self.hint_tag is not None
                and hintgroup is not None
                and hintgroup.find(self.hint_tag) is not None
                and hasattr(self, 'check_hint_condition')):

            rephints = hintgroup.findall(self.hint_tag)
            hints_to_show = self.check_hint_condition(  # lint-amnesty, pylint: disable=assignment-from-no-return
                rephints, student_answers)
            # can be 'on_request' or 'always' (default)

            hintmode = hintgroup.get('mode', 'always')
            for hintpart in hintgroup.findall('hintpart'):
                if hintpart.get('on') in hints_to_show:
                    hint_text = hintpart.find('text').text
                    # make the hint appear after the last answer box in this
                    # response
                    aid = self.answer_ids[-1]
                    new_cmap.set_hint_and_mode(aid, hint_text, hintmode)
            log.debug('after hint: new_cmap = %s', new_cmap)
        else:
            # If no other hint form matches, try extended hints.
            self.get_extended_hints(student_answers, new_cmap)

    @abc.abstractmethod
    def get_score(self, student_answers):
        """
        Return a CorrectMap for the answers expected vs given.  This includes
        (correctness, npoints, msg) for each answer_id.

        Arguments:
         - student_answers : dict of (answer_id, answer) where answer = student input (string)
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abc.abstractmethod
    def get_answers(self):
        """
        Return a dict of (answer_id, answer_text) for each answer for this question.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    def check_hint_condition(self, hxml_set, student_answers):
        """
        Return a list of hints to show.

          - hxml_set        : list of Element trees, each specifying a condition to be
                              satisfied for a named hint condition

          - student_answers : dict of student answers

        Returns a list of names of hint conditions which were satisfied.  Those are used
        to determine which hints are displayed.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    def setup_response(self):
        pass

    def __str__(self):
        return 'LoncapaProblem Response %s' % self.xml.tag

    def _render_response_msg_html(self, response_msg):
        """ Render a <div> for a message that applies to the entire response.

        *response_msg* is a string, which may contain XHTML markup

        Returns an etree element representing the response message <div> """
        # First try wrapping the text in a <div> and parsing
        # it as an XHTML tree
        try:
            response_msg_div = etree.XML(HTML('<div>{}</div>').format(HTML(str(response_msg))))

        # If we can't do that, create the <div> and set the message
        # as the text of the <div>
        except Exception:  # pylint: disable=broad-except
            response_msg_div = etree.Element('div')
            response_msg_div.text = str(response_msg)

        # Set the css class of the message <div>
        response_msg_div.set("class", "response_message")

        return response_msg_div

    # These accessor functions allow polymorphic checking of response
    # objects without having to call hasattr() directly.
    def has_mask(self):
        """True if the response has masking."""
        return hasattr(self, '_has_mask')

    def has_shuffle(self):
        """True if the response has a shuffle transformation."""
        return hasattr(self, '_has_shuffle')

    def has_answerpool(self):
        """True if the response has an answer-pool transformation."""
        return hasattr(self, '_has_answerpool')


#-----------------------------------------------------------------------------
@registry.register
class ChoiceResponse(LoncapaResponse):
    """
    This response type is used when the student chooses from a discrete set of
    choices. Currently, to be marked correct, all "correct" choices must be
    supplied by the student, and no extraneous choices may be included.

    This response type allows for two inputtypes: radiogroups and checkbox
    groups. radiogroups are used when the student should select a single answer,
    and checkbox groups are used when the student may supply 0+ answers.
    Note: it is suggested to include a "None of the above" choice when no
    answer is correct for a checkboxgroup inputtype; this ensures that a student
    must actively mark something to get credit.

    If two choices are marked as correct with a radiogroup, the student will
    have no way to get the answer right.

    TODO: Allow for marking choices as 'optional' and 'required', which would
    not penalize a student for including optional answers and would also allow
    for questions in which the student can supply one out of a set of correct
    answers.This would also allow for survey-style questions in which all
    answers are correct.

    Example:

    <choiceresponse>
        <radiogroup>
            <choice correct="false">
                <text>This is a wrong answer.</text>
            </choice>
            <choice correct="true">
                <text>This is the right answer.</text>
            </choice>
            <choice correct="false">
                <text>This is another wrong answer.</text>
            </choice>
        </radiogroup>
    </choiceresponse>

    In the above example, radiogroup can be replaced with checkboxgroup to allow
    the student to select more than one choice.

    TODO: In order for the inputtypes to render properly, this response type
    must run setup_response prior to the input type rendering. Specifically, the
    choices must be given names. This behavior seems like a leaky abstraction,
    and it'd be nice to change this at some point.

    """
    human_name = _('Checkboxes')
    tags = ['choiceresponse']
    max_inputfields = 1
    allowed_inputfields = ['checkboxgroup', 'radiogroup']
    correct_choices = None
    multi_device_support = True

    def setup_response(self):
        self.assign_choice_names()

        self.correct_choices = set()
        self.incorrect_choices = set()
        for choice in self.get_choices():

            # contextualize the name and correct attributes
            name = contextualize_text(choice.get('name'), self.context)
            correct = contextualize_text(choice.get('correct'), self.context).upper()

            # divide choices into correct and incorrect
            if correct == 'TRUE':
                self.correct_choices.add(name)
            elif correct == 'FALSE':
                self.incorrect_choices.add(name)

    def get_choices(self):
        """Returns this response's XML choice elements."""
        return self.xml.xpath('//*[@id=$id]//choice', id=self.xml.get('id'))

    def assign_choice_names(self):
        """
        Initialize name attributes in <choice> tags for this response.
        """

        for index, choice in enumerate(self.get_choices()):
            choice.set("name", "choice_" + str(index))
            # If a choice does not have an id, assign 'A' 'B', .. used by CompoundHint
            if not choice.get('id'):
                choice.set("id", chr(ord("A") + index))

    def grade_via_every_decision_counts(self, **kwargs):
        """
        Calculates partial credit on the Every Decision Counts scheme.
        For each correctly selected or correctly blank choice, score 1 point.
        Divide by total number of choices.
        Arguments:
            all_choices, the full set of checkboxes
            student_answer, what the student actually chose
            student_non_answers, what the student didn't choose
        Returns a CorrectMap.
        """

        all_choices = kwargs['all_choices']
        student_answer = kwargs['student_answer']
        student_non_answers = kwargs['student_non_answers']

        edc_max_grade = len(all_choices)
        edc_current_grade = 0

        good_answers = sum(1 for answer in student_answer if answer in self.correct_choices)
        good_non_answers = sum(1 for blank in student_non_answers if blank in self.incorrect_choices)
        edc_current_grade = good_answers + good_non_answers

        return_grade = round_away_from_zero(self.get_max_score() * float(edc_current_grade) / float(edc_max_grade), 2)

        if edc_current_grade == edc_max_grade:
            return CorrectMap(self.answer_id, correctness='correct')
        elif edc_current_grade > 0:
            return CorrectMap(self.answer_id, correctness='partially-correct', npoints=return_grade)
        else:
            return CorrectMap(self.answer_id, correctness='incorrect', npoints=0)

    def grade_via_halves(self, **kwargs):
        """
        Calculates partial credit on the Halves scheme.
        If no errors, full credit.
        If one error, half credit as long as there are 3+ choices
        If two errors, 1/4 credit as long as there are 5+ choices
        (If not enough choices, no credit.)
        Arguments:
            all_choices, the full set of checkboxes
            student_answer, what the student actually chose
            student_non_answers, what the student didn't choose
        Returns a CorrectMap
        """

        all_choices = kwargs['all_choices']
        student_answer = kwargs['student_answer']
        student_non_answers = kwargs['student_non_answers']

        halves_error_count = 0

        incorrect_answers = sum(1 for answer in student_answer if answer in self.incorrect_choices)
        missed_answers = sum(1 for blank in student_non_answers if blank in self.correct_choices)
        halves_error_count = incorrect_answers + missed_answers

        if halves_error_count == 0:
            return_grade = self.get_max_score()
            return CorrectMap(self.answer_id, correctness='correct', npoints=return_grade)
        elif halves_error_count == 1 and len(all_choices) > 2:
            return_grade = round_away_from_zero(self.get_max_score() / 2.0, 2)
            return CorrectMap(self.answer_id, correctness='partially-correct', npoints=return_grade)
        elif halves_error_count == 2 and len(all_choices) > 4:
            return_grade = round_away_from_zero(self.get_max_score() / 4.0, 2)
            return CorrectMap(self.answer_id, correctness='partially-correct', npoints=return_grade)
        else:
            return CorrectMap(self.answer_id, 'incorrect')

    def grade_without_partial_credit(self, **kwargs):
        """
        Standard grading for checkbox problems.
        100% credit if all choices are correct; 0% otherwise
        Arguments: student_answer, which is the items the student actually chose
        """

        student_answer = kwargs['student_answer']

        required_selected = len(self.correct_choices - student_answer) == 0
        no_extra_selected = len(student_answer - self.correct_choices) == 0

        correct = required_selected & no_extra_selected

        if correct:
            return CorrectMap(self.answer_id, 'correct')
        else:
            return CorrectMap(self.answer_id, 'incorrect')

    def get_score(self, student_answers):

        # Setting up answer sets:
        #  all_choices: the full set of checkboxes
        #  student_answer: what the student actually chose (note no "s")
        #  student_non_answers: what they didn't choose
        #  self.correct_choices: boxes that should be checked
        #  self.incorrect_choices: boxes that should NOT be checked

        all_choices = self.correct_choices.union(self.incorrect_choices)

        student_answer = student_answers.get(self.answer_id, [])

        if not isinstance(student_answer, list):
            student_answer = [student_answer]

        # When a student leaves all the boxes unmarked, edX throws an error.
        # This line checks for blank answers so that we can throw "false".
        # This is not ideal. "None apply" should be a valid choice.
        # Sadly, this is not the place where we can fix that problem.
        empty_answer = student_answer == []

        if empty_answer:
            return CorrectMap(self.answer_id, 'incorrect')

        student_answer = set(student_answer)

        student_non_answers = all_choices - student_answer

        # No partial credit? Get grade right now.
        if not self.has_partial_credit:
            return self.grade_without_partial_credit(student_answer=student_answer)

        # This below checks to see whether we're using an alternate grading scheme.
        #  Set partial_credit="false" (or remove it) to require an exact answer for any credit.
        #  Set partial_credit="EDC" to count each choice for equal points (Every Decision Counts).
        #  Set partial_credit="halves" to take half credit off for each error.

        # Translators: 'partial_credit' and the items in the 'graders' object
        # are attribute names or values and should not be translated.
        graders = {
            'edc': self.grade_via_every_decision_counts,
            'halves': self.grade_via_halves,
            'false': self.grade_without_partial_credit
        }

        # Only one type of credit at a time.
        if len(self.credit_type) > 1:
            raise LoncapaProblemError('Only one type of partial credit is allowed for Checkbox problems.')

        # Make sure we're using an approved style.
        if self.credit_type[0] not in graders:
            raise LoncapaProblemError('partial_credit attribute should be one of: ' + ','.join(graders))

        # Run the appropriate grader.
        return graders[self.credit_type[0]](
            all_choices=all_choices,
            student_answer=student_answer,
            student_non_answers=student_non_answers
        )

    def get_answers(self):
        return {self.answer_id: list(self.correct_choices)}

    def get_extended_hints(self, student_answers, new_cmap):
        """
        Extract compound and extended hint information from the xml based on the student_answers.
        The hint information goes into the msg= in new_cmap for display.
        Each choice in the checkboxgroup can have 2 extended hints, matching the
        case that the student has or has not selected that choice:
          <checkboxgroup label="Select the best snack">
             <choice correct="true">Donut
               <choicehint selected="tRuE">A Hint!</choicehint>
               <choicehint selected="false">Another hint!</choicehint>
             </choice>
        """
        # Tricky: student_answers may be *empty* here. That is the representation that
        # no checkboxes were selected. For typical responsetypes, you look at
        # student_answers[self.answer_id], but that does not work here.

        # Compound hints are a special thing just for checkboxgroup, trying
        # them first before the regular extended hints.
        if self.get_compound_hints(new_cmap, student_answers):
            return

        # Look at all the choices - each can generate some hint text
        choices = self.xml.xpath('//checkboxgroup[@id=$id]/choice', id=self.answer_id)
        hint_log = []
        label = None
        label_count = 0
        choice_all = []
        # Tricky: in the case that the student selects nothing, there is simply
        # no entry in student_answers, rather than an entry with the empty list value.
        # That explains the following line.
        student_choice_list = student_answers.get(self.answer_id, [])
        # We build up several hints in hint_divs, then wrap it once at the end.
        for choice in choices:
            name = choice.get('name')  # generated name, e.g. choice_2
            choice_all.append(name)
            selected = name in student_choice_list  # looking for 'true' vs. 'false'
            if selected:
                selector = 'true'
            else:
                selector = 'false'
            # We find the matching <choicehint> in python vs xpath so we can be case-insensitive
            hint_nodes = choice.findall('./choicehint')
            for hint_node in hint_nodes:
                if hint_node.get('selected', '').lower() == selector:
                    text = get_inner_html_from_xpath(hint_node)
                    if hint_node.get('label') is not None:  # tricky: label '' vs None is significant
                        label = hint_node.get('label')
                        label_count += 1
                    if text:
                        hint_log.append({'text': text, 'trigger': [{'choice': name, 'selected': selected}]})

        if hint_log:
            # Complication: if there is only a single label specified, we use it.
            # However if there are multiple, we use none.
            if label_count > 1:
                label = None
            new_cmap[self.answer_id]['msg'] += self.make_hint_div(
                None,
                new_cmap[self.answer_id]['correctness'] == 'correct',
                student_choice_list,
                self.tags[0],
                label,
                hint_log,
                multiline_mode=True,  # the one case where we do this
                log_extra={'choice_all': choice_all}  # checkbox specific logging
            )

    def get_compound_hints(self, new_cmap, student_answers):
        """
        Compound hints are a type of extended hint specific to checkboxgroup with the
        <compoundhint value="A C"> meaning choices A and C were selected.
        Checks for a matching compound hint, installing it in new_cmap.
        Returns True if compound condition hints were matched.
        """
        compound_hint_matched = False
        if self.answer_id in student_answers:
            # First create a set of the student's selected ids
            student_set = set()
            names = []
            for student_answer in student_answers[self.answer_id]:
                choice_list = self.xml.xpath('//checkboxgroup[@id=$id]/choice[@name=$name]',
                                             id=self.answer_id, name=student_answer)
                if choice_list:
                    choice = choice_list[0]
                    student_set.add(choice.get('id').upper())
                    names.append(student_answer)

            for compound_hint in self.xml.xpath('//checkboxgroup[@id=$id]/compoundhint', id=self.answer_id):
                # Selector words are space separated and not case-sensitive
                selectors = compound_hint.get('value').upper().split()
                selector_set = set(selectors)

                if selector_set == student_set:
                    # This is the atypical case where the hint text is in an inner div with its own style.
                    hint_text = compound_hint.text.strip()
                    # Compute the choice names just for logging
                    choices = self.xml.xpath('//checkboxgroup[@id=$id]/choice', id=self.answer_id)
                    choice_all = [choice.get('name') for choice in choices]
                    hint_log = [{'text': hint_text, 'trigger': [{'choice': name, 'selected': True} for name in names]}]
                    new_cmap[self.answer_id]['msg'] += self.make_hint_div(
                        compound_hint,
                        new_cmap[self.answer_id]['correctness'] == 'correct',
                        student_answers[self.answer_id],
                        self.tags[0],
                        hint_log=hint_log,
                        log_extra={'trigger_type': 'compound', 'choice_all': choice_all}
                    )
                    compound_hint_matched = True
                    break
        return compound_hint_matched

#-----------------------------------------------------------------------------


@registry.register
class MultipleChoiceResponse(LoncapaResponse):
    """
    Multiple Choice Response
    The shuffle and answer-pool features on this class enable permuting and
    subsetting the choices shown to the student.
    Both features enable name "masking":
    With masking, the regular names of multiplechoice choices
    choice_0 choice_1 ... are not used. Instead we use random masked names
    mask_2 mask_0 ... so that a view-source of the names reveals nothing about
    the original order. We introduce the masked names right at init time, so the
    whole software stack works with just the one system of naming.
    The .has_mask() test on a response checks for masking, implemented by a
    ._has_mask attribute on the response object.
    The logging functionality in capa_block calls the unmask functions here
    to translate back to choice_0 name style for recording in the logs, so
    the logging is in terms of the regular names.
    """
    # TODO: randomize

    human_name = _('Multiple Choice')
    tags = ['multiplechoiceresponse']
    max_inputfields = 1
    allowed_inputfields = ['choicegroup']
    correct_choices = None
    multi_device_support = True

    def setup_response(self):
        """
        Collects information from the XML for later use.

        correct_choices is a list of the correct choices.
        partial_choices is a list of the partially-correct choices.
        partial_values is a list of the scores that go with those
          choices, defaulting to 0.5 if no value is specified.
        """
        # call secondary setup for MultipleChoice questions, to set name
        # attributes
        self.mc_setup_response()

        # define correct choices (after calling secondary setup)
        xml = self.xml
        cxml = xml.xpath('//*[@id=$id]//choice', id=xml.get('id'))

        # contextualize correct attribute and then select ones for which
        # correct = "true"
        self.correct_choices = [
            contextualize_text(choice.get('name'), self.context)
            for choice in cxml
            if contextualize_text(choice.get('correct'), self.context).upper() == "TRUE"
        ]

        if self.has_partial_credit:
            self.partial_choices = [
                contextualize_text(choice.get('name'), self.context)
                for choice in cxml
                if contextualize_text(choice.get('correct'), self.context).lower() == 'partial'
            ]
            self.partial_values = [
                float(choice.get('point_value', default='0.5'))    # Default partial credit: 50%
                for choice in cxml
                if contextualize_text(choice.get('correct'), self.context).lower() == 'partial'
            ]

    def get_extended_hints(self, student_answer_dict, new_cmap):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Extract any hints in a <choicegroup> matching the student's answers
        <choicegroup label="What is your favorite color?" type="MultipleChoice">
          <choice correct="false">Red
            <choicehint>No, Blue!</choicehint>
          </choice>
          ...
        Any hint text is installed in the new_cmap.
        """
        if self.answer_id in student_answer_dict:
            student_answer = student_answer_dict[self.answer_id]

            # Warning: mostly student_answer is a string, but sometimes it is a list of strings.
            if isinstance(student_answer, list):
                student_answer = student_answer[0]

            # Find the named choice used by the student. Silently ignore a non-matching
            # choice name.
            choice = self.xml.find('./choicegroup[@id="{0}"]/choice[@name="{1}"]'.format(self.answer_id,
                                                                                         student_answer))
            if choice is not None:
                hint_node = choice.find('./choicehint')
                new_cmap[self.answer_id]['msg'] += self.make_hint_div(
                    hint_node,
                    choice.get('correct').upper() == 'TRUE',
                    [student_answer],
                    self.tags[0]
                )

    def mc_setup_response(self):
        """
        Initialize name attributes in <choice> stanzas in the <choicegroup> in this response.
        Masks the choice names if applicable.
        """
        i = 0
        for response in self.xml.xpath("choicegroup"):
            # Is Masking enabled? -- check for shuffle or answer-pool features
            # Masking (self._has_mask) is off, to be re-enabled with a future PR.
            rtype = response.get('type')
            if rtype not in ["MultipleChoice"]:
                # force choicegroup to be MultipleChoice if not valid
                response.set("type", "MultipleChoice")
            for choice in list(response):
                # The regular, non-masked name:
                if choice.get("name") is not None:
                    name = "choice_" + choice.get("name")
                else:
                    name = "choice_" + str(i)
                    i += 1
                # If using the masked name, e.g. mask_0, save the regular name
                # to support unmasking later (for the logs).
                # Masking is currently disabled so this code is commented, as
                # the variable `mask_ids` is not defined. (the feature appears to not be fully implemented)
                # The original work for masking was done by Nick Parlante as part of the OLI Hinting feature.
                # if self.has_mask():
                #     mask_name = "mask_" + str(mask_ids.pop())
                #     self._mask_dict[mask_name] = name
                #     choice.set("name", mask_name)
                # else:
                choice.set("name", name)

    def late_transforms(self, problem):
        """
        Rearrangements run late in the __init__ process.
        Cannot do these at response init time, as not enough
        other stuff exists at that time.
        """
        self.do_shuffle(self.xml, problem)
        self.do_answer_pool(self.xml, problem)

    def grade_via_points(self, **kwargs):
        """
        Calculates partial credit based on the Points scheme.
        Answer choices marked "partial" are given partial credit.
        Default is 50%; other amounts may be set in point_value attributes.
        Arguments: student_answers
        Returns: a CorrectMap
        """

        student_answers = kwargs['student_answers']

        if (self.answer_id in student_answers
                and student_answers[self.answer_id] in self.correct_choices):
            return CorrectMap(self.answer_id, correctness='correct')

        elif (
                self.answer_id in student_answers
                and student_answers[self.answer_id] in self.partial_choices
        ):
            choice_index = self.partial_choices.index(student_answers[self.answer_id])
            credit_amount = self.partial_values[choice_index]
            return CorrectMap(self.answer_id, correctness='partially-correct', npoints=credit_amount)
        else:
            return CorrectMap(self.answer_id, 'incorrect')

    def grade_without_partial_credit(self, **kwargs):
        """
        Standard grading for multiple-choice problems.
        100% credit if choices are correct; 0% otherwise
        Arguments: student_answers
        Returns: a CorrectMap
        """

        student_answers = kwargs['student_answers']

        if (self.answer_id in student_answers
                and student_answers[self.answer_id] in self.correct_choices):
            return CorrectMap(self.answer_id, correctness='correct')
        else:
            return CorrectMap(self.answer_id, 'incorrect')

    def get_score(self, student_answers):
        """
        grade student response.
        """

        # No partial credit? Grade it right away.
        if not self.has_partial_credit:
            return self.grade_without_partial_credit(student_answers=student_answers)

        # This below checks to see whether we're using an alternate grading scheme.
        #  Set partial_credit="false" (or remove it) to require an exact answer for any credit.
        #  Set partial_credit="points" to set specific point values for specific choices.

        # Translators: 'partial_credit' and the items in the 'graders' object
        # are attribute names or values and should not be translated.
        graders = {
            'points': self.grade_via_points,
            'false': self.grade_without_partial_credit
        }

        # Only one type of credit at a time.
        if len(self.credit_type) > 1:
            raise LoncapaProblemError('Only one type of partial credit is allowed for Multiple Choice problems.')

        # Make sure we're using an approved style.
        if self.credit_type[0] not in graders:
            raise LoncapaProblemError('partial_credit attribute should be one of: ' + ','.join(graders))

        # Run the appropriate grader.
        return graders[self.credit_type[0]](
            student_answers=student_answers
        )

    def get_answers(self):
        return {self.answer_id: self.correct_choices}

    def unmask_name(self, name):
        """
        Given a masked name, e.g. mask_2, returns the regular name, e.g. choice_0.
        Fails with LoncapaProblemError if called on a response that is not masking.
        """
        # if not self.has_mask():
        #     _ = self.capa_system.i18n.gettext
        #     # Translators: 'unmask_name' is a method name and should not be translated.
        #     msg = "unmask_name called on response that is not masked"
        #     raise LoncapaProblemError(msg)
        # return self._mask_dict[name]  # TODO: this is not defined
        raise NotImplementedError()

    def unmask_order(self):
        """
        Returns a list of the choice names in the order displayed to the user,
        using the regular (non-masked) names.
        """
        # With masking disabled, this computation remains interesting to see
        # the displayed order, even though there is no unmasking.
        choices = self.xml.xpath('choicegroup/choice')
        return [choice.get("name") for choice in choices]

    def do_shuffle(self, tree, problem):
        """
        For a choicegroup with shuffle="true", shuffles the choices in-place in the given tree
        based on the seed. Otherwise does nothing.
        Raises LoncapaProblemError if both shuffle and answer-pool are active:
        a problem should use one or the other but not both.
        Does nothing if the tree has already been processed.
        """
        # The tree is already pared down to this <multichoiceresponse> so this query just
        # gets the child choicegroup (i.e. no leading //)
        choicegroups = tree.xpath('choicegroup[@shuffle="true"]')
        if choicegroups:
            choicegroup = choicegroups[0]
            if choicegroup.get('answer-pool') is not None:
                _ = self.capa_system.i18n.gettext
                # Translators: 'shuffle' and 'answer-pool' are attribute names and should not be translated.
                msg = _("Do not use shuffle and answer-pool at the same time")
                raise LoncapaProblemError(msg)
            # Note in the response that shuffling is done.
            # Both to avoid double-processing, and to feed the logs.
            if self.has_shuffle():
                return
            self._has_shuffle = True  # pylint: disable=attribute-defined-outside-init
            # Move elements from tree to list for shuffling, then put them back.
            ordering = list(choicegroup.getchildren())
            for choice in ordering:
                choicegroup.remove(choice)
            ordering = self.shuffle_choices(ordering, self.get_rng(problem))
            for choice in ordering:
                choicegroup.append(choice)

    def shuffle_choices(self, choices, rng):
        """
        Returns a list of choice nodes with the shuffling done,
        using the provided random number generator.
        Choices with 'fixed'='true' are held back from the shuffle.
        """
        # Separate out a list of the stuff to be shuffled
        # vs. the head/tail of fixed==true choices to be held back from the shuffle.
        # Rare corner case: A fixed==true choice "island" in the middle is lumped in
        # with the tail group of fixed choices.
        # Slightly tricky one-pass implementation using a state machine
        head = []
        middle = []  # only this one gets shuffled
        tail = []
        at_head = True
        for choice in choices:
            if at_head and choice.get('fixed') == 'true':
                head.append(choice)
                continue
            at_head = False
            if choice.get('fixed') == 'true':
                tail.append(choice)
            else:
                middle.append(choice)
        rng.shuffle(middle)
        return head + middle + tail

    def get_rng(self, problem):
        """
        Get the random number generator to be shared by responses
        of the problem, creating it on the problem if needed.
        """
        # Multiple questions in a problem share one random number generator (rng) object
        # stored on the problem. If each question got its own rng, the structure of multiple
        # questions within a problem could appear predictable to the student,
        # e.g. (c) keeps being the correct choice. This is due to the seed being
        # defined at the problem level, so the multiple rng's would be seeded the same.
        # The name _shared_rng begins with an _ to suggest that it is not a facility
        # for general use.
        # pylint: disable=protected-access
        if not hasattr(problem, '_shared_rng'):
            problem._shared_rng = random.Random(self.context['seed'])
        return problem._shared_rng

    def do_answer_pool(self, tree, problem):
        """
        Implements the answer-pool subsetting operation in-place on the tree.
        Allows for problem questions with a pool of answers, from which answer options shown to the student
        and randomly selected so that there is always 1 correct answer and n-1 incorrect answers,
        where the author specifies n as the value of the attribute "answer-pool" within <choicegroup>

        The <choicegroup> tag must have an attribute 'answer-pool' giving the desired
        pool size. If that attribute is zero or not present, no operation is performed.
        Calling this a second time does nothing.
        Raises LoncapaProblemError if the answer-pool value is not an integer,
        or if the number of correct or incorrect choices available is zero.
        """
        choicegroups = tree.xpath("choicegroup[@answer-pool]")
        if choicegroups:
            choicegroup = choicegroups[0]
            num_str = choicegroup.get('answer-pool')
            if num_str == '0':
                return
            try:
                num_choices = int(num_str)
            except ValueError:
                _ = self.capa_system.i18n.gettext
                # Translators: 'answer-pool' is an attribute name and should not be translated.
                msg = _("answer-pool value should be an integer")
                raise LoncapaProblemError(msg)  # lint-amnesty, pylint: disable=raise-missing-from

            # Note in the response that answerpool is done.
            # Both to avoid double-processing, and to feed the logs.
            if self.has_answerpool():
                return
            self._has_answerpool = True  # pylint: disable=attribute-defined-outside-init

            choices_list = list(choicegroup.getchildren())

            # Remove all choices in the choices_list (we will add some back in later)
            for choice in choices_list:
                choicegroup.remove(choice)

            rng = self.get_rng(problem)  # random number generator to use
            # Sample from the answer pool to get the subset choices and solution id
            (solution_id, subset_choices) = self.sample_from_answer_pool(choices_list, rng, num_choices)

            # Add back in randomly selected choices
            for choice in subset_choices:
                choicegroup.append(choice)

            # Filter out solutions that don't correspond to the correct answer we selected to show
            # Note that this means that if the user simply provides a <solution> tag, nothing is filtered
            solutionset = choicegroup.xpath('../following-sibling::solutionset')
            if len(solutionset) != 0:
                solutionset = solutionset[0]
                solutions = solutionset.xpath('./solution')
                for solution in solutions:
                    if solution.get('explanation-id') != solution_id:
                        solutionset.remove(solution)

    def sample_from_answer_pool(self, choices, rng, num_pool):
        """
        Takes in:
            1. list of choices
            2. random number generator
            3. the requested size "answer-pool" number, in effect a max

        Returns a tuple with 2 items:
            1. the solution_id corresponding with the chosen correct answer
            2. (subset) list of choice nodes with num-1 incorrect and 1 correct

        Raises an error if the number of correct or incorrect choices is 0.
        """

        correct_choices = []
        incorrect_choices = []

        for choice in choices:
            if choice.get('correct').upper() == 'TRUE':
                correct_choices.append(choice)
            else:
                incorrect_choices.append(choice)
                # In my small test, capa seems to treat the absence of any correct=
                # attribute as equivalent to ="false", so that's what we do here.

        # We raise an error if the problem is highly ill-formed.
        # There must be at least one correct and one incorrect choice.
        # IDEA: perhaps this sort semantic-lint constraint should be generalized to all multichoice
        # not just down in this corner when answer-pool is used.
        # Or perhaps in the overall author workflow, these errors are unhelpful and
        # should all be removed.
        if len(correct_choices) < 1 or len(incorrect_choices) < 1:
            _ = self.capa_system.i18n.gettext
            # Translators: 'Choicegroup' is an input type and should not be translated.
            msg = _("Choicegroup must include at least 1 correct and 1 incorrect choice")
            raise LoncapaProblemError(msg)

        # Limit the number of incorrect choices to what we actually have
        num_incorrect = num_pool - 1
        num_incorrect = min(num_incorrect, len(incorrect_choices))

        # Select the one correct choice
        index = rng.randint(0, len(correct_choices) - 1)
        correct_choice = correct_choices[index]
        solution_id = correct_choice.get('explanation-id')

        # Put together the result, pushing most of the work onto rng.shuffle()
        subset_choices = [correct_choice]
        rng.shuffle(incorrect_choices)
        subset_choices += incorrect_choices[:num_incorrect]
        rng.shuffle(subset_choices)

        return (solution_id, subset_choices)


@registry.register
class TrueFalseResponse(MultipleChoiceResponse):  # lint-amnesty, pylint: disable=abstract-method, missing-class-docstring

    human_name = _('True/False Choice')
    tags = ['truefalseresponse']

    def mc_setup_response(self):
        i = 0
        for response in self.xml.xpath("choicegroup"):
            response.set("type", "TrueFalse")
            for choice in list(response):
                if choice.get("name") is None:
                    choice.set("name", "choice_" + str(i))
                    i += 1
                else:
                    choice.set("name", "choice_" + choice.get("name"))

    def get_score(self, student_answers):
        correct = set(self.correct_choices)
        answers = student_answers.get(self.answer_id, [])
        if not isinstance(answers, list):
            answers = [answers]

        if correct == set(answers):
            return CorrectMap(self.answer_id, 'correct')

        return CorrectMap(self.answer_id, 'incorrect')

#-----------------------------------------------------------------------------


@registry.register
class OptionResponse(LoncapaResponse):
    """
    TODO: handle randomize
    """

    human_name = _('Dropdown')
    tags = ['optionresponse']
    hint_tag = 'optionhint'
    allowed_inputfields = ['optioninput']
    answer_fields = None
    multi_device_support = True

    def setup_response(self):
        self.answer_fields = self.inputfields

    def get_score(self, student_answers):
        cmap = CorrectMap()
        amap = self.get_answers()
        for aid in amap:
            if aid in student_answers and student_answers[aid] == amap[aid]:
                cmap.set(aid, 'correct')
            else:
                cmap.set(aid, 'incorrect')
            answer_variable = self.get_student_answer_variable_name(student_answers, aid)
            if answer_variable:
                cmap.set_property(aid, 'answervariable', answer_variable)
        return cmap

    def get_answers(self):
        amap = dict([(af.get('id'), contextualize_text(af.get(  # lint-amnesty, pylint: disable=consider-using-dict-comprehension
            'correct'), self.context)) for af in self.answer_fields])
        return amap

    def get_student_answer_variable_name(self, student_answers, aid):
        """
        Return student answers variable name if exist in context else None.
        """
        if aid in student_answers:
            for key, val in six.iteritems(self.context):
                # convert val into unicode because student answer always be a unicode string
                # even it is a list, dict etc.
                if str(val) == student_answers[aid]:
                    return '$' + key
        return None

    def get_extended_hints(self, student_answers, new_cmap):
        """
        Extract optioninput extended hint, e.g.
        <optioninput>
          <option correct="True">Donut <optionhint>Of course</optionhint> </option>
        """
        answer_id = self.answer_ids[0]  # Note *not* self.answer_id
        if answer_id in student_answers:
            student_answer = student_answers[answer_id]
            # If we run into an old-style optioninput, there is no <option> tag, so this safely does nothing
            options = self.xml.xpath('//optioninput[@id=$id]/option', id=answer_id)
            # Extra pass here to ignore whitespace around the answer in the matching
            options = [option for option in options if option.text.strip() == student_answer]
            if options:
                option = options[0]
                hint_node = option.find('./optionhint')
                if hint_node is not None:
                    new_cmap[answer_id]['msg'] += self.make_hint_div(
                        hint_node,
                        option.get('correct').upper() == 'TRUE',
                        [student_answer],
                        self.tags[0]
                    )

#-----------------------------------------------------------------------------


@registry.register
class NumericalResponse(LoncapaResponse):
    """
    This response type expects a number or formulaic expression that evaluates
    to a number (e.g. `4+5/2^2`), and accepts with a tolerance.
    """

    human_name = _('Numerical Input')
    tags = ['numericalresponse']
    hint_tag = 'numericalhint'
    allowed_inputfields = ['textline', 'formulaequationinput']
    required_attributes = ['answer']
    max_inputfields = 1
    multi_device_support = True

    def __init__(self, *args, **kwargs):
        self.correct_answer = ''
        self.additional_answers = []
        self.additional_answer_index = -1
        self.tolerance = default_tolerance
        self.range_tolerance = False
        self.answer_range = self.inclusion = None
        super(NumericalResponse, self).__init__(*args, **kwargs)  # lint-amnesty, pylint: disable=super-with-arguments

    def setup_response(self):
        xml = self.xml
        context = self.context
        answer = xml.get('answer')

        self.additional_answers = (
            [element.get('answer') for element in xml.findall('additional_answer')]
        )

        if answer.startswith(('[', '(')) and answer.endswith((']', ')')):  # range tolerance case
            self.range_tolerance = True
            self.inclusion = (
                True if answer.startswith('[') else False, True if answer.endswith(']') else False  # lint-amnesty, pylint: disable=simplifiable-if-expression
            )
            try:
                self.answer_range = [contextualize_text(x, context) for x in answer[1:-1].split(',')]
                self.correct_answer = answer[0] + self.answer_range[0] + ', ' + self.answer_range[1] + answer[-1]
            except Exception:
                log.debug("Content error--answer '%s' is not a valid range tolerance answer", answer)
                _ = self.capa_system.i18n.gettext
                raise StudentInputError(  # lint-amnesty, pylint: disable=raise-missing-from
                    _("There was a problem with the staff answer to this problem.")
                )
        else:
            self.correct_answer = contextualize_text(answer, context)

            # Find the tolerance
            tolerance_xml = xml.xpath(
                '//*[@id=$id]//responseparam[@type="tolerance"]/@default',
                id=xml.get('id')
            )
            if tolerance_xml:  # If it isn't an empty list...
                self.tolerance = contextualize_text(tolerance_xml[0].strip(), context)

    def get_staff_ans(self, answer):
        """
        Given the staff answer as a string, find its float value.

        Use `evaluator` for this, but for backward compatability, try the
        built-in method `complex` (which used to be the standard).
        """
        try:
            correct_ans = complex(answer)
        except ValueError:
            # When `correct_answer` is not of the form X+Yj, it raises a
            # `ValueError`. Then test if instead it is a math expression.
            # `complex` seems to only generate `ValueErrors`, only catch these.
            try:
                correct_ans = evaluator({}, {}, answer)
            except Exception:
                log.debug("Content error--answer '%s' is not a valid number", answer)
                _ = self.capa_system.i18n.gettext
                raise StudentInputError(  # lint-amnesty, pylint: disable=raise-missing-from
                    _("There was a problem with the staff answer to this problem.")
                )

        return correct_ans

    def get_score(self, student_answers):  # lint-amnesty, pylint: disable=too-many-statements
        """
        Grade a numeric response.
        """
        if self.answer_id not in student_answers:
            return CorrectMap(self.answer_id, 'incorrect')

        # Make sure we're using an approved partial credit style.
        # Currently implemented: 'close' and 'list'
        if self.has_partial_credit:
            graders = ['list', 'close']
            for style in self.credit_type:
                if style not in graders:
                    raise LoncapaProblemError('partial_credit attribute should be one of: ' + ','.join(graders))

        student_answer = student_answers[self.answer_id]

        _ = self.capa_system.i18n.gettext
        general_exception = StudentInputError(
            _("Could not interpret '{student_answer}' as a number.").format(student_answer=html.escape(student_answer))
        )

        # Begin `evaluator` block
        # Catch a bunch of exceptions and give nicer messages to the student.
        try:
            student_float = evaluator({}, {}, student_answer)
        except UndefinedVariable as err:
            raise StudentInputError(  # lint-amnesty, pylint: disable=raise-missing-from
                err.args[0]
            )
        except UnmatchedParenthesis as err:
            raise StudentInputError(  # lint-amnesty, pylint: disable=raise-missing-from
                err.args[0]
            )
        except ValueError as val_err:
            if 'factorial' in str(val_err):  # lint-amnesty, pylint: disable=no-else-raise
                # This is thrown when fact() or factorial() is used in an answer
                #   that evaluates on negative and/or non-integer inputs
                # str(ve) will be: `factorial() only accepts integral values` or
                # `factorial() not defined for negative values`
                raise StudentInputError(  # lint-amnesty, pylint: disable=raise-missing-from
                    _("Factorial function evaluated outside its domain:"
                      "'{student_answer}'").format(student_answer=html.escape(student_answer))
                )
            else:
                raise general_exception  # lint-amnesty, pylint: disable=raise-missing-from
        except ParseException:
            raise StudentInputError(  # lint-amnesty, pylint: disable=raise-missing-from
                _("Invalid math syntax: '{student_answer}'").format(student_answer=html.escape(student_answer))
            )
        except Exception:
            raise general_exception  # lint-amnesty, pylint: disable=raise-missing-from
        # End `evaluator` block -- we figured out the student's answer!

        tree = self.xml

        # What multiple of the tolerance is worth partial credit?
        has_partial_range = tree.xpath('responseparam[@partial_range]')
        if has_partial_range:
            partial_range = float(has_partial_range[0].get('partial_range', default='2'))
        else:
            partial_range = 2

        # Take in alternative answers that are worth partial credit.
        has_partial_answers = tree.xpath('responseparam[@partial_answers]')
        if has_partial_answers:
            partial_answers = has_partial_answers[0].get('partial_answers').split(',')
            for index, word in enumerate(partial_answers):
                partial_answers[index] = word.strip()
                partial_answers[index] = self.get_staff_ans(partial_answers[index])

        else:
            partial_answers = False

        partial_score = 0.5
        is_correct = 'incorrect'

        if self.range_tolerance:
            if isinstance(student_float, complex):
                raise StudentInputError(_("You may not use complex numbers in range tolerance problems"))
            boundaries = []
            for inclusion, answer in zip(self.inclusion, self.answer_range):
                boundary = self.get_staff_ans(answer)
                if boundary.imag != 0:
                    raise StudentInputError(
                        # Translators: This is an error message for a math problem. If the instructor provided a
                        # boundary (end limit) for a variable that is a complex number (a + bi), this message displays.
                        _("There was a problem with the staff answer to this problem: complex boundary.")
                    )
                if isnan(boundary):
                    raise StudentInputError(
                        # Translators: This is an error message for a math problem. If the instructor did not
                        # provide a boundary (end limit) for a variable, this message displays.
                        _("There was a problem with the staff answer to this problem: empty boundary.")
                    )
                boundaries.append(boundary.real)
                if compare_with_tolerance(
                        student_float,
                        boundary,
                        tolerance=float_info.epsilon,
                        relative_tolerance=True
                ):
                    is_correct = 'correct' if inclusion else 'incorrect'
                    break
            else:
                if boundaries[0] < student_float < boundaries[1]:
                    is_correct = 'correct'
                else:
                    if self.has_partial_credit is False:
                        pass
                    elif 'close' in self.credit_type:
                        # Partial credit: 50% if the student is outside the specified boundaries,
                        # but within an extended set of boundaries.

                        extended_boundaries = []
                        boundary_range = boundaries[1] - boundaries[0]
                        extended_boundaries.append(boundaries[0] - partial_range * boundary_range)
                        extended_boundaries.append(boundaries[1] + partial_range * boundary_range)
                        if extended_boundaries[0] < student_float < extended_boundaries[1]:
                            is_correct = 'partially-correct'

        else:
            correct_float = self.get_staff_ans(self.correct_answer)

            # Partial credit is available in three cases:
            #  If the student answer is within expanded tolerance of the actual answer,
            #  the student gets 50% credit. (Currently set as the default.)
            #  Set via partial_credit="close" in the numericalresponse tag.
            #
            #  If the student answer is within regular tolerance of an alternative answer,
            #  the student gets 50% credit. (Same default.)
            #  Set via partial_credit="list"
            #
            #  If the student answer is within expanded tolerance of an alternative answer,
            #  the student gets 25%. (We take the 50% and square it, at the moment.)
            #  Set via partial_credit="list,close" or "close, list" or the like.

            if str(self.tolerance).endswith('%'):
                expanded_tolerance = str(partial_range * float(str(self.tolerance)[:-1])) + '%'
            else:
                expanded_tolerance = partial_range * float(self.tolerance)

            if compare_with_tolerance(student_float, correct_float, self.tolerance):
                is_correct = 'correct'
            elif self.has_partial_credit is False:
                pass
            elif 'list' in self.credit_type:
                for value in partial_answers:
                    if compare_with_tolerance(student_float, value, self.tolerance):
                        is_correct = 'partially-correct'
                        break
                    elif 'close' in self.credit_type:
                        if compare_with_tolerance(student_float, correct_float, expanded_tolerance):
                            is_correct = 'partially-correct'
                            break
                        elif compare_with_tolerance(student_float, value, expanded_tolerance):
                            is_correct = 'partially-correct'
                            partial_score = partial_score * partial_score
                            break
            elif 'close' in self.credit_type:
                if compare_with_tolerance(student_float, correct_float, expanded_tolerance):
                    is_correct = 'partially-correct'

        # Reset self.additional_answer_index to -1 so that we always have a fresh index to look up.
        self.additional_answer_index = -1

        # Compare with additional answers.
        if is_correct == 'incorrect':
            temp_additional_answer_idx = 0
            for additional_answer in self.additional_answers:
                staff_answer = self.get_staff_ans(additional_answer)
                if complex(student_float) == staff_answer:
                    is_correct = 'correct'
                    self.additional_answer_index = temp_additional_answer_idx
                    break
                temp_additional_answer_idx += 1

        if is_correct == 'partially-correct':
            return CorrectMap(self.answer_id, is_correct, npoints=partial_score)
        else:
            return CorrectMap(self.answer_id, is_correct)

    def compare_answer(self, ans1, ans2):
        """
        Outside-facing function that lets us compare two numerical answers,
        with this problem's tolerance.
        """
        return compare_with_tolerance(
            evaluator({}, {}, ans1),
            evaluator({}, {}, ans2),
            self.tolerance
        )

    def validate_answer(self, answer):
        """
        Returns whether this answer is in a valid form.
        """
        try:
            evaluator({}, {}, answer)
            return True
        except (StudentInputError, UndefinedVariable, UnmatchedParenthesis):
            return False

    def get_answers(self):
        _ = self.capa_system.i18n.gettext
        # Example: "Answer: Answer_1 or Answer_2 or Answer_3".
        separator = Text(' {b_start}{or_separator}{b_end} ').format(
            # Translators: Separator used in NumericalResponse to display multiple answers.
            or_separator=_('or'),
            b_start=HTML('<b>'),
            b_end=HTML('</b>'),
        )
        return {self.answer_id: separator.join([self.correct_answer] + self.additional_answers)}

    def set_cmap_msg(self, student_answers, new_cmap, hint_type, hint_index):
        """
        Sets feedback to correct hint node in correct map.

        Arguments:
            student_answers (dict): Dict containing student input.
            new_cmap (dict): Dict containing correct map properties.
            hint_type (str): Hint type, either `correcthint` or `additional_answer`
            hint_index (int): Index of the hint node
        """
        # Note: using self.id here, not the more typical self.answer_id
        hint_nodes = self.xml.xpath('//numericalresponse[@id=$id]/' + hint_type, id=self.id)
        if hint_nodes:
            hint_node = hint_nodes[hint_index]
            if hint_type == 'additional_answer':
                hint_node = hint_nodes[hint_index].find('./correcthint')
            new_cmap[self.answer_id]['msg'] += self.make_hint_div(
                hint_node,
                True,
                [student_answers[self.answer_id]],
                self.tags[0]
            )

    def get_extended_hints(self, student_answers, new_cmap):
        """
        Extract numericalresponse extended hint, e.g.
          <correcthint>Yes, 1+1 IS 2<correcthint>
        """
        if self.answer_id in student_answers:
            if new_cmap.cmap[self.answer_id]['correctness'] == 'correct':  # if the grader liked the student's answer
                # Answer is not an additional answer.
                if self.additional_answer_index == -1:
                    self.set_cmap_msg(student_answers, new_cmap, 'correcthint', 0)
                else:
                    self.set_cmap_msg(student_answers, new_cmap, 'additional_answer', self.additional_answer_index)

#-----------------------------------------------------------------------------


@registry.register
class StringResponse(LoncapaResponse):
    r"""
    This response type allows one or more answers.

    Additional answers are added by `additional_answer` tag.
    If `regexp` is in `type` attribute, then answers and hints are treated as regular expressions.

    Examples:
        <stringresponse answer="Michigan">
            <textline size="20" />
        </stringresponse >

        <stringresponse answer="a1" type="ci regexp">
            <additional_answer>d5</additional_answer>
            <additional_answer answer="a3"><correcthint>a hint - new format</correcthint></additional_answer>
            <textline size="20"/>
            <hintgroup>
                <stringhint answer="a0" type="ci" name="ha0" />
                <stringhint answer="a4" type="ci" name="ha4" />
                <stringhint answer="^\d" type="ci" name="re1" />
                <hintpart on="ha0">
                    <startouttext />+1<endouttext />
                </hintpart >
                <hintpart on="ha4">
                    <startouttext />-1<endouttext />
                </hintpart >
                <hintpart on="re1">
                    <startouttext />Any number+5<endouttext />
                </hintpart >
            </hintgroup>
        </stringresponse>
    """
    human_name = _('Text Input')
    tags = ['stringresponse']
    hint_tag = 'stringhint'
    allowed_inputfields = ['textline']
    required_attributes = ['answer']
    max_inputfields = 1
    correct_answer = []
    multi_device_support = True

    def setup_response_backward(self):
        self.correct_answer = [
            contextualize_text(answer, self.context).strip() for answer in self.xml.get('answer').split('_or_')
        ]

    def setup_response(self):
        self.backward = '_or_' in self.xml.get('answer').lower()
        self.regexp = False
        self.case_insensitive = False
        if self.xml.get('type') is not None:
            self.regexp = 'regexp' in self.xml.get('type').lower().split(' ')
            self.case_insensitive = 'ci' in self.xml.get('type').lower().split(' ')

        # backward compatibility, can be removed in future, it is up to @Lyla Fisher.
        if self.backward:
            self.setup_response_backward()
            return
        # end of backward compatibility

        # XML compatibility note: in 2015, additional_answer switched to having a 'answer' attribute.
        # See make_xml_compatible in capa_problem which translates the old format.
        correct_answers = (
            [self.xml.get('answer')] +
            [element.get('answer') for element in self.xml.findall('additional_answer')]
        )
        self.correct_answer = [contextualize_text(answer, self.context).strip() for answer in correct_answers]

    def get_score(self, student_answers):
        """Grade a string response """
        if self.answer_id not in student_answers:
            correct = False
        else:
            student_answer = student_answers[self.answer_id].strip()
            correct = self.check_string(self.correct_answer, student_answer)
        return CorrectMap(self.answer_id, 'correct' if correct else 'incorrect')

    def check_string_backward(self, expected, given):
        if self.case_insensitive:
            return given.lower() in [i.lower() for i in expected]
        return given in expected

    def get_extended_hints(self, student_answers, new_cmap):
        """
        Find and install extended hints in new_cmap depending on the student answers.
        StringResponse is probably the most complicated form we have.
        The forms show below match in the order given, and the first matching one stops the matching.
        <stringresponse answer="A" type="ci">
          <correcthint>hint1</correcthint>                         <!-- hint for correct answer -->
          <additional_answer answer="B">hint2</additional_answer>  <!-- additional_answer with its own hint -->
          <stringequalhint answer="C">hint3</stringequalhint>      <!-- string matcher/hint for an incorrect answer -->
          <regexphint answer="FG+">hint4</regexphint>              <!-- regex matcher/hint for an incorrect answer -->
          <textline size="20"/>
        </stringresponse>
        The "ci" and "regexp" options are inherited from the parent stringresponse as appropriate.
        """
        if self.answer_id in student_answers:
            student_answer = student_answers[self.answer_id]
            # Note the atypical case of using self.id instead of self.answer_id
            responses = self.xml.xpath('//stringresponse[@id=$id]', id=self.id)
            if responses:
                response = responses[0]

                # First call the existing check_string to see if this is a right answer by that test.
                # It handles the various "ci" "regexp" cases internally.
                expected = response.get('answer').strip()
                if self.check_string([expected], student_answer):
                    hint_node = response.find('./correcthint')
                    if hint_node is not None:
                        new_cmap[self.answer_id]['msg'] += self.make_hint_div(
                            hint_node,
                            True,
                            [student_answer],
                            self.tags[0]
                        )
                    return

                # Then look for additional answer with an answer= attribute
                for node in response.findall('./additional_answer'):
                    if self.match_hint_node(node, student_answer, self.regexp, self.case_insensitive):
                        hint_node = node.find('./correcthint')
                        new_cmap[self.answer_id]['msg'] += self.make_hint_div(
                            hint_node,
                            True,
                            [student_answer],
                            self.tags[0]
                        )
                        return

                # stringequalhint and regexphint represent wrong answers
                for hint_node in response.findall('./stringequalhint'):
                    if self.match_hint_node(hint_node, student_answer, False, self.case_insensitive):
                        new_cmap[self.answer_id]['msg'] += self.make_hint_div(
                            hint_node,
                            False,
                            [student_answer],
                            self.tags[0]
                        )
                        return

                for hint_node in response.findall('./regexphint'):
                    if self.match_hint_node(hint_node, student_answer, True, self.case_insensitive):
                        new_cmap[self.answer_id]['msg'] += self.make_hint_div(
                            hint_node,
                            False,
                            [student_answer],
                            self.tags[0]
                        )
                        return

    def match_hint_node(self, node, given, regex_mode, ci_mode):
        """
        Given an xml extended hint node such as additional_answer or regexphint,
        which contain an answer= attribute, returns True if the given student answer is a match.
        The boolean arguments regex_mode and ci_mode control how the answer stored in
        the question is treated for the comparison (analogously to check_string).
        """
        answer = node.get('answer', '').strip()
        if not answer:
            return False

        if regex_mode:
            flags = 0
            if ci_mode:
                flags = re.IGNORECASE
            try:
                # We follow the check_string convention/exception, adding ^ and $
                regex = re.compile('^' + answer + '$', flags=flags | re.UNICODE)
                return re.search(regex, given)
            except Exception:  # pylint: disable=broad-except
                return False

        if ci_mode:
            return answer.lower() == given.lower()
        else:
            return answer == given

    def check_string(self, expected, given):
        """
        Find given in expected.

        If self.regexp is true, regular expression search is used.
        if self.case_insensitive is true, case insensitive search is used, otherwise case sensitive search is used.
        Spaces around values of attributes are stripped in XML parsing step.

        Args:
            expected: list.
            given: str.

        Returns: bool

        Raises: `ResponseError` if it fails to compile regular expression.

        Note: for old code, which supports _or_ separator, we add some  backward compatibility handling.
        Should be removed soon. When to remove it, is up to Lyla Fisher.
        """
        # if given answer is empty.
        if not given:
            return False

        _ = self.capa_system.i18n.gettext
        # backward compatibility, should be removed in future.
        if self.backward:
            return self.check_string_backward(expected, given)
        # end of backward compatibility

        if self.regexp:  # regexp match
            flags = re.IGNORECASE if self.case_insensitive else 0
            try:
                regexp = re.compile('^' + '|'.join(expected) + '$', flags=flags | re.UNICODE)
                result = re.search(regexp, given)
            except Exception as err:
                msg = '[courseware.capa.responsetypes.stringresponse] {error}: {message}'.format(
                    error=_('error'),
                    message=str(err)
                )
                log.error(msg, exc_info=True)
                raise ResponseError(msg)  # lint-amnesty, pylint: disable=raise-missing-from
            return bool(result)
        else:  # string match
            if self.case_insensitive:
                return given.lower() in [i.lower() for i in expected]
            else:
                return given in expected

    def check_hint_condition(self, hxml_set, student_answers):
        given = student_answers[self.answer_id].strip()
        hints_to_show = []
        for hxml in hxml_set:
            name = hxml.get('name')

            hinted_answer = contextualize_text(hxml.get('answer'), self.context).strip()

            if self.check_string([hinted_answer], given):
                hints_to_show.append(name)
        log.debug('hints_to_show = %s', hints_to_show)
        return hints_to_show

    def get_answers(self):
        _ = self.capa_system.i18n.gettext
        # Translators: Separator used in StringResponse to display multiple answers.
        # Example: "Answer: Answer_1 or Answer_2 or Answer_3".
        separator = HTML(' <b>{}</b> ').format(_('or'))
        return {self.answer_id: separator.join(self.correct_answer)}

#-----------------------------------------------------------------------------


@registry.register
class CustomResponse(LoncapaResponse):
    """
    Custom response.  The python code to be run should be in <answer>...</answer>
    or in a <script>...</script>
    """

    human_name = _('Custom Evaluated Script')
    tags = ['customresponse']

    allowed_inputfields = ['textline', 'textbox', 'crystallography',
                           'chemicalequationinput', 'vsepr_input',
                           'drag_and_drop_input', 'designprotein2dinput',
                           'editageneinput', 'annotationinput',
                           'jsinput', 'formulaequationinput']
    code = None
    expect = None

    # Standard amount for partial credit if not otherwise specified:
    default_pc = 0.5

    def setup_response(self):
        xml = self.xml

        # if <customresponse> has an "expect" (or "answer") attribute then save
        # that
        self.expect = contextualize_text(xml.get('expect') or xml.get('answer'), self.context)

        log.debug('answer_ids=%s', self.answer_ids)

        # the <answer>...</answer> stanza should be local to the current <customresponse>.
        # So try looking there first.
        self.code = None
        answer = None
        try:
            answer = xml.xpath('//*[@id=$id]//answer', id=xml.get('id'))[0]
        except IndexError:
            # print "xml = ",etree.tostring(xml,pretty_print=True)

            # if we have a "cfn" attribute then look for the function specified by cfn, in
            # the problem context ie the comparison function is defined in the
            # <script>...</script> stanza instead
            cfn = xml.get('cfn')
            if cfn:
                log.debug("cfn = %s", cfn)

                # This is a bit twisty.  We used to grab the cfn function from
                # the context, but now that we sandbox Python execution, we
                # can't get functions from previous executions.  So we make an
                # actual function that will re-execute the original script,
                # and invoke the function with the data needed.
                def make_check_function(script_code, cfn):
                    def check_function(expect, ans, **kwargs):
                        extra_args = "".join(", {0}={0}".format(k) for k in kwargs)
                        code = (
                            script_code + "\n" +
                            "cfn_return = %s(expect, ans%s)\n" % (cfn, extra_args)
                        )
                        globals_dict = {
                            'expect': expect,
                            'ans': ans,
                        }
                        globals_dict.update(kwargs)
                        safe_exec.safe_exec(
                            code,
                            globals_dict,
                            python_path=self.context['python_path'],
                            extra_files=self.context['extra_files'],
                            limit_overrides_context=get_course_id_from_capa_block(
                                self.capa_block
                            ),
                            slug=self.id,
                            random_seed=self.context['seed'],
                            unsafely=self.capa_system.can_execute_unsafe_code(),
                        )
                        return globals_dict['cfn_return']
                    return check_function

                self.code = make_check_function(self.context['script_code'], cfn)

        if not self.code:
            if answer is None:
                log.error("[courseware.capa.responsetypes.customresponse] missing"
                          " code checking script! id=%s", self.id)
                self.code = ''
            else:
                answer_src = answer.get('src')
                if answer_src is not None:
                    # TODO: this code seems not to be used any more since self.capa_system.filesystem doesn't exist.
                    self.code = self.capa_system.filesystem.open('src/' + answer_src).read()
                else:
                    self.code = answer.text

    def get_score(self, student_answers):
        """
        student_answers is a dict with everything from request.POST, but with the first part
        of each key removed (the string before the first "_").
        """
        _ = self.capa_system.i18n.gettext

        log.debug('%s: student_answers=%s', str(self), student_answers)

        # ordered list of answer id's
        # sort the responses on the bases of the problem's position number
        # which can be found in the last place in the problem id. Then convert
        # this number into an int, so that we sort on ints instead of strings
        idset = sorted(self.answer_ids, key=lambda x: int(x.split("_")[-1]))
        try:
            # ordered list of answers
            submission = [student_answers[k] for k in idset]
        except Exception as err:
            msg = "[courseware.capa.responsetypes.customresponse] {message}\n idset = {idset}, error = {err}".format(
                message=_("error getting student answer from {student_answers}").format(
                    student_answers=student_answers,
                ),
                idset=idset,
                err=err
            )

            log.error(
                "[courseware.capa.responsetypes.customresponse] error getting"
                " student answer from %s"
                "\n idset = %s, error = %s",
                student_answers, idset, err
            )
            raise Exception(msg)  # lint-amnesty, pylint: disable=raise-missing-from

        # global variable in context which holds the Presentation MathML from dynamic math input
        # ordered list of dynamath responses
        dynamath = [student_answers.get(k + '_dynamath', None) for k in idset]

        # if there is only one box, and it's empty, then don't evaluate
        if len(idset) == 1 and not submission[0]:
            # default to no error message on empty answer (to be consistent with other
            # responsetypes) but allow author to still have the old behavior by setting
            # empty_answer_err attribute
            msg = (HTML('<span class="inline-error">{0}</span>').format(_('No answer entered!'))
                   if self.xml.get('empty_answer_err') else '')
            return CorrectMap(idset[0], 'incorrect', msg=msg)

        # NOTE: correct = 'unknown' could be dangerous. Inputtypes such as textline are
        # not expecting 'unknown's
        correct = ['unknown'] * len(idset)
        messages = [''] * len(idset)
        overall_message = ""

        # put these in the context of the check function evaluator
        # note that this doesn't help the "cfn" version - only the exec version
        self.context.update({
            # my ID
            'response_id': self.id,

            # expected answer (if given as attribute)
            'expect': self.expect,

            # ordered list of student answers from entry boxes in our subtree
            'submission': submission,

            # ordered list of ID's of all entry boxes in our subtree
            'idset': idset,

            # ordered list of all javascript inputs in our subtree
            'dynamath': dynamath,

            # dict of student's responses, with keys being entry box IDs
            'answers': student_answers,

            # the list to be filled in by the check function
            'correct': correct,

            # the list of messages to be filled in by the check function
            'messages': messages,

            # a message that applies to the entire response
            # instead of a particular input
            'overall_message': overall_message,

            # any options to be passed to the cfn
            'options': self.xml.get('options'),
            'testdat': 'hello world',
        })

        # Pass DEBUG to the check function.
        self.context['debug'] = self.capa_system.DEBUG

        # Run the check function
        self.execute_check_function(idset, submission)

        # build map giving "correct"ness of the answer(s)
        correct = self.context['correct']
        messages = self.context['messages']
        overall_message = self.clean_message_html(self.context['overall_message'])
        grade_decimals = self.context.get('grade_decimals')

        correct_map = CorrectMap()
        correct_map.set_overall_message(overall_message)

        for k in range(len(idset)):  # lint-amnesty, pylint: disable=consider-using-enumerate
            max_points = self.maxpoints[idset[k]]
            if grade_decimals:
                npoints = max_points * grade_decimals[k]
            else:
                if correct[k] == 'correct':
                    npoints = max_points
                elif correct[k] == 'partially-correct':
                    npoints = max_points * self.default_pc
                else:
                    npoints = 0
            correct_map.set(idset[k], correct[k], msg=messages[k],
                            npoints=npoints)
        return correct_map

    def execute_check_function(self, idset, submission):  # lint-amnesty, pylint: disable=missing-function-docstring, too-many-statements
        # exec the check function
        if isinstance(self.code, str):  # lint-amnesty, pylint: disable=too-many-nested-blocks
            try:
                safe_exec.safe_exec(
                    self.code,
                    self.context,
                    cache=self.capa_system.cache,
                    python_path=self.context['python_path'],
                    extra_files=self.context['extra_files'],
                    limit_overrides_context=get_course_id_from_capa_block(
                        self.capa_block
                    ),
                    slug=self.id,
                    random_seed=self.context['seed'],
                    unsafely=self.capa_system.can_execute_unsafe_code(),
                )
            except Exception as err:  # pylint: disable=broad-except
                self._handle_exec_exception(err)

        else:
            # self.code is not a string; it's a function we created earlier.

            # this is an interface to the Tutor2 check functions
            tutor_cfn = self.code
            answer_given = submission[0] if (len(idset) == 1) else submission
            kwnames = self.xml.get("cfn_extra_args", "").split()
            kwargs = {n: self.context.get(n) for n in kwnames}
            log.debug(" submission = %s", submission)
            try:
                ret = tutor_cfn(self.expect, answer_given, **kwargs)
            except Exception as err:  # pylint: disable=broad-except
                self._handle_exec_exception(err)
            log.debug(
                "[courseware.capa.responsetypes.customresponse.get_score] ret = %s",
                ret
            )
            if isinstance(ret, dict):
                # One kind of dictionary the check function can return has the
                # form {'ok': BOOLEAN or STRING, 'msg': STRING, 'grade_decimal' (optional): FLOAT (between 0.0 and 1.0)}
                # 'ok' will control the checkmark, while grade_decimal, if present, will scale
                # the score the student receives on the response.
                # If there are multiple inputs, they all get marked
                # to the same correct/incorrect value
                if 'ok' in ret:

                    # Returning any falsy value or the "false" string for "ok" gives incorrect.
                    # Returning any string that includes "partial" for "ok" gives partial credit.
                    # Returning any other truthy value for "ok" gives correct

                    ok_val = str(ret['ok']).lower().strip() if bool(ret['ok']) else 'false'

                    if ok_val == 'false':
                        correct = 'incorrect'
                    elif 'partial' in ok_val:
                        correct = 'partially-correct'
                    else:
                        correct = 'correct'
                    correct = [correct] * len(idset)   # All inputs share the same mark.

                    # old version, no partial credit:
                    # correct = ['correct' if ret['ok'] else 'incorrect'] * len(idset)

                    msg = ret.get('msg', None)
                    msg = self.clean_message_html(msg)

                    # If there is only one input, apply the message to that input
                    # Otherwise, apply the message to the whole problem
                    if len(idset) > 1:
                        self.context['overall_message'] = msg
                    else:
                        self.context['messages'][0] = msg

                    if 'grade_decimal' in ret:
                        decimal = float(ret['grade_decimal'])
                    else:
                        if correct[0] == 'correct':
                            decimal = 1.0
                        elif correct[0] == 'partially-correct':
                            decimal = self.default_pc
                        else:
                            decimal = 0.0
                    grade_decimals = [decimal] * len(idset)
                    self.context['grade_decimals'] = grade_decimals

                # Another kind of dictionary the check function can return has
                # the form:
                # { 'overall_message': STRING,
                #   'input_list': [
                #     {
                #         'ok': BOOLEAN or STRING,
                #         'msg': STRING,
                #         'grade_decimal' (optional): FLOAT (between 0.0 and 1.0)
                #     },
                #   ...
                #   ]
                # }
                # 'ok' will control the checkmark, while grade_decimal, if present, will scale
                # the score the student receives on the response.
                #
                # This allows the function to return an 'overall message'
                # that applies to the entire problem, as well as correct/incorrect
                # status, scaled grades, and messages for individual inputs
                elif 'input_list' in ret:
                    overall_message = ret.get('overall_message', '')
                    input_list = ret['input_list']

                    correct = []
                    messages = []
                    grade_decimals = []

                    # Returning any falsy value or the "false" string for "ok" gives incorrect.
                    # Returning any string that includes "partial" for "ok" gives partial credit.
                    # Returning any other truthy value for "ok" gives correct

                    for input_dict in input_list:
                        if str(input_dict['ok']).lower().strip() == "false" or not input_dict['ok']:
                            correct.append('incorrect')
                        elif 'partial' in str(input_dict['ok']).lower().strip():
                            correct.append('partially-correct')
                        else:
                            correct.append('correct')

                        # old version, no partial credit
                        # correct.append('correct'
                        #                if input_dict['ok'] else 'incorrect')

                        msg = (self.clean_message_html(input_dict['msg'])
                               if 'msg' in input_dict else None)
                        messages.append(msg)
                        if 'grade_decimal' in input_dict:
                            decimal = input_dict['grade_decimal']
                        else:
                            if str(input_dict['ok']).lower().strip() == 'true':
                                decimal = 1.0
                            elif 'partial' in str(input_dict['ok']).lower().strip():
                                decimal = self.default_pc
                            else:
                                decimal = 0.0
                        grade_decimals.append(decimal)

                    self.context['messages'] = messages
                    self.context['overall_message'] = overall_message
                    self.context['grade_decimals'] = grade_decimals

                # Otherwise, we do not recognize the dictionary
                # Raise an exception
                else:
                    log.error(traceback.format_exc())
                    _ = self.capa_system.i18n.gettext
                    raise ResponseError(
                        _("CustomResponse: check function returned an invalid dictionary!")
                    )

            else:

                # Returning any falsy value or the "false" string for "ok" gives incorrect.
                # Returning any string that includes "partial" for "ok" gives partial credit.
                # Returning any other truthy value for "ok" gives correct

                if str(ret).lower().strip() == "false" or not bool(ret):
                    correct = 'incorrect'
                elif 'partial' in str(ret).lower().strip():
                    correct = 'partially-correct'
                else:
                    correct = 'correct'
                correct = [correct] * len(idset)

                # old version, no partial credit:
                # correct = ['correct' if ret else 'incorrect'] * len(idset)

            self.context['correct'] = correct

    def clean_message_html(self, msg):  # lint-amnesty, pylint: disable=missing-function-docstring

        # If *msg* is an empty string, then the code below
        # will return "</html>".  To avoid this, we first check
        # that *msg* is a non-empty string.
        if msg:

            # When we parse *msg* using etree, there needs to be a root
            # element, so we wrap the *msg* text in <html> tags
            msg = HTML('<html>{msg}</html>').format(msg=HTML(msg))

            # Replace < characters
            msg = msg.replace('&#60;', '&lt;')

            # Use etree to prettify the HTML
            msg = etree.tostring(fromstring_bs(msg), pretty_print=True).decode('utf-8')

            msg = msg.replace('&#13;', '')

            # Remove the <html> tags we introduced earlier, so we're
            # left with just the prettified message markup
            msg = re.sub('(?ms)<html>(.*)</html>', '\\1', msg)

            # Strip leading and trailing whitespace
            return msg.strip()

        # If we start with an empty string, then return an empty string
        else:
            return ""

    def get_answers(self):
        """
        Give correct answer expected for this response.

        use default_answer_map from entry elements (eg textline),
        when this response has multiple entry objects.

        but for simplicity, if an "expect" attribute was given by the content author
        ie <customresponse expect="foo" ...> then that.
        """
        if len(self.answer_ids) > 1:
            return self.default_answer_map
        if self.expect:
            return {self.answer_ids[0]: self.expect}
        return self.default_answer_map

    def _handle_exec_exception(self, err):
        """
        Handle an exception raised during the execution of
        custom Python code.

        Raises a ResponseError
        """

        # Log the error if we are debugging
        msg = 'Error occurred while evaluating CustomResponse'
        log.warning(msg, exc_info=True)

        # Notify student with a student input error
        _, _, traceback_obj = sys.exc_info()
        raise ResponseError(str(err), traceback_obj)

#-----------------------------------------------------------------------------


@registry.register
class SymbolicResponse(CustomResponse):
    """
    Symbolic math response checking, using symmath library.
    """

    human_name = _('Symbolic Math Input')
    tags = ['symbolicresponse']
    max_inputfields = 1

    def setup_response(self):
        # Symbolic response always uses symmath_check()
        # If the XML did not specify this, then set it now
        # Otherwise, we get an error from the superclass
        self.xml.set('cfn', 'symmath_check')

        # Let CustomResponse do its setup
        super(SymbolicResponse, self).setup_response()  # lint-amnesty, pylint: disable=super-with-arguments

    def execute_check_function(self, idset, submission):
        # Since we have limited max_inputfields to 1,
        # we can assume that there is only one submission
        answer_given = submission[0]

        # symmath_check() passes the learner's answer, the dynamath MathML and
        # the author's "expect" to sympy's sympify(), which evaluates them as
        # Python. Run it in the sandbox, the way CustomResponse runs author
        # check functions, so nothing learner-controlled is evaluated in this
        # process and the sandbox's time and memory limits apply.
        code = textwrap.dedent("""\
            from symmath import symmath_check
            cfn_return = symmath_check(expect, ans, dynamath=dynamath, options=options, debug=debug)
        """)
        globals_dict = {
            'expect': self.expect,
            'ans': answer_given,
            'dynamath': self.context.get('dynamath'),
            'options': self.context.get('options'),
            'debug': self.context.get('debug'),
        }
        try:
            safe_exec.safe_exec(
                code,
                globals_dict,
                python_path=self.context['python_path'],
                extra_files=self.context['extra_files'],
                limit_overrides_context=get_course_id_from_capa_block(
                    self.capa_block
                ),
                slug=self.id,
                random_seed=self.context['seed'],
                unsafely=self.capa_system.can_execute_unsafe_code(),
            )
            ret = globals_dict['cfn_return']
        except Exception as err:  # pylint: disable=broad-except
            self._handle_exec_exception(err)
        self.context['messages'][0] = self.clean_message_html(ret['msg'])
        self.context['correct'] = ['correct' if ret['ok'] else 'incorrect'] * len(idset)

#-----------------------------------------------------------------------------

## ScoreMessage named tuple ##
## valid:       Flag indicating valid score_msg format (Boolean)
## correct:     Correctness of submission (Boolean)
## score:       Points to be assigned (numeric, can be float)
## msg:         Message from grader to display to student (string)

ScoreMessage = namedtuple('ScoreMessage', ['valid', 'correct', 'points', 'msg'])


@registry.register
class CodeResponse(LoncapaResponse):
    """
    Grade student code using an external queueing server, called 'xqueue'.

    Expects 'xqueue' dict in LoncapaSystem with the following properties that are
    needed by CodeResponse::

        capa_system.xqueue = object with properties:
            interface: XQueueInterface object.
            construct_callback: Per-StudentModule callback URL constructor,
                defaults to using 'score_update' as the correct dispatch (function).
            default_queuename: Default queue name to submit request (string).
        }

    External requests are only submitted for student submission grading, not
    for getting reference answers.

    """

    human_name = _('Code Input')
    tags = ['coderesponse']
    allowed_inputfields = ['textbox', 'filesubmission', 'matlabinput']
    max_inputfields = 1
    payload = None
    initial_display = None
    url = None
    answer = None
    queue_name = None

    def setup_response(self):
        """
        Configure CodeResponse from XML. Supports both CodeResponse and ExternalResponse XML

        TODO: Determines whether in synchronous or asynchronous (queued) mode
        """
        xml = self.xml
        # TODO: XML can override external resource (grader/queue) URL
        self.url = xml.get('url', None)

        # We do not support xqueue within Studio.
        if self.capa_system.xqueue is not None:
            default_queuename = self.capa_system.xqueue.default_queuename
        else:
            default_queuename = None
        self.queue_name = xml.get('queuename', default_queuename)

        # VS[compat]:
        # Check if XML uses the ExternalResponse format or the generic
        # CodeResponse format
        codeparam = self.xml.find('codeparam')
        assert codeparam is not None, "Unsupported old format! <coderesponse> without <codeparam>"
        self._parse_coderesponse_xml(codeparam)

    def _parse_coderesponse_xml(self, codeparam):
        """
        Parse the new CodeResponse XML format. When successful, sets:
            self.initial_display
            self.answer (an answer to display to the student in the LMS)
            self.payload
        """
        grader_payload = codeparam.find('grader_payload')
        grader_payload = grader_payload.text if grader_payload is not None else ''
        self.payload = {
            'grader_payload': grader_payload,
        }

        # matlab api key can be defined in course settings. if so, add it to the grader payload
        api_key = getattr(self.capa_system, 'matlab_api_key', None)
        if api_key and self.xml.find('matlabinput') is not None:
            self.payload['token'] = api_key
            self.payload['endpoint_version'] = "2"
            self.payload['requestor_id'] = self.capa_system.anonymous_student_id

        self.initial_display = find_with_default(
            codeparam, 'initial_display', '')
        _ = self.capa_system.i18n.gettext
        self.answer = find_with_default(codeparam, 'answer_display',
                                        _('No answer provided.'))

    def get_score(self, student_answers):
        _ = self.capa_system.i18n.gettext
        try:
            # Note that submission can be a file
            submission = student_answers[self.answer_id]
        except Exception as err:
            log.error(
                'Error in CodeResponse %s: cannot get student answer for %s;'
                ' student_answers=%s',
                err, self.answer_id, convert_files_to_filenames(student_answers)
            )
            raise Exception(err)  # lint-amnesty, pylint: disable=raise-missing-from

        # We do not support xqueue within Studio.
        if self.capa_system.xqueue is None:
            cmap = CorrectMap()
            cmap.set(self.answer_id, queuestate=None,
                     msg=_('Error: No grader has been set up for this problem.'))
            return cmap

        # Prepare xqueue request
        #------------------------------------------------------------

        qinterface = self.capa_system.xqueue.interface
        qtime = datetime.strftime(datetime.now(UTC), xqueue_interface.dateformat)

        anonymous_student_id = self.capa_system.anonymous_student_id

        # Generate header
        queuekey = xqueue_interface.make_hashkey(
            str(self.capa_system.seed) + qtime + anonymous_student_id + self.answer_id
        )
        callback_url = self.capa_system.xqueue.construct_callback()
        xheader = xqueue_interface.make_xheader(
            lms_callback_url=callback_url,
            lms_key=queuekey,
            queue_name=self.queue_name
        )

        # Generate body
        if is_list_of_files(submission):
            # TODO: Get S3 pointer from the Queue
            self.context.update({'submission': ''})
        else:
            self.context.update({'submission': submission})

        contents = self.payload.copy()

        # Metadata related to the student submission revealed to the external
        # grader
        student_info = {
            'anonymous_student_id': anonymous_student_id,
            'submission_time': qtime,
            'random_seed': self.context['seed'],
        }
        contents.update({'student_info': json.dumps(student_info)})

        # Submit request. When successful, 'msg' is the prior length of the
        # queue

        if is_list_of_files(submission):
            # TODO: Is there any information we want to send here?
            contents.update({'student_response': ''})
            (error, msg) = qinterface.send_to_queue(header=xheader,
                                                    body=json.dumps(contents),
                                                    files_to_upload=submission)
        else:
            contents.update({'student_response': submission})
            (error, msg) = qinterface.send_to_queue(header=xheader,
                                                    body=json.dumps(contents))

        # State associated with the queueing request
        queuestate = {'key': queuekey,
                      'time': qtime, }

        cmap = CorrectMap()
        if error:
            _ = self.capa_system.i18n.gettext
            error_msg = _('Unable to deliver your submission to grader (Reason: {error_msg}).'
                          ' Please try again later.').format(error_msg=msg)
            cmap.set(self.answer_id, queuestate=None, msg=error_msg)
        else:
            # Queueing mechanism flags:
            #   1) Backend: Non-null CorrectMap['queuestate'] indicates that
            #      the problem has been queued
            #   2) Frontend: correctness='incomplete' eventually trickles down
            #      through inputtypes.textbox and .filesubmission to inform the
            #      browser to poll the LMS
            cmap.set(self.answer_id, queuestate=queuestate,
                     correctness='incomplete', msg=msg)

        return cmap

    def update_score(self, score_msg, oldcmap, queuekey):
        """Updates the user's score based on the returned message from the grader."""
        (valid_score_msg, correct, points, msg) = self._parse_score_msg(score_msg)

        _ = self.capa_system.i18n.gettext

        if not valid_score_msg:
            # Translators: 'grader' refers to the edX automatic code grader.
            error_msg = _('Invalid grader reply. Please contact the course staff.')
            oldcmap.set(self.answer_id, msg=error_msg)
            return oldcmap

        correctness = 'correct' if correct else 'incorrect'

        # TODO: Find out how this is used elsewhere, if any
        self.context['correct'] = correctness

        # Replace 'oldcmap' with new grading results if queuekey matches.  If queuekey
        # does not match, we keep waiting for the score_msg whose key actually
        # matches
        if oldcmap.is_right_queuekey(self.answer_id, queuekey):
            # Sanity check on returned points
            if points < 0:  # lint-amnesty, pylint: disable=consider-using-max-builtin
                points = 0
            # Queuestate is consumed
            oldcmap.set(
                self.answer_id, npoints=points, correctness=correctness,
                msg=msg.replace('&nbsp;', '&#160;'), queuestate=None)
        else:
            log.debug(
                'CodeResponse: queuekey %s does not match for answer_id=%s.',
                queuekey,
                self.answer_id
            )

        return oldcmap

    def get_answers(self):
        anshtml = HTML('<span class="code-answer"><pre><code>{}</code></pre></span>').format(self.answer)
        return {self.answer_id: anshtml}

    def get_initial_display(self):
        """
        The course author can specify an initial display
        to be displayed the code response box.
        """
        return {self.answer_id: self.initial_display}

    def _parse_score_msg(self, score_msg):
        """
         Grader reply is a JSON-dump of the following dict
           { 'correct': True/False,
             'score': Numeric value (floating point is okay) to assign to answer
             'msg': grader_msg }

        Returns (valid_score_msg, correct, score, msg):
            valid_score_msg: Flag indicating valid score_msg format (Boolean)
            correct:         Correctness of submission (Boolean)
            score:           Points to be assigned (numeric, can be float)
            msg:             Message from grader to display to student (string)
        """
        fail = (False, False, 0, '')
        try:
            score_result = json.loads(score_msg)
        except (TypeError, ValueError):
            log.error("External grader message should be a JSON-serialized dict."
                      " Received score_msg = %s", score_msg)
            return fail
        if not isinstance(score_result, dict):
            log.error("External grader message should be a JSON-serialized dict."
                      " Received score_result = %s", score_result)
            return fail
        for tag in ['correct', 'score', 'msg']:
            if tag not in score_result:
                log.error("External grader message is missing one or more required"
                          " tags: 'correct', 'score', 'msg'")
                return fail

        # Next, we need to check that the contents of the external grader message is safe for the LMS.
        # 1) Make sure that the message is valid XML (proper opening/closing tags)
        # 2) If it is not valid XML, make sure it is valid HTML.
        #    Note: html5lib parser will try to repair any broken HTML
        #    For example: <aaa></bbb> will become <aaa/>.
        msg = score_result['msg']

        try:
            etree.fromstring(msg)
        except etree.XMLSyntaxError as _err:
            # If `html` contains attrs with no values, like `controls` in <audio controls src='smth'/>,
            # XML parser will raise exception, so wee fallback to html5parser,
            # which will set empty "" values for such attrs.
            try:
                parsed = html5lib.parseFragment(msg, treebuilder='lxml', namespaceHTMLElements=False)
            except ValueError:
                # the parsed message might contain strings that are not
                # xml compatible, in which case, throw the error message
                parsed = False

            if not parsed:
                log.error(
                    "Unable to parse external grader message as valid"
                    " XML: score_msg['msg']=%s",
                    msg,
                )
                return fail

        return (True, score_result['correct'], score_result['score'], msg)


#-----------------------------------------------------------------------------


@registry.register
class ExternalResponse(LoncapaResponse):
    """
    Grade the students input using an external server.

    Typically used by coding problems.

    """

    human_name = _('External Grader')
    tags = ['externalresponse']
    allowed_inputfields = ['textline', 'textbox']
    awdmap = {
        'EXACT_ANS': 'correct',         # TODO: handle other loncapa responses
        'WRONG_FORMAT': 'incorrect',
    }

    def __init__(self, *args, **kwargs):
        self.url = ''
        self.tests = []
        self.code = ''
        super(ExternalResponse, self).__init__(*args, **kwargs)  # lint-amnesty, pylint: disable=super-with-arguments

    def setup_response(self):
        xml = self.xml
        # FIXME - hardcoded URL
        self.url = xml.get('url') or "http://qisx.mit.edu:8889/pyloncapa"

        answer = xml.find('answer')
        if answer is not None:
            answer_src = answer.get('src')
            if answer_src is not None:
                # TODO: this code seems not to be used any more since self.capa_system.filesystem doesn't exist.
                self.code = self.capa_system.filesystem.open('src/' + answer_src).read()
            else:
                self.code = answer.text
        else:
            # no <answer> stanza; get code from <script>
            self.code = self.context['script_code']
            if not self.code:
                msg = '%s: Missing answer script code for externalresponse' % str(
                    self)
                msg += "\nSee XML source line %s" % getattr(
                    self.xml, 'sourceline', '[unavailable]')
                raise LoncapaProblemError(msg)

        self.tests = xml.get('tests')

    def do_external_request(self, cmd, extra_payload):
        """
        Perform HTTP request / post to external server.

        cmd = remote command to perform (str)
        extra_payload = dict of extra stuff to post.

        Return XML tree of response (from response body)
        """
        xmlstr = etree.tostring(self.xml, pretty_print=True)
        payload = {
            'xml': xmlstr,
            'edX_cmd': cmd,
            'edX_tests': self.tests,
            'processor': self.code,
        }
        payload.update(extra_payload)

        try:
            # call external server. TODO: synchronous call, can block for a
            # long time
            req = requests.post(self.url, data=payload)
        except Exception as err:
            msg = 'Error {0} - cannot connect to external server url={1}'.format(err, self.url)
            log.error(msg)
            raise Exception(msg)  # lint-amnesty, pylint: disable=raise-missing-from

        if self.capa_system.DEBUG:
            log.info('response = %s', req.text)

        if (not req.text) or (not req.text.strip()):
            raise Exception(
                'Error: no response from external server url=%s' % self.url)

        try:
            # response is XML; parse it
            rxml = etree.fromstring(req.text)
        except Exception as err:
            msg = 'Error {0} - cannot parse response from external server req.text={1}'.format(err, req.text)
            log.error(msg)
            raise Exception(msg)  # lint-amnesty, pylint: disable=raise-missing-from

        return rxml

    def get_score(self, student_answers):
        idset = sorted(self.answer_ids)
        cmap = CorrectMap()
        try:
            submission = [student_answers[k] for k in idset]
        except Exception as err:
            log.error(
                'Error %s: cannot get student answer for %s; student_answers=%s',
                err,
                self.answer_ids,
                student_answers
            )
            raise Exception(err)  # lint-amnesty, pylint: disable=raise-missing-from

        self.context.update({'submission': submission})

        extra_payload = {'edX_student_response': json.dumps(submission)}

        try:
            rxml = self.do_external_request('get_score', extra_payload)
        except Exception as err:  # pylint: disable=broad-except
            log.error('Error %s', err)
            if self.capa_system.DEBUG:
                cmap.set_dict(dict(list(zip(sorted(
                    self.answer_ids), ['incorrect'] * len(idset)))))
                cmap.set_property(
                    self.answer_ids[0], 'msg',
                    Text('<span class="inline-error">{}</span>').format(str(err))
                )
                return cmap

        awd = rxml.find('awarddetail').text

        self.context['correct'] = ['correct']
        if awd in self.awdmap:
            self.context['correct'][0] = self.awdmap[awd]

        # create CorrectMap
        for key in idset:
            idx = idset.index(key)
            msg = rxml.find('message').text.replace(
                '&nbsp;', '&#160;') if idx == 0 else None
            cmap.set(key, self.context['correct'][idx], msg=msg)

        return cmap

    def get_answers(self):
        """
        Use external server to get expected answers
        """
        try:
            rxml = self.do_external_request('get_answers', {})
            exans = json.loads(rxml.find('expected').text)
        except Exception as err:  # pylint: disable=broad-except
            log.error('Error %s', err)
            if self.capa_system.DEBUG:
                msg = HTML('<span class="inline-error">{}</span>').format(err)
                exans = [''] * len(self.answer_ids)
                exans[0] = msg

        if not len(exans) == len(self.answer_ids):
            log.error('Expected %s answers from external server, only got %s!',
                      len(self.answer_ids), len(exans))
            raise Exception('Short response from external server')
        return dict(list(zip(self.answer_ids, exans)))


#-----------------------------------------------------------------------------

@registry.register
class FormulaResponse(LoncapaResponse):
    """
    Checking of symbolic math response using numerical sampling.
    """

    human_name = _('Math Expression Input')
    tags = ['formularesponse']
    hint_tag = 'formulahint'
    allowed_inputfields = ['textline', 'formulaequationinput']
    required_attributes = ['answer', 'samples']
    max_inputfields = 1
    multi_device_support = True

    def __init__(self, *args, **kwargs):
        self.correct_answer = ''
        self.samples = ''
        self.tolerance = default_tolerance
        self.case_sensitive = False
        super(FormulaResponse, self).__init__(*args, **kwargs)  # lint-amnesty, pylint: disable=super-with-arguments

    def setup_response(self):
        xml = self.xml
        context = self.context
        self.correct_answer = contextualize_text(xml.get('answer'), context)
        self.samples = contextualize_text(xml.get('samples'), context)

        # Find the tolerance
        tolerance_xml = xml.xpath(
            '//*[@id=$id]//responseparam[@type="tolerance"]/@default',
            id=xml.get('id')
        )
        if tolerance_xml:  # If it isn't an empty list...
            self.tolerance = contextualize_text(tolerance_xml[0].strip(), context)

        types = xml.get('type')
        if types is None:
            typeslist = []
        else:
            typeslist = types.split(',')
        if 'ci' in typeslist:
            # Case insensitive
            self.case_sensitive = False
        elif 'cs' in typeslist:
            # Case sensitive
            self.case_sensitive = True
        else:
            # Default
            self.case_sensitive = False

    def get_score(self, student_answers):
        given = student_answers[self.answer_id]
        correctness = self.check_formula(
            self.correct_answer,
            given,
            self.samples
        )
        return CorrectMap(self.answer_id, correctness)

    def tupleize_answers(self, answer, var_dict_list):
        """
        Takes in an answer and a list of dictionaries mapping variables to values.
        Each dictionary represents a test case for the answer.
        Returns a tuple of formula evaluation results.
        """
        _ = self.capa_system.i18n.gettext

        out = []
        for var_dict in var_dict_list:
            try:
                out.append(evaluator(
                    var_dict,
                    {},
                    answer,
                    case_sensitive=self.case_sensitive,
                ))
            except UndefinedVariable as err:
                log.debug(
                    'formularesponse: undefined variable in formula=%s',
                    html.escape(answer)
                )
                raise StudentInputError(  # lint-amnesty, pylint: disable=raise-missing-from
                    err.args[0]
                )
            except UnmatchedParenthesis as err:
                log.debug(
                    'formularesponse: unmatched parenthesis in formula=%s',
                    html.escape(answer)
                )
                raise StudentInputError(  # lint-amnesty, pylint: disable=raise-missing-from
                    err.args[0]
                )
            except ValueError as err:
                if 'factorial' in str(err):
                    # This is thrown when fact() or factorial() is used in a formularesponse answer
                    #   that tests on negative and/or non-integer inputs
                    # str(err) will be: `factorial() only accepts integral values` or
                    # `factorial() not defined for negative values`
                    log.debug(
                        ('formularesponse: factorial function used in response '
                         'that tests negative and/or non-integer inputs. '
                         'Provided answer was: %s'),
                        html.escape(answer)
                    )
                    raise StudentInputError(  # lint-amnesty, pylint: disable=raise-missing-from
                        _("Factorial function not permitted in answer "
                          "for this problem. Provided answer was: "
                          "{bad_input}").format(bad_input=html.escape(answer))
                    )
                # If non-factorial related ValueError thrown, handle it the same as any other Exception
                log.debug('formularesponse: error %s in formula', err)
                raise StudentInputError(  # lint-amnesty, pylint: disable=raise-missing-from
                    _("Invalid input: Could not parse '{bad_input}' as a formula.").format(
                        bad_input=html.escape(answer)
                    )
                )
            except Exception as err:
                # traceback.print_exc()
                log.debug('formularesponse: error %s in formula', err)
                raise StudentInputError(  # lint-amnesty, pylint: disable=raise-missing-from
                    _("Invalid input: Could not parse '{bad_input}' as a formula").format(
                        bad_input=html.escape(answer)
                    )
                )
        return out

    def randomize_variables(self, samples):
        """
        Returns a list of dictionaries mapping variables to random values in range,
        as expected by tupleize_answers.
        """
        variables = samples.split('@')[0].split(',')
        numsamples = int(samples.split('@')[1].split('#')[1])
        sranges = list(zip(*[list(map(float, x.split(","))) for x in samples.split('@')[1].split('#')[0].split(':')]))
        ranges = dict(list(zip(variables, sranges)))

        out = []
        for _ in range(numsamples):
            var_dict = {}
            # ranges give numerical ranges for testing
            for var in ranges:
                # TODO: allow specified ranges (i.e. integers and complex numbers) for random variables
                value = random.uniform(*ranges[var])
                var_dict[str(var)] = value
            out.append(var_dict)
        return out

    def check_formula(self, expected, given, samples):
        """
        Given an expected answer string, a given (student-produced) answer
        string, and a samples string, return whether the given answer is
        "correct" or "incorrect".
        """
        var_dict_list = self.randomize_variables(samples)
        student_result = self.tupleize_answers(given, var_dict_list)
        instructor_result = self.tupleize_answers(expected, var_dict_list)

        correct = all(compare_with_tolerance(student, instructor, self.tolerance)
                      for student, instructor in zip(student_result, instructor_result))
        if correct:
            return "correct"
        else:
            return "incorrect"

    def compare_answer(self, ans1, ans2):
        """
        An external interface for comparing whether a and b are equal.
        """
        internal_result = self.check_formula(ans1, ans2, self.samples)
        return internal_result == "correct"

    def validate_answer(self, answer):
        """
        Returns whether this answer is in a valid form.
        """
        var_dict_list = self.randomize_variables(self.samples)
        try:
            self.tupleize_answers(answer, var_dict_list)
            return True
        except StudentInputError:
            return False

    def strip_dict(self, inp_d):
        """
        Takes a dict. Returns an identical dict, with all non-word
        keys and all non-numeric values stripped out. All values also
        converted to float. Used so we can safely use Python contexts.
        """
        inp_d = dict([(k, numpy.complex(inp_d[k]))  # lint-amnesty, pylint: disable=consider-using-dict-comprehension
                      for k in inp_d if isinstance(k, str) and
                      k.isalnum() and
                      isinstance(inp_d[k], numbers.Number)])
        return inp_d

    def check_hint_condition(self, hxml_set, student_answers):
        given = student_answers[self.answer_id]
        hints_to_show = []
        for hxml in hxml_set:
            samples = hxml.get('samples')
            name = hxml.get('name')
            correct_answer = contextualize_text(
                hxml.get('answer'), self.context)
            # pylint: disable=broad-except
            try:
                correctness = self.check_formula(
                    correct_answer,
                    given,
                    samples
                )
            except Exception:
                correctness = 'incorrect'
            if correctness == 'correct':
                hints_to_show.append(name)
        log.debug('hints_to_show = %s', hints_to_show)
        return hints_to_show

    def get_answers(self):
        return {self.answer_id: self.correct_answer}

#-----------------------------------------------------------------------------


@registry.register
class SchematicResponse(LoncapaResponse):
    """
    Circuit schematic response type.
    """
    human_name = _('Circuit Schematic Builder')
    tags = ['schematicresponse']
    allowed_inputfields = ['schematic']

    def __init__(self, *args, **kwargs):
        self.code = ''
        super(SchematicResponse, self).__init__(*args, **kwargs)  # lint-amnesty, pylint: disable=super-with-arguments

    def setup_response(self):
        xml = self.xml
        answer = xml.xpath('//*[@id=$id]//answer', id=xml.get('id'))[0]
        answer_src = answer.get('src')
        if answer_src is not None:
            # Untested; never used
            self.code = self.capa_system.resources_fs.open('src/' + answer_src).read()
        else:
            self.code = answer.text

    def get_score(self, student_answers):
        submission = [
            json.loads(student_answers[k]) for k in sorted(self.answer_ids)
        ]
        self.context.update({'submission': submission})
        try:
            safe_exec.safe_exec(
                self.code,
                self.context,
                cache=self.capa_system.cache,
                python_path=self.context['python_path'],
                extra_files=self.context['extra_files'],
                limit_overrides_context=get_course_id_from_capa_block(
                    self.capa_block
                ),
                slug=self.id,
                random_seed=self.context['seed'],
                unsafely=self.capa_system.can_execute_unsafe_code(),
            )
        except Exception as err:
            _ = self.capa_system.i18n.gettext
            # Translators: 'SchematicResponse' is a problem type and should not be translated.
            msg = _('Error in evaluating SchematicResponse. The error was: {error_msg}').format(error_msg=err)
            raise ResponseError(msg)  # lint-amnesty, pylint: disable=raise-missing-from
        cmap = CorrectMap()
        cmap.set_dict(dict(list(zip(sorted(self.answer_ids), self.context['correct']))))
        return cmap

    def get_answers(self):
        # use answers provided in input elements
        return self.default_answer_map

#-----------------------------------------------------------------------------


@registry.register
class ImageResponse(LoncapaResponse):
    """
    Handle student response for image input: the input is a click on an image,
    which produces an [x,y] coordinate pair.  The click is correct if it falls
    within a region specified.  This region is a union of rectangles.

    Lon-CAPA requires that each <imageresponse> has a <foilgroup> inside it.
    That doesn't make sense to me (Ike).  Instead, let's have it such that
    <imageresponse> should contain one or more <imageinput> stanzas.
    Each <imageinput> should specify a rectangle(s) or region(s), given as an
    attribute, defining the correct answer.

    <imageinput src="/static/images/Lecture2/S2_p04.png" width="811" height="610"
    rectangle="(10,10)-(20,30);(12,12)-(40,60)"
    regions="[[[10,10], [20,30], [40, 10]], [[100,100], [120,130], [110,150]]]"/>

    Regions is list of lists [region1, region2, region3, ...] where regionN
    is disordered list of points: [[1,1], [100,100], [50,50], [20, 70]].

    If there is only one region in the list, simpler notation can be used:
    regions="[[10,10], [30,30], [10, 30], [30, 10]]" (without explicitly
        setting outer list)

    Returns:
        True, if click is inside any region or rectangle. Otherwise False.
    """

    human_name = _('Image Mapped Input')
    tags = ['imageresponse']
    allowed_inputfields = ['imageinput']

    def __init__(self, *args, **kwargs):
        self.ielements = []
        super(ImageResponse, self).__init__(*args, **kwargs)  # lint-amnesty, pylint: disable=super-with-arguments

    def setup_response(self):
        self.ielements = self.inputfields
        self.answer_ids = [ie.get('id') for ie in self.ielements]

    def get_score(self, student_answers):
        _ = self.capa_system.i18n.gettext
        correct_map = CorrectMap()
        expectedset = self.get_mapped_answers()
        for aid in self.answer_ids:  # loop through IDs of <imageinput>
            # Fields in our stanza
            given = student_answers[aid]  # This should be a string of the form '[x,y]'
            correct_map.set(aid, 'incorrect')
            if not given:  # No answer to parse. Mark as incorrect and move on
                continue
            # Parse given answer
            acoords = re.match(r'\[([0-9]+),([0-9]+)]', given.strip().replace(' ', ''))
            if not acoords:
                msg = _('error grading {image_input_id} (input={user_input})').format(
                    image_input_id=aid,
                    user_input=given
                )
                raise Exception('[capamodule.capa.responsetypes.imageinput] ' + msg)

            (ans_x, ans_y) = [int(x) for x in acoords.groups()]

            rectangles, regions = expectedset
            if rectangles[aid]:  # Rectangles part - for backward compatibility
                # Check whether given point lies in any of the solution
                # rectangles
                solution_rectangles = rectangles[aid].split(';')
                for solution_rectangle in solution_rectangles:
                    # parse expected answer
                    # TODO: Compile regexp on file load
                    sr_coords = re.match(
                        r'[\(\[]([0-9]+),([0-9]+)[\)\]]-[\(\[]([0-9]+),([0-9]+)[\)\]]',
                        solution_rectangle.strip().replace(' ', ''))
                    if not sr_coords:
                        # Translators: {sr_coords} are the coordinates of a rectangle
                        msg = _('Error in problem specification! Cannot parse rectangle in {sr_coords}').format(
                            sr_coords=etree.tostring(self.ielements[aid], pretty_print=True)
                        )
                        raise Exception('[capamodule.capa.responsetypes.imageinput] ' + msg)

                    (llx, lly, urx, ury) = [int(x) for x in sr_coords.groups()]

                    # answer is correct if (x,y) is within the specified
                    # rectangle
                    if (llx <= ans_x <= urx) and (lly <= ans_y <= ury):
                        correct_map.set(aid, 'correct')
                        break
            if correct_map[aid]['correctness'] != 'correct' and regions[aid]:
                parsed_region = json.loads(regions[aid])
                if parsed_region:
                    if not isinstance(parsed_region[0][0], list):
                        # we have [[1,2],[3,4],[5,6]] - single region
                        # instead of [[[1,2],[3,4],[5,6], [[1,2],[3,4],[5,6]]]
                        # or [[[1,2],[3,4],[5,6]]] - multiple regions syntax
                        parsed_region = [parsed_region]
                    for region in parsed_region:
                        polygon = MultiPoint(region).convex_hull
                        if (polygon.geom_type == 'Polygon' and
                                polygon.contains(Point(ans_x, ans_y))):
                            correct_map.set(aid, 'correct')
                            break
        return correct_map

    def get_mapped_answers(self):
        """
        Returns the internal representation of the answers

        Input:
            None
        Returns:
            tuple (dict, dict) -
                rectangles (dict) - a map of inputs to the defined rectangle for that input
                regions (dict) - a map of inputs to the defined region for that input
        """
        answers = (
            dict([(ie.get('id'), ie.get(  # lint-amnesty, pylint: disable=consider-using-dict-comprehension
                'rectangle')) for ie in self.ielements]),
            dict([(ie.get('id'), ie.get('regions')) for ie in self.ielements]))  # lint-amnesty, pylint: disable=consider-using-dict-comprehension
        return answers

    def get_answers(self):
        """
        Returns the external representation of the answers

        Input:
            None
        Returns:
            dict (str, (str, str)) - a map of inputs to a tuple of their rectangle
                and their regions
        """
        answers = {}
        for ielt in self.ielements:
            ie_id = ielt.get('id')
            answers[ie_id] = {'rectangle': ielt.get('rectangle'), 'regions': ielt.get('regions')}

        return answers

#-----------------------------------------------------------------------------


@registry.register
class AnnotationResponse(LoncapaResponse):
    """
    Checking of annotation responses.

    The response contains both a comment (student commentary) and an option (student tag).
    Only the tag is currently graded. Answers may be incorrect, partially correct, or correct.
    """
    human_name = _('Annotation Input')
    tags = ['annotationresponse']
    allowed_inputfields = ['annotationinput']
    max_inputfields = 1
    default_scoring = {'incorrect': 0, 'partially-correct': 1, 'correct': 2}

    def __init__(self, *args, **kwargs):
        self.scoring_map = {}
        self.answer_map = {}
        super(AnnotationResponse, self).__init__(*args, **kwargs)  # lint-amnesty, pylint: disable=super-with-arguments

    def setup_response(self):
        self.scoring_map = self._get_scoring_map()
        self.answer_map = self._get_answer_map()
        self.maxpoints = self._get_max_points()

    def get_score(self, student_answers):
        """
        Returns a CorrectMap for the student answer, which may include
        partially correct answers.
        """
        student_answer = student_answers[self.answer_id]
        student_option = self._get_submitted_option_id(student_answer)

        scoring = self.scoring_map[self.answer_id]
        is_valid = student_option is not None and student_option in list(scoring.keys(
        ))

        (correctness, points) = ('incorrect', None)
        if is_valid:
            correctness = scoring[student_option]['correctness']
            points = scoring[student_option]['points']

        return CorrectMap(self.answer_id, correctness=correctness, npoints=points)

    def get_answers(self):
        return self.answer_map

    def _get_scoring_map(self):
        """Returns a dict of option->scoring for each input."""
        scoring = self.default_scoring
        choices = dict([(choice, choice) for choice in scoring])  # lint-amnesty, pylint: disable=consider-using-dict-comprehension
        scoring_map = {}

        for inputfield in self.inputfields:
            option_scoring = dict([(  # lint-amnesty, pylint: disable=consider-using-dict-comprehension
                option['id'],
                {
                    'correctness': choices.get(option['choice']),
                    'points': scoring.get(option['choice'])
                }
            ) for option in self._find_options(inputfield)])

            scoring_map[inputfield.get('id')] = option_scoring

        return scoring_map

    def _get_answer_map(self):
        """Returns a dict of answers for each input."""
        answer_map = {}
        for inputfield in self.inputfields:
            correct_option = self._find_option_with_choice(
                inputfield, 'correct')
            if correct_option is not None:
                input_id = inputfield.get('id')
                answer_map[input_id] = correct_option.get('description')
        return answer_map

    def _get_max_points(self):
        """Returns a dict of the max points for each input: input id -> maxpoints."""
        scoring = self.default_scoring
        correct_points = scoring.get('correct')
        return dict([(inputfield.get('id'), correct_points) for inputfield in self.inputfields])  # lint-amnesty, pylint: disable=consider-using-dict-comprehension

    def _find_options(self, inputfield):
        """Returns an array of dicts where each dict represents an option. """
        elements = inputfield.findall('./options/option')
        return [
            {
                'id': index,
                'description': option.text,
                'choice': option.get('choice')
            } for (index, option) in enumerate(elements)
        ]

    def _find_option_with_choice(self, inputfield, choice):
        """Returns the option with the given choice value, otherwise None. """
        for option in self._find_options(inputfield):
            if option['choice'] == choice:
                return option

    def _unpack(self, json_value):
        """Unpacks a student response value submitted as JSON."""
        json_d = json.loads(json_value)
        if not isinstance(json_d, dict):
            json_d = {}

        comment_value = json_d.get('comment', '')
        if not isinstance(json_d, str):
            comment_value = ''

        options_value = json_d.get('options', [])
        if not isinstance(options_value, list):
            options_value = []

        return {
            'options_value': options_value,
            'comment_value': comment_value
        }

    def _get_submitted_option_id(self, student_answer):
        """Return the single option that was selected, otherwise None."""
        submitted = self._unpack(student_answer)
        option_ids = submitted['options_value']
        if len(option_ids) == 1:
            return option_ids[0]
        return None


@registry.register
class ChoiceTextResponse(LoncapaResponse):
    """
    Allows for multiple choice responses with text inputs
    Desired semantics match those of NumericalResponse and
    ChoiceResponse.
    """

    human_name = _('Checkboxes With Text Input')
    tags = ['choicetextresponse']
    max_inputfields = 1
    allowed_inputfields = [
        'choicetextgroup',
        'checkboxtextgroup',
        'radiotextgroup',
    ]

    def __init__(self, *args, **kwargs):
        self.correct_inputs = {}
        self.answer_values = {}
        self.correct_choices = {}
        super(ChoiceTextResponse, self).__init__(*args, **kwargs)  # lint-amnesty, pylint: disable=super-with-arguments

    def setup_response(self):
        """
        Sets up three dictionaries for use later:
        `correct_choices`: These are the correct binary choices(radio/checkbox)
        `correct_inputs`: These are the numerical/string answers for required
        inputs.
        `answer_values`: This is a dict, keyed by the name of the binary choice
            which contains the correct answers for the text inputs separated by
            commas e.g. "1, 0.5"

        `correct_choices` and `correct_inputs` are used for grading the problem
        and `answer_values` is used for displaying correct answers.

        """
        _ = self.capa_system.i18n.gettext
        context = self.context
        self.answer_values = {self.answer_id: []}
        self.assign_choice_names()
        correct_xml = self.xml.xpath('//*[@id=$id]//choice[@correct="true"]',
                                     id=self.xml.get('id'))

        for node in correct_xml:
            # For each correct choice, set the `parent_name` to the
            # current choice's name
            parent_name = node.get('name')
            # Add the name of the correct binary choice to the
            # correct choices list as a key. The value is not important.
            self.correct_choices[parent_name] = {'answer': ''}
            # Add the name of the parent to the list of correct answers
            self.answer_values[self.answer_id].append(parent_name)
            answer_list = []
            # Loop over <numtolerance_input> elements inside of the correct choices
            for child in node:
                answer = child.get('answer', None)
                if not answer:
                    # If the question creator does not specify an answer for a
                    # <numtolerance_input> inside of a correct choice, raise an error
                    raise LoncapaProblemError(
                        _("Answer not provided for {input_type}").format(input_type="numtolerance_input")
                    )
                # Contextualize the answer to allow script generated answers.
                answer = contextualize_text(answer, context)
                input_name = child.get('name')
                # Contextualize the tolerance to value.
                tolerance = contextualize_text(
                    child.get('tolerance', default_tolerance),
                    context
                )
                # Add the answer and tolerance information for the current
                # numtolerance_input to `correct_inputs`
                self.correct_inputs[input_name] = {
                    'answer': answer,
                    'tolerance': tolerance
                }
                # Add the correct answer for this input to the list for show
                answer_list.append(answer)
            # Turn the list of numtolerance_input answers into a comma separated string.
            self.answer_values[parent_name] = ', '.join(answer_list)
        # Turn correct choices into a set. Allows faster grading.
        self.correct_choices = set(self.correct_choices.keys())

    def assign_choice_names(self):
        """
        Initialize name attributes in <choice> and <numtolerance_input> tags
        for this response.

        Example:
        Assuming for simplicity that `self.answer_id` = '1_2_1'

        Before the function is called `self.xml` =
        <radiotextgroup>
            <choice correct = "true">
                The number
                    <numtolerance_input answer="5"/>
                Is the mean of the list.
            </choice>
            <choice correct = "false">
                False demonstration choice
            </choice>
        </radiotextgroup>

        After this is called the choices and numtolerance_inputs will have a name
        attribute initialized and self.xml will be:

        <radiotextgroup>
        <choice correct = "true" name ="1_2_1_choiceinput_0bc">
            The number
                <numtolerance_input name = "1_2_1_choiceinput0_numtolerance_input_0"
                 answer="5"/>
            Is the mean of the list.
        </choice>
        <choice correct = "false" name = "1_2_1_choiceinput_1bc>
            False demonstration choice
        </choice>
        </radiotextgroup>
        """

        choices = self.xml.xpath('//*[@id=$id]//choice', id=self.xml.get('id'))
        for index, choice in enumerate(choices):
            # Set the name attribute for <choices>
            # "bc" is appended at the end to indicate that this is a
            # binary choice as opposed to a numtolerance_input, this convention
            # is used when grading the problem
            choice.set(
                "name",
                self.answer_id + "_choiceinput_" + str(index) + "bc"
            )
            # Set Name attributes for <numtolerance_input> elements
            # Look for all <numtolerance_inputs> inside this choice.
            numtolerance_inputs = choice.findall('numtolerance_input')
            # Look for all <decoy_input> inside this choice
            decoys = choice.findall('decoy_input')
            # <decoy_input> would only be used in choices which do not contain
            # <numtolerance_input>
            inputs = numtolerance_inputs if numtolerance_inputs else decoys
            # Give each input inside of the choice a name combining
            # The ordinality of the choice, and the ordinality of the input
            # within that choice e.g. 1_2_1_choiceinput_0_numtolerance_input_1
            for ind, child in enumerate(inputs):
                child.set(
                    "name",
                    self.answer_id + "_choiceinput_" + str(index) +
                    "_numtolerance_input_" + str(ind)
                )

    def get_score(self, student_answers):
        """
        Returns a `CorrectMap` showing whether `student_answers` are correct.

        `student_answers` contains keys for binary inputs(radiobutton,
        checkbox) and numerical inputs. Keys ending with 'bc' are binary
        choice inputs otherwise they are text fields.

        This method first separates the two
        types of answers and then grades them in separate methods.

        The student is only correct if they have both the binary inputs and
        numerical inputs correct.
        """
        answer_dict = student_answers.get(self.answer_id, "")
        binary_choices, numtolerance_inputs = self._split_answers_dict(answer_dict)
        # Check the binary choices first.
        choices_correct = self._check_student_choices(binary_choices)
        inputs_correct = self._check_student_inputs(numtolerance_inputs)
        # Only return correct if the student got both the binary
        # and numtolerance_inputs are correct
        correct = choices_correct and inputs_correct

        return CorrectMap(
            self.answer_id,
            'correct' if correct else 'incorrect'
        )

    def get_answers(self):
        """
        Returns a dictionary containing the names of binary choices as keys
        and a string of answers to any numtolerance_inputs which they may have
        e.g {choice_1bc : "answer1, answer2", choice_2bc : ""}
        """
        return self.answer_values

    def _split_answers_dict(self, a_dict):
        """
        Returns two dicts:
        `binary_choices` : dictionary {input_name: input_value} for
        the binary choices which the student selected.
        and
        `numtolerance_choices` : a dictionary {input_name: input_value}
        for the numtolerance_inputs inside of choices which were selected

        Determines if an input is inside of a binary input by looking at
        the beginning of it's name.

        For example. If a binary_choice was named '1_2_1_choiceinput_0bc'
        All of the numtolerance_inputs in it would have an idea that begins
        with '1_2_1_choice_input_0_numtolerance_input'

        Splits the name of the numtolerance_input at the occurence of
        '_numtolerance_input_' and appends 'bc' to the end to get the name
        of the choice it is contained in.

        Example:
        `a_dict` = {
            '1_2_1_choiceinput_0bc': '1_2_1_choiceinput_0bc',
            '1_2_1_choiceinput_0_numtolerance_input_0': '1',
            '1_2_1_choiceinput_0_numtolerance_input_1': '2'
            '1_2_1_choiceinput_1_numtolerance_input_0': '3'
        }

        In this case, the binary choice is '1_2_1_choiceinput_0bc', and
        the numtolerance_inputs associated with it are
        '1_2_1_choiceinput_0_numtolerance_input_0', and
        '1_2_1_choiceinput_0_numtolerance_input_1'.

        so the two return dictionaries would be
        `binary_choices` = {'1_2_1_choiceinput_0bc': '1_2_1_choiceinput_0bc'}
        and
        `numtolerance_choices` ={
            '1_2_1_choiceinput_0_numtolerance_input_0': '1',
            '1_2_1_choiceinput_0_numtolerance_input_1': '2'
        }

        The entry '1_2_1_choiceinput_1_numtolerance_input_0': '3' is discarded
        because it was not inside of a selected binary choice, and no validation
        should be performed on numtolerance_inputs inside of non-selected choices.
        """

        # Initialize the two dictionaries that are returned
        numtolerance_choices = {}
        binary_choices = {}

        # `selected_choices` is a list of binary choices which were "checked/selected"
        # when the student submitted the problem.
        # Keys in a_dict ending with 'bc' refer to binary choices.
        selected_choices = [key for key in a_dict if key.endswith("bc")]
        for key in selected_choices:
            binary_choices[key] = a_dict[key]

        # Convert the name of a numtolerance_input into the name of the binary
        # choice that it is contained within, and append it to the list if
        # the numtolerance_input's parent binary_choice is contained in
        # `selected_choices`.
        selected_numtolerance_inputs = [
            key for key in a_dict if key.partition("_numtolerance_input_")[0] + "bc"
            in selected_choices
        ]

        for key in selected_numtolerance_inputs:
            numtolerance_choices[key] = a_dict[key]

        return (binary_choices, numtolerance_choices)

    def _check_student_choices(self, choices):
        """
        Compares student submitted checkbox/radiobutton answers against
        the correct answers. Returns True or False.

        True if all of the correct choices are selected and no incorrect
        choices are selected.
        """
        student_choices = set(choices)
        required_selected = len(self.correct_choices - student_choices) == 0
        no_extra_selected = len(student_choices - self.correct_choices) == 0
        correct = required_selected and no_extra_selected
        return correct

    def _check_student_inputs(self, numtolerance_inputs):
        """
        Compares student submitted numerical answers against the correct
        answers and tolerances.

        `numtolerance_inputs` is a dictionary {answer_name : answer_value}

        Performs numerical validation by means of calling
        `compare_with_tolerance()` on all of `numtolerance_inputs`

        Performs a call to `compare_with_tolerance` even on values for
        decoy_inputs. This is used to validate their numericality and
        raise an error if the student entered a non numerical expression.

        Returns True if and only if all student inputs are correct.
        """
        _ = self.capa_system.i18n.gettext
        inputs_correct = True
        for answer_name, answer_value in six.iteritems(numtolerance_inputs):
            # If `self.corrrect_inputs` does not contain an entry for
            # `answer_name`, this means that answer_name is a decoy
            # input's value, and validation of its numericality is the
            # only thing of interest from the later call to
            # `compare_with_tolerance`.
            params = self.correct_inputs.get(answer_name, {'answer': 0})

            correct_ans = params['answer']
            # Set the tolerance to '0' if it was not specified in the xml
            tolerance = params.get('tolerance', default_tolerance)
            # Make sure that the staff answer is a valid number
            try:
                correct_ans = complex(correct_ans)
            except ValueError:
                log.debug(
                    "Content error--answer '%s' is not a valid complex number",
                    correct_ans
                )
                raise StudentInputError(  # lint-amnesty, pylint: disable=raise-missing-from
                    _("The Staff answer could not be interpreted as a number.")
                )
            # Compare the student answer to the staff answer/ or to 0
            # if all that is important is verifying numericality
            try:
                partial_correct = compare_with_tolerance(
                    evaluator({}, {}, answer_value),
                    correct_ans,
                    tolerance
                )
            except:
                # Use the traceback-preserving version of re-raising with a
                # different type
                __, __, trace = sys.exc_info()
                msg = _("Could not interpret '{given_answer}' as a number.").format(
                    given_answer=html.escape(answer_value)
                )
                msg += " ({0})".format(trace)
                raise StudentInputError(msg)  # lint-amnesty, pylint: disable=raise-missing-from

            # Ignore the results of the comparisons which were just for
            # Numerical Validation.
            if answer_name in self.correct_inputs and not partial_correct:
                # If any input is not correct, set the return value to False
                inputs_correct = False
        return inputs_correct

#-----------------------------------------------------------------------------

# TEMPORARY: List of all response subclasses
# FIXME: To be replaced by auto-registration

# pylint: disable=invalid-all-object
__all__ = [
    CodeResponse,
    NumericalResponse,
    FormulaResponse,
    CustomResponse,
    SchematicResponse,
    ExternalResponse,
    ImageResponse,
    OptionResponse,
    SymbolicResponse,
    StringResponse,
    ChoiceResponse,
    MultipleChoiceResponse,
    TrueFalseResponse,
    AnnotationResponse,
    ChoiceTextResponse,
]
# pylint: enable=invalid-all-object
