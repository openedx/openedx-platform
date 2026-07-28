#! /usr/bin/env python3
"""
Command-line script to bulk archive and cleanup retired learners from LMS
"""

import datetime
import gzip
import json
import logging
import sys
import time
from functools import partial
from os import path

import backoff
import boto3
import click
from botocore.exceptions import BotoCoreError, ClientError
from six import text_type

# Add top-level project path to sys.path before importing scripts code
sys.path.append(path.abspath(path.join(path.dirname(__file__), '../..')))

# pylint: disable=wrong-import-position
from scripts.user_retirement.utils.helpers import _config_or_exit, _fail, _fail_exception, _log, _setup_lms_api_or_exit

SCRIPT_SHORTNAME = 'Archive and Cleanup'

# Return codes for various fail cases
ERR_NO_CONFIG = -1
ERR_BAD_CONFIG = -2
ERR_FETCHING = -3
ERR_ARCHIVING = -4
ERR_DELETING = -5
ERR_SETUP_FAILED = -5
ERR_BAD_CLI_PARAM = -6

LOG = partial(_log, SCRIPT_SHORTNAME)
FAIL = partial(_fail, SCRIPT_SHORTNAME)
FAIL_EXCEPTION = partial(_fail_exception, SCRIPT_SHORTNAME)
CONFIG_OR_EXIT = partial(_config_or_exit, FAIL_EXCEPTION, ERR_BAD_CONFIG)
SETUP_LMS_OR_EXIT = partial(_setup_lms_api_or_exit, FAIL, ERR_SETUP_FAILED)

DELAY = 10

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
logging.getLogger('boto').setLevel(logging.INFO)


def _fetch_learners_to_archive_or_exit(config, start_date, end_date, initial_state):
    """
    Makes the call to fetch learners to be cleaned up, returns the list of learners or exits.
    """
    LOG('Fetching users in state {} created from {} to {}'.format(initial_state, start_date, end_date))  # noqa: UP032
    try:
        learners = config['LMS'].get_learners_by_date_and_status(initial_state, start_date, end_date)
        LOG('Successfully fetched {} learners'.format(str(len(learners))))  # noqa: UP032
        return learners
    except Exception as exc:  # pylint: disable=broad-except
        FAIL_EXCEPTION(ERR_FETCHING, 'Unexpected error occurred fetching users to update!', exc)


def _batch_learners(learners=None, batch_size=None):
    """
    To avoid potentially overwheling the LMS with a large number of user retirements to
    delete, create a list of smaller batches of users to iterate over. This has the
    added benefit of reducing the amount of user retirement archive requests that can
    get into a bad state should this script experience an error.

    Args:
        learners (list): List of learners to portion into smaller batches (lists)
        batch_size (int): The number of learners to portion into each batch. If this
            parameter is not supplied, this function will return one batch containing
            all of the learners supplied to it.
    """
    if batch_size:
        return [
            learners[i:i + batch_size] for i, _ in list(enumerate(learners))[::batch_size]
        ]
    else:
        return [learners]


def _on_s3_backoff(details):
    """
    Callback that is called when backoff... backs off
    """
    LOG("Backing off {wait:0.1f} seconds after {tries} tries calling function {target}".format(**details))


@backoff.on_exception(
    backoff.expo,
    (
        ClientError,
        BotoCoreError
    ),
    on_backoff=lambda details: _on_s3_backoff(details),  # pylint: disable=unnecessary-lambda,
    max_time=120,  # 2 minutes
)
def _upload_to_s3(config, filename, dry_run=False):
    """
    Upload the archive file to S3
    """
    try:
        datestr = datetime.datetime.now().strftime('%Y/%m/')
        s3 = boto3.resource('s3')
        bucket_name = config['s3_archive']['bucket_name']
        # Dry runs of this script should only generate the retirement archive file, not push it to s3.
        bucket = s3.Bucket(bucket_name)
        key = 'raw/' + datestr + filename
        if dry_run:
            LOG('Dry run. Skipping the step to upload data to {}'.format(key))  # noqa: UP032
            return
        else:
            bucket.upload_file(filename, key)
            LOG('Successfully uploaded retirement data to {}'.format(key))  # noqa: UP032
    except Exception as exc:
        LOG(text_type(exc))
        raise


