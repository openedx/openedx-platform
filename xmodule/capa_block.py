# pylint: disable=too-many-lines
"""
Implements the Problem XBlock, which is built on top of the CAPA subsystem.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import logging
import os
import re
import struct
import sys
import traceback
from zoneinfo import ZoneInfo

import nh3
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.template.loader import render_to_string
from django.utils.encoding import smart_str
from django.utils.functional import cached_property
from lxml import etree
from web_fragments.fragment import Fragment
from xblock.core import XBlock
from xblock.exceptions import NotFoundError, ProcessingError
from xblock.fields import (
    Boolean,
    Date,
    Dict,
    Float,
    Integer,
    List,
    ListScoreField,
    Scope,
    ScoreField,
    String,
    Timedelta,
    XMLString,
)
from xblock.progress import Progress
from xblock.scorable import ScorableXBlockMixin, Score, ShowCorrectness
from xblocks_contrib.problem import ProblemBlock as _ExtractedProblemBlock

from common.djangoapps.xblock_django.constants import (
    ATTR_KEY_DEPRECATED_ANONYMOUS_USER_ID,
    ATTR_KEY_USER_ID,
    ATTR_KEY_USER_IS_STAFF,
)
from openedx.core.djangolib.markup import HTML, Text
from xmodule.capa import responsetypes
from xmodule.capa.capa_problem import LoncapaProblem, LoncapaSystem
from xmodule.capa.inputtypes import Status
from xmodule.capa.responsetypes import LoncapaProblemError, ResponseError, StudentInputError
from xmodule.capa.util import convert_files_to_filenames, get_inner_html_from_xpath
from xmodule.raw_block import RawMixin
from xmodule.util.builtin_assets import add_css_to_fragment, add_webpack_js_to_fragment
from xmodule.x_module import XModuleMixin, XModuleToXBlockMixin, shim_xmodule_js
from xmodule.xml_block import XmlMixin

log = logging.getLogger("edx.courseware")

# Make '_' a no-op so we can scrape strings. Using lambda instead of
#  `django.utils.translation.gettext_noop` because Django cannot be imported in this file
_ = lambda text: text  # pylint: disable=unnecessary-lambda-assignment

# Generate this many different variants of problems with rerandomize=per_student
NUM_RANDOMIZATION_BINS = 20
# Never produce more than this many different seeds, no matter what.
MAX_RANDOMIZATION_BINS = 1000


try:
    FEATURES = getattr(settings, "FEATURES", {})
except ImproperlyConfigured:
    FEATURES = {}


class SHOWANSWER:  # pylint: disable=too-few-public-methods
    """
    Constants for when to show answer
    """

    ALWAYS = "always"
    ANSWERED = "answered"
    ATTEMPTED = "attempted"
    CLOSED = "closed"
    FINISHED = "finished"
    CORRECT_OR_PAST_DUE = "correct_or_past_due"
    PAST_DUE = "past_due"
    NEVER = "never"
    AFTER_SOME_NUMBER_OF_ATTEMPTS = "after_attempts"
    AFTER_ALL_ATTEMPTS = "after_all_attempts"
    AFTER_ALL_ATTEMPTS_OR_CORRECT = "after_all_attempts_or_correct"
    ATTEMPTED_NO_PAST_DUE = "attempted_no_past_due"


class GRADING_METHOD:  # pylint: disable=too-few-public-methods,invalid-name
    """
    Constants for grading method options.
    """

    LAST_SCORE = "last_score"
    FIRST_SCORE = "first_score"
    HIGHEST_SCORE = "highest_score"
    AVERAGE_SCORE = "average_score"


class RANDOMIZATION:  # pylint: disable=too-few-public-methods
    """
    Constants for problem randomization
    """

    ALWAYS = "always"
    ONRESET = "onreset"
    NEVER = "never"
    PER_STUDENT = "per_student"


class Randomization(String):  # pylint: disable=too-few-public-methods
    """
    Define a field to store how to randomize a problem.
    """

    def from_json(self, value):
        """Convert stored randomization flags into their internal enum values."""
        if value in ("", "true"):
            return RANDOMIZATION.ALWAYS

        if value == "false":
            return RANDOMIZATION.PER_STUDENT

        return value

    to_json = from_json


@XBlock.needs("user")
@XBlock.needs("i18n")
@XBlock.needs("cache")
@XBlock.needs("sandbox")
@XBlock.needs("xqueue")
@XBlock.needs("replace_urls")
@XBlock.wants("call_to_action")
class _BuiltInProblemBlock(  # pylint: disable=too-many-public-methods,too-many-instance-attributes,too-many-ancestors
    ScorableXBlockMixin,
    RawMixin,
    XmlMixin,
    XModuleToXBlockMixin,
    XModuleMixin,
):
    """
    An XBlock representing a "problem".

    A problem contains zero or more respondable items, such as multiple choice,
    numeric response, true/false, etc. See xmodule/capa/responsetypes.py
    for the full ensemble.

    The rendering logic of a problem is largely encapsulated within
    LoncapaProblem, LoncapaSystem and related classes. This block serves to
    host the Loncapa system within the XBlock runtime and connect it to the
    greater LMS/CMS.

    As historical context: the acronym LON-CAPA references the "Learning
    Online - Computer-Assisted Personalized Approach" LMS, from which this
    system is inspired.
    """

    INDEX_CONTENT_TYPE = "CAPA"

    is_extracted = False

    has_score = True
    show_in_read_only_mode = True
    template_dir_name = "problem"
    has_author_view = True

    icon_class = "problem"

    uses_xmodule_styles_setup = True

    display_name = String(
        display_name=_("Display Name"),
        help=_("The display name for this component."),
        scope=Scope.settings,
        # it'd be nice to have a useful default but it screws up other things; so,
        # use display_name_with_default for those
        default=_("Blank Problem"),
    )
    attempts = Integer(
        help=_("Number of attempts taken by the student on this problem"), default=0, scope=Scope.user_state
    )
    max_attempts = Integer(
        display_name=_("Maximum Attempts"),
        help=_(
            "Defines the number of times a student can try to answer this problem. "
            "If the value is not set, infinite attempts are allowed."
        ),
        values={"min": 0},
        scope=Scope.settings,
    )
    grading_method = String(
        display_name=_("Grading Method"),
        help=_(
            "Define the grading method for this problem. By default, "
            "it's the score of the last submission made by the student."
        ),
        scope=Scope.settings,
        default=GRADING_METHOD.LAST_SCORE,
        values=[
            {"display_name": _("Last Score"), "value": GRADING_METHOD.LAST_SCORE},
            {"display_name": _("First Score"), "value": GRADING_METHOD.FIRST_SCORE},
            {"display_name": _("Highest Score"), "value": GRADING_METHOD.HIGHEST_SCORE},
            {"display_name": _("Average Score"), "value": GRADING_METHOD.AVERAGE_SCORE},
        ],
    )
    due = Date(help=_("Date that this problem is due by"), scope=Scope.settings)
    graceperiod = Timedelta(
        help=_("Amount of time after the due date that submissions will be accepted"), scope=Scope.settings
    )
    show_correctness = String(
        display_name=_("Show Results"),
        help=_(
            "Defines when to show whether a learner's answer to the problem is correct. "
            "Configured on the subsection."
        ),
        scope=Scope.settings,
        default=ShowCorrectness.ALWAYS,
        values=[
            {"display_name": _("Always"), "value": ShowCorrectness.ALWAYS},
            {"display_name": _("Never"), "value": ShowCorrectness.NEVER},
            {"display_name": _("Past Due"), "value": ShowCorrectness.PAST_DUE},
        ],
    )
    showanswer = String(
        display_name=_("Show Answer"),
        help=_("Defines when to show the answer to the problem. A default value can be set in Advanced Settings."),
        scope=Scope.settings,
        default=SHOWANSWER.FINISHED,
        values=[
            {"display_name": _("Always"), "value": SHOWANSWER.ALWAYS},
            {"display_name": _("Answered"), "value": SHOWANSWER.ANSWERED},
            {"display_name": _("Attempted or Past Due"), "value": SHOWANSWER.ATTEMPTED},
            {"display_name": _("Closed"), "value": SHOWANSWER.CLOSED},
            {"display_name": _("Finished"), "value": SHOWANSWER.FINISHED},
            {"display_name": _("Correct or Past Due"), "value": SHOWANSWER.CORRECT_OR_PAST_DUE},
            {"display_name": _("Past Due"), "value": SHOWANSWER.PAST_DUE},
            {"display_name": _("Never"), "value": SHOWANSWER.NEVER},
            {"display_name": _("After Some Number of Attempts"), "value": SHOWANSWER.AFTER_SOME_NUMBER_OF_ATTEMPTS},
            {"display_name": _("After All Attempts"), "value": SHOWANSWER.AFTER_ALL_ATTEMPTS},
            {"display_name": _("After All Attempts or Correct"), "value": SHOWANSWER.AFTER_ALL_ATTEMPTS_OR_CORRECT},
            {"display_name": _("Attempted"), "value": SHOWANSWER.ATTEMPTED_NO_PAST_DUE},
        ],
    )
    attempts_before_showanswer_button = Integer(
        display_name=_("Show Answer: Number of Attempts"),
        help=_(
            "Number of times the student must attempt to answer the question before the Show Answer button appears."
        ),
        values={"min": 0},
        default=0,
        scope=Scope.settings,
    )
    force_save_button = Boolean(
        help=_("Whether to force the save button to appear on the page"), scope=Scope.settings, default=False
    )
    show_reset_button = Boolean(
        display_name=_("Show Reset Button"),
        help=_(
            "Determines whether a 'Reset' button is shown so the user may reset their answer. "
            "A default value can be set in Advanced Settings."
        ),
        scope=Scope.settings,
        default=False,
    )
    rerandomize = Randomization(
        display_name=_("Randomization"),
        help=_(
            "Defines when to randomize the variables specified in the associated Python script. "
            'For problems that do not randomize values, specify "Never". '
        ),
        default=RANDOMIZATION.NEVER,
        scope=Scope.settings,
        values=[
            {"display_name": _("Always"), "value": RANDOMIZATION.ALWAYS},
            {"display_name": _("On Reset"), "value": RANDOMIZATION.ONRESET},
            {"display_name": _("Never"), "value": RANDOMIZATION.NEVER},
            {"display_name": _("Per Student"), "value": RANDOMIZATION.PER_STUDENT},
        ],
    )
    data = XMLString(
        help=_("XML data for the problem"),
        scope=Scope.content,
        enforce_type=True,
        default="<problem></problem>",
    )
    correct_map = Dict(
        help=_("Dictionary with the correctness of current student answers"), scope=Scope.user_state, default={}
    )
    correct_map_history = List(help=_("List of correctness maps for each attempt"), scope=Scope.user_state, default=[])
    input_state = Dict(help=_("Dictionary for maintaining the state of inputtypes"), scope=Scope.user_state)
    student_answers = Dict(help=_("Dictionary with the current student responses"), scope=Scope.user_state)
    student_answers_history = List(
        help=_("List of student answers for each attempt"), scope=Scope.user_state, default=[]
    )

    # enforce_type is set to False here because this field is saved as a dict in the database.
    score = ScoreField(help=_("Dictionary with the current student score"), scope=Scope.user_state, enforce_type=False)
    score_history = ListScoreField(
        help=_("List of scores for each attempt"), scope=Scope.user_state, default=[], enforce_type=False
    )
    has_saved_answers = Boolean(
        help=_("Whether or not the answers have been saved since last submit"), scope=Scope.user_state, default=False
    )
    done = Boolean(help=_("Whether the student has answered the problem"), scope=Scope.user_state, default=False)
    seed = Integer(help=_("Random seed for this student"), scope=Scope.user_state)
    last_submission_time = Date(help=_("Last submission time"), scope=Scope.user_state)
    submission_wait_seconds = Integer(
        display_name=_("Timer Between Attempts"),
        help=_("Seconds a student must wait between submissions for a problem with multiple attempts."),
        scope=Scope.settings,
        default=0,
    )
    weight = Float(
        display_name=_("Problem Weight"),
        help=_(
            "Defines the number of points each problem is worth. "
            "If the value is not set, each response field in the problem is worth one point."
        ),
        values={"min": 0, "step": 0.1},
        scope=Scope.settings,
    )
    markdown = String(help=_("Markdown source of this module"), default=None, scope=Scope.settings)
    source_code = String(
        help=_("Source code for LaTeX and Word problems. This feature is not well-supported."), scope=Scope.settings
    )
    use_latex_compiler = Boolean(help=_("Enable LaTeX templates?"), default=False, scope=Scope.settings)
    matlab_api_key = String(
        display_name=_("Matlab API key"),
        help=_(
            "Enter the API key provided by MathWorks for accessing the MATLAB Hosted Service. "
            "This key is granted for exclusive use by this course for the specified duration. "
            "Please do not share the API key with other courses and notify MathWorks immediately "
            "if you believe the key is exposed or compromised. To obtain a key for your course, "
            "or to report an issue, please contact moocsupport@mathworks.com"
        ),
        scope=Scope.settings,
    )
    markdown_edited = Boolean(
        help=_("Indicates if the problem was edited using the Markdown editor in the Authoring MFE."),
        scope=Scope.settings,
        default=False,
    )

    def bind_for_student(self, *args, **kwargs):
        super().bind_for_student(*args, **kwargs)

        # Capa was an XModule. When bind_for_student() was called on it with a new runtime, a new CapaModule object
        # was initialized when XModuleDescriptor._xmodule() was called next. self.lcp was constructed in CapaModule
        # init(). To keep the same behaviour, we delete self.lcp in bind_for_student().
        if "lcp" in self.__dict__:
            del self.__dict__["lcp"]

    def student_view(self, _context, show_detailed_errors=False):
        """
        Return the student view.
        """
        # self.score is initialized in self.lcp but in this method is accessed before self.lcp so just call it first.
        try:
            self.lcp
        except Exception as err:  # pylint: disable=broad-exception-caught
            html = self.handle_fatal_lcp_error(err if show_detailed_errors else None)
        else:
            html = self.get_html()
        fragment = Fragment(html)
        add_css_to_fragment(fragment, "ProblemBlockDisplay.css")
        add_webpack_js_to_fragment(fragment, "ProblemBlockDisplay")
        shim_xmodule_js(fragment, "Problem")
        return fragment

    def public_view(self, context):
        """
        Return the view seen by users who aren't logged in or who aren't
        enrolled in the course.
        """
        if getattr(self.runtime, "suppports_state_for_anonymous_users", False):
            # The new XBlock runtime can generally support capa problems for users who aren't logged in, so show the
            # normal student_view. To prevent anonymous users from viewing specific problems, adjust course policies
            # and/or content groups.
            return self.student_view(context)

        # Show a message that this content requires users to login/enroll.
        return super().public_view(context)

    def author_view(self, context):
        """
        Renders the Studio preview view.
        """
        return self.student_view(context, show_detailed_errors=True)

    def handle_ajax(self, dispatch, data):  # pylint: disable=too-many-locals
        """
        This is called by courseware.block_render, to handle an AJAX call.

        `data` is request.POST.

        Returns a json dictionary:
        { 'progress_changed' : True/False,
          'progress' : 'none'/'in_progress'/'done',
          <other request-specific values here > }
        """
        # self.score is initialized in self.lcp but in this method is accessed before self.lcp so just call it first.
        self.lcp  # pylint: disable=pointless-statement
        handlers = {
            "hint_button": self.hint_button,
            "problem_get": self.get_problem,
            "problem_check": self.submit_problem,
            "problem_reset": self.reset_problem,
            "problem_save": self.save_problem,
            "problem_show": self.get_answer,
            "score_update": self.update_score,
            "input_ajax": self.handle_input_ajax,
            "ungraded_response": self.handle_ungraded_response,
        }

        _ = self.runtime.service(self, "i18n").gettext

        generic_error_message = _(
            "We're sorry, there was an error with processing your request. "
            "Please try reloading your page and trying again."
        )

        not_found_error_message = _(
            "The state of this problem has changed since you loaded this page. Please refresh your page."
        )

        if dispatch not in handlers:
            return f"Error: {dispatch} is not a known capa action"

        before = self.get_progress()
        before_attempts = self.attempts

        try:
            result = handlers[dispatch](data)

        except NotFoundError as ex:
            log.info(
                "Unable to find data when dispatching %s to %s for user %s",
                dispatch,
                self.scope_ids.usage_id,
                self.scope_ids.user_id,
            )
            _, _, traceback_obj = sys.exc_info()
            raise ProcessingError(not_found_error_message).with_traceback(traceback_obj) from ex

        except Exception as ex:
            log.exception(
                "Unknown error when dispatching %s to %s for user %s",
                dispatch,
                self.scope_ids.usage_id,
                self.scope_ids.user_id,
            )
            _, _, traceback_obj = sys.exc_info()
            raise ProcessingError(generic_error_message).with_traceback(traceback_obj) from ex

        after = self.get_progress()
        after_attempts = self.attempts
        progress_changed = (after != before) or (after_attempts != before_attempts)
        curr_score, total_possible = self.get_display_progress()

        result.update(
            {
                "progress_changed": progress_changed,
                "current_score": curr_score,
                "total_possible": total_possible,
                "attempts_used": after_attempts,
            }
        )

        return json.dumps(result, cls=ComplexEncoder)

    @property
    def display_name_with_default(self):
        """
        Constructs the display name for a CAPA problem.

        Default to the display_name if it isn't None or not an empty string,
        else fall back to problem category.
        """
        if self.display_name is None or not self.display_name.strip():
            return self.location.block_type

        return self.display_name

    def grading_method_display_name(self) -> str | None:
        """
        Return the grading method
        """
        _ = self.runtime.service(self, "i18n").gettext
        display_name = {
            GRADING_METHOD.LAST_SCORE: _("Last Score"),
            GRADING_METHOD.FIRST_SCORE: _("First Score"),
            GRADING_METHOD.HIGHEST_SCORE: _("Highest Score"),
            GRADING_METHOD.AVERAGE_SCORE: _("Average Score"),
        }
        return display_name[self.grading_method]

    @property
    def debug(self):
        """
        If CAPA block fails to render, we want course authors to be able to see
        the error in Studio. At the same time, in production, we don't want
        to show errors to students.
        """
        return getattr(self.runtime, "is_author_mode", False)

    @classmethod
    def filter_templates(cls, template, course):
        """
        Filter template that contains 'latex' from templates.

        Show them only if use_latex_compiler is set to True in
        course settings.
        """
        return "latex" not in template["template_id"] or course.use_latex_compiler

    # VS[compat]
    # TODO (cpennington): Delete this method once all fall 2012 course are being
    # edited in the cms
    @classmethod
    def backcompat_paths(cls, path):
        """Return legacy filesystem paths for backward compatibility."""
        return [
            "problems/" + path[8:],
            path[8:],
        ]

    @property
    def non_editable_metadata_fields(self):
        """Return metadata fields that cannot be edited in Studio."""
        non_editable_fields = super().non_editable_metadata_fields
        non_editable_fields.extend(
            [
                ProblemBlock.due,
                ProblemBlock.graceperiod,
                ProblemBlock.force_save_button,
                ProblemBlock.markdown,
                ProblemBlock.use_latex_compiler,
                ProblemBlock.show_correctness,
                # Temporarily remove the ability to see MATLAB API key in Studio, as
                # a pre-cursor to removing it altogether.
                #   https://github.com/openedx/public-engineering/issues/192
                ProblemBlock.matlab_api_key,
            ]
        )
        return non_editable_fields

    @property
    def problem_types(self):
        """Low-level problem type introspection for content libraries filtering by problem type"""
        try:
            tree = etree.XML(self.data)
        except etree.XMLSyntaxError:
            log.error("Error parsing problem types from xml for capa block %s", self.display_name)
            return None  # short-term fix to prevent errors (TNL-5057). Will be more properly addressed in TNL-4525.
        registered_tags = responsetypes.registry.registered_tags()
        return {node.tag for node in tree.iter() if node.tag in registered_tags}

    def index_dictionary(self):
        """
        Return dictionary prepared with module content and type for indexing.
        """
        xblock_body = super().index_dictionary()

        # Make optioninput's options index friendly by replacing the actual tag with the values
        capa_content = re.sub(r'<optioninput options="\(([^"]+)\)".*?>\s*|\S*<\/optioninput>', r"\1", self.data)

        # Remove the following tags with content that can leak hints or solutions:
        # - `solution` (with optional attributes) and `solutionset`.
        # - `targetedfeedback` (with optional attributes) and `targetedfeedbackset`.
        # - `answer` (with optional attributes).
        # - `script` (with optional attributes).
        # - `style` (with optional attributes).
        # - various types of hints (with optional attributes) and `hintpart`.
        capa_content = re.sub(
            re.compile(
                r"""
                    <solution.*?>.*?</solution.*?> |
                    <targetedfeedback.*?>.*?</targetedfeedback.*?> |
                    <answer.*?>.*?</answer> |
                    <script.*?>.*?</script> |
                    <style.*?>.*?</style> |
                    <[a-z]*hint.*?>.*?</[a-z]*hint.*?>
                """,
                re.DOTALL | re.VERBOSE,
            ),
            "",
            capa_content,
        )
        # Strip out all other tags, leaving their content. But we want spaces between adjacent tags, so that
        # <choice correct="true"><div>Option A</div></choice><choice correct="false"><div>Option B</div></choice>
        # becomes "Option A Option B" not "Option AOption B" (these will appear in search results)
        capa_content = re.sub(r"</(\w+)><([^>]+)>", r"</\1> <\2>", capa_content)
        capa_content = re.sub(r"(\s|&nbsp;|//)+", " ", nh3.clean(capa_content, tags=set())).strip()

        capa_body = {
            "capa_content": capa_content,
            "display_name": self.display_name,
        }
        if "content" in xblock_body:
            xblock_body["content"].update(capa_body)
        else:
            xblock_body["content"] = capa_body
        xblock_body["content_type"] = self.INDEX_CONTENT_TYPE
        xblock_body["problem_types"] = list(self.problem_types)
        return xblock_body

    def has_support(self, view, functionality):  # pylint: disable=unused-argument
        """
        Override the XBlock.has_support method to return appropriate
        value for the multi-device functionality.
        Returns whether the given view has support for the given functionality.
        """
        if functionality == "multi_device":
            types = self.problem_types  # Avoid calculating this property twice
            return types is not None and all(
                responsetypes.registry.get_class_for_tag(tag).multi_device_support for tag in types
            )
        return False

    def max_score(self):
        """
        Return the problem's max score if problem is instantiated successfully, else return max score of 0.
        """
        capa_system = LoncapaSystem(
            ajax_url=None,
            anonymous_student_id=None,
            cache=None,
            can_execute_unsafe_code=None,
            get_python_lib_zip=None,
            DEBUG=None,
            i18n=self.runtime.service(self, "i18n"),
            render_template=None,
            resources_fs=self.runtime.resources_fs,
            seed=None,
            xqueue=None,
            matlab_api_key=None,
        )
        try:
            lcp = LoncapaProblem(
                problem_text=self.data,
                id=self.location.html_id(),
                capa_system=capa_system,
                capa_block=self,
                state={},
                seed=1,
                minimal_init=True,
            )
        except responsetypes.LoncapaProblemError:
            log.exception("LcpFatalError for block %s while getting max score", str(self.location))
            maximum_score = 0
        else:
            maximum_score = lcp.get_max_score()
        return maximum_score

    def generate_report_data(self, user_state_iterator, limit_responses=None):
        """
        Return a list of student responses to this block in a readable way.

        Arguments:
            user_state_iterator: iterator over UserStateClient objects.
                E.g. the result of user_state_client.iter_all_for_block(block_key)

            limit_responses (int|None): maximum number of responses to include.
                Set to None (default) to include all.

        Returns:
            each call returns a tuple like:
            ("username", {
                           "Question": "2 + 2 equals how many?",
                           "Answer": "Four",
                           "Answer ID": "98e6a8e915904d5389821a94e48babcf_10_1"
            })
        """
        if self.category != "problem":
            raise NotImplementedError()

        if limit_responses == 0:
            # Don't even start collecting answers
            return
        capa_system = LoncapaSystem(
            ajax_url=None,
            # TODO set anonymous_student_id to the anonymous ID of the user which answered each problem
            # Anonymous ID is required for Matlab, CodeResponse, and some custom problems that include
            # '$anonymous_student_id' in their XML.
            # For the purposes of this report, we don't need to support those use cases.
            anonymous_student_id=None,
            cache=None,
            can_execute_unsafe_code=lambda: False,
            get_python_lib_zip=(lambda: self.runtime.service(self, "sandbox").get_python_lib_zip()),
            DEBUG=None,
            i18n=self.runtime.service(self, "i18n"),
            render_template=None,
            resources_fs=self.runtime.resources_fs,
            seed=1,
            xqueue=None,
            matlab_api_key=None,
        )
        _ = capa_system.i18n.gettext

        count = 0
        for user_state in user_state_iterator:

            if "student_answers" not in user_state.state:
                continue
            try:
                lcp = LoncapaProblem(
                    problem_text=self.data,
                    id=self.location.html_id(),
                    capa_system=capa_system,
                    # We choose to run without a fully initialized CapaModule
                    capa_block=None,
                    state={
                        "done": user_state.state.get("done"),
                        "correct_map": user_state.state.get("correct_map"),
                        "student_answers": user_state.state.get("student_answers"),
                        "has_saved_answers": user_state.state.get("has_saved_answers"),
                        "input_state": user_state.state.get("input_state"),
                        "seed": user_state.state.get("seed"),
                    },
                    seed=user_state.state.get("seed"),
                    # extract_tree=False allows us to work without a fully initialized CapaModule
                    # We'll still be able to find particular data in the XML when we need it
                    extract_tree=False,
                )

                for answer_id, orig_answers in lcp.student_answers.items():
                    # Some types of problems have data in lcp.student_answers that isn't in lcp.problem_data.
                    # E.g. formulae do this to store the MathML version of the answer.
                    # We exclude these rows from the report because we only need the text-only answer.
                    if answer_id.endswith("_dynamath"):
                        continue

                    if limit_responses and count >= limit_responses:
                        # End the iterator here
                        return

                    question_text = lcp.find_question_label(answer_id)
                    answer_text = lcp.find_answer_text(answer_id, current_answer=orig_answers)
                    correct_answer_text = lcp.find_correct_answer_text(answer_id)

                    count += 1
                    report = {
                        _("Answer ID"): answer_id,
                        _("Question"): question_text,
                        _("Answer"): answer_text,
                    }
                    if correct_answer_text is not None:
                        report[_("Correct Answer")] = correct_answer_text
                    yield (user_state.username, report)
            except LoncapaProblemError:
                # Capture a backtrace for errors from failed loncapa problems
                log.exception(
                    "An error occurred generating a problem report on course %s, problem %s, and student %s",
                    self.course_id,
                    self.scope_ids.usage_id,
                    self.scope_ids.user_id,
                )
                # Also input error in report
                report = {
                    _("Answer ID"): "Python Error",
                    _("Question"): "Generating a report on the problem failed.",
                    _("Answer"): "Python Error: No Answer Retrieved",
                }
                yield (user_state.username, report)

    @property
    def course_end_date(self):
        """
        Return the end date of the problem's course
        """

        try:
            course_block_key = self.runtime.course_entry.structure["root"]
            return self.runtime.course_entry.structure["blocks"][course_block_key].fields["end"]
        except (AttributeError, KeyError):
            return None

    @property
    def close_date(self):
        """
        Return the date submissions should be closed from.
        """

        due_date = self.due or self.course_end_date

        if self.graceperiod is not None and due_date:
            return due_date + self.graceperiod

        return due_date

    def get_seed(self):
        """
        Generate the seed if not set and return it.
        """
        if self.seed is None:
            self.choose_new_seed()
        return self.seed

    @cached_property
    def lcp(self):  # pylint: disable=method-hidden
        """Lazily create and return a LoncapaProblem instance for this block."""
        try:
            lcp = self.new_lcp(self.get_state_for_lcp())
        except Exception as err:
            msg = f"cannot create LoncapaProblem {str(self.location)}: {err}"
            raise LoncapaProblemError(msg).with_traceback(sys.exc_info()[2])

        if self.score is None:
            self.set_score(self.score_from_lcp(lcp))

        assert self.seed is not None
        return lcp

    def choose_new_seed(self):
        """
        Choose a new seed.
        """
        if self.rerandomize == RANDOMIZATION.NEVER:
            self.seed = 1
        elif self.rerandomize == RANDOMIZATION.PER_STUDENT:
            user_id = self.runtime.service(self, "user").get_current_user().opt_attrs.get(ATTR_KEY_USER_ID) or 0
            # see comment on randomization_bin
            self.seed = randomization_bin(user_id, str(self.location).encode("utf-8"))
        else:
            self.seed = struct.unpack("i", os.urandom(4))[0]

            # So that sandboxed code execution can be cached, but still have an interesting
            # number of possibilities, cap the number of different random seeds.
            self.seed %= MAX_RANDOMIZATION_BINS

    def new_lcp(self, state, text=None):
        """
        Generate a new Loncapa Problem
        """
        if text is None:
            text = self.data

        user_service = self.runtime.service(self, "user")
        anonymous_student_id = user_service.get_current_user().opt_attrs.get(ATTR_KEY_DEPRECATED_ANONYMOUS_USER_ID)
        seed = user_service.get_current_user().opt_attrs.get(ATTR_KEY_USER_ID) or 0

        sandbox_service = self.runtime.service(self, "sandbox")
        cache_service = self.runtime.service(self, "cache")
        xqueue_service = self.runtime.service(self, "xqueue")

        is_studio = getattr(self.runtime, "is_author_mode", False)

        capa_system = LoncapaSystem(
            ajax_url=self.ajax_url,
            anonymous_student_id=anonymous_student_id,
            cache=cache_service,
            can_execute_unsafe_code=sandbox_service.can_execute_unsafe_code,
            get_python_lib_zip=sandbox_service.get_python_lib_zip,
            DEBUG=self.debug,
            i18n=self.runtime.service(self, "i18n"),
            render_template=render_to_string,
            resources_fs=self.runtime.resources_fs,
            seed=seed,  # Why do we do this if we have self.seed?
            xqueue=None if is_studio else xqueue_service,
            matlab_api_key=self.matlab_api_key,
        )

        return LoncapaProblem(
            problem_text=text,
            id=self.location.html_id(),
            state=state,
            seed=self.get_seed(),
            capa_system=capa_system,
            capa_block=self,  # njp
        )

    def get_state_for_lcp(self):
        """
        Give a dictionary holding the state of the module
        """
        return {
            "done": self.done,
            "correct_map": self.correct_map,
            "correct_map_history": self.correct_map_history,
            "student_answers": self.student_answers,
            "has_saved_answers": self.has_saved_answers,
            "input_state": self.input_state,
            "seed": self.get_seed(),
        }

    def set_state_from_lcp(self):
        """
        Set the module's state from the settings in `self.lcp`
        """
        lcp_state = self.lcp.get_state()
        self.done = lcp_state["done"]
        self.correct_map = lcp_state["correct_map"]
        self.correct_map_history = lcp_state["correct_map_history"]
        self.input_state = lcp_state["input_state"]
        self.student_answers = lcp_state["student_answers"]
        self.has_saved_answers = lcp_state["has_saved_answers"]

    def set_last_submission_time(self):
        """
        Set the module's last submission time (when the problem was submitted)
        """
        self.last_submission_time = datetime.datetime.now(ZoneInfo("UTC"))

    def get_progress(self):
        """
        For now, just return weighted earned / weighted possible
        """
        if self.score:
            raw_earned = self.score.raw_earned
            raw_possible = self.score.raw_possible
        else:
            raw_earned = raw_possible = 0

        if raw_possible > 0:
            if self.weight is not None:
                # Progress objects expect total > 0
                if self.weight == 0:
                    return None

                # scale score and total by weight/total:
                weighted_earned = raw_earned * self.weight / raw_possible
                weighted_possible = self.weight
            else:
                weighted_earned = raw_earned
                weighted_possible = raw_possible
            try:
                return Progress(weighted_earned, weighted_possible)
            except (TypeError, ValueError):
                log.exception("Got bad progress")
                return None
        return None

    def get_display_progress(self):
        """
        Return (score, total) to be displayed to the learner.
        """
        progress = self.get_progress()
        score, total = progress.frac() if progress else (0, 0)

        # Withhold the score if hiding correctness
        if not self.correctness_available():
            score = None

        return score, total

    def get_html(self):
        """
        Return some html with data about the module
        """
        curr_score, total_possible = self.get_display_progress()

        return render_to_string(
            "problem_ajax.html",
            {
                "element_id": self.location.html_id(),
                "id": str(self.location),
                "ajax_url": self.ajax_url,
                "current_score": curr_score,
                "total_possible": total_possible,
                "attempts_used": self.attempts,
                "content": self.get_problem_html(encapsulate=False),
                "graded": self.graded,  # pylint: disable=no-member
            },
        )

    def handle_fatal_lcp_error(self, error):
        """
        Log a fatal LoncapaProblem error and return an HTML message for display to the user.
        """
        log.exception("LcpFatalError Encountered for %s", str(self.location))
        if error:
            return HTML('<p>Error formatting HTML for problem:</p><p><pre style="color:red">{msg}</pre></p>').format(
                msg=str(error)
            )

        return HTML(
            "<p>Could not format HTML for problem. Contact course staff in the discussion forum for assistance.</p>"
        )

    def submit_button_name(self):
        """
        Determine the name for the "submit" button.
        """
        # The logic flow is a little odd so that _('xxx') strings can be found for
        # translation while also running _() just once for each string.
        _ = self.runtime.service(self, "i18n").gettext
        submit = _("Submit")

        return submit

    def submit_button_submitting_name(self):
        """
        Return the "Submitting" text for the "submit" button.

        After the user presses the "submit" button, the button will briefly
        display the value returned by this function until a response is
        received by the server.
        """
        _ = self.runtime.service(self, "i18n").gettext
        return _("Submitting")

    def should_enable_submit_button(self):
        """
        Return True/False to indicate whether to enable the "Submit" button.
        """
        submitted_without_reset = self.is_submitted() and self.rerandomize == RANDOMIZATION.ALWAYS

        # If the problem is closed (past due / too many attempts)
        # then we disable the "submit" button
        # Also, disable the "submit" button if we're waiting
        # for the user to reset a randomized problem
        if self.closed() or submitted_without_reset:
            return False

        return True

    def should_show_reset_button(self):
        """
        Return True/False to indicate whether to show the "Reset" button.
        """
        is_survey_question = self.max_attempts == 0

        # If the problem is closed (and not a survey question with max_attempts==0),
        # then do NOT show the reset button.
        if self.closed() and not is_survey_question:
            return False

        # Button only shows up for randomized problems if the question has been submitted
        if self.rerandomize in [RANDOMIZATION.ALWAYS, RANDOMIZATION.ONRESET] and self.is_submitted():
            return True

        # Do NOT show the button if the problem is correct
        if self.is_correct():
            return False

        return self.show_reset_button

    def should_show_save_button(self):
        """
        Return True/False to indicate whether to show the "Save" button.
        """

        # If the user has forced the save button to display,
        # then show it as long as the problem is not closed
        # (past due / too many attempts)
        if self.force_save_button:
            return not self.closed()

        is_survey_question = self.max_attempts == 0
        needs_reset = self.is_submitted() and self.rerandomize == RANDOMIZATION.ALWAYS

        # If the student has unlimited attempts, and their answers
        # are not randomized, then we do not need a save button
        # because they can use the "Check" button without consequences.
        #
        # The consequences we want to avoid are:
        # * Using up an attempt (if max_attempts is set)
        # * Changing the current problem, and no longer being
        #   able to view it (if rerandomize is "always")
        #
        # In those cases. the if statement below is false,
        # and the save button can still be displayed.
        #
        if self.max_attempts is None and self.rerandomize != RANDOMIZATION.ALWAYS:
            return False

        # If the problem is closed (and not a survey question with max_attempts==0),
        # then do NOT show the save button
        # If we're waiting for the user to reset a randomized problem
        # then do NOT show the save button
        if (self.closed() and not is_survey_question) or needs_reset:
            return False

        return True

    def handle_problem_html_error(self, err):
        """
        Create a dummy problem to represent any errors.

        Change our problem to a dummy problem containing a warning message to
        display to users. Returns the HTML to show to users

        `err` is the Exception encountered while rendering the problem HTML.
        """
        problem_display_name = self.display_name_with_default
        problem_location = str(self.location)
        log.exception("ProblemGetHtmlError: %r, %r, %s", problem_display_name, problem_location, str(err))

        if self.debug:
            msg = HTML("[courseware.capa.capa_block] Failed to generate HTML for problem {url}").format(
                url=str(self.location)
            )
            msg += HTML("<p>Error:</p><p><pre>{msg}</pre></p>").format(msg=str(err))
            msg += HTML("<p><pre>{tb}</pre></p>").format(tb=traceback.format_exc())
            html = msg

        else:
            # We're in non-debug mode, and possibly even in production. We want
            #   to avoid bricking of problem as much as possible

            # Presumably, student submission has corrupted LoncapaProblem HTML.
            #   First, pull down all student answers

            student_answers = self.lcp.student_answers
            answer_ids = list(student_answers.keys())

            # Some inputtypes, such as dynamath, have additional "hidden" state that
            #   is not exposed to the student. Keep those hidden
            # TODO: Use regex, e.g. 'dynamath' is suffix at end of answer_id
            hidden_state_keywords = ["dynamath"]
            for answer_id in answer_ids:
                for hidden_state_keyword in hidden_state_keywords:
                    if answer_id.find(hidden_state_keyword) >= 0:
                        student_answers.pop(answer_id)

            # Next, generate a fresh LoncapaProblem
            self.lcp = self.new_lcp(None)
            self.set_state_from_lcp()
            self.set_score(self.score_from_lcp(self.lcp))
            # Prepend a scary warning to the student
            _ = self.runtime.service(self, "i18n").gettext
            warning_msg = Text(_("Warning: The problem has been reset to its initial state!"))
            warning = HTML('<div class="capa_reset"> <h2>{}</h2>').format(warning_msg)

            # Translators: Following this message, there will be a bulleted list of items.
            warning_msg = _("The problem's state was corrupted by an invalid submission. The submission consisted of:")
            warning += HTML("{}<ul>").format(warning_msg)

            for student_answer in student_answers.values():
                if student_answer != "":
                    warning += HTML("<li>{}</li>").format(student_answer)

            warning_msg = _("If this error persists, please contact the course staff.")
            warning += HTML("</ul>{}</div>").format(warning_msg)

            html = warning
            try:
                html += self.lcp.get_html()
            except Exception as error:
                # Couldn't do it. Give up.
                log.exception(
                    "ProblemGetHtmlError: Unable to generate html from LoncapaProblem: %r, %r, %s",
                    problem_display_name,
                    problem_location,
                    str(error),
                )
                raise

        return html

    def _should_enable_demand_hint(self, demand_hints, hint_index=None):
        """
        Should the demand hint option be enabled?

        Arguments:
            hint_index (int): The current hint index, or None (default value) if no hint is currently being shown.
            demand_hints (list): List of hints.
        Returns:
            bool: True is the demand hint is possible.
            bool: True is demand hint should be enabled.
        """
        # hint_index is the index of the last hint that will be displayed in this rendering,
        # so add 1 to check if others exist.
        if hint_index is None:
            should_enable = len(demand_hints) > 0
        else:
            should_enable = len(demand_hints) > 0 and hint_index + 1 < len(demand_hints)
        return len(demand_hints) > 0, should_enable

    def get_demand_hint(self, hint_index):
        """
        Return html for the problem, including demand hints.

        hint_index (int): (None is the default) if not None, this is the index of the next demand
            hint to show.
        """
        demand_hints = self.lcp.tree.xpath("//problem/demandhint/hint")
        hint_index = hint_index % len(demand_hints)

        _ = self.runtime.service(self, "i18n").gettext

        counter = 0
        total_text = ""
        while counter <= hint_index:
            # Translators: {previous_hints} is the HTML of hints that have already been generated, {hint_number_prefix}
            # is a header for this hint, and {hint_text} is the text of the hint itself.
            # This string is being passed to translation only for possible reordering of the placeholders.
            total_text = HTML(_("{previous_hints}{list_start_tag}{strong_text}{hint_text}</li>")).format(
                previous_hints=HTML(total_text),
                list_start_tag=HTML('<li class="hint-index-{counter}" tabindex="-1">').format(counter=counter),
                strong_text=HTML("<strong>{hint_number_prefix}</strong>").format(
                    # Translators: e.g. "Hint 1 of 3: " meaning we are showing the first of three hints.
                    # This text is shown in bold before the accompanying hint text.
                    hint_number_prefix=Text(_("Hint ({hint_num} of {hints_count}): ")).format(
                        hint_num=counter + 1, hints_count=len(demand_hints)
                    )
                ),
                # Course-authored HTML demand hints are supported.
                hint_text=HTML(
                    self.runtime.service(self, "replace_urls").replace_urls(
                        get_inner_html_from_xpath(demand_hints[counter])
                    )
                ),
            )
            counter += 1

        total_text = HTML("<ol>{hints}</ol>").format(hints=total_text)

        # Log this demand-hint request. Note that this only logs the last hint requested (although now
        # all previously shown hints are still displayed).
        event_info = {}
        event_info["module_id"] = str(self.location)
        event_info["hint_index"] = hint_index
        event_info["hint_len"] = len(demand_hints)
        event_info["hint_text"] = get_inner_html_from_xpath(demand_hints[hint_index])
        self.runtime.publish(self, "edx.problem.hint.demandhint_displayed", event_info)

        _, should_enable_next_hint = self._should_enable_demand_hint(demand_hints=demand_hints, hint_index=hint_index)

        # We report the index of this hint, the client works out what index to use to get the next hint
        return {
            "success": True,
            "hint_index": hint_index,
            "should_enable_next_hint": should_enable_next_hint,
            "msg": total_text,
        }

    def get_problem_html(self, encapsulate=True, submit_notification=False):  # pylint: disable=too-many-locals
        """
        Return html for the problem.

        Adds submit, reset, save, and hint buttons as necessary based on the problem config
        and state.

        encapsulate (bool): if True (the default) embed the html in a problem <div>
        submit_notification (bool): True if the submit notification should be added
        """
        try:
            html = self.lcp.get_html()

        # If we cannot construct the problem HTML,
        # then generate an error message instead.
        except Exception as err:  # pylint: disable=broad-exception-caught
            html = self.handle_problem_html_error(err)

        html = self.remove_tags_from_html(html)
        _ = self.runtime.service(self, "i18n").gettext

        # Enable/Disable Submit button if should_enable_submit_button returns True/False.
        submit_button = self.submit_button_name()
        submit_button_submitting = self.submit_button_submitting_name()
        should_enable_submit_button = self.should_enable_submit_button()
        submit_disabled_ctas = None
        if not should_enable_submit_button:
            cta_service = self.runtime.service(self, "call_to_action")
            if cta_service:
                submit_disabled_ctas = cta_service.get_ctas(self, "capa_submit_disabled")

        content = {
            "name": self.display_name_with_default,
            "html": smart_str(html),
            "weight": self.weight,
        }

        # If demand hints are available, emit hint button and div.
        demand_hints = self.lcp.tree.xpath("//problem/demandhint/hint")
        demand_hint_possible, should_enable_next_hint = self._should_enable_demand_hint(demand_hints=demand_hints)

        answer_notification_type, answer_notification_message = self._get_answer_notification(
            render_notifications=submit_notification
        )

        save_message = None
        if self.has_saved_answers:
            save_message = _("Your answers were previously saved. Click '{button_name}' to grade them.").format(
                button_name=self.submit_button_name()
            )

        context = {
            "problem": content,
            "id": str(self.location),
            "short_id": self.location.html_id(),
            "submit_button": submit_button,
            "submit_button_submitting": submit_button_submitting,
            "should_enable_submit_button": should_enable_submit_button,
            "reset_button": self.should_show_reset_button(),
            "save_button": self.should_show_save_button(),
            "answer_available": self.answer_available(),
            "grading_method": self.grading_method_display_name(),
            "attempts_used": self.attempts,
            "attempts_allowed": self.max_attempts,
            "demand_hint_possible": demand_hint_possible,
            "should_enable_next_hint": should_enable_next_hint,
            "answer_notification_type": answer_notification_type,
            "answer_notification_message": answer_notification_message,
            "has_saved_answers": self.has_saved_answers,
            "save_message": save_message,
            "submit_disabled_cta": submit_disabled_ctas[0] if submit_disabled_ctas else None,
        }

        html = render_to_string("problem.html", context)

        if encapsulate:
            html = HTML('<div id="problem_{id}" class="problem" data-url="{ajax_url}">{html}</div>').format(
                id=self.location.html_id(), ajax_url=self.ajax_url, html=HTML(html)
            )

        # Now do all the substitutions which the LMS block_render normally does, but
        # we need to do here explicitly since we can get called for our HTML via AJAX
        html = self.runtime.service(self, "replace_urls").replace_urls(html)

        return html

    def _get_answer_notification(self, render_notifications):  # pylint: disable=too-many-branches
        """
        Generate the answer notification type and message from the current problem status.

         Arguments:
             render_notifications (bool): If false the method will return an None for type and message
        """
        answer_notification_message = None
        answer_notification_type = None

        if render_notifications:
            progress = self.get_progress()
            id_list = list(self.lcp.correct_map.keys())

            # Show only a generic message if hiding correctness
            if not self.correctness_available():
                answer_notification_type = "submitted"
            elif len(id_list) == 1:
                # Only one answer available
                answer_notification_type = self.lcp.correct_map.get_correctness(id_list[0])
            elif len(id_list) > 1:
                # Check the multiple answers that are available
                answer_notification_type = self.lcp.correct_map.get_correctness(id_list[0])
                for answer_id in id_list[1:]:
                    if self.lcp.correct_map.get_correctness(answer_id) != answer_notification_type:
                        # There is at least 1 of the following combinations of correctness states
                        # Correct and incorrect, Correct and partially correct, or Incorrect and partially correct
                        # which all should have a message type of Partially Correct
                        answer_notification_type = "partially-correct"
                        break

            # Build the notification message based on the notification type and translate it.
            ngettext = self.runtime.service(self, "i18n").ngettext
            _ = self.runtime.service(self, "i18n").gettext
            if answer_notification_type == "incorrect":
                if progress is not None:
                    answer_notification_message = ngettext(
                        "Incorrect ({progress} point)", "Incorrect ({progress} points)", progress.frac()[1]
                    ).format(progress=str(progress))
                else:
                    answer_notification_message = _("Incorrect")
            elif answer_notification_type == "correct":
                if progress is not None:
                    answer_notification_message = ngettext(
                        "Correct ({progress} point)", "Correct ({progress} points)", progress.frac()[1]
                    ).format(progress=str(progress))
                else:
                    answer_notification_message = _("Correct")
            elif answer_notification_type == "partially-correct":
                if progress is not None:
                    answer_notification_message = ngettext(
                        "Partially correct ({progress} point)",
                        "Partially correct ({progress} points)",
                        progress.frac()[1],
                    ).format(progress=str(progress))
                else:
                    answer_notification_message = _("Partially Correct")
            elif answer_notification_type == "submitted":
                answer_notification_message = _("Answer submitted.")

        return answer_notification_type, answer_notification_message

    def remove_tags_from_html(self, html):
        """
        The capa xml includes many tags such as <additional_answer> or <demandhint> which are not
        meant to be part of the client html. We strip them all and return the resulting html.
        """
        tags = [
            "demandhint",
            "choicehint",
            "optionhint",
            "stringhint",
            "numerichint",
            "optionhint",
            "correcthint",
            "regexphint",
            "additional_answer",
            "stringequalhint",
            "compoundhint",
            "stringequalhint",
        ]
        for tag in tags:
            html = re.sub(
                rf"<{tag}.*?>.*?</{tag}>", "", html, flags=re.DOTALL
            )  # xss-lint: disable=python-interpolate-html
            # Some of these tags span multiple lines
        # Note: could probably speed this up by calling sub() once with a big regex
        # vs. simply calling sub() many times as we have here.
        return html

    def hint_button(self, data):
        """
        Hint button handler, returns new html using hint_index from the client.
        """
        hint_index = int(data["hint_index"])
        return self.get_demand_hint(hint_index)

    def used_all_attempts(self):
        """All attempts have been used"""
        return self.max_attempts is not None and self.attempts >= self.max_attempts

    def is_past_due(self):
        """
        Is it now past this problem's due date, including grace period?
        """
        return self.close_date is not None and datetime.datetime.now(ZoneInfo("UTC")) > self.close_date

    def closed(self):
        """
        Is the student still allowed to submit answers?
        """
        if self.used_all_attempts():
            return True
        if self.is_past_due():
            return True

        return False

    def is_submitted(self):
        """
        Used to decide to show or hide RESET or CHECK buttons.

        Means that student submitted problem and nothing more.
        Problem can be completely wrong.
        Pressing RESET button makes this function to return False.
        """
        # used by conditional block
        return self.lcp.done

    def is_attempted(self):
        """
        Has the problem been attempted?

        used by conditional block
        """
        return self.attempts > 0

    def is_correct(self):
        """
        True iff full points
        """
        # self.score is initialized in self.lcp but in this method is accessed before self.lcp so just call it first.
        self.lcp  # pylint: disable=pointless-statement
        return self.score.raw_earned == self.score.raw_possible

    def answer_available(self):  # pylint: disable=too-many-branches,too-many-return-statements
        """
        Is the user allowed to see an answer?
        """
        user_is_staff = self.runtime.service(self, "user").get_current_user().opt_attrs.get(ATTR_KEY_USER_IS_STAFF)
        if not self.correctness_available():
            # If correctness is being withheld, then don't show answers either.
            return False
        if self.showanswer == "":
            return False
        if self.showanswer == SHOWANSWER.NEVER:
            return False
        if user_is_staff:
            # This is after the 'never' check because admins can see the answer
            # unless the problem explicitly prevents it
            return True
        if self.showanswer == SHOWANSWER.ATTEMPTED:
            return self.is_attempted() or self.is_past_due()
        if self.showanswer == SHOWANSWER.ANSWERED:
            # NOTE: this is slightly different from 'attempted' -- resetting the problems
            # makes lcp.done False, but leaves attempts unchanged.
            return self.is_correct()
        if self.showanswer == SHOWANSWER.CLOSED:
            return self.closed()
        if self.showanswer == SHOWANSWER.FINISHED:
            return self.closed() or self.is_correct()

        if self.showanswer == SHOWANSWER.CORRECT_OR_PAST_DUE:
            return self.is_correct() or self.is_past_due()
        if self.showanswer == SHOWANSWER.PAST_DUE:
            return self.is_past_due()
        if self.showanswer == SHOWANSWER.AFTER_SOME_NUMBER_OF_ATTEMPTS:
            required_attempts = self.attempts_before_showanswer_button
            if self.max_attempts and required_attempts >= self.max_attempts:
                required_attempts = self.max_attempts
            return self.attempts >= required_attempts
        if self.showanswer == SHOWANSWER.ALWAYS:
            return True
        if self.showanswer == SHOWANSWER.AFTER_ALL_ATTEMPTS:
            return self.used_all_attempts()
        if self.showanswer == SHOWANSWER.AFTER_ALL_ATTEMPTS_OR_CORRECT:
            return self.used_all_attempts() or self.is_correct()
        if self.showanswer == SHOWANSWER.ATTEMPTED_NO_PAST_DUE:
            return self.is_attempted()
        return False

    def correctness_available(self):
        """
        Is the user allowed to see whether she's answered correctly?

        Limits access to the correct/incorrect flags, messages, and problem score.
        """
        user_is_staff = self.runtime.service(self, "user").get_current_user().opt_attrs.get(ATTR_KEY_USER_IS_STAFF)
        return ShowCorrectness.correctness_available(
            show_correctness=self.show_correctness,
            due_date=self.close_date,
            has_staff_access=user_is_staff,
        )

    def update_score(self, data):
        """
        Delivers grading response (e.g. from asynchronous code checking) to
            the capa problem, so its score can be updated

        'data' must have a key 'response' which is a string that contains the
            grader's response

        No ajax return is needed. Return empty dict.
        """
        queuekey = data["queuekey"]
        score_msg = data["xqueue_body"]
        self.lcp.update_score(score_msg, queuekey)
        self.set_state_from_lcp()
        self.set_score(self.score_from_lcp(self.lcp))
        self.publish_grade(grader_response=True)

        return {}  # No AJAX return is needed

    def handle_ungraded_response(self, data):
        """
        Delivers a response from the XQueue to the capa problem

        The score of the problem will not be updated

        Args:
            - data (dict) must contain keys:
                            queuekey - a key specific to this response
                            xqueue_body - the body of the response
        Returns:
            empty dictionary

        No ajax return is needed, so an empty dict is returned
        """
        queuekey = data["queuekey"]
        score_msg = data["xqueue_body"]

        # pass along the xqueue message to the problem
        self.lcp.ungraded_response(score_msg, queuekey)
        self.set_state_from_lcp()
        return {}

    def handle_input_ajax(self, data):
        """
        Handle ajax calls meant for a particular input in the problem

        Args:
            - data (dict) - data that should be passed to the input
        Returns:
            - dict containing the response from the input
        """
        response = self.lcp.handle_input_ajax(data)

        # save any state changes that may occur
        self.set_state_from_lcp()
        return response

    def get_answer(self, _data):
        """
        For the "show answer" button.

        Returns the answers and rendered "correct status span" HTML:
            {'answers' : answers, 'correct_status_html': correct_status_span_html}.
            The "correct status span" HTML is injected beside the correct answers
            for radio button and checkmark problems, so that there is a visual
            indication of the correct answers that is not solely based on color
            (and also screen reader text).
        """
        event_info = {}
        event_info["problem_id"] = str(self.location)
        self.publish_unmasked("showanswer", event_info)
        if not self.answer_available():
            raise NotFoundError("Answer is not available")

        answers = self.lcp.get_question_answers()
        self.set_state_from_lcp()

        # answers (eg <solution>) may have embedded images
        #   but be careful, some problems are using non-string answer dicts
        new_answers = {}
        for answer_id, answer_value in answers.items():
            try:
                answer_content = self.runtime.service(self, "replace_urls").replace_urls(answer_value)
                new_answer = {answer_id: answer_content}
            except TypeError:
                log.debug("Unable to perform URL substitution on answers[%s]: %s", answer_id, answer_value)
                new_answer = {answer_id: answer_value}
            new_answers.update(new_answer)

        return {
            "answers": new_answers,
            "correct_status_html": render_to_string(
                "status_span.html",
                {"status": Status("correct", self.runtime.service(self, "i18n").gettext)},
            ),
        }

    # Figure out if we should move these to capa_problem?
    def get_problem(self, _data):
        """
        Return results of get_problem_html, as a simple dict for json-ing.
        { 'html': <the-html> }

        Used if we want to reconfirm we have the right thing e.g. after
        several AJAX calls.
        """
        return {"html": self.get_problem_html(encapsulate=False, submit_notification=True)}

    @staticmethod
    def make_dict_of_responses(data):
        """
        Make dictionary of student responses (aka "answers")

        `data` is POST dictionary (webob.multidict.MultiDict).

        The `data` dict has keys of the form 'x_y', which are mapped
        to key 'y' in the returned dict.  For example,
        'input_1_2_3' would be mapped to '1_2_3' in the returned dict.

        Some inputs always expect a list in the returned dict
        (e.g. checkbox inputs).  The convention is that
        keys in the `data` dict that end with '[]' will always
        have list values in the returned dict.
        For example, if the `data` dict contains {'input_1[]': 'test' }
        then the output dict would contain {'1': ['test'] }
        (the value is a list).

        Some other inputs such as ChoiceTextInput expect a dict of values in the returned
        dict  If the key ends with '{}' then we will assume that the value is a json
        encoded dict and deserialize it.
        For example, if the `data` dict contains {'input_1{}': '{"1_2_1": 1}'}
        then the output dict would contain {'1': {"1_2_1": 1} }
        (the value is a dictionary)

        Raises an exception if:

        -A key in the `data` dictionary does not contain at least one underscore
          (e.g. "input" is invalid, but "input_1" is valid)

        -Two keys end up with the same name in the returned dict.
          (e.g. 'input_1' and 'input_1[]', which both get mapped to 'input_1'
           in the returned dict)
        """
        answers = {}

        # webob.multidict.MultiDict is a view of a list of tuples,
        # so it will return a multi-value key once for each value.
        # We only want to consider each key a single time, so we use set(data.keys())
        for key in set(data.keys()):
            # e.g. input_resistor_1 ==> resistor_1
            _, _, name = key.partition("_")

            # If key has no underscores, then partition
            # will return (key, '', '')
            # We detect this and raise an error
            if not name:
                raise ValueError(f"{key} must contain at least one underscore")

            # This allows for answers which require more than one value for
            # the same form input (e.g. checkbox inputs). The convention is that
            # if the name ends with '[]' (which looks like an array), then the
            # answer will be an array.
            # if the name ends with '{}' (Which looks like a dict),
            # then the answer will be a dict
            is_list_key = name.endswith("[]")
            is_dict_key = name.endswith("{}")
            name = name[:-2] if is_list_key or is_dict_key else name

            if is_list_key:
                val = data.getall(key)
            elif is_dict_key:
                try:
                    val = json.loads(data[key])
                # If the submission wasn't deserializable, raise an error.
                except (KeyError, ValueError) as exc:
                    raise ValueError(f"Invalid submission: {data[key]} for {key}") from exc
            else:
                val = data[key]

            # If the name already exists, then we don't want
            # to override it.  Raise an error instead
            if name in answers:
                raise ValueError(f"Key {name} already exists in answers dict")

            answers[name] = val

        return answers

    def publish_grade(self, score=None, only_if_higher=None, **kwargs):
        """
        Publishes the student's current grade to the system as an event
        """
        if not score:
            score = self.score
        event = {
            "value": score.raw_earned,
            "max_value": score.raw_possible,
            "only_if_higher": only_if_higher,
        }
        if kwargs.get("grader_response"):
            event["grader_response"] = kwargs["grader_response"]

        self.runtime.publish(self, "grade", event)

        return {"grade": self.score.raw_earned, "max_grade": self.score.raw_possible}

    def submit_problem(  # pylint: disable=too-many-statements,too-many-branches,too-many-locals
        self, data, override_time=False
    ):
        """
        Checks whether answers to a problem are correct

        Returns a map of correct/incorrect answers:
          {'success' : 'correct' | 'incorrect' | AJAX alert msg string,
           'contents' : html}
        """
        event_info = {}
        event_info["state"] = self.lcp.get_state()
        event_info["problem_id"] = str(self.location)

        self.lcp.has_saved_answers = False
        answers = self.make_dict_of_responses(data)
        answers_without_files = convert_files_to_filenames(answers)
        self.student_answers_history.append(answers_without_files)
        event_info["answers"] = answers_without_files

        # Can override current time
        current_time = datetime.datetime.now(ZoneInfo("UTC"))
        if override_time is not False:
            current_time = override_time

        _ = self.runtime.service(self, "i18n").gettext

        # Too late. Cannot submit
        if self.closed():
            log.error(
                "ProblemClosedError: Problem %s, close date: %s, due:%s, is_past_due: %s, attempts: %s/%s,",
                str(self.location),
                self.close_date,
                self.due,
                self.is_past_due(),
                self.attempts,
                self.max_attempts,
            )
            event_info["failure"] = "closed"
            self.publish_unmasked("problem_check_fail", event_info)
            raise NotFoundError(_("Problem is closed."))

        # Problem submitted. Student should reset before checking again
        if self.done and self.rerandomize == RANDOMIZATION.ALWAYS:
            event_info["failure"] = "unreset"
            self.publish_unmasked("problem_check_fail", event_info)
            raise NotFoundError(_("Problem must be reset before it can be submitted again."))

        # Problem queued. Students must wait a specified waittime before they are allowed to submit
        # IDEA: consider stealing code from below: pretty-print of seconds, cueing of time remaining
        if self.lcp.is_queued():
            prev_submit_time = self.lcp.get_recentmost_queuetime()

            xqueue_service = self.lcp.capa_system.xqueue
            waittime_between_requests = xqueue_service.waittime if xqueue_service else 0
            if (current_time - prev_submit_time).total_seconds() < waittime_between_requests:
                msg = _("You must wait at least {wait} seconds between submissions.").format(
                    wait=waittime_between_requests
                )
                return {"success": msg, "html": ""}

        # Wait time between resets: check if is too soon for submission.
        if self.last_submission_time is not None and self.submission_wait_seconds not in [0, None]:
            seconds_since_submission = (current_time - self.last_submission_time).total_seconds()
            if seconds_since_submission < self.submission_wait_seconds:
                remaining_secs = int(self.submission_wait_seconds - seconds_since_submission)
                msg = _("You must wait at least {wait_secs} between submissions. {remaining_secs} remaining.").format(
                    wait_secs=self.pretty_print_seconds(self.submission_wait_seconds),
                    remaining_secs=self.pretty_print_seconds(remaining_secs),
                )
                return {"success": msg, "html": ""}

        try:
            # expose the attempt number to a potential python custom grader
            # self.lcp.context['attempt'] refers to the attempt number (1-based)
            self.lcp.context["attempt"] = self.attempts + 1
            correct_map = self.lcp.grade_answers(answers)
            # self.attempts refers to the number of attempts that did not
            # raise an error (0-based)
            self.attempts = self.attempts + 1
            self.lcp.done = True
            self.set_state_from_lcp()

            current_score = self.score_from_lcp(self.lcp)
            self.score_history.append(current_score)
            current_score = self.get_score_with_grading_method(current_score)
            self.set_score(current_score)
            self.set_last_submission_time()

        except (StudentInputError, ResponseError, LoncapaProblemError) as inst:
            if self.debug:
                log.warning("StudentInputError in capa_block:problem_check", exc_info=True)

            # Save the user's state before failing
            self.set_state_from_lcp()
            self.set_score(self.score_from_lcp(self.lcp))

            # If the user is a staff member, include
            # the full exception, including traceback,
            # in the response
            user_is_staff = self.runtime.service(self, "user").get_current_user().opt_attrs.get(ATTR_KEY_USER_IS_STAFF)
            if user_is_staff:
                msg = f"Staff debug info: {traceback.format_exc()}"

            # Otherwise, display just an error message,
            # without a stack trace
            else:
                full_error = inst.args[0]
                try:
                    # only return the error value of the exception
                    msg = full_error.split("\\n")[-2].split(": ", 1)[1]
                except IndexError:
                    msg = full_error

            return {"success": msg}

        except Exception as err:
            # Save the user's state before failing
            self.set_state_from_lcp()
            self.set_score(self.score_from_lcp(self.lcp))

            if self.debug:
                msg = f"Error checking problem: {str(err)}"
                msg += f"\nTraceback:\n{traceback.format_exc()}"
                return {"success": msg}
            raise
        published_grade = self.publish_grade()

        # success = correct if ALL questions in this problem are correct
        success = "correct"
        for answer_id in correct_map:
            if not correct_map.is_correct(answer_id):
                success = "incorrect"

        # NOTE: We are logging both full grading and queued-grading submissions. In the latter,
        #       'success' will always be incorrect
        event_info["grade"] = published_grade["grade"]
        event_info["max_grade"] = published_grade["max_grade"]
        event_info["correct_map"] = correct_map.get_dict()
        event_info["success"] = success
        event_info["attempts"] = self.attempts
        event_info["submission"] = self.get_submission_metadata_safe(answers_without_files, correct_map)
        self.publish_unmasked("problem_check", event_info)

        # render problem into HTML
        html = self.get_problem_html(encapsulate=False, submit_notification=True)

        # Withhold success indicator if hiding correctness
        if not self.correctness_available():
            success = "submitted"

        return {"success": success, "contents": html}

    def get_score_with_grading_method(self, current_score: Score) -> Score:
        """
        Calculate and return the current score based on the grading method.

        Args:
            current_score (Score): The current score of the LON-CAPA problem.

        In this method:
            - The current score is obtained from the LON-CAPA problem.
            - The score history is updated adding the current score.

        Returns:
            Score: The score based on the grading method.
        """
        grading_method_handler = GradingMethodHandler(
            current_score,
            self.grading_method,
            self.score_history,
            self.max_score(),
        )
        return grading_method_handler.get_score()

    def publish_unmasked(self, title, event_info):
        """
        All calls to runtime.publish route through here so that the
        choice names can be unmasked.
        """
        # Do the unmask translates on a copy of event_info,
        # avoiding problems where an event_info is unmasked twice.
        event_unmasked = copy.deepcopy(event_info)
        self.unmask_event(event_unmasked)
        self.runtime.publish(self, title, event_unmasked)

    def unmask_event(self, event_info):
        """
        Translates in-place the event_info to account for masking
        and adds information about permutation options in force.
        """
        # answers is like: {u'i4x-Stanford-CS99-problem-dada976e76f34c24bc8415039dee1300_2_1': u'mask_0'}
        # Each response values has an answer_id which matches the key in answers.
        for response in self.lcp.responders.values():
            # Un-mask choice names in event_info for masked responses.
            if response.has_mask():
                # We don't assume much about the structure of event_info,
                # but check for the existence of the things we need to un-mask.

                # Look for answers/id
                answer = event_info.get("answers", {}).get(response.answer_id)
                if answer is not None:
                    event_info["answers"][response.answer_id] = response.unmask_name(answer)

                # Look for state/student_answers/id
                answer = event_info.get("state", {}).get("student_answers", {}).get(response.answer_id)
                if answer is not None:
                    event_info["state"]["student_answers"][response.answer_id] = response.unmask_name(answer)

                # Look for old_state/student_answers/id  -- parallel to the above case, happens on reset
                answer = event_info.get("old_state", {}).get("student_answers", {}).get(response.answer_id)
                if answer is not None:
                    event_info["old_state"]["student_answers"][response.answer_id] = response.unmask_name(answer)

            # Add 'permutation' to event_info for permuted responses.
            permutation_option = None
            if response.has_shuffle():
                permutation_option = "shuffle"
            elif response.has_answerpool():
                permutation_option = "answerpool"

            if permutation_option is not None:
                # Add permutation record tuple: (one of:'shuffle'/'answerpool', [as-displayed list])
                if "permutation" not in event_info:
                    event_info["permutation"] = {}
                event_info["permutation"][response.answer_id] = (permutation_option, response.unmask_order())

    def pretty_print_seconds(self, num_seconds):
        """
        Returns time duration nicely formated, e.g. "3 minutes 4 seconds"
        """
        # Here _ is the N variant ungettext that does pluralization with a 3-arg call
        ngettext = self.runtime.service(self, "i18n").ngettext
        hours = num_seconds // 3600
        sub_hour = num_seconds % 3600
        minutes = sub_hour // 60
        seconds = sub_hour % 60
        display = ""
        if hours > 0:
            display += ngettext("{num_hour} hour", "{num_hour} hours", hours).format(num_hour=hours)
        if minutes > 0:
            if display != "":
                display += " "
            # translators: "minute" refers to a minute of time
            display += ngettext("{num_minute} minute", "{num_minute} minutes", minutes).format(num_minute=minutes)
        # Taking care to make "0 seconds" instead of "" for 0 time
        if seconds > 0 or (hours == 0 and minutes == 0):
            if display != "":
                display += " "
            # translators: "second" refers to a second of time
            display += ngettext("{num_second} second", "{num_second} seconds", seconds).format(num_second=seconds)
        return display

    def get_submission_metadata_safe(self, answers, correct_map):
        """
        Ensures that no exceptions are thrown while generating input metadata summaries.  Returns the
        summary if it is successfully created, otherwise an empty dictionary.
        """
        try:
            return self.get_submission_metadata(answers, correct_map)
        except Exception:  # pylint: disable=broad-exception-caught
            # NOTE: The above process requires deep inspection of capa structures that may break for some
            # uncommon problem types.  Ensure that it does not prevent answer submission in those
            # cases.  Any occurrences of errors in this block should be investigated and resolved.
            log.exception("Unable to gather submission metadata, it will not be included in the event.")

        return {}

    def get_submission_metadata(self, answers, correct_map):
        """
        Return a map of inputs to their corresponding summarized metadata.

        Returns:
            A map whose keys are a unique identifier for the input (in this case a capa input_id) and
            whose values are:

                question (str): Is the prompt that was presented to the student.  It corresponds to the
                    label of the input.
                answer (mixed): Is the answer the student provided.  This may be a rich structure,
                    however it must be json serializable.
                response_type (str): The XML tag of the capa response type.
                input_type (str): The XML tag of the capa input type.
                correct (bool): Whether or not the provided answer is correct.  Will be an empty
                    string if correctness could not be determined.
                variant (str): In some cases the same question can have several different variants.
                    This string should uniquely identify the variant of the question that was answered.
                    In the capa context this corresponds to the `seed`.

        This function attempts to be very conservative and make very few assumptions about the structure
        of the problem.  If problem related metadata cannot be located it should be replaced with empty
        strings ''.
        """
        input_metadata = {}
        for input_id, internal_answer in answers.items():
            answer_input = self.lcp.inputs.get(input_id)

            if answer_input is None:
                log.warning("Input id %s is not mapped to an input type.", input_id)

            answer_response = None
            for responder in self.lcp.responders.values():
                if input_id in responder.answer_ids:
                    answer_response = responder

            if answer_response is None:
                log.warning("Answer responder could not be found for input_id %s.", input_id)

            user_visible_answer = internal_answer
            if hasattr(answer_input, "get_user_visible_answer"):
                user_visible_answer = answer_input.get_user_visible_answer(internal_answer)

            # If this problem has rerandomize enabled, then it will generate N variants of the
            # question, one per unique seed value.  In this case we would like to know which
            # variant was selected.  Ideally it would be nice to have the exact question that
            # was presented to the user, with values interpolated etc, but that can be done
            # later if necessary.
            variant = ""
            if self.rerandomize != RANDOMIZATION.NEVER:
                variant = self.get_seed()

            is_correct = correct_map.is_correct(input_id)
            if is_correct is None:
                is_correct = ""

            response_data = getattr(answer_input, "response_data", {})
            input_metadata[input_id] = {
                "question": response_data.get("label", ""),
                "answer": user_visible_answer,
                "response_type": getattr(getattr(answer_response, "xml", None), "tag", ""),
                "input_type": getattr(answer_input, "tag", ""),
                "correct": is_correct,
                "variant": variant,
                "group_label": response_data.get("group_label", ""),
            }

        return input_metadata

    def save_problem(self, data):
        """
        Save the passed in answers.
        Returns a dict { 'success' : bool, 'msg' : message }
        The message is informative on success, and an error message on failure.
        """
        event_info = {}
        event_info["state"] = self.lcp.get_state()
        event_info["problem_id"] = str(self.location)

        answers = self.make_dict_of_responses(data)
        event_info["answers"] = answers
        _ = self.runtime.service(self, "i18n").gettext

        # Too late. Cannot submit
        if self.closed() and not self.max_attempts == 0:
            event_info["failure"] = "closed"
            self.publish_unmasked("save_problem_fail", event_info)
            return {
                "success": False,
                # Translators: 'closed' means the problem's due date has passed.
                # You may no longer attempt to solve the problem.
                "msg": _("Problem is closed."),
            }

        # Problem submitted. Student should reset before saving
        # again.
        if self.done and self.rerandomize == RANDOMIZATION.ALWAYS:
            event_info["failure"] = "done"
            self.publish_unmasked("save_problem_fail", event_info)
            return {"success": False, "msg": _("Problem needs to be reset prior to save.")}

        self.lcp.student_answers = answers
        self.lcp.has_saved_answers = True

        self.set_state_from_lcp()
        self.set_score(self.score_from_lcp(self.lcp))

        self.publish_unmasked("save_problem_success", event_info)
        msg = _("Your answers have been saved.")
        if not self.max_attempts == 0:
            msg = _("Your answers have been saved but not graded. Click '{button_name}' to grade them.").format(
                button_name=self.submit_button_name()
            )
        return {"success": True, "msg": msg, "html": self.get_problem_html(encapsulate=False)}

    def reset_problem(self, _data):
        """
        Changes problem state to unfinished -- removes student answers,
        Causes problem to rerender itself if randomization is enabled.

        Returns a dictionary of the form:
          {'success': True/False,
           'html': Problem HTML string }

        If an error occurs, the dictionary will also have an
        `error` key containing an error message.
        """
        event_info = {}
        event_info["old_state"] = self.lcp.get_state()
        event_info["problem_id"] = str(self.location)
        _ = self.runtime.service(self, "i18n").gettext

        if self.closed():
            event_info["failure"] = "closed"
            self.publish_unmasked("reset_problem_fail", event_info)
            return {
                "success": False,
                # Translators: 'closed' means the problem's due date has passed.
                # You may no longer attempt to solve the problem.
                "msg": _("You cannot select Reset for a problem that is closed."),
            }

        if not self.is_submitted():
            event_info["failure"] = "not_done"
            self.publish_unmasked("reset_problem_fail", event_info)
            return {
                "success": False,
                "msg": _("You must submit an answer before you can select Reset."),
            }

        if self.is_submitted() and self.rerandomize in [RANDOMIZATION.ALWAYS, RANDOMIZATION.ONRESET]:
            # Reset random number generator seed.
            self.choose_new_seed()

        # Generate a new problem with either the previous seed or a new seed
        self.lcp = self.new_lcp(None)

        # Pull in the new problem seed
        self.set_state_from_lcp()
        self.set_score(self.score_from_lcp(self.lcp))

        # Grade may have changed, so publish new value
        self.publish_grade()

        event_info["new_state"] = self.lcp.get_state()
        self.publish_unmasked("reset_problem", event_info)

        return {
            "success": True,
            "html": self.get_problem_html(encapsulate=False),
        }

    # ScorableXBlockMixin methods

    def rescore(self, only_if_higher=False):
        """
        Checks whether the existing answers to a problem are correct.

        This is called when the correct answer to a problem has been changed,
        and the grade should be re-evaluated.

        If only_if_higher is True, the answer and grade are updated
        only if the resulting score is higher than before.

        Returns a dict with one key:
            {'success' : 'correct' | 'incorrect' | AJAX alert msg string }

        Raises NotFoundError if called on a problem that has not yet been
        answered, or NotImplementedError if it's a problem that cannot be rescored.

        Returns the error messages for exceptions occurring while performing
        the rescoring, rather than throwing them.
        """
        event_info = {"state": self.lcp.get_state(), "problem_id": str(self.location)}

        _ = self.runtime.service(self, "i18n").gettext

        if not self.lcp.supports_rescoring():
            event_info["failure"] = "unsupported"
            self.publish_unmasked("problem_rescore_fail", event_info)

            # Translators: 'rescoring' refers to the act of re-submitting a student's
            # solution so it can get a new score.
            raise NotImplementedError(_("Problem's definition does not support rescoring."))

        if not self.done:
            event_info["failure"] = "unanswered"
            self.publish_unmasked("problem_rescore_fail", event_info)
            raise NotFoundError(_("Problem must be answered before it can be graded again."))

        # get old score, for comparison:
        orig_score = self.get_score()
        event_info["orig_score"] = orig_score.raw_earned
        event_info["orig_total"] = orig_score.raw_possible
        try:
            calculated_score = self.calculate_score()
        except (
            StudentInputError,
            ResponseError,
            LoncapaProblemError,
        ):
            log.warning("Input error in capa_block:problem_rescore", exc_info=True)
            event_info["failure"] = "input_error"
            self.publish_unmasked("problem_rescore_fail", event_info)
            raise

        except Exception:
            event_info["failure"] = "unexpected"
            self.publish_unmasked("problem_rescore_fail", event_info)
            raise

        # rescoring should have no effect on attempts, so don't
        # need to increment here, or mark done.  Just save.
        self.set_state_from_lcp()
        self.publish_grade(score=calculated_score, only_if_higher=only_if_higher)

        event_info["new_score"] = calculated_score.raw_earned
        event_info["new_total"] = calculated_score.raw_possible

        # success = correct if ALL questions in this problem are correct
        success = "correct"
        for answer_id in self.lcp.correct_map:
            if not self.lcp.correct_map.is_correct(answer_id):
                success = "incorrect"

        # NOTE: We are logging both full grading and queued-grading submissions. In the latter,
        #       'success' will always be incorrect
        event_info["correct_map"] = self.lcp.correct_map.get_dict()
        event_info["success"] = success
        event_info["attempts"] = self.attempts
        self.publish_unmasked("problem_rescore", event_info)

    def get_rescore_with_grading_method(self) -> Score:
        """
        Calculate and return the rescored score based on the grading method.

        In this method:
            - The list with the correctness maps is updated.
            - The list with the score history is updated based on the correctness maps.
            - The final score is calculated based on the grading method.

        Returns:
            Score: The score calculated based on the grading method.
        """
        self.update_correctness_list()
        self.score_history = self.calculate_score_list()
        grading_method_handler = GradingMethodHandler(
            self.score,
            self.grading_method,
            self.score_history,
            self.max_score(),
        )
        return grading_method_handler.get_score()

    def has_submitted_answer(self):
        """Return True if the learner has already submitted an answer."""
        return self.done

    def set_score(self, score):
        """
        Sets the internal score for the problem. This is not derived directly
        from the internal LCP in keeping with the ScorableXBlock spec.
        """
        self.score = score

    def get_score(self):
        """
        Returns the score currently set on the block.
        """
        return self.score

    def update_correctness_list(self):
        """
        Updates the `correct_map_history` and the `correct_map` of the LCP.

        Operates by creating a new correctness map based on the current
        state of the LCP, and updating the old correctness map of the LCP.
        """
        # Make sure that the attempt number is always at least 1 for grading purposes,
        # even if the number of attempts have been reset and this problem is regraded.
        self.lcp.context["attempt"] = max(self.attempts, 1)
        new_correct_map_list = []
        for student_answers, correct_map in zip(self.student_answers_history, self.correct_map_history):
            new_correct_map = self.lcp.get_grade_from_current_answers(student_answers, correct_map)
            new_correct_map_list.append(new_correct_map)
        self.lcp.correct_map_history = new_correct_map_list
        if new_correct_map_list:
            self.lcp.correct_map.update(new_correct_map_list[-1])

    def calculate_score(self):
        """
        Returns the score calculated from the current problem state.

        The score is calculated based on the grading method.
        """
        return self.get_rescore_with_grading_method()

    def calculate_score_list(self):
        """
        Returns the score calculated from the current problem state.
        """
        new_score_list = []

        for correct_map in self.lcp.correct_map_history:
            new_score = self.lcp.calculate_score(correct_map)
            new_score_list.append(Score(raw_earned=new_score["score"], raw_possible=new_score["total"]))
        return new_score_list

    def score_from_lcp(self, lcp):
        """
        Returns the score associated with the correctness map
        currently stored by the LCP.
        """
        lcp_score = lcp.calculate_score()
        return Score(raw_earned=lcp_score["score"], raw_possible=lcp_score["total"])


class GradingMethodHandler:
    """
    A class for handling grading method and calculating scores.

    This class allows for flexible handling of grading methods, including options
    such as considering the last score, the first score, the highest score,
    or the average score.

    Attributes:
        - score (Score): The current score.
        - grading_method (str): The chosen grading method.
        - score_history (list[Score]): A list to store the history of scores.
        - max_score (int): The maximum possible score.
        - mapping_method (dict): A dictionary mapping the grading
            method to the corresponding handler.

    Methods:
        - get_score(): Retrieves the updated score based on the grading method.
        - handle_last_score(): Handles the last score method.
        - handle_first_score(): Handles the first score method.
        - handle_highest_score(): Handles the highest score method.
        - handle_average_score(): Handles the average score method.
    """

    def __init__(
        self,
        score: Score,
        grading_method: str,
        score_history: list[Score],
        max_score: int,
    ):
        self.score = score
        self.grading_method = grading_method
        self.score_history = score_history
        if not self.score_history:
            self.score_history.append(score)
        self.max_score = max_score
        self.mapping_method = {
            GRADING_METHOD.LAST_SCORE: self.handle_last_score,
            GRADING_METHOD.FIRST_SCORE: self.handle_first_score,
            GRADING_METHOD.HIGHEST_SCORE: self.handle_highest_score,
            GRADING_METHOD.AVERAGE_SCORE: self.handle_average_score,
        }

    def get_score(self) -> Score:
        """
        Retrieves the updated score based on the grading method.

        Returns:
            - Score: The updated score based on the chosen grading method.
        """
        return self.mapping_method[self.grading_method]()

    def handle_last_score(self) -> Score:
        """
        Retrieves the score based on the last score.
        It is the last score in the score history.

        Returns:
            - Score: The score based on the last score.
        """
        return self.score_history[-1]

    def handle_first_score(self) -> Score:
        """
        Retrieves the score based on the first score.
        It is the first score in the score history.

        Returns:
            - Score: The score based on the first score.
        """
        return self.score_history[0]

    def handle_highest_score(self) -> Score:
        """
        Retrieves the score based on the highest score.
        It is the highest score in the score history.

        Returns:
            - Score: The score based on the highest score.
        """
        return max(self.score_history)

    def handle_average_score(self) -> Score:
        """
        Calculates the average score based on all attempts. The average score is
        the sum of all scores divided by the number of scores.

        Returns:
            - Score: The average score based on all attempts.
        """
        total = sum(score.raw_earned for score in self.score_history)
        average_score = round(total / len(self.score_history), 2)
        return Score(raw_earned=average_score, raw_possible=self.max_score)


class ComplexEncoder(json.JSONEncoder):
    """
    Extend the JSON encoder to correctly handle complex numbers
    """

    def default(self, o):
        """
        Print a nicely formatted complex number, or default to the JSON encoder
        """
        if isinstance(o, complex):
            return f"{o.real:.7g}{o.imag:+.7g}*j"
        return json.JSONEncoder.default(self, o)


def randomization_bin(seed, problem_id):
    """
    Pick a randomization bin for the problem given the user's seed and a problem id.

    We do this because we only want e.g. 20 randomizations of a problem to make analytics
    interesting.  To avoid having sets of students that always get the same problems,
    we'll combine the system's per-student seed with the problem id in picking the bin.
    """
    r_hash = hashlib.sha256()
    r_hash.update(str(seed).encode())
    r_hash.update(str(problem_id).encode())
    # get the first few digits of the hash, convert to an int, then mod.
    return int(r_hash.hexdigest()[:7], 16) % NUM_RANDOMIZATION_BINS


ProblemBlock = _ExtractedProblemBlock if settings.USE_EXTRACTED_PROBLEM_BLOCK else _BuiltInProblemBlock
ProblemBlock.__name__ = "ProblemBlock"
