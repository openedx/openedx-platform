"""
Tests the logic of problems with a delay between attempt submissions.

Note that this test file is based off of test_capa_block.py and as
such, uses the same CapaFactory problem setup to test the functionality
of the submit_problem method of a capa block when the "delay between quiz
submissions" setting is set to different values
"""


import datetime
import textwrap
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator
from xblock.exceptions import NotFoundError
from xblock.field_data import DictFieldData
from xblock.fields import ScopeIds
from xblock.scorable import Score

from xmodule.capa_block import ProblemBlock

from . import get_test_system


class CapaFactoryWithDelay:
    """
    Create problem blocks class, specialized for delay_between_attempts
    test cases. This factory seems different enough from the one in
    test_capa_block that unifying them is unattractive.
    Removed the unused optional arguments.
    """

    sample_problem_xml = textwrap.dedent("""\
        <?xml version="1.0"?>
        <problem>
            <text>
                <p>What is pi, to two decimal places?</p>
            </text>
        <numericalresponse answer="3.14">
        <textline math="1" size="30"/>
        </numericalresponse>
        </problem>
    """)

    num = 0

    @classmethod
    def next_num(cls):
        """
        Return the next cls number
        """
        cls.num += 1
        return cls.num

    @classmethod
    def input_key(cls, input_num=2):
        """
        Return the input key to use when passing GET parameters
        """
        return "input_" + cls.answer_key(input_num)

    @classmethod
    def answer_key(cls, input_num=2):
        """
        Return the key stored in the capa problem answer dict
        """
        return (
            "%s_%d_1" % (
                "-".join(['i4x', 'edX', 'capa_test', 'problem', 'SampleProblem%d' % cls.num]),
                input_num,
            )
        )

    @classmethod
    def create(
        cls,
        max_attempts=None,
        attempts=None,
        correct=False,
        last_submission_time=None,
        submission_wait_seconds=None
    ):
        """
        Optional parameters here are cut down to what we actually use vs. the regular CapaFactory.
        """
        location = BlockUsageLocator(CourseLocator('edX', 'capa_test', 'run', deprecated=True),
                                     'problem', f'SampleProblem{cls.next_num()}', deprecated=True)
        field_data = {'data': cls.sample_problem_xml}

        if max_attempts is not None:
            field_data['max_attempts'] = max_attempts
        if last_submission_time is not None:
            field_data['last_submission_time'] = last_submission_time
        if submission_wait_seconds is not None:
            field_data['submission_wait_seconds'] = submission_wait_seconds

        if attempts is not None:
            # converting to int here because I keep putting "0" and "1" in the tests
            # since everything else is a string.
            field_data['attempts'] = int(attempts)

        system = get_test_system(render_template=Mock(return_value="<div>Test Template HTML</div>"))
        block = ProblemBlock(
            system,
            DictFieldData(field_data),
            ScopeIds(None, None, location, location),
        )

        if correct:
            # Could set the internal state formally, but here we just jam in the score.
            block.score = Score(raw_earned=1, raw_possible=1)
        else:
            block.score = Score(raw_earned=0, raw_possible=1)

        return block