def _format_datetime_for_athena(timestamp):
    """
    Takes a JSON serialized timestamp string and returns a format of it that is queryable as a datetime in Athena
    """
    return timestamp.replace('T', ' ').rstrip('Z')


def _archive_retirements_or_exit(config, learners, dry_run=False):
    """
    Creates an archive file with all of the retirements and uploads it to S3

    The format of learners from LMS should be a list of these:
    {
    'id': 46, # This is the UserRetirementStatus ID!
    'user':
        {
        'id': 5213599,  # THIS is the LMS User ID
        'username': 'retired__user_88ad587896920805c26041a2e75c767c75471ee9',
        'email': 'retired__user_d08919da55a0e03c032425567e4a33e860488a96@retired.invalid',
        'profile':
            {
            'id': 2842382,
            'name': ''
            }
        },
    'current_state':
    {
        'id': 41,
        'state_name': 'COMPLETE',
        'state_execution_order': 13
    },
    'last_state': {
        'id': 1,
        'state_name': 'PENDING',
        'state_execution_order': 1
    },
    'created': '2018-10-18T20:35:52.349757Z',  # This is the UserRetirementStatus creation date
    'modified': '2018-10-18T20:35:52.350050Z',  # This is the UserRetirementStatus last touched date
    'original_username': 'retirement_test',
    'original_email': 'orig@foo.invalid',
    'original_name': 'Retirement Test',
    'retired_username': 'retired__user_88ad587896920805c26041a2e75c767c75471ee9',
    'retired_email': 'retired__user_d08919da55a0e03c032425567e4a33e860488a96@retired.invalid'
    }
    """
    LOG('Archiving retirements for {} learners to {}'.format(len(learners), config['s3_archive']['bucket_name']))
    try:
        now = _get_utc_now()
        filename = 'retirement_archive_{}.json.gz'.format(now.strftime('%Y_%d_%m_%H_%M_%S'))
        LOG('Creating retirement archive file {}'.format(filename))  # noqa: UP032

        # The file format is one JSON object per line with the newline as a separator. This allows for
        # easy queries via AWS Athena if we need to confirm learner deletion.
        with gzip.open(filename, 'wt') as out:
            for learner in learners:
                user = {
                    'user_id': learner['user']['id'],
                    'original_username': learner['original_username'],
                    'original_email': learner['original_email'],
                    'original_name': learner['original_name'],
                    'retired_username': learner['retired_username'],
                    'retired_email': learner['retired_email'],
                    'retirement_request_date': _format_datetime_for_athena(learner['created']),
                    'last_modified_date': _format_datetime_for_athena(learner['modified']),
                }
                json.dump(user, out)
                out.write("\n")
        if dry_run:
            LOG('Dry run. Logging the contents of {} for debugging'.format(filename))  # noqa: UP032
            with gzip.open(filename, 'r') as archive_file:
                for line in archive_file.readlines():
                    LOG(line)
        _upload_to_s3(config, filename, dry_run)
    except Exception as exc:  # pylint: disable=broad-except
        FAIL_EXCEPTION(ERR_ARCHIVING, 'Unexpected error occurred archiving retirements!', exc)


def _cleanup_retirements_or_exit(config, learners, redacted_username='redacted',
                                 redacted_email='redacted', redacted_name='redacted'):
    """
    Bulk deletes the retirements for this run after redacting PII fields
    """
    LOG('Cleaning up retirements for {} learners'.format(len(learners)))  # noqa: UP032
    try:
        usernames = [l['original_username'] for l in learners]
        config['LMS'].bulk_cleanup_retirements(
            usernames, redacted_username, redacted_email, redacted_name
        )
    except Exception as exc:  # pylint: disable=broad-except
        FAIL_EXCEPTION(ERR_DELETING, 'Unexpected error occurred redacting/deleting retirements!', exc)


def _get_utc_now():
    """
    Helper function only used to make unit test mocking/patching easier.
    """
    return datetime.datetime.utcnow()


