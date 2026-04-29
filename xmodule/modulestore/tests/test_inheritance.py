"""
Unit tests for testing inheritance mixins
"""


import unittest
from unittest.mock import Mock

import ddt
from django.utils.timezone import now, timedelta
from xblock.core import XBlock
from xblock.fields import ScopeIds
from xblock.test.tools import TestRuntime

from xmodule.modulestore.inheritance import InheritanceMixin


class TestXBlock(XBlock):
    """
    An empty Xblock, to be used, when creating a block with mixins.
    """
    pass  # pylint: disable=unnecessary-pass


@ddt.ddt
class TestInheritanceMixin(unittest.TestCase):
    """
    Test Suite to verify various methods of the InheritanceMixin
    """

    def setUp(self):
        """
        Create a test xblock with mock runtime.
        """
        runtime = TestRuntime(
            Mock(entry_point=XBlock.entry_point), mixins=[InheritanceMixin], services={'field-data': {}}
        )
        self.xblock = runtime.construct_xblock_from_class(
            TestXBlock, ScopeIds('user', 'TestXBlock', 'def_id', 'usage_id')
        )
        super().setUp()

    def add_submission_deadline_information(self, due_date, graceperiod, self_paced):
        """
        Helper function to add pacing, due date and graceperiod fields to Xblock.
        """
        self.xblock.due = due_date
        self.xblock.graceperiod = graceperiod
        self.xblock.self_paced = self_paced

    @ddt.data(
        (False, now(), None, True),
        (True, now(), None, False),
        (False, now(), timedelta(days=1), False),
        (True, now(), timedelta(days=1), False),
        (False, now() - timedelta(hours=1), None, True),
    )
    @ddt.unpack
    def test_submission_deadline(self, self_paced, due_date, graceperiod, is_past_deadline):
        """
        Verifies the deadline passed boolean value w.r.t pacing and due date.

        Given the pacing information, due date and graceperiod,
        confirm if the submission deadline has passed or not.
        """
        self.add_submission_deadline_information(due_date, graceperiod, self_paced)
        assert is_past_deadline == self.xblock.has_deadline_passed()
