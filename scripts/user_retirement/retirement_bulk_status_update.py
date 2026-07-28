#! /usr/bin/env python3
"""
Command-line script to bulk update retirement states in LMS
"""

import logging
import sys
from datetime import datetime
from functools import partial
from os import path

import click
from six import text_type

# Add top-level project path to sys.path before importing scripts code
sys.path.append(path.abspath(path.join(path.dirname(__file__), '../..')))

# pylint: disable=wrong-import-position
from scripts.user_retirement.utils.helpers import _config_or_exit, _fail, _fail_exception, _log, _setup_lms_api_or_exit

SCRIPT_SHORTNAME = 'Bulk Status'

# Return codes for various fail cases
ERR_NO_CONFIG = -1
ERR_BAD_CONFIG = -2
ERR_FETCHING = -3
ERR_UPDATING = -4
ERR_SETUP_FAILED = -5

LOG = partial(_log, SCRIPT_SHORTNAME)
FAIL = partial(_fail, SCRIPT_SHORTNAME)
FAIL_EXCEPTION = partial(_fail_exception, SCRIPT_SHORTNAME)
CONFIG_OR_EXIT = partial(_config_or_exit, FAIL_EXCEPTION, ERR_BAD_CONFIG)
SETUP_LMS_OR_EXIT = partial(_setup_lms_api_or_exit, FAIL, ERR_SETUP_FAILED)

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


def validate_dates(_, __, value):
    """
    Click input validator for date options.
    - Validates string format
    - Transforms the string into a datetime.Date object
    - Validates the date is less than or equal to today
    - Returns the Date, or raises a click.BadParameter
    """
    try:
        date = datetime.strptime(value, '%Y-%m-%d').date()
        if date > datetime.now().date():
            raise ValueError()
        return date
    except ValueError:
        raise click.BadParameter('Dates need to be in the format of YYYY-MM-DD and today or earlier.')  # noqa: B904


def _fetch_learners_to_update_or_exit(config, start_date, end_date, initial_state):
    """
    Makes the call to fetch learners to be bulk updated, returns the list of learners
    or exits.
    """
    LOG('Fetching users in state {} created from {} to {}'.format(initial_state, start_date, end_date))  # noqa: UP032
    try:
        return config['LMS'].get_learners_by_date_and_status(initial_state, start_date, end_date)
    except Exception as exc:  # pylint: disable=broad-except
        FAIL_EXCEPTION(ERR_FETCHING, 'Unexpected error occurred fetching users to update!', exc)


def _update_learners_or_exit(config, learners, new_state=None, rewind_state=False):
    """
    Iterates the list of learners, setting each to the new state. On any error
    it will exit the script. If rewind_state is set to True then the learner
    will be reset to their previous state.
    """
    if (not new_state and not rewind_state) or (rewind_state and new_state):
        FAIL(ERR_BAD_CONFIG, "You must specify either the boolean rewind_state or a new state to set learners to.")
    LOG('Updating {} learners to {}'.format(len(learners), new_state))  # noqa: UP032
    try:
        for learner in learners:
            if rewind_state:
                new_state = learner['last_state']['state_name']
            config['LMS'].update_learner_retirement_state(
                learner['original_username'],
                new_state,
                'Force updated via retirement_bulk_status_update script',
                force=True
            )
    except Exception as exc:  # pylint: disable=broad-except
        FAIL_EXCEPTION(ERR_UPDATING, 'Unexpected error occurred updating users!', exc)


@click.command("update_statuses")
@click.option(
    '--config_file',
    help='YAML file that contains retirement-related configuration for this environment.'
)
@click.option(
    '--initial_state',
    help='Find learners in this retirement state. Use the state name ex: PENDING, COMPLETE'
)
@click.option(
    '--new_state',
    help='Set any found learners to this new state. Use the state name ex: PENDING, COMPLETE',
    default=None
)
@click.option(
    '--start_date',
    callback=validate_dates,
    help='(YYYY-MM-DD) Earliest creation date for retirements to act on.'
)
@click.option(
    '--end_date',
    callback=validate_dates,
    help='(YYYY-MM-DD) Latest creation date for retirements to act on.'
)
@click.option(
    '--rewind-state',
    help='Rewinds to the last_state for learners. Useful for resetting ERRORED users',
    default=False,
    is_flag=True
)
def update_statuses(config_file, initial_state, new_state, start_date, end_date, rewind_state):
    """
    Bulk-updates user retirement statuses which are in the specified state -and- retirement was
    requested between a start date and end date.
    """
    try:
        LOG('Starting bulk update script: Config: {}'.format(config_file))  # noqa: UP032

        if not config_file:
            FAIL(ERR_NO_CONFIG, 'No config file passed in.')

        config = CONFIG_OR_EXIT(config_file)
        SETUP_LMS_OR_EXIT(config)

        learners = _fetch_learners_to_update_or_exit(config, start_date, end_date, initial_state)
        _update_learners_or_exit(config, learners, new_state, rewind_state)

        LOG('Bulk update complete')
    except Exception as exc:
        print(text_type(exc))
        raise


if __name__ == '__main__':
    # pylint: disable=unexpected-keyword-arg, no-value-for-parameter
    update_statuses(auto_envvar_prefix='RETIREMENT')
