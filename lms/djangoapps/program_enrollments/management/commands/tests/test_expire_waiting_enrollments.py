"""
Tests for the expire_waiting_enrollments management command.
"""
from unittest.mock import patch

import ddt
import pytest
from django.core.management import call_command
from django.test import TestCase

from lms.djangoapps.program_enrollments.management.commands import expire_waiting_enrollments


@ddt.ddt
class TestExpireWaitingEnrollments(TestCase):
    """ Test expire_waiting_enrollments command """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.command = expire_waiting_enrollments.Command()

    @ddt.data(90, None)
    @patch('lms.djangoapps.program_enrollments.tasks.expire_waiting_enrollments', autospec=True)
    def test_task_fired_with_args(self, expire_days_argument, mock_task):
        mock_task.return_value = {}
        expected_expiration = 60
        command = 'expire_waiting_enrollments'
        if expire_days_argument:
            expected_expiration = expire_days_argument
            call_command(command, expiration_days=expire_days_argument)
        else:
            call_command(command)

        mock_task.assert_called_with(expected_expiration)

    @patch('lms.djangoapps.program_enrollments.tasks.expire_waiting_enrollments', autospec=True)
    def test_task_failure_fails_command(self, mock_task):
        mock_task.side_effect = Exception('BOOM!')
        with pytest.raises(Exception):
            call_command('expire_waiting_enrollments')
