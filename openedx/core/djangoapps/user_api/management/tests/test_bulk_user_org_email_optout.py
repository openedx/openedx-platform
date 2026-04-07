"""
Test the test_bulk_user_org_email_optout management command
"""


import io  # lint-amnesty, pylint: disable=unused-import
import os
import tempfile
from contextlib import contextmanager
from unittest import mock

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


CSV_DATA = """1,UniversityX
2,CollegeX
3,StateUX
"""


@contextmanager
def _create_test_csv(csv_data):
    """
    Context manager to create and populate a CSV file - and delete it after usage.
    """
    __, file_name = tempfile.mkstemp(text=True)
    with open(file_name, 'w') as file_pointer:
        file_pointer.write(csv_data)
    try:
        yield file_name
    finally:
        os.unlink(file_name)


@mock.patch('openedx.core.djangoapps.user_api.management.commands.bulk_user_org_email_optout.log.info')
def test_successful_dry_run(mock_logger):
    """
    Run the command with default states for a successful initial population
    """
    with _create_test_csv(CSV_DATA) as tmp_csv_file:
        args = ['--dry_run', f'--optout_csv_path={tmp_csv_file}']
        call_command('bulk_user_org_email_optout', *args)
        assert mock_logger.call_count == 3
        mock_logger.assert_any_call("Read %s opt-out rows from CSV file '%s'.", 3, tmp_csv_file)
        mock_logger.assert_any_call(
            'Attempting opt-out for rows (%s, %s) through (%s, %s)...', '1', 'UniversityX', '3', 'StateUX'
        )
        mock_logger.assert_any_call(
            'INSERT INTO user_api_userorgtag (`user_id`, `org`, `key`, `value`, `created`, `modified`) \
VALUES (1,"UniversityX","email-optin","False",NOW(),NOW()),(2,"CollegeX","email-optin","False",NOW(),NOW()),\
(3,"StateUX","email-optin","False",NOW(),NOW()) ON DUPLICATE KEY UPDATE value="False", modified=NOW();')