@click.command("archive_and_cleanup")
@click.option(
    '--config_file',
    help='YAML file that contains retirement-related configuration for this environment.'
)
@click.option(
    '--cool_off_days',
    help='Number of days a retirement should exist before being archived and deleted.',
    type=int,
    default=37  # 7 days before retirement, 30 after
)
@click.option(
    '--dry_run',
    help='''
        Should this script be run in a dry-run mode, in which generated retirement
        archive files are not pushed to s3 and retirements are not cleaned up in the LMS
    ''',
    type=bool,
    default=False
)
@click.option(
    '--start_date',
    help='''
        Start of window used to select user retirements for archival. Only user retirements
        added to the retirement queue after this date will be processed.
    ''',
    type=click.DateTime(formats=['%Y-%m-%d'])
)
@click.option(
    '--end_date',
    help='''
        End of window used to select user retirments for archival. Only user retirments
        added to the retirement queue before this date will be processed. In the case that
        this date is more recent than the value specified in the `cool_off_days` parameter,
        an error will be thrown. If this parameter is not used, the script will default to
        using an end_date based upon the `cool_off_days` parameter.
    ''',
    type=click.DateTime(formats=['%Y-%m-%d'])
)
@click.option(
    '--batch_size',
    help='Number of user retirements to process',
    type=int
)
@click.option(
    '--redacted_username',
    help='Value to use for redacted username field',
    type=str,
    default='redacted'
)
@click.option(
    '--redacted_email',
    help='Value to use for redacted email field',
    type=str,
    default='redacted'
)
@click.option(
    '--redacted_name',
    help='Value to use for redacted name field',
    type=str,
    default='redacted'
)
def archive_and_cleanup(config_file, cool_off_days, dry_run, start_date, end_date, batch_size,
                        redacted_username, redacted_email, redacted_name):
    """
    Cleans up UserRetirementStatus rows in LMS by:
    1- Getting all rows currently in COMPLETE that were created --cool_off_days ago or more,
        unless a specific timeframe is specified
    2- Archiving them to S3 in an Athena-queryable format
    3- Deleting them from LMS (by username)
    """
    try:
        LOG('Starting bulk update script: Config: {}'.format(config_file))  # noqa: UP032

        if not config_file:
            FAIL(ERR_NO_CONFIG, 'No config file passed in.')

        config = CONFIG_OR_EXIT(config_file)
        SETUP_LMS_OR_EXIT(config)

        if not start_date:
            # This date is just a bogus "earliest possible value" since the call requires one
            start_date = datetime.datetime.strptime('2018-01-01', '%Y-%m-%d')
        if end_date:
            if end_date > _get_utc_now() - datetime.timedelta(days=cool_off_days):
                FAIL(ERR_BAD_CLI_PARAM, 'End date cannot occur within the cool_off_days period')
        else:
            # Set an end_date of `cool_off_days` days before the time that this script is run
            end_date = _get_utc_now() - datetime.timedelta(days=cool_off_days)

        if start_date >= end_date:
            FAIL(ERR_BAD_CLI_PARAM, 'Conflicting start and end dates passed on CLI')

        LOG(
            'Fetching retirements for learners that have a COMPLETE status and were created '  # noqa: UP032
            'between {} and {}.'.format(
                start_date, end_date
            )
        )
        learners = _fetch_learners_to_archive_or_exit(
            config, start_date, end_date, 'COMPLETE'
        )

        learners_to_process = _batch_learners(learners, batch_size)
        num_batches = len(learners_to_process)

        if learners_to_process:
            for index, batch in enumerate(learners_to_process):
                LOG(
                    'Processing batch {} out of {} of user retirement requests'.format(  # noqa: UP032
                        str(index + 1), str(num_batches)
                    )
                )
                _archive_retirements_or_exit(config, batch, dry_run)

                if dry_run:
                    LOG('This is a dry-run. Exiting before any retirements are cleaned up')
                else:
                    _cleanup_retirements_or_exit(
                        config, batch, redacted_username, redacted_email, redacted_name
                    )
                    LOG('Archive and cleanup complete for batch #{}'.format(str(index + 1)))  # noqa: UP032
                    time.sleep(DELAY)
        else:
            LOG('No learners found!')
    except Exception as exc:
        LOG(text_type(exc))
        raise


if __name__ == '__main__':
    # pylint: disable=unexpected-keyword-arg, no-value-for-parameter
    archive_and_cleanup(auto_envvar_prefix='RETIREMENT')
