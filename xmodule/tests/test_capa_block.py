# pylint: disable=too-many-lines
"""
Tests of the Capa XModule
"""

import datetime
import json
import os
import random
import textwrap
import unittest
from unittest.mock import DEFAULT, Mock, PropertyMock, patch
from zoneinfo import ZoneInfo

import ddt
import pytest
import requests
import webob
from codejail.safe_exec import SafeExecException
from django.test import override_settings
from django.utils.encoding import smart_str
from lxml import etree
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator
from webob.multidict import MultiDict
from xblock.exceptions import NotFoundError
from xblock.field_data import DictFieldData
from xblock.fields import ScopeIds
from xblock.scorable import Score

from lms.djangoapps.courseware.user_state_client import XBlockUserState
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.capa import responsetypes
from xmodule.capa.correctmap import CorrectMap
from xmodule.capa.responsetypes import (
    LoncapaProblemError,
    ResponseError,
    StudentInputError,
)
from xmodule.capa.tests.test_util import UseUnsafeCodejail
from xmodule.capa.xqueue_interface import XQueueInterface
from xmodule.capa_block import ComplexEncoder, ProblemBlock
from xmodule.tests import DATA_DIR

from ..capa_block import RANDOMIZATION, SHOWANSWER
from . import get_test_system


class CapaFactory:
    """
    A helper class to create problem blocks with various parameters for testing.
    """

    sample_problem_xml = textwrap.dedent(
        """\
        <?xml version="1.0"?>
        <problem>
            <text>
                <p>What is pi, to two decimal places?</p>
            </text>
        <numericalresponse answer="3.14">
        <textline math="1" size="30"/>
        </numericalresponse>
        </problem>
    """
    )

    num = 0

    @classmethod
    def next_num(cls):
        """Increment and return a unique number for naming problems."""
        cls.num += 1
        return cls.num

    @classmethod
    def input_key(cls, response_num=2, input_num=1):
        """
        Return the input key to use when passing GET parameters
        """
        return "input_" + cls.answer_key(response_num, input_num)

    @classmethod
    def answer_key(cls, response_num=2, input_num=1):
        """
        Return the key stored in the capa problem answer dict
        """
        return (
            f"{'-'.join(['i4x', 'edX', 'capa_test', 'problem', f'SampleProblem{cls.num}'])}_{response_num}_{input_num}"
        )

    @classmethod
    def create(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        cls,
        attempts=None,
        problem_state=None,
        correct=False,
        xml=None,
        override_get_score=True,
        render_template=None,
        **kwargs,
    ):
        """
        All parameters are optional, and are added to the created problem if specified.

        Arguments:
            graceperiod:
            due:
            max_attempts:
            showanswer:
            force_save_button:
            rerandomize: all strings, as specified in the policy for the problem

            problem_state: a dict to be serialized into the instance_state of the block.

            attempts: also added to instance state.  Will be converted to an int.

            render_template: pass function or Mock for testing
        """
        location = BlockUsageLocator(
            CourseLocator("edX", "capa_test", "2012_Fall", deprecated=True),
            "problem",
            f"SampleProblem{cls.next_num()}",
            deprecated=True,
        )
        if xml is None:
            xml = cls.sample_problem_xml
        field_data = {"data": xml}
        field_data.update(kwargs)
        if problem_state is not None:
            field_data.update(problem_state)
        if attempts is not None:
            # converting to int here because I keep putting "0" and "1" in the tests
            # since everything else is a string.
            field_data["attempts"] = int(attempts)

        system = get_test_system(
            course_id=location.course_key,
            user_is_staff=kwargs.get("user_is_staff", False),
            render_template=render_template or Mock(return_value="<div>Test Template HTML</div>"),
        )
        block = ProblemBlock(
            system,
            DictFieldData(field_data),
            ScopeIds(None, "problem", location, location),
        )
        assert block.lcp

        if override_get_score:
            if correct:
                # TODO: probably better to actually set the internal state properly, but...
                block.score = Score(raw_earned=1, raw_possible=1)
            else:
                block.score = Score(raw_earned=0, raw_possible=1)

        block.graded = "False"
        block.weight = 1
        return block


class CapaFactoryWithFiles(CapaFactory):
    """
    A factory for creating a Capa problem with files attached.
    """

    sample_problem_xml = textwrap.dedent(
        """\
        <problem>
            <coderesponse queuename="BerkeleyX-cs188x">
                <!-- actual filenames here don't matter for server-side tests,
                     they are only acted upon in the browser. -->
                <filesubmission
                    points="25"
                    allowed_files="prog1.py prog2.py prog3.py"
                    required_files="prog1.py prog2.py prog3.py"
                />
                <codeparam>
                    <answer_display>
                        If you're having trouble with this Project,
                        please refer to the Lecture Slides and attend office hours.
                    </answer_display>
                    <grader_payload>{"project": "p3"}</grader_payload>
                </codeparam>
            </coderesponse>

            <customresponse>
                <text>
                    If you worked with a partner, enter their username or email address. If you
                    worked alone, enter None.
                </text>

                <textline points="0" size="40" correct_answer="Your partner's username or 'None'"/>
                <answer type="loncapa/python">
correct=['correct']
s = str(submission[0]).strip()
if submission[0] == '':
    correct[0] = 'incorrect'
                </answer>
            </customresponse>
        </problem>
    """
    )


