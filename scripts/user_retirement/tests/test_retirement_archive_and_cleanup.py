"""
Test the retirement_archive_and_cleanup.py script
"""

import datetime
import os
from unittest.mock import DEFAULT, call, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from click.testing import CliRunner
from moto import mock_aws

from scripts.user_retirement.retirement_archive_and_cleanup import (
    ERR_ARCHIVING,
    ERR_BAD_CLI_PARAM,
    ERR_BAD_CONFIG,
    ERR_DELETING,
    ERR_FETCHING,
    ERR_NO_CONFIG,
    ERR_SETUP_FAILED,
    _upload_to_s3,
    archive_and_cleanup,
)
from scripts.user_retirement.tests.retirement_helpers import fake_config_file, get_fake_user_retirement

FAKE_BUCKET_NAME = "fake_test_bucket"


def _call_script(cool_off_days=37, batch_size=None, dry_run=None, start_date=None, end_date=None):
    """
    Call the archive script with the given params and a generic config file.
    Returns the CliRunner.invoke results
    """
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open('test_config.yml', 'w') as f:
            fake_config_file(f)

        base_args = [
            '--config_file', 'test_config.yml',
            '--cool_off_days', cool_off_days,
        ]
        if batch_size:
            base_args += ['--batch_size', batch_size]
        if dry_run:
            base_args += ['--dry_run', dry_run]
        if start_date:
            base_args += ['--start_date', start_date]
        if end_date:
            base_args += ['--end_date', end_date]

        result = runner.invoke(archive_and_cleanup, args=base_args)
    print(result)
    print(result.output)
    return result


def _fake_learner(ordinal):
    """
    Creates a simple fake learner
    """
    return get_fake_user_retirement(
        user_id=ordinal,
        original_username='test{}'.format(ordinal),
        original_email='test{}@edx.invalid'.format(ordinal),
        original_name='test {}'.format(ordinal),
        retired_username='retired_{}'.format(ordinal),
        retired_email='retired_test{}@edx.invalid'.format(ordinal),
        last_state_name='COMPLETE'
    )


def fake_learners_to_retire():
    """
    A simple hard-coded list of fake learners
    """
    return [
        _fake_learner(1),
        _fake_learner(2),
        _fake_learner(3)
    ]


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token', return_value=('THIS_IS_A_JWT', None))
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    get_learners_by_date_and_status=DEFAULT,
    bulk_cleanup_retirements=DEFAULT
)
@mock_aws
def test_successful(*args, **kwargs):
    conn = boto3.resource('s3')
    conn.create_bucket(Bucket=FAKE_BUCKET_NAME)

    mock_get_access_token = args[0]
    mock_get_learners = kwargs['get_learners_by_date_and_status']
    mock_bulk_cleanup_retirements = kwargs['bulk_cleanup_retirements']

    mock_get_learners.return_value = fake_learners_to_retire()

    result = _call_script()

    # Called once to get the LMS token
    assert mock_get_access_token.call_count == 1
    mock_get_learners.assert_called_once()
    mock_bulk_cleanup_retirements.assert_called_once_with(
        ['test1', 'test2', 'test3'])

    assert result.exit_code == 0
    assert 'Archive and cleanup complete' in result.output


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token', return_value=('THIS_IS_A_JWT', None))
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    get_learners_by_date_and_status=DEFAULT,
    bulk_cleanup_retirements=DEFAULT
)
@mock_aws
def test_successful_with_batching(*args, **kwargs):
    conn = boto3.resource('s3')
    conn.create_bucket(Bucket=FAKE_BUCKET_NAME)

    mock_get_access_token = args[0]
    mock_get_learners = kwargs['get_learners_by_date_and_status']
    mock_bulk_cleanup_retirements = kwargs['bulk_cleanup_retirements']

    mock_get_learners.return_value = fake_learners_to_retire()

    result = _call_script(batch_size=2)

    # Called once to get the LMS token
    assert mock_get_access_token.call_count == 1
    mock_get_learners.assert_called_once()
    get_learner_calls = [call(['test1', 'test2']), call(['test3'])]
    mock_bulk_cleanup_retirements.assert_has_calls(get_learner_calls)

    assert result.exit_code == 0
    assert 'Archive and cleanup complete for batch #1' in result.output
    assert 'Archive and cleanup complete for batch #2' in result.output


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token', return_value=('THIS_IS_A_JWT', None))
@patch.multiple(
    'scripts.user_retirement.utils.edx_api.LmsApi',
    get_learners_by_date_and_status=DEFAULT,
    bulk_cleanup_retirements=DEFAULT
)
@mock_aws
def test_successful_dry_run(*args, **kwargs):
    mock_get_access_token = args[0]
    mock_get_learners = kwargs['get_learners_by_date_and_status']
    mock_bulk_cleanup_retirements = kwargs['bulk_cleanup_retirements']

    mock_get_learners.return_value = fake_learners_to_retire()

    result = _call_script(dry_run=True)

    # Called once to get the LMS token
    assert mock_get_access_token.call_count == 1
    mock_get_learners.assert_called_once()
    mock_bulk_cleanup_retirements.assert_not_called()

    assert result.exit_code == 0
    assert 'Dry run. Skipping the step to upload data to' in result.output
    assert 'This is a dry-run. Exiting before any retirements are cleaned up' in result.output


