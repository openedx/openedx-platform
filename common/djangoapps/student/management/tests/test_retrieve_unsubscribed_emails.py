"""Tests for retrieve unsubscribed emails management command"""

from datetime import datetime, timedelta
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock, call, patch

import six
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings


class RetrieveUnsubscribedEmailsTests(TestCase):
    """
    Tests for the retrieve_unsubscribed_emails command.
    """

    def setUp(self):
        super().setUp()

        self.start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        self.end_date = datetime.now().strftime('%Y-%m-%d')

    @staticmethod
    def _write_test_csv(csv, lines):
        """
        Write a test csv file with the lines provided
        """
        csv.write(b"email,unsubscribed_at\n")
        for line in lines:
            csv.write(six.b(line))
        csv.seek(0)
        return csv

    @override_settings(
        BRAZE_UNSUBSCRIBED_EMAILS_FROM_EMAIL='test@example.com',
        BRAZE_UNSUBSCRIBED_EMAILS_RECIPIENT_EMAIL=['test@example.com']
    )
    @patch('common.djangoapps.student.management.commands.retrieve_unsubscribed_emails.EmailMultiAlternatives.send')
    @patch('common.djangoapps.student.management.commands.retrieve_unsubscribed_emails.get_email_client')
    @patch('common.djangoapps.student.management.commands.retrieve_unsubscribed_emails.logger.info')
    def test_retrieve_unsubscribed_emails_command(self, mock_logger_info, mock_get_email_client, mock_send):
        """
        Test the retrieve_unsubscribed_emails command
        """
        mock_email_client = mock_get_email_client.return_value
        mock_email_client.retrieve_unsubscribed_emails.return_value = [
            {'email': 'test1@example.com', 'unsubscribed_at': '2023-06-01 10:00:00'},
            {'email': 'test2@example.com', 'unsubscribed_at': '2023-06-02 12:00:00'},
        ]
        mock_send.return_value = MagicMock()

        call_command('retrieve_unsubscribed_emails')

        mock_logger_info.assert_has_calls([
            call(f'Retrieving unsubscribed emails from {self.start_date} to {self.end_date}'),
            call('Email addresses for users that unsubscribed from emails between '
                 f'{self.start_date} - {self.end_date} retrieved successfully from Braze'),
            call('Write unsubscribed emails data into CSV file successfully.'),
            call(f'Unsubscribed emails data sent successfully to {settings.BRAZE_UNSUBSCRIBED_EMAILS_RECIPIENT_EMAIL}')
        ])
        mock_send.assert_called_once()

        with NamedTemporaryFile() as csv:
            filepath = csv.name
            lines = [
                'test1@example.com,2023-06-01 10:00:00',
                'test2@example.com,2023-06-02 12:00:00'
            ]
            self._write_test_csv(csv, lines)

            with open(filepath, 'r') as csv_file:
                csv_data = csv_file.read()
                self.assertIn('test1@example.com,2023-06-01 10:00:00', csv_data)
                self.assertIn('test2@example.com,2023-06-02 12:00:00', csv_data)

    @override_settings(
        BRAZE_UNSUBSCRIBED_EMAILS_FROM_EMAIL='test@example.com',
        BRAZE_UNSUBSCRIBED_EMAILS_RECIPIENT_EMAIL=['test@example.com']
    )
    @patch('common.djangoapps.student.management.commands.retrieve_unsubscribed_emails.EmailMultiAlternatives.send')
    @patch('common.djangoapps.student.management.commands.retrieve_unsubscribed_emails.get_email_client')
    @patch('common.djangoapps.student.management.commands.retrieve_unsubscribed_emails.logger.info')
    def test_retrieve_unsubscribed_emails_command_with_dates(self, mock_logger_info, mock_get_email_client, mock_send):
        """
        Test the retrieve_unsubscribed_emails command with custom start and end dates.
        """
        mock_email_client = mock_get_email_client.return_value
        mock_email_client.retrieve_unsubscribed_emails.return_value = [
            {'email': 'test3@example.com', 'unsubscribed_at': '2023-06-03 08:00:00'},
            {'email': 'test4@example.com', 'unsubscribed_at': '2023-06-04 14:00:00'},
        ]
        mock_send.return_value = MagicMock()

        call_command(
            'retrieve_unsubscribed_emails',
            '--start_date', self.start_date,
            '--end_date', self.end_date,
        )

        mock_logger_info.assert_has_calls([
            call(f'Retrieving unsubscribed emails from {self.start_date} to {self.end_date}'),
            call('Email addresses for users that unsubscribed from emails between '
                 f'{self.start_date} - {self.end_date} retrieved successfully from Braze'),
            call('Write unsubscribed emails data into CSV file successfully.'),
            call(f'Unsubscribed emails data sent successfully to {settings.BRAZE_UNSUBSCRIBED_EMAILS_RECIPIENT_EMAIL}')
        ])
        mock_send.assert_called_once()

        with NamedTemporaryFile() as csv:
            filepath = csv.name
            lines = [
                'test3@example.com,2023-06-03 08:00:00',
                'test4@example.com,2023-06-04 14:00:00'
            ]
            self._write_test_csv(csv, lines)

            with open(filepath, 'r') as csv_file:
                csv_data = csv_file.read()
                self.assertIn('test3@example.com,2023-06-03 08:00:00', csv_data)
                self.assertIn('test4@example.com,2023-06-04 14:00:00', csv_data)

    @patch('common.djangoapps.student.management.commands.retrieve_unsubscribed_emails.EmailMultiAlternatives.send')
    @patch('common.djangoapps.student.management.commands.retrieve_unsubscribed_emails.get_email_client')
    @patch('common.djangoapps.student.management.commands.retrieve_unsubscribed_emails.logger.exception')
    def test_retrieve_unsubscribed_emails_command_braze_exception(self, mock_logger_exception, mock_get_email_client,
                                                                  mock_send):
        """
        Test the retrieve_unsubscribed_emails command when an exception is raised.
        """
        mock_email_client = mock_get_email_client.return_value
        mock_email_client.retrieve_unsubscribed_emails.side_effect = Exception('Braze API error')
        mock_send.return_value = MagicMock()

        with self.assertRaises(CommandError):
            call_command('retrieve_unsubscribed_emails')

        mock_logger_exception.assert_called_once_with(
            'Unable to retrieve unsubscribed emails from Braze due to exception: Braze API error'
        )
        mock_send.assert_not_called()

    @patch('common.djangoapps.student.management.commands.retrieve_unsubscribed_emails.EmailMultiAlternatives.send')
    @patch('common.djangoapps.student.management.commands.retrieve_unsubscribed_emails.get_email_client')
    @patch('common.djangoapps.student.management.commands.retrieve_unsubscribed_emails.logger.info')
    def test_retrieve_unsubscribed_emails_command_no_data(self, mock_logger_info, mock_get_email_client, mock_send):
        """
        Test the retrieve_unsubscribed_emails command when no unsubscribed emails are returned.
        """
        mock_email_client = mock_get_email_client.return_value
        mock_email_client.retrieve_unsubscribed_emails.return_value = []
        mock_send.return_value = MagicMock()

        call_command('retrieve_unsubscribed_emails')

        mock_logger_info.assert_has_calls([
            call(f'Retrieving unsubscribed emails from {self.start_date} to {self.end_date}'),
            call(f'No unsubscribed emails found between {self.start_date} - {self.end_date}.'),
        ])
        mock_send.assert_not_called()

    @override_settings(
        BRAZE_UNSUBSCRIBED_EMAILS_FROM_EMAIL='test@example.com',
        BRAZE_UNSUBSCRIBED_EMAILS_RECIPIENT_EMAIL=['test@example.com']
    )
    @patch('common.djangoapps.student.management.commands.retrieve_unsubscribed_emails.EmailMultiAlternatives.send')
    @patch('common.djangoapps.student.management.commands.retrieve_unsubscribed_emails.get_email_client')
    @patch('common.djangoapps.student.management.commands.retrieve_unsubscribed_emails.logger.exception')
    def test_retrieve_unsubscribed_emails_command_error_sending_email(self, mock_logger_exception,
                                                                      mock_get_email_client, mock_send):
        """
        Test the retrieve_unsubscribed_emails command when an error occurs during email sending.
        """
        mock_email_client = mock_get_email_client.return_value
        mock_email_client.retrieve_unsubscribed_emails.return_value = [
            {'email': 'test1@example.com', 'unsubscribed_at': '2023-06-01 10:00:00'},
        ]
        mock_send.side_effect = Exception('Email sending error')

        with self.assertRaises(CommandError):
            call_command('retrieve_unsubscribed_emails')

        mock_logger_exception.assert_called_once_with(
            'Unable to retrieve unsubscribed emails from Braze due to exception: Email sending error'
        )
        mock_send.assert_called_once()