@ddt.ddt
@skip_unless_lms
@pytest.mark.django_db
class ProblemBlockTest(unittest.TestCase):  # pylint: disable=too-many-public-methods
    """Tests for various problem types in XBlocks."""

    def setUp(self):
        super().setUp()

        now = datetime.datetime.now(ZoneInfo("UTC"))
        day_delta = datetime.timedelta(days=1)
        self.yesterday_str = str(now - day_delta)
        self.today_str = str(now)
        self.tomorrow_str = str(now + day_delta)

        # in the capa grace period format, not in time delta format
        self.two_day_delta_str = "2 days"

    def test_import(self):
        """Verify CapaFactory creates blocks with zero initial score and unique URLs."""
        block = CapaFactory.create()
        assert block.get_score().raw_earned == 0

        other_block = CapaFactory.create()
        assert block.get_score().raw_earned == 0
        assert block.url_name != other_block.url_name, "Factory should be creating unique names for each problem"

    def test_correct(self):
        """
        Check that the factory creates correct and incorrect problems properly.
        """
        block = CapaFactory.create()
        assert block.get_score().raw_earned == 0

        other_block = CapaFactory.create(correct=True)
        assert other_block.get_score().raw_earned == 1

    def test_get_score(self):
        """
        Tests the internals of get_score. In keeping with the ScorableXBlock spec,
        Capa blocks store their score independently of the LCP internals, so it must
        be explicitly updated.
        """
        student_answers = {"1_2_1": "abcd"}
        correct_map = CorrectMap(answer_id="1_2_1", correctness="correct", npoints=0.9)
        block = CapaFactory.create(correct=True, override_get_score=False)
        block.lcp.correct_map = correct_map
        block.lcp.student_answers = student_answers
        assert block.get_score().raw_earned == 0.0
        block.set_score(block.score_from_lcp(block.lcp))
        assert block.get_score().raw_earned == 0.9

        other_correct_map = CorrectMap(answer_id="1_2_1", correctness="incorrect", npoints=0.1)
        other_block = CapaFactory.create(correct=False, override_get_score=False)
        other_block.lcp.correct_map = other_correct_map
        other_block.lcp.student_answers = student_answers
        assert other_block.get_score().raw_earned == 0.0
        other_block.set_score(other_block.score_from_lcp(other_block.lcp))
        assert other_block.get_score().raw_earned == 0.1

    def test_showanswer_default(self):
        """
        Make sure the show answer logic does the right thing.
        """
        # default, no due date, showanswer 'closed', so problem is open, and show_answer
        # not visible.
        problem = CapaFactory.create()
        assert not problem.answer_available()

    @ddt.data(
        (requests.exceptions.ReadTimeout, (1, "failed to read from the server")),
        (requests.exceptions.ConnectionError, (1, "cannot connect to server")),
    )
    @ddt.unpack
    def test_xqueue_request_exception(self, exception, result):
        """
        Makes sure that platform will raise appropriate exception in case of
        connect/read timeout(s) to request to xqueue
        """
        xqueue_interface = XQueueInterface("http://example.com/xqueue", Mock())
        with patch.object(xqueue_interface.session, "post", side_effect=exception):
            response = xqueue_interface._http_post("http://some/fake/url", {})  # pylint: disable=protected-access
            assert response == result

    def test_showanswer_attempted(self):
        """Check answer availability changes after attempting the problem."""
        problem = CapaFactory.create(showanswer="attempted")
        assert not problem.answer_available()
        problem.attempts = 1
        assert problem.answer_available()

    @ddt.data(
        # If show_correctness=always, Answer is visible after attempted
        (
            {
                "showanswer": "attempted",
                "max_attempts": "1",
                "show_correctness": "always",
            },
            False,
            True,
        ),
        # If show_correctness=never, Answer is never visible
        (
            {
                "showanswer": "attempted",
                "max_attempts": "1",
                "show_correctness": "never",
            },
            False,
            False,
        ),
        # If show_correctness=past_due, answer is not visible before due date
        (
            {
                "showanswer": "attempted",
                "show_correctness": "past_due",
                "max_attempts": "1",
                "due": "tomorrow_str",
            },
            False,
            False,
        ),
        # If show_correctness=past_due, answer is visible after due date
        (
            {
                "showanswer": "attempted",
                "show_correctness": "past_due",
                "max_attempts": "1",
                "due": "yesterday_str",
            },
            True,
            True,
        ),
    )
    @ddt.unpack
    def test_showanswer_hide_correctness(
        self, problem_data, answer_available_no_attempt, answer_available_after_attempt
    ):
        """
        Ensure that the answer will not be shown when correctness is being hidden.
        """
        if "due" in problem_data:
            problem_data["due"] = getattr(self, problem_data["due"])
        problem = CapaFactory.create(**problem_data)
        assert problem.answer_available() == answer_available_no_attempt
        problem.attempts = 1
        assert problem.answer_available() == answer_available_after_attempt

    def test_showanswer_closed(self):
        """Check show answer visibility with showanswer='closed' and various conditions."""

        # can see after attempts used up, even with due date in the future
        used_all_attempts = CapaFactory.create(
            showanswer="closed", max_attempts="1", attempts="1", due=self.tomorrow_str
        )
        assert used_all_attempts.answer_available()

        # can see after due date
        after_due_date = CapaFactory.create(showanswer="closed", max_attempts="1", attempts="0", due=self.yesterday_str)

        assert after_due_date.answer_available()

        # can't see because attempts left
        attempts_left_open = CapaFactory.create(
            showanswer="closed", max_attempts="1", attempts="0", due=self.tomorrow_str
        )
        assert not attempts_left_open.answer_available()

        # Can't see because grace period hasn't expired
        still_in_grace = CapaFactory.create(
            showanswer="closed",
            max_attempts="1",
            attempts="0",
            due=self.yesterday_str,
            graceperiod=self.two_day_delta_str,
        )
        assert not still_in_grace.answer_available()

    def test_showanswer_correct_or_past_due(self):
        """
        With showanswer="correct_or_past_due" should show answer after the answer is correct
        or after the problem is closed for everyone--e.g. after due date + grace period.
        """

        # can see because answer is correct, even with due date in the future
        answer_correct = CapaFactory.create(
            showanswer="correct_or_past_due", max_attempts="1", attempts="0", due=self.tomorrow_str, correct=True
        )
        assert answer_correct.answer_available()

        # can see after due date, even when answer isn't correct
        past_due_date = CapaFactory.create(
            showanswer="correct_or_past_due", max_attempts="1", attempts="0", due=self.yesterday_str
        )
        assert past_due_date.answer_available()

        # can also see after due date when answer _is_ correct
        past_due_date_correct = CapaFactory.create(
            showanswer="correct_or_past_due", max_attempts="1", attempts="0", due=self.yesterday_str, correct=True
        )
        assert past_due_date_correct.answer_available()

        # Can't see because grace period hasn't expired and answer isn't correct
        still_in_grace = CapaFactory.create(
            showanswer="correct_or_past_due",
            max_attempts="1",
            attempts="1",
            due=self.yesterday_str,
            graceperiod=self.two_day_delta_str,
        )
        assert not still_in_grace.answer_available()

    def test_showanswer_past_due(self):
        """
        With showanswer="past_due" should only show answer after the problem is closed
        for everyone--e.g. after due date + grace period.
        """

        # can't see after attempts used up, even with due date in the future
        used_all_attempts = CapaFactory.create(
            showanswer="past_due", max_attempts="1", attempts="1", due=self.tomorrow_str
        )
        assert not used_all_attempts.answer_available()

        # can see after due date
        past_due_date = CapaFactory.create(
            showanswer="past_due", max_attempts="1", attempts="0", due=self.yesterday_str
        )
        assert past_due_date.answer_available()

        # can't see because attempts left
        attempts_left_open = CapaFactory.create(
            showanswer="past_due", max_attempts="1", attempts="0", due=self.tomorrow_str
        )
        assert not attempts_left_open.answer_available()

        # Can't see because grace period hasn't expired, even though have no more
        # attempts.
        still_in_grace = CapaFactory.create(
            showanswer="past_due",
            max_attempts="1",
            attempts="1",
            due=self.yesterday_str,
            graceperiod=self.two_day_delta_str,
        )
        assert not still_in_grace.answer_available()

    def test_showanswer_after_attempts_with_max(self):
        """
        Button should not be visible when attempts < required attempts.

        Even with max attempts set, the show answer button should only
        show up after the user has attempted answering the question for
        the requisite number of times, i.e `attempts_before_showanswer_button`
        """
        problem = CapaFactory.create(
            showanswer="after_attempts",
            attempts="2",
            attempts_before_showanswer_button="3",
            max_attempts="5",
        )
        assert not problem.answer_available()

    def test_showanswer_after_attempts_no_max(self):
        """
        Button should not be visible when attempts < required attempts.

        Even when max attempts is NOT set, the answer should still
        only be available after the student has attempted the
        problem at least `attempts_before_showanswer_button` times
        """
        problem = CapaFactory.create(
            showanswer="after_attempts",
            attempts="2",
            attempts_before_showanswer_button="3",
        )
        assert not problem.answer_available()

    def test_showanswer_after_attempts_used_all_attempts(self):
        """
        Button should be visible even after all attempts are used up.

        As long as the student has attempted  the question for
        the requisite number of times, then the show ans. button is
        visible even after they have exhausted their attempts.
        """
        problem = CapaFactory.create(
            showanswer="after_attempts",
            attempts_before_showanswer_button="2",
            max_attempts="3",
            attempts="3",
            due=self.tomorrow_str,
        )
        assert problem.answer_available()

    def test_showanswer_after_attempts_past_due_date(self):
        """
        Show Answer button should be visible even after the due date.

        As long as the student has attempted the problem for the requisite
        number of times, the answer should be available past the due date.
        """
        problem = CapaFactory.create(
            showanswer="after_attempts",
            attempts_before_showanswer_button="2",
            attempts="2",
            due=self.yesterday_str,
        )
        assert problem.answer_available()

    def test_showanswer_after_attempts_still_in_grace(self):
        """
        If attempts > required attempts, ans. is available in grace period.

        As long as the user has attempted for the requisite # of times,
        the show answer button is visible throughout the grace period.
        """
        problem = CapaFactory.create(
            showanswer="after_attempts",
            after_attempts="3",
            attempts="4",
            due=self.yesterday_str,
            graceperiod=self.two_day_delta_str,
        )
        assert problem.answer_available()

    def test_showanswer_after_attempts_large(self):
        """
        If required attempts > max attempts then required attempts = max attempts.

        Ensure that if attempts_before_showanswer_button > max_attempts,
        the button should show up after all attempts are used up,
        i.e after_attempts falls back to max_attempts
        """
        problem = CapaFactory.create(
            showanswer="after_attempts",
            attempts_before_showanswer_button="5",
            max_attempts="3",
            attempts="3",
        )
        assert problem.answer_available()

    def test_showanswer_after_attempts_zero(self):
        """
        Button should always be visible if required min attempts = 0.

        If attempts_before_showanswer_button = 0, then the show answer
        button should be visible at all times.
        """
        problem = CapaFactory.create(
            showanswer="after_attempts",
            attempts_before_showanswer_button="0",
            attempts="0",
        )
        assert problem.answer_available()

    def test_showanswer_finished(self):
        """
        With showanswer="finished" should show answer after the problem is closed,
        or after the answer is correct.
        """

        # can see after attempts used up, even with due date in the future
        used_all_attempts = CapaFactory.create(
            showanswer="finished", max_attempts="1", attempts="1", due=self.tomorrow_str
        )
        assert used_all_attempts.answer_available()

        # can see after due date
        past_due_date = CapaFactory.create(
            showanswer="finished", max_attempts="1", attempts="0", due=self.yesterday_str
        )
        assert past_due_date.answer_available()

        # can't see because attempts left and wrong
        attempts_left_open = CapaFactory.create(
            showanswer="finished", max_attempts="1", attempts="0", due=self.tomorrow_str
        )
        assert not attempts_left_open.answer_available()

        # _can_ see because attempts left and right
        correct_ans = CapaFactory.create(
            showanswer="finished", max_attempts="1", attempts="0", due=self.tomorrow_str, correct=True
        )
        assert correct_ans.answer_available()

        # Can see even though grace period hasn't expired, because have no more
        # attempts.
        still_in_grace = CapaFactory.create(
            showanswer="finished",
            max_attempts="1",
            attempts="1",
            due=self.yesterday_str,
            graceperiod=self.two_day_delta_str,
        )
        assert still_in_grace.answer_available()

    def test_showanswer_answered(self):
        """
        Tests that with showanswer="answered" should show answer after the problem is correctly answered.
        It should *NOT* show answer if the answer is incorrect.
        """
        # Can not see "Show Answer" when student answer is wrong
        answer_wrong = CapaFactory.create(
            showanswer=SHOWANSWER.ANSWERED, max_attempts="1", attempts="0", due=self.tomorrow_str, correct=False
        )
        assert not answer_wrong.answer_available()

        # Expect to see "Show Answer" when answer is correct
        answer_correct = CapaFactory.create(
            showanswer=SHOWANSWER.ANSWERED, max_attempts="1", attempts="0", due=self.tomorrow_str, correct=True
        )
        assert answer_correct.answer_available()

    @ddt.data("", "other-value")
    def test_show_correctness_other(self, show_correctness):
        """
        Test that correctness is visible if show_correctness is not set to one of the values
        from SHOW_CORRECTNESS constant.
        """
        problem = CapaFactory.create(show_correctness=show_correctness)
        assert problem.correctness_available()

    def test_show_correctness_default(self):
        """
        Test that correctness is visible by default.
        """
        problem = CapaFactory.create()
        assert problem.correctness_available()

    def test_show_correctness_never(self):
        """
        Test that correctness is hidden when show_correctness turned off.
        """
        problem = CapaFactory.create(show_correctness="never")
        assert not problem.correctness_available()

    @ddt.data(
        # Correctness not visible if due date in the future, even after using up all attempts
        (
            {
                "show_correctness": "past_due",
                "max_attempts": "1",
                "attempts": "1",
                "due": "tomorrow_str",
            },
            False,
        ),
        # Correctness visible if due date in the past
        (
            {
                "show_correctness": "past_due",
                "max_attempts": "1",
                "attempts": "0",
                "due": "yesterday_str",
            },
            True,
        ),
        # Correctness not visible if due date in the future
        (
            {
                "show_correctness": "past_due",
                "max_attempts": "1",
                "attempts": "0",
                "due": "tomorrow_str",
            },
            False,
        ),
        # Correctness not visible because grace period hasn't expired,
        # even after using up all attempts
        (
            {
                "show_correctness": "past_due",
                "max_attempts": "1",
                "attempts": "1",
                "due": "yesterday_str",
                "graceperiod": "two_day_delta_str",
            },
            False,
        ),
    )
    @ddt.unpack
    def test_show_correctness_past_due(self, problem_data, expected_result):
        """
        Test that with show_correctness="past_due", correctness will only be visible
        after the problem is closed for everyone--e.g. after due date + grace period.
        """
        problem_data["due"] = getattr(self, problem_data["due"])
        if "graceperiod" in problem_data:
            problem_data["graceperiod"] = getattr(self, problem_data["graceperiod"])
        problem = CapaFactory.create(**problem_data)
        assert problem.correctness_available() == expected_result

    def test_closed(self):
        """Verify problem closed status based on attempts and due date."""

        # Attempts < Max attempts --> NOT closed
        block = CapaFactory.create(max_attempts="1", attempts="0")
        assert not block.closed()

        # Attempts < Max attempts --> NOT closed
        block = CapaFactory.create(max_attempts="2", attempts="1")
        assert not block.closed()

        # Attempts = Max attempts --> closed
        block = CapaFactory.create(max_attempts="1", attempts="1")
        assert block.closed()

        # Attempts > Max attempts --> closed
        block = CapaFactory.create(max_attempts="1", attempts="2")
        assert block.closed()

        # Max attempts = 0 --> closed
        block = CapaFactory.create(max_attempts="0", attempts="2")
        assert block.closed()

        # Past due --> closed
        block = CapaFactory.create(max_attempts="1", attempts="0", due=self.yesterday_str)
        assert block.closed()

    @patch.object(ProblemBlock, "course_end_date", new_callable=PropertyMock)
    def test_closed_for_archive(self, mock_course_end_date):
        """Check closed status for archived and active courses with/without grace periods."""

        # Utility to create a datetime object in the past
        def past_datetime(days):
            return datetime.datetime.now(ZoneInfo("UTC")) - datetime.timedelta(days=days)

        # Utility to create a datetime object in the future
        def future_datetime(days):
            return datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(days=days)

        block = CapaFactory.create(max_attempts="1", attempts="0")

        # For active courses without graceperiod
        mock_course_end_date.return_value = future_datetime(10)
        assert not block.closed()

        # For archive courses without graceperiod
        mock_course_end_date.return_value = past_datetime(10)
        assert block.closed()

        # For active courses with graceperiod
        mock_course_end_date.return_value = future_datetime(10)
        block.graceperiod = datetime.timedelta(days=2)
        assert not block.closed()

        # For archive courses with graceperiod
        mock_course_end_date.return_value = past_datetime(2)
        block.graceperiod = datetime.timedelta(days=3)
        assert not block.closed()

    def test_parse_get_params(self):
        """Test parsing of GET parameters into response dictionaries with validation."""

        # Valid GET param dict
        # 'input_5' intentionally left unset,
        valid_get_dict = MultiDict(
            {
                "input_1": "test",
                "input_1_2": "test",
                "input_1_2_3": "test",
                "input_[]_3": "test",
                "input_4": None,
                "input_6": 5,
            }
        )

        result = ProblemBlock.make_dict_of_responses(valid_get_dict)

        # Expect that we get a dict with "input" stripped from key names
        # and that we get the same values back
        for key in result:
            original_key = "input_" + key
            assert original_key in valid_get_dict, f"Output dict should have key {original_key}"
            assert valid_get_dict[original_key] == result[key]

        # Valid GET param dict with list keys
        # Each tuple represents a single parameter in the query string
        valid_get_dict = MultiDict((("input_2[]", "test1"), ("input_2[]", "test2")))
        result = ProblemBlock.make_dict_of_responses(valid_get_dict)
        assert "2" in result
        assert ["test1", "test2"] == result["2"]

        # If we use [] at the end of a key name, we should always
        # get a list, even if there's just one value
        valid_get_dict = MultiDict({"input_1[]": "test"})
        result = ProblemBlock.make_dict_of_responses(valid_get_dict)
        assert result["1"] == ["test"]

        # If we have no underscores in the name, then the key is invalid
        invalid_get_dict = MultiDict({"input": "test"})
        with pytest.raises(ValueError):
            result = ProblemBlock.make_dict_of_responses(invalid_get_dict)

        # Two equivalent names (one list, one non-list)
        # One of the values would overwrite the other, so detect this
        # and raise an exception
        invalid_get_dict = MultiDict({"input_1[]": "test 1", "input_1": "test 2"})
        with pytest.raises(ValueError):
            result = ProblemBlock.make_dict_of_responses(invalid_get_dict)

    def test_submit_problem_correct(self):
        """Verify submitting a correct problem updates attempts, grading, and HTML content."""

        block = CapaFactory.create(attempts=1)

        # Simulate that all answers are marked correct, no matter
        # what the input is, by patching CorrectMap.is_correct()
        # Also simulate rendering the HTML
        with patch("xmodule.capa.correctmap.CorrectMap.is_correct") as mock_is_correct:
            with patch("xmodule.capa_block.ProblemBlock.get_problem_html") as mock_html:
                mock_is_correct.return_value = True
                mock_html.return_value = "Test HTML"

                # Check the problem
                get_request_dict = {CapaFactory.input_key(): "3.14"}
                result = block.submit_problem(get_request_dict)

        # Expect that the problem is marked correct
        assert result["success"] == "correct"

        # Expect that we get the (mocked) HTML
        assert result["contents"] == "Test HTML"

        # Expect that the number of attempts is incremented by 1
        assert block.attempts == 2
        # and that this was considered attempt number 2 for grading purposes
        assert block.lcp.context["attempt"] == 2

    @patch("xmodule.capa.correctmap.CorrectMap.is_correct")
    @patch("xmodule.capa_block.ProblemBlock.get_problem_html")
    def test_submit_problem_with_grading_method_disable(
        self, mock_html: Mock, mock_is_correct: Mock
    ):
        """
        Test that without a specific grading method, the score behaves as
        standard (Last Attempt).
        """
        block = CapaFactory.create(attempts=0, max_attempts=3)
        mock_html.return_value = "Test HTML"

        # First Attempt
        mock_is_correct.return_value = True
        get_request_dict = {CapaFactory.input_key(): "3.14"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 1
        assert block.lcp.context["attempt"] == 1
        assert block.score == Score(raw_earned=1, raw_possible=1)

        # Second Attempt
        mock_is_correct.return_value = False
        get_request_dict = {CapaFactory.input_key(): "3.50"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 2
        assert block.lcp.context["attempt"] == 2
        assert block.score == Score(raw_earned=0, raw_possible=1)

        # Third Attempt
        mock_is_correct.return_value = True
        get_request_dict = {CapaFactory.input_key(): "3.14"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 3
        assert block.lcp.context["attempt"] == 3
        assert block.score == Score(raw_earned=1, raw_possible=1)

    @patch("xmodule.capa.correctmap.CorrectMap.is_correct")
    @patch("xmodule.capa_block.ProblemBlock.get_problem_html")
    def test_submit_problem_with_grading_method_enable(self, mock_html: Mock, mock_is_correct: Mock):
        """
        Test that the grading method is enabled when submit a problem.
        Then, the `get_score_with_grading_method` method should be called.
        """
        block = CapaFactory.create(attempts=0)
        mock_html.return_value = "Test HTML"
        mock_is_correct.return_value = True

        with patch.object(
            ProblemBlock, "get_score_with_grading_method", wraps=block.get_score_with_grading_method
        ) as mock_get_score:
            get_request_dict = {CapaFactory.input_key(): "3.14"}
            block.submit_problem(get_request_dict)

            assert block.attempts == 1
            assert block.lcp.context["attempt"] == 1
            assert block.score == Score(raw_earned=1, raw_possible=1)
            mock_get_score.assert_called()

    @patch("xmodule.capa.correctmap.CorrectMap.is_correct")
    @patch("xmodule.capa_block.ProblemBlock.get_problem_html")
    def test_submit_problem_grading_method_always_enabled(self, mock_html: Mock, mock_is_correct: Mock):
        """
        Test problem submission when grading method is always enabled by default.

        The final score is calculated according to the grading method, as grading
        is now always enabled.
        """
        block = CapaFactory.create(attempts=0, max_attempts=4, grading_method="highest_score")
        mock_html.return_value = "Test HTML"

        # First Attempt
        mock_is_correct.return_value = True
        get_request_dict = {CapaFactory.input_key(): "3.14"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 1
        assert block.lcp.context["attempt"] == 1
        assert block.score == Score(raw_earned=1, raw_possible=1)

        # Second Attempt
        mock_is_correct.return_value = False
        get_request_dict = {CapaFactory.input_key(): "3.50"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 2
        assert block.lcp.context["attempt"] == 2
        assert block.score == Score(raw_earned=1, raw_possible=1)

        # Third Attempt
        mock_is_correct.return_value = False
        get_request_dict = {CapaFactory.input_key(): "3.96"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 3
        assert block.lcp.context["attempt"] == 3
        assert block.score == Score(raw_earned=1, raw_possible=1)

        # Fourth Attempt
        block.grading_method = "highest_score"
        mock_is_correct.return_value = False
        get_request_dict = {CapaFactory.input_key(): "3.99"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 4
        assert block.lcp.context["attempt"] == 4
        assert block.score == Score(raw_earned=1, raw_possible=1)

    @patch("xmodule.capa.correctmap.CorrectMap.is_correct")
    @patch("xmodule.capa_block.ProblemBlock.get_problem_html")
    def test_submit_problem_grading_method_always_enabled_highest_score(self, mock_html: Mock, mock_is_correct: Mock):
        """
        Test problem submission when grading method is always enabled by default
        with 'highest_score' grading method.

        The final score is calculated according to the grading method, as grading
        is now always enabled.
        """
        block = CapaFactory.create(attempts=0, max_attempts=4, grading_method="highest_score")
        mock_html.return_value = "Test HTML"

        # First Attempt
        mock_is_correct.return_value = True
        get_request_dict = {CapaFactory.input_key(): "3.14"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 1
        assert block.lcp.context["attempt"] == 1
        assert block.score == Score(raw_earned=1, raw_possible=1)

        # Second Attempt
        mock_is_correct.return_value = False
        get_request_dict = {CapaFactory.input_key(): "3.50"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 2
        assert block.lcp.context["attempt"] == 2
        assert block.score == Score(raw_earned=1, raw_possible=1)

        # Third Attempt
        mock_is_correct.return_value = False
        get_request_dict = {CapaFactory.input_key(): "3.96"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 3
        assert block.lcp.context["attempt"] == 3
        assert block.score == Score(raw_earned=1, raw_possible=1)

        # Fourth Attempt
        mock_is_correct.return_value = True
        get_request_dict = {CapaFactory.input_key(): "3.14"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 4
        assert block.lcp.context["attempt"] == 4
        assert block.score == Score(raw_earned=1, raw_possible=1)

    @patch("xmodule.capa.correctmap.CorrectMap.is_correct")
    @patch("xmodule.capa_block.ProblemBlock.get_problem_html")
    def test_submit_problem_correct_last_score(self, mock_html: Mock, mock_is_correct: Mock):
        """
        Test the `last_score` grading method.

        When the grading method is `last_score`,
        the final score is always the last attempt.
        """
        # default grading method is last_score
        block = CapaFactory.create(attempts=0, max_attempts=2)
        mock_html.return_value = "Test HTML"

        # First Attempt
        mock_is_correct.return_value = True
        get_request_dict = {CapaFactory.input_key(): "3.14"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 1
        assert block.lcp.context["attempt"] == 1
        assert block.score == Score(raw_earned=1, raw_possible=1)

        # Second Attempt
        mock_is_correct.return_value = False
        get_request_dict = {CapaFactory.input_key(): "3.54"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 2
        assert block.lcp.context["attempt"] == 2
        assert block.score == Score(raw_earned=0, raw_possible=1)

    @patch("xmodule.capa.correctmap.CorrectMap.is_correct")
    @patch("xmodule.capa_block.ProblemBlock.get_problem_html")
    def test_submit_problem_correct_highest_score(self, mock_html: Mock, mock_is_correct: Mock):
        """
        Test the `highest_score` grading method.

        When the grading method is `highest_score`,
        the final score is the highest score among all attempts.
        """
        block = CapaFactory.create(attempts=0, max_attempts=2, grading_method="highest_score")
        mock_html.return_value = "Test HTML"

        # First Attempt
        mock_is_correct.return_value = True
        get_request_dict = {CapaFactory.input_key(): "3.14"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 1
        assert block.lcp.context["attempt"] == 1
        assert block.score == Score(raw_earned=1, raw_possible=1)

        # Second Attempt
        mock_is_correct.return_value = False
        get_request_dict = {CapaFactory.input_key(): "3.54"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 2
        assert block.lcp.context["attempt"] == 2
        assert block.score == Score(raw_earned=1, raw_possible=1)

    @patch("xmodule.capa.correctmap.CorrectMap.is_correct")
    @patch("xmodule.capa_block.ProblemBlock.get_problem_html")
    def test_submit_problem_correct_first_score(self, mock_html: Mock, mock_is_correct: Mock):
        """
        Test the `first_score` grading method.

        When the grading method is `first_score`,
        the final score is the first score among all attempts.
        """
        block = CapaFactory.create(attempts=0, max_attempts=2, grading_method="first_score")
        mock_html.return_value = "Test HTML"

        # First Attempt
        mock_is_correct.return_value = False

        get_request_dict = {CapaFactory.input_key(): "3.14"}
        block.submit_problem(get_request_dict)

        assert block.attempts == 1
        assert block.lcp.context["attempt"] == 1
        assert block.score == Score(raw_earned=0, raw_possible=1)

        # Second Attempt
        mock_is_correct.return_value = True
        get_request_dict = {CapaFactory.input_key(): "3.54"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 2
        assert block.lcp.context["attempt"] == 2
        assert block.score == Score(raw_earned=0, raw_possible=1)

    @patch("xmodule.capa.correctmap.CorrectMap.is_correct")
    @patch("xmodule.capa_block.ProblemBlock.get_problem_html")
    def test_submit_problem_correct_average_score(self, mock_html: Mock, mock_is_correct: Mock):
        """
        Test the `average_score` grading method.

        When the grading method is `average_score`,
        the final score is the average score among all attempts.
        """
        block = CapaFactory.create(attempts=0, max_attempts=4, grading_method="average_score")
        mock_html.return_value = "Test HTML"

        # First Attempt
        mock_is_correct.return_value = False
        get_request_dict = {CapaFactory.input_key(): "3.14"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 1
        assert block.lcp.context["attempt"] == 1
        assert block.score == Score(raw_earned=0, raw_possible=1)

        # Second Attempt
        mock_is_correct.return_value = True
        get_request_dict = {CapaFactory.input_key(): "3.54"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 2
        assert block.lcp.context["attempt"] == 2
        assert block.score == Score(raw_earned=0.5, raw_possible=1)

        # Third Attempt
        mock_is_correct.return_value = False
        get_request_dict = {CapaFactory.input_key(): "3.45"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 3
        assert block.lcp.context["attempt"] == 3
        assert block.score == Score(raw_earned=0.33, raw_possible=1)

        # Fourth Attempt
        mock_is_correct.return_value = False
        get_request_dict = {CapaFactory.input_key(): "41.3"}

        block.submit_problem(get_request_dict)

        assert block.attempts == 4
        assert block.lcp.context["attempt"] == 4
        assert block.score == Score(raw_earned=0.25, raw_possible=1)

    def test_submit_problem_incorrect(self):
        """Verify submitting an incorrect answer marks failure and increments attempts."""

        block = CapaFactory.create(attempts=0)

        # Simulate marking the input incorrect
        with patch("xmodule.capa.correctmap.CorrectMap.is_correct") as mock_is_correct:
            mock_is_correct.return_value = False

            # Check the problem
            get_request_dict = {CapaFactory.input_key(): "0"}
            result = block.submit_problem(get_request_dict)

        # Expect that the problem is marked correct
        assert result["success"] == "incorrect"

        # Expect that the number of attempts is incremented by 1
        assert block.attempts == 1
        # and that this is considered the first attempt
        assert block.lcp.context["attempt"] == 1

    def test_submit_problem_closed(self):
        """Ensure submitting a closed problem raises NotFoundError and does not increment attempts."""
        block = CapaFactory.create(attempts=3)

        # Problem closed -- cannot submit
        # Simulate that ProblemBlock.closed() always returns True
        with patch("xmodule.capa_block.ProblemBlock.closed") as mock_closed:
            mock_closed.return_value = True
            with pytest.raises(NotFoundError):
                get_request_dict = {CapaFactory.input_key(): "3.14"}
                block.submit_problem(get_request_dict)

        # Expect that number of attempts NOT incremented
        assert block.attempts == 3

    @ddt.data(RANDOMIZATION.ALWAYS, "true")
    def test_submit_problem_resubmitted_with_randomize(self, rerandomize):
        """Verify resubmission is blocked when rerandomization is enabled and problem is done."""
        # Randomize turned on
        block = CapaFactory.create(rerandomize=rerandomize, attempts=0)

        # Simulate that the problem is completed
        block.done = True

        # Expect that we cannot submit
        with pytest.raises(NotFoundError):
            get_request_dict = {CapaFactory.input_key(): "3.14"}
            block.submit_problem(get_request_dict)

        # Expect that number of attempts NOT incremented
        assert block.attempts == 0

    @ddt.data(RANDOMIZATION.NEVER, "false", RANDOMIZATION.PER_STUDENT)
    def test_submit_problem_resubmitted_no_randomize(self, rerandomize):
        """Verify resubmission succeeds when rerandomization is disabled."""
        # Randomize turned off
        block = CapaFactory.create(rerandomize=rerandomize, attempts=0, done=True)

        # Expect that we can submit successfully
        get_request_dict = {CapaFactory.input_key(): "3.14"}
        result = block.submit_problem(get_request_dict)

        assert result["success"] == "correct"

        # Expect that number of attempts IS incremented, still same attempt
        assert block.attempts == 1
        assert block.lcp.context["attempt"] == 1

    def test_submit_problem_queued(self):
        """Ensure queued problems return a wait message and do not increment attempts."""
        block = CapaFactory.create(attempts=1)

        # Simulate that the problem is queued
        multipatch = patch.multiple(
            "xmodule.capa.capa_problem.LoncapaProblem", is_queued=DEFAULT, get_recentmost_queuetime=DEFAULT
        )
        with multipatch as values:
            values["is_queued"].return_value = True
            values["get_recentmost_queuetime"].return_value = datetime.datetime.now(ZoneInfo("UTC"))

            get_request_dict = {CapaFactory.input_key(): "3.14"}
            result = block.submit_problem(get_request_dict)

            # Expect an AJAX alert message in 'success'
            assert "You must wait" in result["success"]

        # Expect that the number of attempts is NOT incremented
        assert block.attempts == 1

    @pytest.mark.django_db
    @patch.object(XQueueInterface, "_http_post")
    def test_submit_problem_with_files(self, mock_xqueue_post):
        """Verify file-upload submissions are sent correctly to XQueue via submit_problem."""
        # Check a problem with uploaded files, using the submit_problem API.

        # The files we'll be uploading.
        fnames = ["prog1.py", "prog2.py", "prog3.py"]
        fpaths = [os.path.join(DATA_DIR, "capa", fname) for fname in fnames]
        fileobjs = [open(fpath, encoding="utf-8") for fpath in fpaths]  # pylint: disable=consider-using-with
        for fileobj in fileobjs:
            self.addCleanup(fileobj.close)

        block = CapaFactoryWithFiles.create()

        # Mock the XQueueInterface post method
        mock_xqueue_post.return_value = (0, "ok")

        # Create a request dictionary for submit_problem.
        get_request_dict = {
            CapaFactoryWithFiles.input_key(response_num=2): fileobjs,
            CapaFactoryWithFiles.input_key(response_num=3): "None",
        }

        block.submit_problem(get_request_dict)

        # _http_post is called like this:
        #   _http_post(
        #       'http://example.com/xqueue/xqueue/submit/',
        #       {
        #           'xqueue_header':
        #               '{"lms_key": "df34fb702620d7ae892866ba57572491", '
        #               '"lms_callback_url": "/", '
        #               '"queue_name": "BerkeleyX-cs188x"}',
        #           'xqueue_body':
        #               '{"student_info": "{\\"anonymous_student_id\\": '
        #               '\\"student\\", \\"submission_time\\": '
        #               '\\"20131117183318\\"}", '
        #               '"grader_payload": "{\\"project\\": \\"p3\\"}", '
        #               '"student_response": ""}',
        #       },
        #       files={
        #           path(
        #               u'/home/ned/edx/edx-platform/common/test/data/uploads/'
        #               'asset.html'
        #           ):
        #               <open file u'/home/ned/edx/edx-platform/common/test/data/uploads/'
        #               'asset.html', mode 'r' at 0x49c5f60>,
        #           path(
        #               u'/home/ned/edx/edx-platform/common/test/data/uploads/'
        #               'image.jpg'
        #           ):
        #               <open file u'/home/ned/edx/edx-platform/common/test/data/uploads/'
        #               'image.jpg', mode 'r' at 0x49c56f0>,
        #           path(
        #               u'/home/ned/edx/edx-platform/common/test/data/uploads/'
        #               'textbook.pdf'
        #           ):
        #               <open file  u'/home/ned/edx/edx-platform/common/test/data/uploads/'
        #               'textbook.pdf', mode 'r' at 0x49c5a50>,
        #       },
        #   )

        assert mock_xqueue_post.call_count == 1
        _, kwargs = mock_xqueue_post.call_args
        self.assertCountEqual(fpaths, list(kwargs["files"].keys()))
        for fpath, fileobj in kwargs["files"].items():
            assert fpath == fileobj.name

    @pytest.mark.django_db
    @patch.object(XQueueInterface, "_http_post")
    def test_submit_problem_with_files_as_xblock(self, mock_xqueue_post):
        """Verify file-upload submissions work correctly via the XBlock handler API."""
        # Check a problem with uploaded files, using the XBlock API.

        # The files we'll be uploading.
        fnames = ["prog1.py", "prog2.py", "prog3.py"]
        fpaths = [os.path.join(DATA_DIR, "capa", fname) for fname in fnames]
        fileobjs = []
        for fpath in fpaths:
            with open(fpath, encoding="utf-8") as f:
                fileobjs.append(f.read())

        block = CapaFactoryWithFiles.create()

        # Mock the XQueueInterface post method
        mock_xqueue_post.return_value = (0, "ok")

        # Create a webob Request with the files uploaded.
        post_data = []
        for fname, fileobj in zip(fnames, fileobjs):
            post_data.append((CapaFactoryWithFiles.input_key(response_num=2), (fname, fileobj)))
        post_data.append((CapaFactoryWithFiles.input_key(response_num=3), "None"))
        request = webob.Request.blank("/some/fake/url", POST=post_data, content_type="multipart/form-data")

        block.handle("xmodule_handler", request, "problem_check")

        assert mock_xqueue_post.call_count == 1
        _, kwargs = mock_xqueue_post.call_args
        self.assertCountEqual(fnames, list(kwargs["files"].keys()))
        for fpath, fileobj in kwargs["files"].items():
            assert fpath == fileobj.name

    def test_submit_problem_error(self):
        """Ensure expected grading errors return messages without incrementing attempts."""

        # Try each exception that capa_block should handle
        exception_classes = [StudentInputError, LoncapaProblemError, ResponseError]
        for exception_class in exception_classes:
            # Create the block
            block = CapaFactory.create(attempts=1, user_is_staff=False)

            # Simulate answering a problem that raises the exception
            with patch("xmodule.capa.capa_problem.LoncapaProblem.grade_answers") as mock_grade:
                mock_grade.side_effect = exception_class("test error")

                get_request_dict = {CapaFactory.input_key(): "3.14"}
                result = block.submit_problem(get_request_dict)

            # Expect an AJAX alert message in 'success'
            expected_msg = "test error"

            assert expected_msg == result["success"]

            # Expect that the number of attempts is NOT incremented
            assert block.attempts == 1
            # but that this was considered attempt number 2 for grading purposes
            assert block.lcp.context["attempt"] == 2

    def test_submit_problem_error_with_codejail_exception(self):
        """Verify codejail execution errors are sanitized and handled correctly."""

        # Try each exception that capa_block should handle
        exception_classes = [StudentInputError, LoncapaProblemError, ResponseError]
        for exception_class in exception_classes:

            # Create the block
            block = CapaFactory.create(attempts=1, user_is_staff=False)

            # Simulate a codejail exception "Exception: Couldn't execute jailed code"
            with patch("xmodule.capa.capa_problem.LoncapaProblem.grade_answers") as mock_grade:
                try:
                    raise ResponseError(
                        "Couldn't execute jailed code: stdout: '', "
                        "stderr: 'Traceback (most recent call last):\\n"
                        '  File "jailed_code", line 15, in <module>\\n'
                        '    exec code in g_dict\\n  File "<string>", line 67, in <module>\\n'
                        '  File "<string>", line 65, in check_func\\n'
                        "Exception: Couldn't execute jailed code\\n' with status code: 1",
                    )
                except ResponseError as err:
                    mock_grade.side_effect = exception_class(str(err))
                get_request_dict = {CapaFactory.input_key(): "3.14"}
                result = block.submit_problem(get_request_dict)

            # Expect an AJAX alert message in 'success' without the text of the stack trace
            expected_msg = "Couldn't execute jailed code"
            assert expected_msg == result["success"]

            # Expect that the number of attempts is NOT incremented
            assert block.attempts == 1
            # but that this was considered the second attempt for grading purposes
            assert block.lcp.context["attempt"] == 2

    @override_settings(DEBUG=True)
    def test_submit_problem_other_errors(self):
        """
        Test that errors other than the expected kinds give an appropriate message.

        See also `test_submit_problem_error` for the "expected kinds" or errors.
        """
        # Create the block
        block = CapaFactory.create(attempts=1, user_is_staff=False)
        block.runtime.is_author_mode = True

        # Simulate answering a problem that raises the exception
        with patch("xmodule.capa.capa_problem.LoncapaProblem.grade_answers") as mock_grade:
            error_msg = "Superterrible error happened: ☠"
            mock_grade.side_effect = Exception(error_msg)

            get_request_dict = {CapaFactory.input_key(): "3.14"}
            result = block.submit_problem(get_request_dict)

        # Expect an AJAX alert message in 'success'
        assert error_msg in result["success"]

    def test_submit_problem_zero_max_grade(self):
        """
        Test that a capa problem with a max grade of zero doesn't generate an error.
        """
        # Create the block
        block = CapaFactory.create(attempts=1)

        # Override the problem score to have a total of zero.
        block.lcp.get_score = lambda: {"score": 0, "total": 0}

        # Check the problem
        get_request_dict = {CapaFactory.input_key(): "3.14"}
        block.submit_problem(get_request_dict)

    def test_submit_problem_error_nonascii(self):
        """Ensure non-ASCII error messages are preserved and handled correctly."""

        # Try each exception that capa_block should handle
        exception_classes = [StudentInputError, LoncapaProblemError, ResponseError]
        for exception_class in exception_classes:
            # Create the block
            block = CapaFactory.create(attempts=1, user_is_staff=False)

            # Simulate answering a problem that raises the exception
            with patch("xmodule.capa.capa_problem.LoncapaProblem.grade_answers") as mock_grade:
                mock_grade.side_effect = exception_class("ȧƈƈḗƞŧḗḓ ŧḗẋŧ ƒǿř ŧḗşŧīƞɠ")

                get_request_dict = {CapaFactory.input_key(): "3.14"}
                result = block.submit_problem(get_request_dict)

            # Expect an AJAX alert message in 'success'
            expected_msg = "ȧƈƈḗƞŧḗḓ ŧḗẋŧ ƒǿř ŧḗşŧīƞɠ"

            assert expected_msg == result["success"]

            # Expect that the number of attempts is NOT incremented
            assert block.attempts == 1
            # but that this was considered the second attempt for grading purposes
            assert block.lcp.context["attempt"] == 2

    def test_submit_problem_error_with_staff_user(self):
        """Verify staff users receive full traceback information on errors."""

        # Try each exception that capa block should handle
        for exception_class in [StudentInputError, LoncapaProblemError, ResponseError]:
            # Create the block
            block = CapaFactory.create(attempts=1, user_is_staff=True)

            # Simulate answering a problem that raises an exception
            with patch("xmodule.capa.capa_problem.LoncapaProblem.grade_answers") as mock_grade:
                mock_grade.side_effect = exception_class("test error")

                get_request_dict = {CapaFactory.input_key(): "3.14"}
                result = block.submit_problem(get_request_dict)

            # Expect an AJAX alert message in 'success'
            assert "test error" in result["success"]

            # We DO include traceback information for staff users
            assert "Traceback" in result["success"]

            # Expect that the number of attempts is NOT incremented
            assert block.attempts == 1
            # but that it was considered the second attempt for grading purposes
            assert block.lcp.context["attempt"] == 2

    @ddt.data(
        ("never", True, None, "submitted"),
        ("never", False, None, "submitted"),
        ("past_due", True, None, "submitted"),
        ("past_due", False, None, "submitted"),
        ("always", True, 1, "correct"),
        ("always", False, 0, "incorrect"),
    )
    @ddt.unpack
    def test_handle_ajax_show_correctness(self, show_correctness, is_correct, expected_score, expected_success):
        """Verify AJAX submission respects show_correctness settings."""
        block = CapaFactory.create(show_correctness=show_correctness, due=self.tomorrow_str, correct=is_correct)

        # Simulate marking the input correct/incorrect
        with patch("xmodule.capa.correctmap.CorrectMap.is_correct") as mock_is_correct:
            mock_is_correct.return_value = is_correct

            # Check the problem
            get_request_dict = {CapaFactory.input_key(): "0"}
            json_result = block.handle_ajax("problem_check", get_request_dict)
            result = json.loads(json_result)

        # Expect that the AJAX result withholds correctness and score
        assert result["current_score"] == expected_score
        assert result["success"] == expected_success

        # Expect that the number of attempts is incremented by 1
        assert block.attempts == 1
        assert block.lcp.context["attempt"] == 1

    def test_reset_problem(self):
        """Ensure resetting a completed problem regenerates state and HTML."""
        block = CapaFactory.create(done=True)
        block.new_lcp = Mock(wraps=block.new_lcp)
        block.choose_new_seed = Mock(wraps=block.choose_new_seed)

        # Stub out HTML rendering
        with patch("xmodule.capa_block.ProblemBlock.get_problem_html") as mock_html:
            mock_html.return_value = "<div>Test HTML</div>"

            # Reset the problem
            get_request_dict = {}
            result = block.reset_problem(get_request_dict)

        # Expect that the request was successful
        assert ("success" in result) and result["success"]

        # Expect that the problem HTML is retrieved
        assert "html" in result
        assert result["html"] == "<div>Test HTML</div>"

        # Expect that the problem was reset
        block.new_lcp.assert_called_once_with(None)

    def test_reset_problem_closed(self):
        """Verify reset is blocked when the problem is closed."""
        # pre studio default
        block = CapaFactory.create(rerandomize=RANDOMIZATION.ALWAYS)

        # Simulate that the problem is closed
        with patch("xmodule.capa_block.ProblemBlock.closed") as mock_closed:
            mock_closed.return_value = True

            # Try to reset the problem
            get_request_dict = {}
            result = block.reset_problem(get_request_dict)

        # Expect that the problem was NOT reset
        assert ("success" in result) and (not result["success"])

    def test_reset_problem_not_done(self):
        """Verify reset is blocked when the problem is not yet completed."""
        # Simulate that the problem is NOT done
        block = CapaFactory.create(done=False)

        # Try to reset the problem
        get_request_dict = {}
        result = block.reset_problem(get_request_dict)

        # Expect that the problem was NOT reset
        assert ("success" in result) and (not result["success"])

    def test_rescore_problem_correct(self):
        """Ensure rescoring marks the problem correct without incrementing attempts."""

        block = CapaFactory.create(attempts=0, done=True)

        # Simulate that all answers are marked correct, no matter
        # what the input is, by patching LoncapaResponse.evaluate_answers()
        with patch("xmodule.capa.responsetypes.LoncapaResponse.evaluate_answers") as mock_evaluate_answers:
            mock_evaluate_answers.return_value = CorrectMap(
                answer_id=CapaFactory.answer_key(),
                correctness="correct",
                npoints=1,
            )
            with patch("xmodule.capa.correctmap.CorrectMap.is_correct") as mock_is_correct:
                mock_is_correct.return_value = True

                # Check the problem
                get_request_dict = {CapaFactory.input_key(): "1"}
                block.submit_problem(get_request_dict)
            block.rescore(only_if_higher=False)

        # Expect that the problem is marked correct
        assert block.is_correct() is True

        # Expect that the number of attempts is not incremented
        assert block.attempts == 1
        # and that this was considered attempt number 1 for grading purposes
        assert block.lcp.context["attempt"] == 1

    def test_rescore_problem_additional_correct(self):
        """Verify rescoring updates scores correctly when new correct answers are added."""
        # make sure it also works when new correct answer has been added
        block = CapaFactory.create(attempts=0)
        answer_id = CapaFactory.answer_key()

        # Check the problem
        get_request_dict = {CapaFactory.input_key(): "1"}
        result = block.submit_problem(get_request_dict)

        # Expect that the problem is marked incorrect and user didn't earn score
        assert result["success"] == "incorrect"
        assert block.get_score() == (0, 1)
        assert block.correct_map[answer_id]["correctness"] == "incorrect"

        # Expect that the number of attempts has incremented to 1
        assert block.attempts == 1
        assert block.lcp.context["attempt"] == 1

        # Simulate that after making an incorrect answer to the correct answer
        # the new calculated score is (1,1)
        # by patching CorrectMap.is_correct() and NumericalResponse.get_staff_ans()
        # In case of rescore with only_if_higher=True it should update score of block
        # if previous score was lower

        with patch("xmodule.capa.correctmap.CorrectMap.is_correct") as mock_is_correct:
            mock_is_correct.return_value = True
            block.set_score(block.score_from_lcp(block.lcp))
            with patch("xmodule.capa.responsetypes.NumericalResponse.get_staff_ans") as get_staff_ans:
                get_staff_ans.return_value = 1 + 0j
                block.rescore(only_if_higher=True)

        # Expect that the problem is marked correct and user earned the score
        assert block.get_score() == (1, 1)
        assert block.correct_map[answer_id]["correctness"] == "correct"
        # Expect that the number of attempts is not incremented
        assert block.attempts == 1
        # and hence that this was still considered the first attempt for grading purposes
        assert block.lcp.context["attempt"] == 1

    def test_rescore_problem_incorrect(self):
        """Ensure rescoring marks the problem incorrect without changing attempts."""
        # make sure it also works when attempts have been reset,
        # so add this to the test:
        block = CapaFactory.create(attempts=0, done=True)

        # Simulate that all answers are marked incorrect, no matter
        # what the input is, by patching LoncapaResponse.evaluate_answers()
        with patch("xmodule.capa.responsetypes.LoncapaResponse.evaluate_answers") as mock_evaluate_answers:
            mock_evaluate_answers.return_value = CorrectMap(CapaFactory.answer_key(), "incorrect")
            block.rescore(only_if_higher=False)

        # Expect that the problem is marked incorrect
        assert block.is_correct() is False

        # Expect that the number of attempts is not incremented
        assert block.attempts == 0
        # and that this is treated as the first attempt for grading purposes
        assert block.lcp.context["attempt"] == 1

    def test_rescore_problem_with_grading_method_disable(self):
        """
        Test the rescore method with grading method logic enabled by default.
        """
        block = CapaFactory.create(attempts=0, done=True, grading_method="highest_score")

        block.rescore(only_if_higher=False)

        assert block.attempts == 0
        assert block.lcp.context["attempt"] == 1

    def test_rescore_problem_with_grading_method_enable(self):
        """
        Test the rescore method with grading method enabled.
        In this case, the rescore method should call `get_rescore_with_grading_method` method.
        """
        block = CapaFactory.create(attempts=0, done=True)

        with patch.object(
            ProblemBlock, "get_rescore_with_grading_method", wraps=block.get_rescore_with_grading_method
        ) as mock_get_rescore:

            block.rescore(only_if_higher=False)

            assert block.attempts == 0
            assert block.lcp.context["attempt"] == 1
            mock_get_rescore.assert_called()

    @patch("xmodule.capa_block.ProblemBlock.publish_grade")
    def test_rescore_problem_grading_method_always_enabled(self, mock_publish_grade: Mock):
        """
        Test the rescore method when grading method is always enabled by default.

        The final score is calculated according to the grading method, as grading
        is now always enabled.
        """
        block = CapaFactory.create(attempts=0, max_attempts=3)

        get_request_dict = {CapaFactory.input_key(): "3.21"}
        block.submit_problem(get_request_dict)

        get_request_dict = {CapaFactory.input_key(): "3.45"}
        block.submit_problem(get_request_dict)

        get_request_dict = {CapaFactory.input_key(): "3.14"}
        block.submit_problem(get_request_dict)

        # Score is calculated according to the grading method
        assert block.score == Score(raw_earned=1, raw_possible=1)
        block.rescore(only_if_higher=False)

        # Still Score is the last score
        mock_publish_grade.assert_called_with(score=Score(raw_earned=1, raw_possible=1), only_if_higher=False)

        # Rescore with different grading methods
        block.grading_method = "first_score"
        block.rescore(only_if_higher=False)
        mock_publish_grade.assert_called_with(score=Score(raw_earned=0, raw_possible=1), only_if_higher=False)

        # Change grading method to 'highest_score'
        block.grading_method = "highest_score"
        block.rescore(only_if_higher=False)
        mock_publish_grade.assert_called_with(score=Score(raw_earned=1, raw_possible=1), only_if_higher=False)

        # Change grading method to 'average_score'
        block.grading_method = "average_score"
        block.rescore(only_if_higher=False)

        mock_publish_grade.assert_called_with(
            score=Score(raw_earned=0.33, raw_possible=1), only_if_higher=False
        )

    @patch("xmodule.capa_block.ProblemBlock.publish_grade")
    def test_rescore_problem_grading_method_always_enabled_with_various_methods(self, mock_publish_grade: Mock):
        """
        Test the rescore method when grading method is always enabled by default
        with different grading methods.

        The final score is calculated according to the grading method, as grading
        is now always enabled.
        """
        block = CapaFactory.create(attempts=0, max_attempts=3)

        get_request_dict = {CapaFactory.input_key(): "3.21"}
        block.submit_problem(get_request_dict)

        get_request_dict = {CapaFactory.input_key(): "3.45"}
        block.submit_problem(get_request_dict)

        get_request_dict = {CapaFactory.input_key(): "3.14"}
        block.submit_problem(get_request_dict)

        # Grading method is 'last_score' by default
        assert block.grading_method == "last_score"
        assert block.score == Score(raw_earned=1, raw_possible=1)

        # Change grading method to 'first_score'
        block.grading_method = "first_score"
        block.rescore(only_if_higher=False)
        mock_publish_grade.assert_called_with(score=Score(raw_earned=0, raw_possible=1), only_if_higher=False)

        # Change grading method to 'highest_score'
        block.grading_method = "highest_score"
        block.rescore(only_if_higher=False)
        mock_publish_grade.assert_called_with(score=Score(raw_earned=1, raw_possible=1), only_if_higher=False)

        # Change grading method to 'average_score'
        block.grading_method = "average_score"
        block.rescore(only_if_higher=False)
        mock_publish_grade.assert_called_with(score=Score(raw_earned=0.33, raw_possible=1), only_if_higher=False)

        block.rescore(only_if_higher=False)
        assert block.score == Score(raw_earned=1, raw_possible=1)

    @patch("xmodule.capa_block.ProblemBlock.publish_grade")
    def test_rescore_problem_update_grading_method(self, mock_publish_grade: Mock):
        """
        Test the rescore method when the grading method is updated.

        When the grading method is updated, the final
        score is the score based on the new grading method.
        """
        block = CapaFactory.create(attempts=0, max_attempts=3)

        get_request_dict = {CapaFactory.input_key(): "3.21"}
        block.submit_problem(get_request_dict)

        get_request_dict = {CapaFactory.input_key(): "3.45"}
        block.submit_problem(get_request_dict)

        get_request_dict = {CapaFactory.input_key(): "3.14"}
        block.submit_problem(get_request_dict)

        # Grading method is 'last_score'
        assert block.grading_method == "last_score"
        assert block.score == Score(raw_earned=1, raw_possible=1)

        # Change grading method to 'first_score'
        block.grading_method = "first_score"
        block.rescore(only_if_higher=False)

        mock_publish_grade.assert_called_with(score=Score(raw_earned=0, raw_possible=1), only_if_higher=False)

        # Change grading method to 'highest_score'
        block.grading_method = "highest_score"
        block.rescore(only_if_higher=False)

        mock_publish_grade.assert_called_with(score=Score(raw_earned=1, raw_possible=1), only_if_higher=False)

        # Change grading method to 'average_score'
        block.grading_method = "average_score"
        block.rescore(only_if_higher=False)

        mock_publish_grade.assert_called_with(score=Score(raw_earned=0.33, raw_possible=1), only_if_higher=False)

    def test_rescore_problem_not_done(self):
        """Ensure rescoring an unfinished problem raises NotFoundError."""
        # Simulate that the problem is NOT done
        block = CapaFactory.create(done=False)

        # Try to rescore the problem, and get exception
        with pytest.raises(NotFoundError):
            block.rescore(only_if_higher=False)

    def test_rescore_problem_not_supported(self):
        """Ensure rescoring raises NotImplementedError when unsupported by the problem."""
        block = CapaFactory.create(done=True)

        # Try to rescore the problem, and get exception
        with patch("xmodule.capa.capa_problem.LoncapaProblem.supports_rescoring") as mock_supports_rescoring:
            mock_supports_rescoring.return_value = False
            with pytest.raises(NotImplementedError):
                block.rescore(only_if_higher=False)

    def test_calculate_score_list(self):
        """
        Test that the `calculate_score_list` method returns the correct list of scores.
        """
        block = CapaFactory.create(correct=True)
        correct_map = CorrectMap(answer_id="1_2_1", correctness="correct", npoints=1)
        block.lcp.correct_map_history = [correct_map, correct_map]

        with patch.object(block.lcp, "calculate_score", return_value={"score": 1, "total": 2}):
            result = block.calculate_score_list()
            expected_result = [Score(raw_earned=1, raw_possible=2), Score(raw_earned=1, raw_possible=2)]
            self.assertEqual(result, expected_result)

    def test_calculate_score_list_empty(self):
        """
        Test that the `calculate_score_list` method returns an
        empty list when the `correct_map_history` is empty.

        The `calculate_score` method should not be called.
        """
        block = CapaFactory.create(correct=True)
        block.lcp.correct_map_history = []

        with patch.object(block.lcp, "calculate_score", return_value=Mock()):
            result = block.calculate_score_list()
            self.assertEqual(result, [])
            block.lcp.calculate_score.assert_not_called()

    def test_update_correctness_list_updates_attempt(self):
        """
        Test that the `update_correctness_list` method updates the attempt number.
        """
        block = CapaFactory.create(correct=True, attempts=0)

        block.update_correctness_list()

        self.assertEqual(block.lcp.context["attempt"], 1)

    def test_update_correctness_list_with_history(self):
        """
        Test that the `update_correctness_list` method updates the correct map history.
        """
        block = CapaFactory.create(correct=True, attempts=2)
        correct_map = CorrectMap(answer_id="1_2_1", correctness="correct", npoints=1)
        student_answers = {"1_2_1": "abcd"}
        block.correct_map_history = [correct_map]
        block.student_answers_history = [student_answers]

        with patch.object(block.lcp, "get_grade_from_current_answers", return_value=correct_map):
            block.update_correctness_list()
            self.assertEqual(block.lcp.context["attempt"], 2)
            block.lcp.get_grade_from_current_answers.assert_called_once_with(student_answers, correct_map)
            self.assertEqual(block.lcp.correct_map_history, [correct_map])
            self.assertEqual(block.lcp.correct_map.get_dict(), correct_map.get_dict())

    def test_update_correctness_list_without_history(self):
        """
        Test that the `update_correctness_list` method does not
        update the correct map history because the history is empty.

        The `get_grade_from_current_answers` method should not be called.
        """
        block = CapaFactory.create(correct=True, attempts=1)
        block.correct_map_history = []
        block.student_answers_history = []

        with patch.object(block.lcp, "get_grade_from_current_answers", return_value=Mock()):
            block.update_correctness_list()
            self.assertEqual(block.lcp.context["attempt"], 1)
            block.lcp.get_grade_from_current_answers.assert_not_called()

    def test_get_rescore_with_grading_method(self):
        """
        Test that the `get_rescore_with_grading_method` method returns the correct score.
        """
        block = CapaFactory.create(done=True, attempts=0, max_attempts=2)
        get_request_dict = {CapaFactory.input_key(): "3.21"}
        block.submit_problem(get_request_dict)
        get_request_dict = {CapaFactory.input_key(): "3.14"}
        block.submit_problem(get_request_dict)

        result = block.get_rescore_with_grading_method()

        self.assertEqual(result, Score(raw_earned=1, raw_possible=1))

    def test_get_score_with_grading_method(self):
        """
        Test that the `get_score_with_grading_method` method
        returns the correct score based on the grading method.
        """
        block = CapaFactory.create(done=True, attempts=0, max_attempts=2)
        get_request_dict = {CapaFactory.input_key(): "3.21"}
        block.submit_problem(get_request_dict)
        get_request_dict = {CapaFactory.input_key(): "3.14"}
        block.submit_problem(get_request_dict)
        expected_score = Score(raw_earned=1, raw_possible=1)

        score = block.get_score_with_grading_method(block.score_from_lcp(block.lcp))

        self.assertEqual(score, expected_score)
        self.assertEqual(block.score, expected_score)

    @patch("xmodule.capa_block.ProblemBlock.score_from_lcp")
    def test_get_score_with_grading_method_updates_score(self, mock_score_from_lcp: Mock):
        """
        Test that the `get_score_with_grading_method` method returns the correct score.

        Check that the score is returned with the correct score and the score
        history is updated including that score.
        """
        block = CapaFactory.create(attempts=1)
        current_score = Score(raw_earned=1, raw_possible=1)
        mock_score_from_lcp.return_value = current_score

        score = block.get_score_with_grading_method(current_score)

        self.assertEqual(score, current_score)
        self.assertEqual(block.score_history, [current_score])

    def test_get_score_with_grading_method_calls_grading_method_handler(self):
        """
        Test that the `get_score_with_grading_method` method calls
        the grading method handler with the appropriate arguments.
        """
        block = CapaFactory.create(attempts=1)
        current_score = Score(raw_earned=0, raw_possible=1)

        with patch("xmodule.capa_block.GradingMethodHandler") as mock_handler:
            mock_handler.return_value.get_score.return_value = current_score
            block.get_score_with_grading_method(current_score)
            mock_handler.assert_called_once_with(
                Score(raw_earned=0, raw_possible=1),
                "last_score",
                block.score_history,
                current_score.raw_possible,
            )

    def capa_factory_for_problem_xml(self, xml):
        """Return a custom CapaFactory configured with the given problem XML."""

        class CustomCapaFactory(CapaFactory):
            """
            A factory for creating a Capa problem with arbitrary xml.
            """

            sample_problem_xml = textwrap.dedent(xml)

        return CustomCapaFactory

    def test_codejail_error_upon_problem_creation(self):
        """Verify codejail execution errors during problem creation raise LoncapaProblemError."""
        # Simulate a codejail safe_exec failure upon problem creation.
        # Create a problem with some script attached.
        xml_str = textwrap.dedent(
            """
            <problem>
                <script>test=True</script>
            </problem>
        """
        )
        factory = self.capa_factory_for_problem_xml(xml_str)

        # When codejail safe_exec fails upon problem creation, a LoncapaProblemError should be raised.
        with pytest.raises(LoncapaProblemError):
            with patch("xmodule.capa.capa_problem.safe_exec") as mock_safe_exec:
                mock_safe_exec.side_effect = SafeExecException()
                factory.create()

    def _rescore_problem_error_helper(self, exception_class):
        """Helper to allow testing all errors that rescoring might return."""
        # Create the block
        block = CapaFactory.create(attempts=0)
        CapaFactory.answer_key()

        # Check the problem
        get_request_dict = {CapaFactory.input_key(): "1"}
        block.submit_problem(get_request_dict)

        # Simulate answering a problem that raises the exception
        with patch("xmodule.capa.capa_problem.LoncapaProblem.get_grade_from_current_answers") as mock_rescore:
            mock_rescore.side_effect = exception_class("test error \u03a9")
            with pytest.raises(exception_class):
                block.rescore(only_if_higher=False)

        # Expect that the number of attempts is NOT incremented
        assert block.attempts == 1
        # and that this was considered the first attempt for grading purposes
        assert block.lcp.context["attempt"] == 1

    def test_rescore_problem_student_input_error(self):
        """Ensure StudentInputError during rescore is handled correctly."""
        self._rescore_problem_error_helper(StudentInputError)

    def test_rescore_problem_problem_error(self):
        """Ensure LoncapaProblemError during rescore is handled correctly."""
        self._rescore_problem_error_helper(LoncapaProblemError)

    def test_rescore_problem_response_error(self):
        """Ensure ResponseError during rescore is handled correctly."""
        self._rescore_problem_error_helper(ResponseError)

    def test_save_problem(self):
        """Verify saving a problem persists answers and returns success."""
        block = CapaFactory.create(done=False)

        # Save the problem
        get_request_dict = {CapaFactory.input_key(): "3.14"}
        result = block.save_problem(get_request_dict)

        # Expect that answers are saved to the problem
        expected_answers = {CapaFactory.answer_key(): "3.14"}
        assert block.lcp.student_answers == expected_answers

        # Expect that the result is success
        assert ("success" in result) and result["success"]

    def test_save_problem_closed(self):
        """Ensure saving a closed problem fails."""
        block = CapaFactory.create(done=False)

        # Simulate that the problem is closed
        with patch("xmodule.capa_block.ProblemBlock.closed") as mock_closed:
            mock_closed.return_value = True

            # Try to save the problem
            get_request_dict = {CapaFactory.input_key(): "3.14"}
            result = block.save_problem(get_request_dict)

        # Expect that the result is failure
        assert ("success" in result) and (not result["success"])

    @ddt.data(RANDOMIZATION.ALWAYS, "true")
    def test_save_problem_submitted_with_randomize(self, rerandomize):
        """Verify saving fails when problem is submitted and rerandomization is enabled."""
        # Capa XModule treats 'always' and 'true' equivalently
        block = CapaFactory.create(rerandomize=rerandomize, done=True)

        # Try to save
        get_request_dict = {CapaFactory.input_key(): "3.14"}
        result = block.save_problem(get_request_dict)

        # Expect that we cannot save
        assert ("success" in result) and (not result["success"])

    @ddt.data(RANDOMIZATION.NEVER, "false", RANDOMIZATION.PER_STUDENT)
    def test_save_problem_submitted_no_randomize(self, rerandomize):
        """Verify saving succeeds when problem is submitted without rerandomization."""
        # Capa XBlock treats 'false' and 'per_student' equivalently
        block = CapaFactory.create(rerandomize=rerandomize, done=True)

        # Try to save
        get_request_dict = {CapaFactory.input_key(): "3.14"}
        result = block.save_problem(get_request_dict)

        # Expect that we succeed
        assert ("success" in result) and result["success"]

    def test_submit_button_name(self):
        """Verify the submit button label is correct."""
        block = CapaFactory.create(attempts=0)
        assert block.submit_button_name() == "Submit"

    def test_submit_button_submitting_name(self):
        """Verify the submitting button label is correct."""
        block = CapaFactory.create(attempts=1, max_attempts=10)
        assert block.submit_button_submitting_name() == "Submitting"

    def test_should_enable_submit_button(self):
        """Verify submit button enablement logic across deadlines, attempts, and states."""

        attempts = random.randint(1, 10)

        # If we're after the deadline, disable the submit button
        block = CapaFactory.create(due=self.yesterday_str)
        assert not block.should_enable_submit_button()

        # If user is out of attempts, disable the submit button
        block = CapaFactory.create(attempts=attempts, max_attempts=attempts)
        assert not block.should_enable_submit_button()

        # If survey question (max_attempts = 0), disable the submit button
        block = CapaFactory.create(max_attempts=0)
        assert not block.should_enable_submit_button()

        # If user submitted a problem but hasn't reset,
        # disable the submit button
        # Note:  we can only reset when rerandomize="always" or "true"
        block = CapaFactory.create(rerandomize=RANDOMIZATION.ALWAYS, done=True)
        assert not block.should_enable_submit_button()

        block = CapaFactory.create(rerandomize="true", done=True)
        assert not block.should_enable_submit_button()

        # Otherwise, enable the submit button
        block = CapaFactory.create()
        assert block.should_enable_submit_button()

        # If the user has submitted the problem
        # and we do NOT have a reset button, then we can enable the submit button
        # Setting rerandomize to "never" or "false" ensures that the reset button
        # is not shown
        block = CapaFactory.create(rerandomize=RANDOMIZATION.NEVER, done=True)
        assert block.should_enable_submit_button()

        block = CapaFactory.create(rerandomize="false", done=True)
        assert block.should_enable_submit_button()

        block = CapaFactory.create(rerandomize=RANDOMIZATION.PER_STUDENT, done=True)
        assert block.should_enable_submit_button()

    def test_should_show_reset_button(self):
        """Verify reset button visibility logic across problem states and settings."""

        attempts = random.randint(1, 10)

        # If we're after the deadline, do NOT show the reset button
        block = CapaFactory.create(due=self.yesterday_str, done=True)
        assert not block.should_show_reset_button()

        # If the user is out of attempts, do NOT show the reset button
        block = CapaFactory.create(attempts=attempts, max_attempts=attempts, done=True)
        assert not block.should_show_reset_button()

        # pre studio default value, DO show the reset button
        block = CapaFactory.create(rerandomize=RANDOMIZATION.ALWAYS, done=True)
        assert block.should_show_reset_button()

        # If survey question for capa (max_attempts = 0),
        # DO show the reset button
        block = CapaFactory.create(rerandomize=RANDOMIZATION.ALWAYS, max_attempts=0, done=True)
        assert block.should_show_reset_button()

        # If the question is not correct
        # DO show the reset button
        block = CapaFactory.create(rerandomize=RANDOMIZATION.ALWAYS, max_attempts=0, done=True, correct=False)
        assert block.should_show_reset_button()

        # If the question is correct and randomization is never
        # DO not show the reset button
        block = CapaFactory.create(rerandomize=RANDOMIZATION.NEVER, max_attempts=0, done=True, correct=True)
        assert not block.should_show_reset_button()

        # If the question is correct and randomization is always
        # Show the reset button
        block = CapaFactory.create(rerandomize=RANDOMIZATION.ALWAYS, max_attempts=0, done=True, correct=True)
        assert block.should_show_reset_button()

        # Don't show reset button if randomization is turned on and the question is not done
        block = CapaFactory.create(rerandomize=RANDOMIZATION.ALWAYS, show_reset_button=False, done=False)
        assert not block.should_show_reset_button()

        # Show reset button if randomization is turned on and the problem is done
        block = CapaFactory.create(rerandomize=RANDOMIZATION.ALWAYS, show_reset_button=False, done=True)
        assert block.should_show_reset_button()

    def test_should_show_save_button(self):
        """Verify save button visibility logic across attempts, deadlines, and randomization."""

        attempts = random.randint(1, 10)

        # If we're after the deadline, do NOT show the save button
        block = CapaFactory.create(due=self.yesterday_str, done=True)
        assert not block.should_show_save_button()

        # If the user is out of attempts, do NOT show the save button
        block = CapaFactory.create(attempts=attempts, max_attempts=attempts, done=True)
        assert not block.should_show_save_button()

        # If user submitted a problem but hasn't reset, do NOT show the save button
        block = CapaFactory.create(rerandomize=RANDOMIZATION.ALWAYS, done=True)
        assert not block.should_show_save_button()

        block = CapaFactory.create(rerandomize="true", done=True)
        assert not block.should_show_save_button()

        # If the user has unlimited attempts and we are not randomizing,
        # then do NOT show a save button
        # because they can keep using "Check"
        block = CapaFactory.create(max_attempts=None, rerandomize=RANDOMIZATION.NEVER, done=False)
        assert not block.should_show_save_button()

        block = CapaFactory.create(max_attempts=None, rerandomize="false", done=True)
        assert not block.should_show_save_button()

        block = CapaFactory.create(max_attempts=None, rerandomize=RANDOMIZATION.PER_STUDENT, done=True)
        assert not block.should_show_save_button()

        # pre-studio default, DO show the save button
        block = CapaFactory.create(rerandomize=RANDOMIZATION.ALWAYS, done=False)
        assert block.should_show_save_button()

        # If we're not randomizing and we have limited attempts,  then we can save
        block = CapaFactory.create(rerandomize=RANDOMIZATION.NEVER, max_attempts=2, done=True)
        assert block.should_show_save_button()

        block = CapaFactory.create(rerandomize="false", max_attempts=2, done=True)
        assert block.should_show_save_button()

        block = CapaFactory.create(rerandomize=RANDOMIZATION.PER_STUDENT, max_attempts=2, done=True)
        assert block.should_show_save_button()

        # If survey question for capa (max_attempts = 0),
        # DO show the save button
        block = CapaFactory.create(max_attempts=0, done=False)
        assert block.should_show_save_button()

    def test_should_show_save_button_force_save_button(self):
        """Verify force_save_button overrides normal save button visibility rules."""
        # If we're after the deadline, do NOT show the save button
        # even though we're forcing a save
        block = CapaFactory.create(due=self.yesterday_str, force_save_button="true", done=True)
        assert not block.should_show_save_button()

        # If the user is out of attempts, do NOT show the save button
        attempts = random.randint(1, 10)
        block = CapaFactory.create(attempts=attempts, max_attempts=attempts, force_save_button="true", done=True)
        assert not block.should_show_save_button()

        # Otherwise, if we force the save button,
        # then show it even if we would ordinarily
        # require a reset first
        block = CapaFactory.create(force_save_button="true", rerandomize=RANDOMIZATION.ALWAYS, done=True)
        assert block.should_show_save_button()

        block = CapaFactory.create(force_save_button="true", rerandomize="true", done=True)
        assert block.should_show_save_button()

    def test_no_max_attempts(self):
        """Ensure problems with empty max_attempts render without errors."""
        block = CapaFactory.create(max_attempts="")
        html = block.get_problem_html()
        assert html is not None
        # assert that we got here without exploding

    @patch("xmodule.capa_block.render_to_string")
    def test_get_problem_html(self, render_template):
        """Verify problem HTML rendering uses correct template context and encapsulation."""
        render_template.return_value = "<div>Test Template HTML</div>"
        block = CapaFactory.create()

        # We've tested the show/hide button logic in other tests,
        # so here we hard-wire the values
        enable_submit_button = bool(random.randint(0, 1) % 2)
        show_reset_button = bool(random.randint(0, 1) % 2)
        show_save_button = bool(random.randint(0, 1) % 2)

        block.should_enable_submit_button = Mock(return_value=enable_submit_button)
        block.should_show_reset_button = Mock(return_value=show_reset_button)
        block.should_show_save_button = Mock(return_value=show_save_button)

        # Patch the capa problem's HTML rendering
        with patch("xmodule.capa.capa_problem.LoncapaProblem.get_html") as mock_html:
            mock_html.return_value = "<div>Test Problem HTML</div>"

            # Render the problem HTML
            html = block.get_problem_html(encapsulate=False)

            # Also render the problem encapsulated in a <div>
            html_encapsulated = block.get_problem_html(encapsulate=True)

        # Expect that we get the rendered template back
        assert html == "<div>Test Template HTML</div>"

        # Check the rendering context
        render_args, _ = render_template.call_args
        assert len(render_args) == 2

        template_name = render_args[0]
        assert template_name == "problem.html"

        context = render_args[1]
        assert context["problem"]["html"] == "<div>Test Problem HTML</div>"
        assert bool(context["should_enable_submit_button"]) == enable_submit_button
        assert bool(context["reset_button"]) == show_reset_button
        assert bool(context["save_button"]) == show_save_button
        assert not context["demand_hint_possible"]

        # Assert that the encapsulated html contains the original html
        assert html in html_encapsulated

    demand_xml = """
        <problem>
        <p>That is the question</p>
        <multiplechoiceresponse>
          <choicegroup type="MultipleChoice">
            <choice correct="false">Alpha <choicehint>A hint</choicehint>
            </choice>
            <choice correct="true">Beta</choice>
          </choicegroup>
        </multiplechoiceresponse>
        <demandhint>
          <hint>Demand 1</hint>
          <hint>Demand 2</hint>
        </demandhint>
        </problem>"""

    @patch("xmodule.capa_block.render_to_string")
    def test_demand_hint(self, render_template):
        """Verify image-based demand hints render correctly without static URL issues."""
        # HTML generation is mocked out to be meaningless here, so instead we check
        # the context dict passed into HTML generation.
        render_template.return_value = "<div>Test Template HTML</div>"
        block = CapaFactory.create(xml=self.demand_xml)
        block.get_problem_html()  # ignoring html result
        context = render_template.call_args[0][1]
        assert context["demand_hint_possible"]
        assert context["should_enable_next_hint"]

        # Check the AJAX call that gets the hint by index
        result = block.get_demand_hint(0)
        assert result["hint_index"] == 0
        assert result["should_enable_next_hint"]

        result = block.get_demand_hint(1)
        assert result["hint_index"] == 1
        assert not result["should_enable_next_hint"]

        result = block.get_demand_hint(2)  # here the server wraps around to index 0
        assert result["hint_index"] == 0
        assert result["should_enable_next_hint"]

    @patch("xmodule.capa_block.render_to_string")
    def test_single_demand_hint(self, render_template):
        """
        Test the hint button enabled state when there is just a single hint.
        """
        test_xml = """
            <problem>
            <p>That is the question</p>
            <multiplechoiceresponse>
              <choicegroup type="MultipleChoice">
                <choice correct="false">Alpha <choicehint>A hint</choicehint>
                </choice>
                <choice correct="true">Beta</choice>
              </choicegroup>
            </multiplechoiceresponse>
            <demandhint>
              <hint>Only demand hint</hint>
            </demandhint>
            </problem>"""
        render_template.return_value = "<div>Test Template HTML</div>"
        block = CapaFactory.create(xml=test_xml)
        block.get_problem_html()  # ignoring html result
        context = render_template.call_args[0][1]
        assert context["demand_hint_possible"]
        assert context["should_enable_next_hint"]

        # Check the AJAX call that gets the hint by index
        result = block.get_demand_hint(0)
        assert result["hint_index"] == 0
        assert not result["should_enable_next_hint"]

    @patch("xmodule.capa_block.render_to_string")
    def test_image_hint(self, render_template):
        """
        Test the hint button shows an image without the static url.
        """
        test_xml = """
            <problem>
            <p>That is the question</p>
            <multiplechoiceresponse>
              <choicegroup type="MultipleChoice">
                <choice correct="false">Alpha <choicehint>A hint</choicehint>
                </choice>
                <choice correct="true">Beta</choice>
              </choicegroup>
            </multiplechoiceresponse>
            <demandhint>
              <hint>
                <img src="/static/7b1d74b2383b7d25a70ae4991190c222_28-collection-of-dark-souls-bonfire-clipart-high-quality-free-_1200-1386.jpeg"> </img>
                You can add an optional hint like this. Problems that have a hint include a hint button, and this text appears the first time learners select the button.</hint>
            </demandhint>
            </problem>"""
        render_template.return_value = "<div>Test Template HTML</div>"
        block = CapaFactory.create(xml=test_xml)
        block.get_problem_html()  # ignoring html result
        context = render_template.call_args[0][1]
        assert context["demand_hint_possible"]
        assert context["should_enable_next_hint"]

        # Check the AJAX call that gets the hint by index
        result = block.get_demand_hint(0)
        assert result["hint_index"] == 0
        assert not result["should_enable_next_hint"]

    def test_demand_hint_logging(self):
        """
        Test calling get_demand_hunt() results in an event being published.
        """
        block = CapaFactory.create(xml=self.demand_xml)
        with patch.object(block.runtime, "publish") as mock_publish:
            block.get_problem_html()
            block.get_demand_hint(0)
            mock_publish.assert_called_with(
                block,
                "edx.problem.hint.demandhint_displayed",
                {"hint_index": 0, "module_id": str(block.location), "hint_text": "Demand 1", "hint_len": 2},
            )

    def test_input_state_consistency(self):
        """Verify input_state keys remain consistent and isolated across block instances."""
        block1 = CapaFactory.create()
        block2 = CapaFactory.create()

        # check to make sure that the input_state and the keys have the same values
        block1.set_state_from_lcp()
        assert list(block1.lcp.inputs.keys()) == list(block1.input_state.keys())

        block2.set_state_from_lcp()

        intersection = set(block2.input_state.keys()).intersection(set(block1.input_state.keys()))
        assert len(intersection) == 0

    @patch("xmodule.capa_block.render_to_string")
    def test_get_problem_html_error(self, render_template):
        """
        In production, when an error occurs with the problem HTML
        rendering, a "dummy" problem is created with an error
        message to display to the user.
        """
        render_template.return_value = "<div>Test Template HTML</div>"
        block = CapaFactory.create()

        # Save the original problem so we can compare it later
        original_problem = block.lcp

        # Simulate throwing an exception when the capa problem
        # is asked to render itself as HTML
        block.lcp.get_html = Mock(side_effect=Exception("Test"))

        # Try to render the block with DEBUG turned off
        html = block.get_problem_html()

        assert html is not None

        # Check the rendering context
        render_args, _ = render_template.call_args
        context = render_args[1]
        assert "error" in context["problem"]["html"]

        # Expect that the block has created a new dummy problem with the error
        assert original_problem != block.lcp

    @patch("xmodule.capa_block.render_to_string")
    def test_get_problem_html_error_preview(self, render_template):
        """
        Test the html response when an error occurs with DEBUG off in Studio.
        """
        render_template.return_value = "<div>Test Template HTML</div>"
        block = CapaFactory.create()

        # Simulate throwing an exception when the capa problem
        # is asked to render itself as HTML
        error_msg = "Superterrible error happened: ☠"
        block.lcp.get_html = Mock(side_effect=Exception(error_msg))

        block.runtime.is_author_mode = True

        # Try to render the block with the author mode turned on
        html = block.get_problem_html()

        assert html is not None

        # Check the rendering context
        render_args, _ = render_template.call_args
        context = render_args[1]
        assert error_msg in context["problem"]["html"]

    @override_settings(DEBUG=True)
    @patch("xmodule.capa_block.render_to_string")
    def test_get_problem_html_error_w_debug(self, render_template):
        """
        Test the html response when an error occurs with DEBUG on
        """
        render_template.return_value = "<div>Test Template HTML</div>"
        block = CapaFactory.create()
        block.runtime.is_author_mode = True

        # Simulate throwing an exception when the capa problem
        # is asked to render itself as HTML
        error_msg = "Superterrible error happened: ☠"
        block.lcp.get_html = Mock(side_effect=Exception(error_msg))

        # Try to render the block with DEBUG turned on
        html = block.get_problem_html()

        assert html is not None

        # Check the rendering context
        render_args, _ = render_template.call_args
        context = render_args[1]
        assert error_msg in context["problem"]["html"]

    @ddt.data(
        "false", "true", RANDOMIZATION.NEVER, RANDOMIZATION.PER_STUDENT, RANDOMIZATION.ALWAYS, RANDOMIZATION.ONRESET
    )
    def test_random_seed_no_change(self, rerandomize):
        """Verify problem seed remains stable when rerandomization does not apply."""

        # Run the test for each possible rerandomize value

        block = CapaFactory.create(rerandomize=rerandomize)

        # Get the seed
        # By this point, the block should have persisted the seed
        seed = block.seed
        assert seed is not None

        # If we're not rerandomizing, the seed is always set
        # to the same value (1)
        if rerandomize == RANDOMIZATION.NEVER:
            assert seed == 1, f"Seed should always be 1 when rerandomize='{rerandomize}'"

        # Check the problem
        get_request_dict = {CapaFactory.input_key(): "3.14"}
        block.submit_problem(get_request_dict)

        # Expect that the seed is the same
        assert seed == block.seed

        # Save the problem
        block.save_problem(get_request_dict)

        # Expect that the seed is the same
        assert seed == block.seed

    @ddt.data(
        "false", "true", RANDOMIZATION.NEVER, RANDOMIZATION.PER_STUDENT, RANDOMIZATION.ALWAYS, RANDOMIZATION.ONRESET
    )
    def test_random_seed_with_reset(self, rerandomize):
        """
        Run the test for each possible rerandomize value
        """

        def _reset_and_get_seed(block):
            """
            Reset the XBlock and return the block's seed
            """

            # Simulate submitting an attempt
            # We need to do this, or reset_problem() will
            # fail because it won't re-randomize until the problem has been submitted
            # the problem yet.
            block.done = True

            # Reset the problem
            block.reset_problem({})

            # Return the seed
            return block.seed

        def _retry_and_check(num_tries, test_func):
            """
            Returns True if *test_func* was successful
            (returned True) within *num_tries* attempts

            *test_func* must be a function
            of the form test_func() -> bool
            """
            success = False
            for __ in range(num_tries):
                if test_func() is True:
                    success = True
                    break
            return success

        block = CapaFactory.create(rerandomize=rerandomize, done=True)

        # Get the seed
        # By this point, the block should have persisted the seed
        seed = block.seed
        assert seed is not None

        # We do NOT want the seed to reset if rerandomize
        # is set to 'never' -- it should still be 1
        # The seed also stays the same if we're randomizing
        # 'per_student': the same student should see the same problem
        if rerandomize in [RANDOMIZATION.NEVER, "false", RANDOMIZATION.PER_STUDENT]:
            assert seed == _reset_and_get_seed(block)

        # Otherwise, we expect the seed to change
        # to another valid seed
        else:

            # Since there's a small chance (expected) we might get the
            # same seed again, give it 60 chances
            # to generate a different seed
            success = _retry_and_check(60, lambda: _reset_and_get_seed(block) != seed)

            assert block.seed is not None
            msg = "Could not get a new seed from reset after 60 tries"
            assert success, msg

    @ddt.data(
        "false", "true", RANDOMIZATION.NEVER, RANDOMIZATION.PER_STUDENT, RANDOMIZATION.ALWAYS, RANDOMIZATION.ONRESET
    )
    def test_random_seed_with_reset_question_unsubmitted(self, rerandomize):
        """
        Run the test for each possible rerandomize value
        """

        def _reset_and_get_seed(block):
            """
            Reset the XBlock and return the block's seed
            """

            # Reset the problem
            # By default, the problem is instantiated as unsubmitted
            block.reset_problem({})

            # Return the seed
            return block.seed

        block = CapaFactory.create(rerandomize=rerandomize, done=False)

        # Get the seed
        # By this point, the block should have persisted the seed
        seed = block.seed
        assert seed is not None

        # the seed should never change because the student hasn't finished the problem
        assert seed == _reset_and_get_seed(block)

    @ddt.data(RANDOMIZATION.ALWAYS, RANDOMIZATION.PER_STUDENT, "true", RANDOMIZATION.ONRESET)
    def test_random_seed_bins(self, rerandomize):
        """Ensure generated random seeds fall within the expected numeric range."""
        # Assert that we are limiting the number of possible seeds.
        # Get a bunch of seeds, they should all be in 0-999.
        i = 200
        while i > 0:
            block = CapaFactory.create(rerandomize=rerandomize)
            assert 0 <= block.seed < 1000
            i -= 1

    @patch("xmodule.capa_block.log")
    @patch("xmodule.capa_block.Progress")
    def test_get_progress_error(self, mock_progress, mock_log):
        """
        Check that an exception given in `Progress` produces a `log.exception` call.
        """
        error_types = [TypeError, ValueError]
        for error_type in error_types:
            mock_progress.side_effect = error_type
            block = CapaFactory.create()
            assert block.get_progress() is None
            mock_log.exception.assert_called_once_with("Got bad progress")
            mock_log.reset_mock()

    @patch("xmodule.capa_block.Progress")
    def test_get_progress_no_error_if_weight_zero(self, mock_progress):
        """
        Check that if the weight is 0 get_progress does not try to create a Progress object.
        """
        mock_progress.return_value = True
        block = CapaFactory.create()
        block.weight = 0
        progress = block.get_progress()
        assert progress is None
        assert not mock_progress.called

    @patch("xmodule.capa_block.Progress")
    def test_get_progress_calculate_progress_fraction(self, mock_progress):
        """
        Check that score and total are calculated correctly for the progress fraction.
        """
        block = CapaFactory.create()
        block.weight = 1
        block.get_progress()
        mock_progress.assert_called_with(0, 1)

        other_block = CapaFactory.create(correct=True)
        other_block.weight = 1
        other_block.get_progress()
        mock_progress.assert_called_with(1, 1)

    @ddt.data(
        ("never", True, None),
        ("never", False, None),
        ("past_due", True, None),
        ("past_due", False, None),
        ("always", True, 1),
        ("always", False, 0),
    )
    @ddt.unpack
    def test_get_display_progress_show_correctness(self, show_correctness, is_correct, expected_score):
        """
        Check that score and total are calculated correctly for the progress fraction.
        """
        block = CapaFactory.create(correct=is_correct, show_correctness=show_correctness, due=self.tomorrow_str)
        block.weight = 1
        score, total = block.get_display_progress()
        assert score == expected_score
        assert total == 1

    def test_get_html(self):
        """
        Check that get_html() calls get_progress() with no arguments.
        """
        block = CapaFactory.create()
        block.get_progress = Mock(wraps=block.get_progress)
        block.get_html()
        block.get_progress.assert_called_with()

    def test_get_problem(self):
        """
        Check that get_problem() returns the expected dictionary.
        """
        block = CapaFactory.create()
        assert block.get_problem("data") == {"html": block.get_problem_html(encapsulate=False)}

    # Standard question with shuffle="true" used by a few tests
    common_shuffle_xml = textwrap.dedent(
        """
        <problem>
        <multiplechoiceresponse>
          <choicegroup type="MultipleChoice" shuffle="true">
            <choice correct="false">Apple</choice>
            <choice correct="false">Banana</choice>
            <choice correct="false">Chocolate</choice>
            <choice correct ="true">Donut</choice>
          </choicegroup>
        </multiplechoiceresponse>
        </problem>
    """
    )

    def test_check_unmask(self):
        """
        Check that shuffle unmasking is plumbed through: when submit_problem is called,
        unmasked names should appear in the publish event_info.
        """
        block = CapaFactory.create(xml=self.common_shuffle_xml)
        with patch.object(block.runtime, "publish") as mock_publish:
            get_request_dict = {CapaFactory.input_key(): "choice_3"}  # the correct choice
            block.submit_problem(get_request_dict)
            mock_call = mock_publish.mock_calls[1]
            event_info = mock_call[1][2]
            assert event_info["answers"][CapaFactory.answer_key()] == "choice_3"
            # 'permutation' key added to record how problem was shown
            assert event_info["permutation"][CapaFactory.answer_key()] == (
                "shuffle",
                ["choice_3", "choice_1", "choice_2", "choice_0"],
            )
            assert event_info["success"] == "correct"

    def test_check_unmask_answerpool(self):
        """Check answer-pool question publish uses unmasked names"""
        xml = textwrap.dedent(
            """
            <problem>
            <multiplechoiceresponse>
              <choicegroup type="MultipleChoice" answer-pool="4">
                <choice correct="false">Apple</choice>
                <choice correct="false">Banana</choice>
                <choice correct="false">Chocolate</choice>
                <choice correct ="true">Donut</choice>
              </choicegroup>
            </multiplechoiceresponse>
            </problem>
        """
        )
        block = CapaFactory.create(xml=xml)
        with patch.object(block.runtime, "publish") as mock_publish:
            get_request_dict = {CapaFactory.input_key(): "choice_2"}  # mask_X form when masking enabled
            block.submit_problem(get_request_dict)
            mock_call = mock_publish.mock_calls[1]
            event_info = mock_call[1][2]
            assert event_info["answers"][CapaFactory.answer_key()] == "choice_2"
            # 'permutation' key added to record how problem was shown
            assert event_info["permutation"][CapaFactory.answer_key()] == (
                "answerpool",
                ["choice_1", "choice_3", "choice_2", "choice_0"],
            )
            assert event_info["success"] == "incorrect"

    @ddt.unpack
    @ddt.data(
        {"display_name": None, "expected_display_name": "problem"},
        {"display_name": "", "expected_display_name": "problem"},
        {"display_name": " ", "expected_display_name": "problem"},
        {"display_name": "CAPA 101", "expected_display_name": "CAPA 101"},
    )
    def test_problem_display_name_with_default(self, display_name, expected_display_name):
        """
        Verify that display_name_with_default works as expected.
        """
        block = CapaFactory.create(display_name=display_name)
        assert block.display_name_with_default == expected_display_name

    @ddt.data(
        "",
        "   ",
    )
    @patch("xmodule.capa_block.render_to_string")
    def test_problem_no_display_name(self, display_name, render_template):
        """
        Verify that if problem display name is not provided then a default name is used.
        """
        render_template.return_value = "<div>Test Template HTML</div>"
        block = CapaFactory.create(display_name=display_name)
        block.get_problem_html()
        render_args, _ = render_template.call_args
        context = render_args[1]
        assert context["problem"]["name"] == block.location.block_type


@ddt.ddt
@pytest.mark.django_db
class ProblemBlockXMLTest(unittest.TestCase):
    """Tests XML strings for various problem types in XBlocks."""

    sample_checkbox_problem_xml = textwrap.dedent(
        """
        <problem>
            <p>Title</p>

            <p>Description</p>

            <p>Example</p>

            <p>The following languages are in the Indo-European family:</p>
            <choiceresponse>
              <checkboxgroup>
                <choice correct="true">Urdu</choice>
                <choice correct="false">Finnish</choice>
                <choice correct="true">Marathi</choice>
                <choice correct="true">French</choice>
                <choice correct="false">Hungarian</choice>
              </checkboxgroup>
            </choiceresponse>

            <p>Note: Make sure you select all of the correct options—there may be more than one!</p>

            <solution>
            <div class="detailed-solution">
            <p>Explanation</p>

            <p>Solution for CAPA problem</p>

            </div>
            </solution>

        </problem>
    """
    )

    sample_dropdown_problem_xml = textwrap.dedent(
        """
        <problem>
            <p>Dropdown problems allow learners to select only one option from a list of options.</p>

            <p>Description</p>

            <p>You can use the following example problem as a model.</p>

            <p> Which of the following countries celebrates its independence on August 15?</p>


            <optionresponse>
              <optioninput options="('India','Spain','China','Bermuda')" correct="India"></optioninput>
            </optionresponse>

             <solution>
            <div class="detailed-solution">
            <p>Explanation</p>

            <p> India became an independent nation on August 15, 1947.</p>

            </div>
            </solution>

        </problem>
    """
    )

    sample_multichoice_problem_xml = textwrap.dedent(
        """
        <problem>
            <p>Multiple choice problems allow learners to select only one option.</p>

            <p>When you add the problem, be sure to select Settings to specify a Display Name and other values.</p>

            <p>You can use the following example problem as a model.</p>

            <p>Which of the following countries has the largest population?</p>
            <multiplechoiceresponse>
              <choicegroup type="MultipleChoice">
                <choice correct="false">Brazil
                    <choicehint>timely feedback -- explain why an almost correct answer is wrong</choicehint>
                </choice>
                <choice correct="false">Germany</choice>
                <choice correct="true">Indonesia</choice>
                <choice correct="false">Russia</choice>
              </choicegroup>
            </multiplechoiceresponse>

            <solution>
            <div class="detailed-solution">
            <p>Explanation</p>

            <p>According to September 2014 estimates:</p>
            <p>The population of Indonesia is approximately 250 million.</p>
            <p>The population of Brazil  is approximately 200 million.</p>
            <p>The population of Russia is approximately 146 million.</p>
            <p>The population of Germany is approximately 81 million.</p>

            </div>
            </solution>

        </problem>
    """
    )

    sample_numerical_input_problem_xml = textwrap.dedent(
        """
        <problem>
            <p>In a numerical input problem, learners enter numbers or a specific and relatively simple mathematical
            expression. Learners enter the response in plain text, and the system then converts the text to a symbolic
            expression that learners can see below the response field.</p>

            <p>The system can handle several types of characters, including basic operators, fractions, exponents, and
            common constants such as "i". You can refer learners to "Entering Mathematical and Scientific Expressions"
            in the edX Guide for Students for more information.</p>

            <p>When you add the problem, be sure to select Settings to specify a Display Name and other values that
            apply.</p>

            <p>You can use the following example problems as models.</p>

            <p>How many miles away from Earth is the sun? Use scientific notation to answer.</p>

            <numericalresponse answer="9.3*10^7">
              <formulaequationinput/>
            </numericalresponse>

            <p>The square of what number is -100?</p>

            <numericalresponse answer="10*i">
              <formulaequationinput/>
            </numericalresponse>

            <solution>
            <div class="detailed-solution">
            <p>Explanation</p>

            <p>The sun is 93,000,000, or 9.3*10^7, miles away from Earth.</p>
            <p>-100 is the square of 10 times the imaginary number, i.</p>

            </div>
            </solution>

        </problem>
    """
    )

    sample_text_input_problem_xml = textwrap.dedent(
        """
        <problem>
            <p>In text input problems, also known as "fill-in-the-blank" problems, learners enter text into a response
            field. The text can include letters and characters such as punctuation marks. The text that the learner
            enters must match your specified answer text exactly. You can specify more than one correct answer.
            Learners must enter a response that matches one of the correct answers exactly.</p>

            <p>When you add the problem, be sure to select Settings to specify a Display Name and other values that
            apply.</p>

            <p>You can use the following example problem as a model.</p>

            <p>What was the first post-secondary school in China to allow both male and female students?</p>

            <stringresponse answer="Nanjing Higher Normal Institute" type="ci" >
              <additional_answer answer="National Central University"></additional_answer>
              <additional_answer answer="Nanjing University"></additional_answer>
              <textline size="20"/>
            </stringresponse>

            <solution>
            <div class="detailed-solution">
            <p>Explanation</p>

            <p>Nanjing Higher Normal Institute first admitted female students in 1920.</p>

            </div>
            </solution>

        </problem>
    """
    )

    sample_checkboxes_with_hints_and_feedback_problem_xml = textwrap.dedent(
        """
        <problem>
            <p>You can provide feedback for each option in a checkbox problem, with distinct feedback depending on
            whether or not the learner selects that option.</p>

            <p>You can also provide compound feedback for a specific combination of answers. For example, if you have
            three possible answers in the problem, you can configure specific feedback for when a learner selects each
            combination of possible answers.</p>

            <p>You can also add hints for learners.</p>

            <p>Be sure to select Settings to specify a Display Name and other values that apply.</p>

            <p>Use the following example problem as a model.</p>

            <p>Which of the following is a fruit? Check all that apply.</p>
            <choiceresponse>
              <checkboxgroup>
                <choice correct="true">apple
                  <choicehint selected="true">You are correct that an apple is a fruit because it is the fertilized
                  ovary that comes from an apple tree and contains seeds.</choicehint>
                  <choicehint selected="false">Remember that an apple is also a fruit.</choicehint></choice>
                <choice correct="true">pumpkin
                  <choicehint selected="true">You are correct that a pumpkin is a fruit because it is the fertilized
                  ovary of a squash plant and contains seeds.</choicehint>
                  <choicehint selected="false">Remember that a pumpkin is also a fruit.</choicehint></choice>
                <choice correct="false">potato
                  <choicehint selected="true">A potato is a vegetable, not a fruit, because it does not come from a
                  flower and does not contain seeds.</choicehint>
                  <choicehint selected="false">You are correct that a potato is a vegetable because it is an edible
                  part of a plant in tuber form.</choicehint></choice>
                <choice correct="true">tomato
                  <choicehint selected="true">You are correct that a tomato is a fruit because it is the fertilized
                  ovary of a tomato plant and contains seeds.</choicehint>
                  <choicehint selected="false">Many people mistakenly think a tomato is a vegetable. However, because
                  a tomato is the fertilized ovary of a tomato plant and contains seeds, it is a fruit.</choicehint>
                  </choice>
                <compoundhint value="A B D">An apple, pumpkin, and tomato are all fruits as they all are fertilized
                ovaries of a plant and contain seeds.</compoundhint>
                <compoundhint value="A B C D">You are correct that an apple, pumpkin, and tomato are all fruits as they
                all are fertilized ovaries of a plant and contain seeds. However, a potato is not a fruit as it is an
                edible part of a plant in tuber form and is a vegetable.</compoundhint>
              </checkboxgroup>
            </choiceresponse>


            <demandhint>
              <hint>A fruit is the fertilized ovary from a flower.</hint>
              <hint>A fruit contains seeds of the plant.</hint>
            </demandhint>
        </problem>
    """
    )

    sample_dropdown_with_hints_and_feedback_problem_xml = textwrap.dedent(
        """
        <problem>
            <p>You can provide feedback for each available option in a dropdown problem.</p>

            <p>You can also add hints for learners.</p>

            <p>Be sure to select Settings to specify a Display Name and other values that apply.</p>

            <p>Use the following example problem as a model.</p>

            <p> A/an ________ is a vegetable.</p>
            <optionresponse>
              <optioninput>
                <option correct="False">apple <optionhint>An apple is the fertilized ovary that comes from an apple
                tree and contains seeds, meaning it is a fruit.</optionhint></option>
                <option correct="False">pumpkin <optionhint>A pumpkin is the fertilized ovary of a squash plant and
                contains seeds, meaning it is a fruit.</optionhint></option>
                <option correct="True">potato <optionhint>A potato is an edible part of a plant in tuber form and is a
                vegetable.</optionhint></option>
                <option correct="False">tomato <optionhint>Many people mistakenly think a tomato is a vegetable.
                However, because a tomato is the fertilized ovary of a tomato plant and contains seeds, it is a fruit.
                </optionhint></option>
              </optioninput>
            </optionresponse>

            <demandhint>
              <hint>A fruit is the fertilized ovary from a flower.</hint>
              <hint>A fruit contains seeds of the plant.</hint>
            </demandhint>
        </problem>
    """
    )

    sample_multichoice_with_hints_and_feedback_problem_xml = textwrap.dedent(
        """
        <problem>
            <p>You can provide feedback for each option in a multiple choice problem.</p>

            <p>You can also add hints for learners.</p>

            <p>Be sure to select Settings to specify a Display Name and other values that apply.</p>

            <p>Use the following example problem as a model.</p>

            <p>Which of the following is a vegetable?</p>
            <multiplechoiceresponse>
              <choicegroup type="MultipleChoice">
                <choice correct="false">apple <choicehint>An apple is the fertilized ovary that comes from an apple
                tree and contains seeds, meaning it is a fruit.</choicehint></choice>
                <choice correct="false">pumpkin <choicehint>A pumpkin is the fertilized ovary of a squash plant and
                contains seeds, meaning it is a fruit.</choicehint></choice>
                <choice correct="true">potato <choicehint>A potato is an edible part of a plant in tuber form and is a
                vegetable.</choicehint></choice>
                <choice correct="false">tomato <choicehint>Many people mistakenly think a tomato is a vegetable.
                However, because a tomato is the fertilized ovary of a tomato plant and contains seeds, it is a fruit.
                </choicehint></choice>
              </choicegroup>
            </multiplechoiceresponse>


            <demandhint>
              <hint>A fruit is the fertilized ovary from a flower.</hint>
              <hint>A fruit contains seeds of the plant.</hint>
            </demandhint>
        </problem>
    """
    )

    sample_numerical_input_with_hints_and_feedback_problem_xml = textwrap.dedent(
        """
        <problem>
            <p>You can provide feedback for correct answers in numerical input problems. You cannot provide feedback
            for incorrect answers.</p>

            <p>Use feedback for the correct answer to reinforce the process for arriving at the numerical value.</p>

            <p>You can also add hints for learners.</p>

            <p>Be sure to select Settings to specify a Display Name and other values that apply.</p>

            <p>Use the following example problem as a model.</p>

            <p>What is the arithmetic mean for the following set of numbers? (1, 5, 6, 3, 5)</p>

            <numericalresponse answer="4">
              <formulaequationinput/>
              <correcthint>The mean for this set of numbers is 20 / 5, which equals 4.</correcthint>
            </numericalresponse>
            <solution>
            <div class="detailed-solution">
            <p>Explanation</p>

            <p>The mean is calculated by summing the set of numbers and dividing by n. In this case:
            (1 + 5 + 6 + 3 + 5) / 5 = 20 / 5 = 4.</p>

            </div>
            </solution>

            <demandhint>
              <hint>The mean is calculated by summing the set of numbers and dividing by n.</hint>
              <hint>n is the count of items in the set.</hint>
            </demandhint>
        </problem>
    """
    )

    sample_text_input_with_hints_and_feedback_problem_xml = textwrap.dedent(
        """
        <problem>
            <p>You can provide feedback for the correct answer in text input problems, as well as for specific
            incorrect answers.</p>

            <p>Use feedback on expected incorrect answers to address common misconceptions and to provide guidance on
            how to arrive at the correct answer.</p>

            <p>Be sure to select Settings to specify a Display Name and other values that apply.</p>

            <p>Use the following example problem as a model.</p>

            <p>Which U.S. state has the largest land area?</p>

            <stringresponse answer="Alaska" type="ci" >
              <correcthint>Alaska is 576,400 square miles, more than double the land area of the second largest state,
              Texas.</correcthint>
              <stringequalhint answer="Texas">While many people think Texas is the largest state, it is actually the
              second largest, with 261,797 square miles.</stringequalhint>
              <stringequalhint answer="California">California is the third largest state, with 155,959 square miles.
              </stringequalhint>
              <textline size="20"/>
            </stringresponse>

            <demandhint>
              <hint>Consider the square miles, not population.</hint>
              <hint>Consider all 50 states, not just the continental United States.</hint>
            </demandhint>
        </problem>
    """
    )

    def _create_block(self, xml, name=None):
        """Creates a ProblemBlock to run test against"""
        block = CapaFactory.create()
        block.data = xml
        if name:
            block.display_name = name
        return block

    @ddt.data(*sorted(responsetypes.registry.registered_tags()))
    def test_all_response_types(self, response_tag):
        """Tests that every registered response tag is correctly returned"""
        xml = "<problem><{response_tag}></{response_tag}></problem>".format(response_tag=response_tag)
        name = "Some Capa Problem"
        block = self._create_block(xml, name=name)
        assert block.problem_types == {response_tag}
        assert block.index_dictionary() == {
            "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
            "problem_types": [response_tag],
            "content": {"display_name": name, "capa_content": ""},
        }

    def test_response_types_ignores_non_response_tags(self):
        """Ensure non-response XML tags are ignored when determining problem response types."""
        xml = textwrap.dedent(
            """
            <problem>
            <p>Label</p>
            <div>Some comment</div>
            <multiplechoiceresponse>
              <choicegroup type="MultipleChoice" answer-pool="4">
                <choice correct="false">Apple</choice>
                <choice correct="false">Banana</choice>
                <choice correct="false">Chocolate</choice>
                <choice correct ="true">Donut</choice>
              </choicegroup>
            </multiplechoiceresponse>
            </problem>
        """
        )
        name = "Test Capa Problem"
        block = self._create_block(xml, name=name)
        assert block.problem_types == {"multiplechoiceresponse"}
        assert block.index_dictionary() == {
            "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
            "problem_types": ["multiplechoiceresponse"],
            "content": {"display_name": name, "capa_content": "Label Some comment Apple Banana Chocolate Donut"},
        }

    def test_response_types_multiple_tags(self):
        """Verify indexing behavior when multiple response types are present in a single problem."""
        xml = textwrap.dedent(
            """
            <problem>
                <p>Label</p>
                <div>Some comment</div>
                <multiplechoiceresponse>
                  <choicegroup type="MultipleChoice" answer-pool="1">
                    <choice correct ="true">Donut</choice>
                  </choicegroup>
                </multiplechoiceresponse>
                <multiplechoiceresponse>
                  <choicegroup type="MultipleChoice" answer-pool="1">
                    <choice correct ="true">Buggy</choice>
                  </choicegroup>
                </multiplechoiceresponse>
                <optionresponse>
                    <optioninput options="('1','2')" correct="2"></optioninput>
                </optionresponse>
            </problem>
        """
        )
        name = "Other Test Capa Problem"
        block = self._create_block(xml, name=name)
        assert block.problem_types == {"multiplechoiceresponse", "optionresponse"}

        # We are converting problem_types to a set to compare it later without taking into account the order
        # the reasoning behind is that the problem_types (property) is represented by dict and when it is converted
        # to list its ordering is different everytime.

        indexing_result = block.index_dictionary()
        indexing_result["problem_types"] = set(indexing_result["problem_types"])
        self.assertDictEqual(
            indexing_result,
            {
                "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
                "problem_types": {"optionresponse", "multiplechoiceresponse"},
                "content": {"display_name": name, "capa_content": "Label Some comment Donut Buggy '1','2'"},
            },
        )

    def test_solutions_not_indexed(self):
        """Confirm that solutions, scripts, styles, answers, and hints are excluded from indexing."""
        xml = textwrap.dedent(
            """
            <problem>
                <solution>Test solution.</solution>
                <solution explanation-id="solution0">Test solution with attribute.</solution>
                <solutionset>
                    Test solutionset.
                    <solution explanation-id="solution1">Test solution within solutionset.</solution>
                </solutionset>

                <targetedfeedback>Test feedback.</targetedfeedback>
                <targetedfeedback explanation-id="feedback0">Test feedback with attribute.</targetedfeedback>
                <targetedfeedbackset>
                    Test FeedbackSet.
                    <targetedfeedback explanation-id="feedback1">Test feedback within feedbackset.</targetedfeedback>
                </targetedfeedbackset>

                <answer>Test answer.</answer>
                <answer type="loncapa/python">Test answer with attribute.</answer>

                <script>Test script.</script>
                <script type="loncapa/python">Test script with attribute.</script>

                <style>Test style.</style>
                <style media="all and (max-width: 1920px)">Test style with attribute.</style>

                <choicehint>Test choicehint.</choicehint>
                <hint>Test hint.</hint>
                <hintpart>Test hintpart.</hintpart>
            </problem>
        """
        )
        name = "Blank Common Capa Problem"
        block = self._create_block(xml, name=name)
        assert block.index_dictionary() == {
            "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
            "problem_types": [],
            "content": {"display_name": name, "capa_content": ""},
        }

    def test_indexing_checkboxes(self):
        """Verify correct indexing of checkbox-based problems and extracted content."""
        name = "Checkboxes"
        block = self._create_block(self.sample_checkbox_problem_xml, name=name)
        capa_content = textwrap.dedent(
            """
            Title
            Description
            Example
            The following languages are in the Indo-European family:
            Urdu
            Finnish
            Marathi
            French
            Hungarian
            Note: Make sure you select all of the correct options—there may be more than one!
        """
        )
        assert block.problem_types == {"choiceresponse"}
        assert block.index_dictionary() == {
            "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
            "problem_types": ["choiceresponse"],
            "content": {"display_name": name, "capa_content": capa_content.replace("\n", " ").strip()},
        }

    def test_indexing_dropdown(self):
        """Verify correct indexing of dropdown-based problems and extracted content."""
        name = "Dropdown"
        block = self._create_block(self.sample_dropdown_problem_xml, name=name)
        capa_content = textwrap.dedent(
            """
            Dropdown problems allow learners to select only one option from a list of options.
            Description
            You can use the following example problem as a model.
            Which of the following countries celebrates its independence on August 15? 'India','Spain','China','Bermuda'
        """
        )
        assert block.problem_types == {"optionresponse"}
        assert block.index_dictionary() == {
            "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
            "problem_types": ["optionresponse"],
            "content": {"display_name": name, "capa_content": capa_content.replace("\n", " ").strip()},
        }

    def test_indexing_multiple_choice(self):
        """Verify correct indexing of multiple-choice problems and extracted content."""
        name = "Multiple Choice"
        block = self._create_block(self.sample_multichoice_problem_xml, name=name)
        capa_content = textwrap.dedent(
            """
            Multiple choice problems allow learners to select only one option.
            When you add the problem, be sure to select Settings to specify a Display Name and other values.
            You can use the following example problem as a model.
            Which of the following countries has the largest population?
            Brazil
            Germany
            Indonesia
            Russia
        """
        )
        assert block.problem_types == {"multiplechoiceresponse"}
        assert block.index_dictionary() == {
            "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
            "problem_types": ["multiplechoiceresponse"],
            "content": {"display_name": name, "capa_content": capa_content.replace("\n", " ").strip()},
        }

    def test_indexing_numerical_input(self):
        """Verify correct indexing of numerical input problems and extracted content."""
        name = "Numerical Input"
        block = self._create_block(self.sample_numerical_input_problem_xml, name=name)
        capa_content = textwrap.dedent(
            """
            In a numerical input problem, learners enter numbers or a specific and relatively simple mathematical
            expression. Learners enter the response in plain text, and the system then converts the text to a symbolic
            expression that learners can see below the response field.
            The system can handle several types of characters, including basic operators, fractions, exponents, and
            common constants such as "i". You can refer learners to "Entering Mathematical and Scientific Expressions"
            in the edX Guide for Students for more information.
            When you add the problem, be sure to select Settings to specify a Display Name and other values that
            apply.
            You can use the following example problems as models.
            How many miles away from Earth is the sun? Use scientific notation to answer.
            The square of what number is -100?
        """
        )
        assert block.problem_types == {"numericalresponse"}
        assert block.index_dictionary() == {
            "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
            "problem_types": ["numericalresponse"],
            "content": {"display_name": name, "capa_content": capa_content.replace("\n", " ").strip()},
        }

    def test_indexing_text_input(self):
        """Verify correct indexing of text input problems and extracted content."""
        name = "Text Input"
        block = self._create_block(self.sample_text_input_problem_xml, name=name)
        capa_content = textwrap.dedent(
            """
            In text input problems, also known as "fill-in-the-blank" problems, learners enter text into a response
            field. The text can include letters and characters such as punctuation marks. The text that the learner
            enters must match your specified answer text exactly. You can specify more than one correct answer.
            Learners must enter a response that matches one of the correct answers exactly.
            When you add the problem, be sure to select Settings to specify a Display Name and other values that
            apply.
            You can use the following example problem as a model.
            What was the first post-secondary school in China to allow both male and female students?
        """
        )
        assert block.problem_types == {"stringresponse"}
        assert block.index_dictionary() == {
            "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
            "problem_types": ["stringresponse"],
            "content": {"display_name": name, "capa_content": capa_content.replace("\n", " ").strip()},
        }

    def test_indexing_non_latin_problem(self):
        """Ensure non-Latin characters are preserved correctly in indexed problem content."""
        sample_text_input_problem_xml = textwrap.dedent(
            """
            <problem>
                <script type="text/python">FX1_VAL='Καλημέρα'</script>
                <p>Δοκιμή με μεταβλητές με Ελληνικούς χαρακτήρες μέσα σε python: $FX1_VAL</p>
            </problem>
        """
        )
        name = "Non latin Input"
        block = self._create_block(sample_text_input_problem_xml, name=name)
        capa_content = "Δοκιμή με μεταβλητές με Ελληνικούς χαρακτήρες μέσα σε python: $FX1_VAL"

        block_dict = block.index_dictionary()
        assert block_dict["content"]["capa_content"] == smart_str(capa_content)

    def test_indexing_checkboxes_with_hints_and_feedback(self):
        """Verify indexing of checkbox problems containing hints and feedback."""
        name = "Checkboxes with Hints and Feedback"
        block = self._create_block(self.sample_checkboxes_with_hints_and_feedback_problem_xml, name=name)
        capa_content = textwrap.dedent(
            """
            You can provide feedback for each option in a checkbox problem, with distinct feedback depending on
            whether or not the learner selects that option.
            You can also provide compound feedback for a specific combination of answers. For example, if you have
            three possible answers in the problem, you can configure specific feedback for when a learner selects each
            combination of possible answers.
            You can also add hints for learners.
            Be sure to select Settings to specify a Display Name and other values that apply.
            Use the following example problem as a model.
            Which of the following is a fruit? Check all that apply.
            apple
            pumpkin
            potato
            tomato
        """
        )
        assert block.problem_types == {"choiceresponse"}
        assert block.index_dictionary() == {
            "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
            "problem_types": ["choiceresponse"],
            "content": {"display_name": name, "capa_content": capa_content.replace("\n", " ").strip()},
        }

    def test_indexing_dropdown_with_hints_and_feedback(self):
        """Verify indexing of dropdown problems containing hints and feedback."""
        name = "Dropdown with Hints and Feedback"
        block = self._create_block(self.sample_dropdown_with_hints_and_feedback_problem_xml, name=name)
        capa_content = textwrap.dedent(
            """
            You can provide feedback for each available option in a dropdown problem.
            You can also add hints for learners.
            Be sure to select Settings to specify a Display Name and other values that apply.
            Use the following example problem as a model.
            A/an ________ is a vegetable.
            apple
            pumpkin
            potato
            tomato
        """
        )
        assert block.problem_types == {"optionresponse"}
        assert block.index_dictionary() == {
            "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
            "problem_types": ["optionresponse"],
            "content": {"display_name": name, "capa_content": capa_content.replace("\n", " ").strip()},
        }

    def test_indexing_multiple_choice_with_hints_and_feedback(self):
        """Verify indexing of multiple-choice problems containing hints and feedback."""
        name = "Multiple Choice with Hints and Feedback"
        block = self._create_block(self.sample_multichoice_with_hints_and_feedback_problem_xml, name=name)
        capa_content = textwrap.dedent(
            """
            You can provide feedback for each option in a multiple choice problem.
            You can also add hints for learners.
            Be sure to select Settings to specify a Display Name and other values that apply.
            Use the following example problem as a model.
            Which of the following is a vegetable?
            apple
            pumpkin
            potato
            tomato
        """
        )
        assert block.problem_types == {"multiplechoiceresponse"}
        assert block.index_dictionary() == {
            "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
            "problem_types": ["multiplechoiceresponse"],
            "content": {"display_name": name, "capa_content": capa_content.replace("\n", " ").strip()},
        }

    def test_indexing_numerical_input_with_hints_and_feedback(self):
        """Verify indexing of numerical input problems containing hints and feedback."""
        name = "Numerical Input with Hints and Feedback"
        block = self._create_block(self.sample_numerical_input_with_hints_and_feedback_problem_xml, name=name)
        capa_content = textwrap.dedent(
            """
            You can provide feedback for correct answers in numerical input problems. You cannot provide feedback
            for incorrect answers.
            Use feedback for the correct answer to reinforce the process for arriving at the numerical value.
            You can also add hints for learners.
            Be sure to select Settings to specify a Display Name and other values that apply.
            Use the following example problem as a model.
            What is the arithmetic mean for the following set of numbers? (1, 5, 6, 3, 5)
        """
        )
        assert block.problem_types == {"numericalresponse"}
        assert block.index_dictionary() == {
            "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
            "problem_types": ["numericalresponse"],
            "content": {"display_name": name, "capa_content": capa_content.replace("\n", " ").strip()},
        }

    def test_indexing_text_input_with_hints_and_feedback(self):
        """Verify indexing of text input problems containing hints and feedback."""
        name = "Text Input with Hints and Feedback"
        block = self._create_block(self.sample_text_input_with_hints_and_feedback_problem_xml, name=name)
        capa_content = textwrap.dedent(
            """
            You can provide feedback for the correct answer in text input problems, as well as for specific
            incorrect answers.
            Use feedback on expected incorrect answers to address common misconceptions and to provide guidance on
            how to arrive at the correct answer.
            Be sure to select Settings to specify a Display Name and other values that apply.
            Use the following example problem as a model.
            Which U.S. state has the largest land area?
        """
        )
        assert block.problem_types == {"stringresponse"}
        assert block.index_dictionary() == {
            "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
            "problem_types": ["stringresponse"],
            "content": {"display_name": name, "capa_content": capa_content.replace("\n", " ").strip()},
        }

    def test_indexing_problem_with_html_tags(self):
        """Ensure HTML tags, comments, scripts, and styles are safely ignored during indexing."""
        sample_problem_xml = textwrap.dedent(
            """
            <problem>
                <style>p {left: 10px;}</style>
                <!-- Beginning of the html -->
                <p>This has HTML comment in it.<!-- Commenting Content --></p>
                <!-- Here comes CDATA -->
                <![CDATA[This is just a CDATA!]]>
                <p>HTML end.</p>
                <!-- Script that makes everything alive! -->
                <script>
                    var alive;
                </script>
            </problem>
        """
        )
        name = "Mixed business"
        block = self._create_block(sample_problem_xml, name=name)
        capa_content = "This has HTML comment in it. HTML end."
        assert block.index_dictionary() == {
            "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
            "problem_types": [],
            "content": {"display_name": name, "capa_content": capa_content},
        }

    def test_indexing_problem_with_no_whitespace_between_tags(self):
        """
        The new (MFE) visual editor for capa problems renders the OLX without spaces between the tags.
        We want to make sure the index description is still readable and has whitespace.
        """
        sample_problem_xml = (
            '<problem display_name="No spaces">'
            "<choiceresponse><div>Question text here.</div><checkboxgroup>"
            '<choice correct="true"><div>Option A</div></choice>'
            '<choice correct="false"><div>Option B</div></choice>'
            "</checkboxgroup></choiceresponse>"
            "</problem>"
        )
        name = "No spaces"
        block = self._create_block(sample_problem_xml, name=name)
        capa_content = "Question text here. Option A Option B"
        assert block.index_dictionary() == {
            "content_type": ProblemBlock.INDEX_CONTENT_TYPE,
            "problem_types": ["choiceresponse"],
            "content": {"display_name": name, "capa_content": capa_content},
        }

    def test_invalid_xml_handling(self):
        """
        Tests to confirm that invalid XML throws errors during xblock creation,
        so as not to allow bad data into modulestore.
        """
        sample_invalid_xml = textwrap.dedent(
            """
            <problem>
            </proble-oh no my finger broke and I can't close the problem tag properly...
        """
        )
        with pytest.raises(etree.XMLSyntaxError):
            self._create_block(sample_invalid_xml, name="Invalid XML")

    def test_invalid_dropdown_xml(self):
        """
        Verify the capa problem cannot be created from dropdown xml with multiple correct answers.
        """
        problem_xml = textwrap.dedent(
            """
        <problem>
            <optionresponse>
              <p>You can use this template as a guide to the simple editor markdown and OLX markup to use for dropdown
               problems. Edit this component to replace this template with your own assessment.</p>
            <label>Add the question text, or prompt, here. This text is required.</label>
            <description>You can add an optional tip or note related to the prompt like this. </description>
            <optioninput>
                <option correct="False">an incorrect answer</option>
                <option correct="True">the correct answer</option>
                <option correct="True">an incorrect answer</option>
              </optioninput>
            </optionresponse>
        </problem>
        """
        )
        with pytest.raises(Exception):
            CapaFactory.create(xml=problem_xml)


class ComplexEncoderTest(unittest.TestCase):
    """Tests JSON encoding of complex numbers."""

    def test_default(self):
        """
        Check that complex numbers can be encoded into JSON.
        """
        complex_num = 1 - 1j
        expected_str = "1-1*j"
        json_str = json.dumps(complex_num, cls=ComplexEncoder)
        assert expected_str == json_str[1:(-1)]
        # ignore quotes


@skip_unless_lms
@UseUnsafeCodejail()
@pytest.mark.django_db
class ProblemCheckTrackingTest(unittest.TestCase):
    """
    Ensure correct tracking information is included in events emitted during problem checks.
    """

    def setUp(self):
        super().setUp()
        self.maxDiff = None  # pylint: disable=invalid-name

    def test_choice_answer_text(self):
        """Verify tracked submission data for multiple choice, option, and checkbox responses."""
        xml = """\
            <problem display_name="Multiple Choice Questions">
              <optionresponse>
                <label>What color is the open ocean on a sunny day?</label>
                <optioninput options="('yellow','blue','green')" correct="blue"/>
              </optionresponse>

              <multiplechoiceresponse>
                <label>Which piece of furniture is built for sitting?</label>
                <choicegroup type="MultipleChoice">
                  <choice correct="false"><text>a table</text></choice>
                  <choice correct="false"><text>a desk</text></choice>
                  <choice correct="true"><text>a chair</text></choice>
                  <choice correct="false"><text>a bookshelf</text></choice>
                </choicegroup>
              </multiplechoiceresponse>

              <choiceresponse>
                <label>Which of the following are musical instruments?</label>
                <checkboxgroup>
                  <choice correct="true">a piano</choice>
                  <choice correct="false">a tree</choice>
                  <choice correct="true">a guitar</choice>
                  <choice correct="false">a window</choice>
                </checkboxgroup>
              </choiceresponse>
            </problem>
            """

        # Whitespace screws up comparisons
        xml = "".join(line.strip() for line in xml.split("\n"))
        factory = self.capa_factory_for_problem_xml(xml)
        block = factory.create()

        answer_input_dict = {
            factory.input_key(2): "blue",
            factory.input_key(3): "choice_0",
            factory.input_key(4): ["choice_0", "choice_1"],
        }
        event = self.get_event_for_answers(block, answer_input_dict)

        assert event["submission"] == {
            factory.answer_key(2): {
                "question": "What color is the open ocean on a sunny day?",
                "answer": "blue",
                "response_type": "optionresponse",
                "input_type": "optioninput",
                "correct": True,
                "group_label": "",
                "variant": "",
            },
            factory.answer_key(3): {
                "question": "Which piece of furniture is built for sitting?",
                "answer": "<text>a table</text>",
                "response_type": "multiplechoiceresponse",
                "input_type": "choicegroup",
                "correct": False,
                "group_label": "",
                "variant": "",
            },
            factory.answer_key(4): {
                "question": "Which of the following are musical instruments?",
                "answer": ["a piano", "a tree"],
                "response_type": "choiceresponse",
                "input_type": "checkboxgroup",
                "correct": False,
                "group_label": "",
                "variant": "",
            },
        }

    def capa_factory_for_problem_xml(self, xml):
        """Create a custom CapaFactory for a given problem XML string."""

        class CustomCapaFactory(CapaFactory):
            """
            A factory for creating a Capa problem with arbitrary xml.
            """

            sample_problem_xml = textwrap.dedent(xml)

        return CustomCapaFactory

    def get_event_for_answers(self, block, answer_input_dict):
        """Submit answers and return the emitted tracking event payload."""
        with patch.object(block.runtime, "publish") as mock_publish:
            block.submit_problem(answer_input_dict)

            assert len(mock_publish.mock_calls) >= 2
            # There are potentially 2 track logs: answers and hint. [-1]=answers.
            mock_call = mock_publish.mock_calls[-1]
            event = mock_call[1][2]

            return event

    def test_numerical_textline(self):
        """Verify tracking data for numerical textline responses."""
        factory = CapaFactory
        block = factory.create()

        answer_input_dict = {factory.input_key(2): "3.14"}

        event = self.get_event_for_answers(block, answer_input_dict)
        assert event["submission"] == {
            factory.answer_key(2): {
                "question": "",
                "answer": "3.14",
                "response_type": "numericalresponse",
                "input_type": "textline",
                "correct": True,
                "group_label": "",
                "variant": "",
            }
        }

    def test_multiple_inputs(self):
        """Verify tracking data for multiple inputs within a single response group."""
        group_label = "Choose the correct color"
        input1_label = "What color is the sky?"
        input2_label = "What color are pine needles?"
        factory = self.capa_factory_for_problem_xml(
            f"""\
            <problem display_name="Multiple Inputs">
            <optionresponse>
                <label>{group_label}</label>
                <optioninput options="('yellow','blue','green')" correct="blue" label="{input1_label}"/>
                <optioninput options="('yellow','blue','green')" correct="green" label="{input2_label}"/>
            </optionresponse>
            </problem>
            """
        )
        block = factory.create()
        answer_input_dict = {
            factory.input_key(2, 1): "blue",
            factory.input_key(2, 2): "yellow",
        }

        event = self.get_event_for_answers(block, answer_input_dict)
        assert event["submission"] == {
            factory.answer_key(2, 1): {
                "group_label": group_label,
                "question": input1_label,
                "answer": "blue",
                "response_type": "optionresponse",
                "input_type": "optioninput",
                "correct": True,
                "variant": "",
            },
            factory.answer_key(2, 2): {
                "group_label": group_label,
                "question": input2_label,
                "answer": "yellow",
                "response_type": "optionresponse",
                "input_type": "optioninput",
                "correct": False,
                "variant": "",
            },
        }

    def test_optioninput_extended_xml(self):
        """Test the new XML form of writing with <option> tag instead of options= attribute."""
        group_label = "Are you the Gatekeeper?"
        input1_label = "input 1 label"
        input2_label = "input 2 label"
        factory = self.capa_factory_for_problem_xml(
            f"""\
            <problem display_name="Woo Hoo">
                <optionresponse>
                   <label>{group_label}</label>
                   <optioninput label="{input1_label}">
                       <option correct="True" label="Good Job">
                           apple
                           <optionhint>
                               banana
                           </optionhint>
                       </option>
                       <option correct="False" label="blorp">
                           cucumber
                           <optionhint>
                               donut
                           </optionhint>
                       </option>
                   </optioninput>

                   <optioninput label="{input2_label}">
                       <option correct="True">
                           apple
                           <optionhint>
                               banana
                           </optionhint>
                       </option>
                       <option correct="False">
                           cucumber
                           <optionhint>
                               donut
                           </optionhint>
                       </option>
                   </optioninput>
                 </optionresponse>
            </problem>
            """
        )
        block = factory.create()

        answer_input_dict = {
            factory.input_key(2, 1): "apple",
            factory.input_key(2, 2): "cucumber",
        }

        event = self.get_event_for_answers(block, answer_input_dict)
        assert event["submission"] == {
            factory.answer_key(2, 1): {
                "group_label": group_label,
                "question": input1_label,
                "answer": "apple",
                "response_type": "optionresponse",
                "input_type": "optioninput",
                "correct": True,
                "variant": "",
            },
            factory.answer_key(2, 2): {
                "group_label": group_label,
                "question": input2_label,
                "answer": "cucumber",
                "response_type": "optionresponse",
                "input_type": "optioninput",
                "correct": False,
                "variant": "",
            },
        }

    def test_rerandomized_inputs(self):
        """Ensure variant seed is included in tracking data for rerandomized problems."""
        factory = CapaFactory
        block = factory.create(rerandomize=RANDOMIZATION.ALWAYS)

        answer_input_dict = {factory.input_key(2): "3.14"}

        event = self.get_event_for_answers(block, answer_input_dict)
        assert event["submission"] == {
            factory.answer_key(2): {
                "question": "",
                "answer": "3.14",
                "response_type": "numericalresponse",
                "input_type": "textline",
                "correct": True,
                "group_label": "",
                "variant": block.seed,
            }
        }

    @pytest.mark.django_db
    @patch.object(XQueueInterface, "_http_post")
    def test_file_inputs(self, mock_xqueue_post):
        """Verify tracking data for file submission and custom response inputs."""
        fnames = ["prog1.py", "prog2.py", "prog3.py"]
        fpaths = [os.path.join(DATA_DIR, "capa", fname) for fname in fnames]
        fileobjs = []
        for fpath in fpaths:
            with open(fpath, encoding="utf-8") as f:
                fileobjs.append(f.read())

        factory = CapaFactoryWithFiles
        block = factory.create()

        # Mock the XQueueInterface post method
        mock_xqueue_post.return_value = (0, "ok")

        answer_input_dict = {
            CapaFactoryWithFiles.input_key(response_num=2): fileobjs,
            CapaFactoryWithFiles.input_key(response_num=3): "None",
        }

        event = self.get_event_for_answers(block, answer_input_dict)
        assert event["submission"] == {
            factory.answer_key(2): {
                "question": "",
                "answer": fileobjs,
                "response_type": "coderesponse",
                "input_type": "filesubmission",
                "correct": False,
                "group_label": "",
                "variant": "",
            },
            factory.answer_key(3): {
                "answer": "None",
                "correct": True,
                "group_label": "",
                "question": "",
                "response_type": "customresponse",
                "input_type": "textline",
                "variant": "",
            },
        }

    def test_get_answer_with_jump_to_id_urls(self):
        """
        Make sure replace_jump_to_id_urls() is called in get_answer.
        """
        problem_xml = textwrap.dedent(
            """
        <problem>
            <p>What is 1+4?</p>
                <numericalresponse answer="5">
                  <formulaequationinput />
                </numericalresponse>

                <solution>
                <div class="detailed-solution">
                <p>Explanation</p>
                <a href="/jump_to_id/c0f8d54964bc44a4a1deb8ecce561ecd">here's the same link to the hint page.</a>
                </div>
                </solution>
        </problem>
        """
        )

        data = {}
        problem = CapaFactory.create(showanswer="always", xml=problem_xml)
        problem.runtime.service(problem, "replace_urls").replace_urls = Mock()

        problem.get_answer(data)
        assert problem.runtime.service(problem, "replace_urls").replace_urls.called


class ProblemBlockReportGenerationTest(unittest.TestCase):
    """
    Ensure that Capa report generation works correctly
    """

    def setUp(self):
        self.find_question_label_patcher = patch(
            "xmodule.capa.capa_problem.LoncapaProblem.find_question_label", lambda self, answer_id: answer_id
        )
        self.find_answer_text_patcher = patch(
            "xmodule.capa.capa_problem.LoncapaProblem.find_answer_text",
            lambda self, answer_id, current_answer: current_answer,
        )
        self.find_question_label_patcher.start()
        self.find_answer_text_patcher.start()
        self.addCleanup(self.find_question_label_patcher.stop)
        self.addCleanup(self.find_answer_text_patcher.stop)

    def _mock_user_state_generator(self, user_count=1, response_count=10):
        for uid in range(user_count):
            yield self._user_state(username=f"user{uid}", response_count=response_count)

    def _user_state(self, username="testuser", response_count=10, suffix=""):
        return XBlockUserState(
            username=username,
            state={
                "student_answers": {
                    f"{username}_answerid_{aid}{suffix}": f"{username}_answer_{aid}" for aid in range(response_count)
                },
                "seed": 1,
                "correct_map": {},
            },
            block_key=None,
            updated=None,
            scope=None,
        )

    def _get_block(self):
        """Create and return a mock ProblemBlock with default test data."""
        scope_ids = Mock(block_type="problem")
        block = ProblemBlock(get_test_system(), scope_ids=scope_ids)
        block.runtime = Mock()
        block.data = "<problem/>"
        return block

    def test_generate_report_data_not_implemented(self):
        """Verify report generation is not supported for non-problem blocks."""
        scope_ids = Mock(block_type="noproblem")
        block = ProblemBlock(get_test_system(), scope_ids=scope_ids)
        with pytest.raises(NotImplementedError):
            next(block.generate_report_data(iter([])))

    def test_generate_report_data_limit_responses(self):
        """Ensure report generation respects the response limit."""
        block = self._get_block()
        report_data = list(block.generate_report_data(self._mock_user_state_generator(), 2))
        assert 2 == len(report_data)

    def test_generate_report_data_dont_limit_responses(self):
        """Verify all responses are included when no limit is provided."""
        block = self._get_block()
        user_count = 5
        response_count = 10
        report_data = list(
            block.generate_report_data(
                self._mock_user_state_generator(
                    user_count=user_count,
                    response_count=response_count,
                )
            )
        )
        assert (user_count * response_count) == len(report_data)

    def test_generate_report_data_skip_dynamath(self):
        """Ensure Dynamath responses are excluded from reports."""
        block = self._get_block()
        iterator = iter([self._user_state(suffix="_dynamath")])
        report_data = list(block.generate_report_data(iterator))
        assert 0 == len(report_data)

    def test_generate_report_data_report_loncapa_error(self):
        """Verify LonCapa errors are captured and reported instead of aborting."""
        # Test to make sure reports continue despite loncappa errors, and write them into the report.
        block = self._get_block()
        with patch("xmodule.capa_block.LoncapaProblem") as mock_loncapa_problem:
            mock_loncapa_problem.side_effect = LoncapaProblemError
            report_data = list(
                block.generate_report_data(
                    self._mock_user_state_generator(
                        user_count=1,
                        response_count=5,
                    )
                )
            )
            assert "Python Error: No Answer Retrieved" in list(report_data[0][1].values())