def test_no_config():
    runner = CliRunner()
    result = runner.invoke(
        archive_and_cleanup,
        args=[
            '--cool_off_days', 37
        ]
    )
    assert result.exit_code == ERR_NO_CONFIG
    assert 'No config file passed in.' in result.output


def test_bad_config():
    runner = CliRunner()
    result = runner.invoke(
        archive_and_cleanup,
        args=[
            '--config_file', 'does_not_exist.yml',
            '--cool_off_days', 37
        ]
    )
    assert result.exit_code == ERR_BAD_CONFIG
    assert 'does_not_exist.yml' in result.output


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token', return_value=('THIS_IS_A_JWT', None))
@patch('scripts.user_retirement.utils.edx_api.LmsApi.__init__', side_effect=Exception)
def test_setup_failed(*_):
    result = _call_script()
    assert result.exit_code == ERR_SETUP_FAILED


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token', return_value=('THIS_IS_A_JWT', None))
@patch('scripts.user_retirement.utils.edx_api.LmsApi.get_learners_by_date_and_status', side_effect=Exception)
def test_bad_fetch(*_):
    result = _call_script()
    assert result.exit_code == ERR_FETCHING
    assert 'Unexpected error occurred fetching users to update!' in result.output


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token', return_value=('THIS_IS_A_JWT', None))
@patch('scripts.user_retirement.utils.edx_api.LmsApi.get_learners_by_date_and_status',
       return_value=fake_learners_to_retire())
@patch('scripts.user_retirement.utils.edx_api.LmsApi.bulk_cleanup_retirements', side_effect=Exception)
@patch('scripts.user_retirement.retirement_archive_and_cleanup._upload_to_s3')
def test_bad_lms_deletion(*_):
    result = _call_script()
    assert result.exit_code == ERR_DELETING
    assert 'Unexpected error occurred deleting retirements!' in result.output


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token', return_value=('THIS_IS_A_JWT', None))
@patch('scripts.user_retirement.utils.edx_api.LmsApi.get_learners_by_date_and_status',
       return_value=fake_learners_to_retire())
@patch('scripts.user_retirement.utils.edx_api.LmsApi.bulk_cleanup_retirements')
@patch('scripts.user_retirement.retirement_archive_and_cleanup._upload_to_s3', side_effect=Exception)
def test_bad_s3_upload(*_):
    result = _call_script()
    assert result.exit_code == ERR_ARCHIVING
    assert 'Unexpected error occurred archiving retirements!' in result.output


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token', return_value=('THIS_IS_A_JWT', None))
def test_conflicting_dates(*_):
    result = _call_script(start_date=datetime.datetime(
        2021, 10, 10), end_date=datetime.datetime(2018, 10, 10))
    assert result.exit_code == ERR_BAD_CLI_PARAM
    assert 'Conflicting start and end dates passed on CLI' in result.output


@patch('scripts.user_retirement.utils.edx_api.BaseApiClient.get_access_token', return_value=('THIS_IS_A_JWT', None))
@patch(
    'scripts.user_retirement.retirement_archive_and_cleanup._get_utc_now',
    return_value=datetime.datetime(2021, 2, 2, 0, 0)
)
def test_conflicting_cool_off_date(*_):
    result = _call_script(
        cool_off_days=10,
        start_date=datetime.datetime(2021, 1, 1), end_date=datetime.datetime(2021, 2, 1)
    )
    assert result.exit_code == ERR_BAD_CLI_PARAM
    assert 'End date cannot occur within the cool_off_days period' in result.output


@mock_aws
def test_s3_upload_data():
    """
    Test case to verify s3 upload and download.
    """
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket=FAKE_BUCKET_NAME)
    config = {'s3_archive': {'bucket_name': FAKE_BUCKET_NAME}}
    filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_data', 'uploading.txt')
    key = 'raw/' + datetime.datetime.now().strftime('%Y/%m/') + filename

    # first try dry run without uploading. Try to get object should raise error
    with pytest.raises(ClientError) as exc_info:
        _upload_to_s3(config, filename, True)
        s3.get_object(Bucket=FAKE_BUCKET_NAME, Key=key)
        assert exc_info.value.response['Error']['Code'] == 'NoSuchKey'

    # upload a file, download and compare its content.
    _upload_to_s3(config, filename, False)
    resp = s3.get_object(Bucket=FAKE_BUCKET_NAME, Key=key)
    data = resp["Body"].read()
    assert data.decode() == "Upload this file on s3 in tests."