@pytest.mark.django_db
class XModuleQuizAttemptsDelayTest(unittest.TestCase):
    """
    Class to test delay between quiz attempts.
    """

    def create_and_check(self,
                         num_attempts=None,
                         last_submission_time=None,
                         submission_wait_seconds=None,
                         considered_now=None,
                         skip_submit_problem=False):
        """Unified create and check code for the tests here."""
        block = CapaFactoryWithDelay.create(
            attempts=num_attempts,
            max_attempts=99,
            last_submission_time=last_submission_time,
            submission_wait_seconds=submission_wait_seconds
        )
        block.done = False
        get_request_dict = {CapaFactoryWithDelay.input_key(): "3.14"}
        if skip_submit_problem:
            return (block, None)
        if considered_now is not None:
            result = block.submit_problem(get_request_dict, considered_now)
        else:
            result = block.submit_problem(get_request_dict)
        return (block, result)

    def test_first_submission(self):
        # Not attempted yet
        num_attempts = 0
        (block, result) = self.create_and_check(
            num_attempts=num_attempts,
            last_submission_time=None
        )
        # Successfully submitted and answered
        # Also, the number of attempts should increment by 1
        assert result['success'] == 'correct'
        assert block.attempts == (num_attempts + 1)

    def test_no_wait_time(self):
        num_attempts = 1
        (block, result) = self.create_and_check(
            num_attempts=num_attempts,
            last_submission_time=datetime.datetime.now(ZoneInfo("UTC")),
            submission_wait_seconds=0
        )
        # Successfully submitted and answered
        # Also, the number of attempts should increment by 1
        assert result['success'] == 'correct'
        assert block.attempts == (num_attempts + 1)

    def test_submit_quiz_in_rapid_succession(self):
        # Already attempted once (just now) and thus has a submitted time
        num_attempts = 1
        (block, result) = self.create_and_check(
            num_attempts=num_attempts,
            last_submission_time=datetime.datetime.now(ZoneInfo("UTC")),
            submission_wait_seconds=123
        )
        # You should get a dialog that tells you to wait
        # Also, the number of attempts should not be incremented
        self.assertRegex(result['success'], r"You must wait at least.*")
        assert block.attempts == num_attempts

    def test_submit_quiz_too_soon(self):
        # Already attempted once (just now)
        num_attempts = 1
        (block, result) = self.create_and_check(
            num_attempts=num_attempts,
            last_submission_time=datetime.datetime(2013, 12, 6, 0, 17, 36, tzinfo=ZoneInfo("UTC")),
            submission_wait_seconds=180,
            considered_now=datetime.datetime(2013, 12, 6, 0, 18, 36, tzinfo=ZoneInfo("UTC"))
        )
        # You should get a dialog that tells you to wait 2 minutes
        # Also, the number of attempts should not be incremented
        self.assertRegex(result['success'], r"You must wait at least 3 minutes between submissions. 2 minutes remaining\..*")  # lint-amnesty, pylint: disable=line-too-long
        assert block.attempts == num_attempts

    def test_submit_quiz_1_second_too_soon(self):
        # Already attempted once (just now)
        num_attempts = 1
        (block, result) = self.create_and_check(
            num_attempts=num_attempts,
            last_submission_time=datetime.datetime(2013, 12, 6, 0, 17, 36, tzinfo=ZoneInfo("UTC")),
            submission_wait_seconds=180,
            considered_now=datetime.datetime(2013, 12, 6, 0, 20, 35, tzinfo=ZoneInfo("UTC"))
        )
        # You should get a dialog that tells you to wait 2 minutes
        # Also, the number of attempts should not be incremented
        self.assertRegex(result['success'], r"You must wait at least 3 minutes between submissions. 1 second remaining\..*")  # lint-amnesty, pylint: disable=line-too-long
        assert block.attempts == num_attempts

    def test_submit_quiz_as_soon_as_allowed(self):
        # Already attempted once (just now)
        num_attempts = 1
        (block, result) = self.create_and_check(
            num_attempts=num_attempts,
            last_submission_time=datetime.datetime(2013, 12, 6, 0, 17, 36, tzinfo=ZoneInfo("UTC")),
            submission_wait_seconds=180,
            considered_now=datetime.datetime(2013, 12, 6, 0, 20, 36, tzinfo=ZoneInfo("UTC"))
        )
        # Successfully submitted and answered
        # Also, the number of attempts should increment by 1
        assert result['success'] == 'correct'
        assert block.attempts == (num_attempts + 1)

    def test_submit_quiz_after_delay_expired(self):
        # Already attempted once (just now)
        num_attempts = 1
        (block, result) = self.create_and_check(
            num_attempts=num_attempts,
            last_submission_time=datetime.datetime(2013, 12, 6, 0, 17, 36, tzinfo=ZoneInfo("UTC")),
            submission_wait_seconds=180,
            considered_now=datetime.datetime(2013, 12, 6, 0, 24, 0, tzinfo=ZoneInfo("UTC"))
        )
        # Successfully submitted and answered
        # Also, the number of attempts should increment by 1
        assert result['success'] == 'correct'
        assert block.attempts == (num_attempts + 1)

    def test_still_cannot_submit_after_max_attempts(self):
        # Already attempted once (just now) and thus has a submitted time
        num_attempts = 99
        # Regular create_and_check should fail
        with pytest.raises(NotFoundError):
            (block, unused_result) = self.create_and_check(
                num_attempts=num_attempts,
                last_submission_time=datetime.datetime(2013, 12, 6, 0, 17, 36, tzinfo=ZoneInfo("UTC")),
                submission_wait_seconds=180,
                considered_now=datetime.datetime(2013, 12, 6, 0, 24, 0, tzinfo=ZoneInfo("UTC"))
            )

        # Now try it without the submit_problem
        (block, unused_result) = self.create_and_check(
            num_attempts=num_attempts,
            last_submission_time=datetime.datetime(2013, 12, 6, 0, 17, 36, tzinfo=ZoneInfo("UTC")),
            submission_wait_seconds=180,
            considered_now=datetime.datetime(2013, 12, 6, 0, 24, 0, tzinfo=ZoneInfo("UTC")),
            skip_submit_problem=True
        )
        # Expect that number of attempts NOT incremented
        assert block.attempts == num_attempts

    def test_submit_quiz_with_long_delay(self):
        # Already attempted once (just now)
        num_attempts = 1
        (block, result) = self.create_and_check(
            num_attempts=num_attempts,
            last_submission_time=datetime.datetime(2013, 12, 6, 0, 17, 36, tzinfo=ZoneInfo("UTC")),
            submission_wait_seconds=60 * 60 * 2,
            considered_now=datetime.datetime(2013, 12, 6, 2, 15, 35, tzinfo=ZoneInfo("UTC"))
        )
        # You should get a dialog that tells you to wait 2 minutes
        # Also, the number of attempts should not be incremented
        self.assertRegex(result['success'], r"You must wait at least 2 hours between submissions. 2 minutes 1 second remaining\..*")  # lint-amnesty, pylint: disable=line-too-long
        assert block.attempts == num_attempts

    def test_submit_quiz_with_involved_pretty_print(self):
        # Already attempted once (just now)
        num_attempts = 1
        (block, result) = self.create_and_check(
            num_attempts=num_attempts,
            last_submission_time=datetime.datetime(2013, 12, 6, 0, 17, 36, tzinfo=ZoneInfo("UTC")),
            submission_wait_seconds=60 * 60 * 2 + 63,
            considered_now=datetime.datetime(2013, 12, 6, 1, 15, 40, tzinfo=ZoneInfo("UTC"))
        )
        # You should get a dialog that tells you to wait 2 minutes
        # Also, the number of attempts should not be incremented
        self.assertRegex(result['success'], r"You must wait at least 2 hours 1 minute 3 seconds between submissions. 1 hour 2 minutes 59 seconds remaining\..*")  # lint-amnesty, pylint: disable=line-too-long
        assert block.attempts == num_attempts

    def test_submit_quiz_with_nonplural_pretty_print(self):
        # Already attempted once (just now)
        num_attempts = 1
        (block, result) = self.create_and_check(
            num_attempts=num_attempts,
            last_submission_time=datetime.datetime(2013, 12, 6, 0, 17, 36, tzinfo=ZoneInfo("UTC")),
            submission_wait_seconds=60,
            considered_now=datetime.datetime(2013, 12, 6, 0, 17, 36, tzinfo=ZoneInfo("UTC"))
        )
        # You should get a dialog that tells you to wait 2 minutes
        # Also, the number of attempts should not be incremented
        self.assertRegex(result['success'], r"You must wait at least 1 minute between submissions. 1 minute remaining\..*")  # lint-amnesty, pylint: disable=line-too-long
        assert block.attempts == num_attempts
