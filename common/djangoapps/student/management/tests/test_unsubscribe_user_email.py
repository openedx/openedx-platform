"""Tests for unsubscribe user's email management command"""

from tempfile import NamedTemporaryFile  # noqa: I001
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

COMMAND = 'unsubscribe_user_email'


class UnsubscribeUserEmailTests(TestCase):
    """
    Tests unsubscribe_user_email command
    """

    def setUp(self):
        """
        Set up tests
        """
        super().setUp()

        self.lines = [
            f"test_user{i}@test.com" for i in range(100)
        ]
        self.invalid_csv_path = '/test/test.csv'

    @staticmethod
    def _write_test_csv(csv, lines):
        """Write a test csv file with the lines provided"""

        csv.write(b"email\n")
        for line in lines:
            csv.write(line.encode())
        csv.seek(0)
        return csv

    @patch("common.djangoapps.student.management.commands.unsubscribe_user_email.get_email_client")
    def test_unsubscribe_user_email(self, mock_get_email_client):
        """ Test CSV file to unsubscribe user's email"""

        with NamedTemporaryFile() as csv:
            csv = self._write_test_csv(csv, self.lines)

            call_command(
                COMMAND,
                '--csv_path',
                csv.name
            )

        mock_get_email_client.assert_called_once()

    def test_command_error_for_csv_path(self):
        """ Test command error raised if csv_path is not valid"""

        with pytest.raises(CommandError):
            call_command(
                COMMAND,
                '--csv_path',
                self.invalid_csv_path
            )
