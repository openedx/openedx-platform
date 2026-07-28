#! /usr/bin/env python3
"""
Command-line script to drive the user retirement workflow for a single user

To run this script you will need a username to run against and a YAML config file in the format:

client_id: <client id from LMS DOT>
client_secret: <client secret from LMS DOT>
base_urls:
    lms: http://localhost:18000/
    ecommerce: http://localhost:18130/
    credentials: http://localhost:18150/
retirement_pipeline:
    - ['RETIRING_LICENSE_MANAGER', 'LICENSE_MANAGER_COMPLETE', 'LICENSE_MANAGER', 'retire_learner']
    - ['RETIRING_FORUMS', 'FORUMS_COMPLETE', 'LMS', 'retirement_retire_forum']
    - ['RETIRING_EMAIL_LISTS', 'EMAIL_LISTS_COMPLETE', 'LMS', 'retirement_retire_mailings']
    - ['RETIRING_ENROLLMENTS', 'ENROLLMENTS_COMPLETE', 'LMS', 'retirement_unenroll']
    - ['RETIRING_LMS', 'LMS_COMPLETE', 'LMS', 'retirement_lms_retire']
"""

import logging
import sys
from functools import partial
from os import path
from time import time

import click

# Add top-level project path to sys.path before importing scripts code
sys.path.append(path.abspath(path.join(path.dirname(__file__), '../..')))

from scripts.user_retirement.utils.exception import HttpDoesNotExistException

# pylint: disable=wrong-import-position
from scripts.user_retirement.utils.helpers import (
    _config_or_exit,
    _fail,
    _fail_exception,
    _get_error_str_from_exception,
    _log,
    _setup_all_apis_or_exit,
)

# Return codes for various fail cases
ERR_SETUP_FAILED = -1
ERR_USER_AT_END_STATE = -2
ERR_USER_IN_WORKING_STATE = -3
ERR_WHILE_RETIRING = -4
ERR_BAD_LEARNER = -5
ERR_UNKNOWN_STATE = -6
ERR_BAD_CONFIG = -7

SCRIPT_SHORTNAME = 'Learner Retirement'
LOG = partial(_log, SCRIPT_SHORTNAME)
FAIL = partial(_fail, SCRIPT_SHORTNAME)
FAIL_EXCEPTION = partial(_fail_exception, SCRIPT_SHORTNAME)
CONFIG_OR_EXIT = partial(_config_or_exit, FAIL_EXCEPTION, ERR_BAD_CONFIG)
SETUP_ALL_APIS_OR_EXIT = partial(_setup_all_apis_or_exit, FAIL_EXCEPTION, ERR_SETUP_FAILED)

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

# "Magic" states with special meaning, these are required to be in LMS
START_STATE = 'PENDING'
ERROR_STATE = 'ERRORED'
COMPLETE_STATE = 'COMPLETE'
ABORTED_STATE = 'ABORTED'
END_STATES = (ERROR_STATE, ABORTED_STATE, COMPLETE_STATE)

# We'll store the access token here once retrieved
AUTH_HEADER = {}


def _get_learner_state_index_or_exit(learner, config):
    """
    Returns the index in the ALL_STATES retirement state list, validating that it is in
    an appropriate state to work on.
    """
    try:
        learner_state = learner['current_state']['state_name']
        learner_state_index = config['all_states'].index(learner_state)

        if learner_state in END_STATES:
            FAIL(ERR_USER_AT_END_STATE, 'User already in end state: {}'.format(learner_state))  # noqa: UP032

        if learner_state in config['working_states']:
            FAIL(ERR_USER_IN_WORKING_STATE, 'User is already in a working state! {}'.format(learner_state))  # noqa: UP032

        return learner_state_index
    except KeyError:
        FAIL(ERR_BAD_LEARNER, 'Bad learner response missing current_state or state_name: {}'.format(learner))  # noqa: UP032
    except ValueError:
        FAIL(ERR_UNKNOWN_STATE, 'Unknown learner retirement state for learner: {}'.format(learner))  # noqa: UP032


def _config_retirement_pipeline(config):
    """
    Organizes the pipeline and populate the various state types
    """
    # List of states where an API call is currently in progress
    retirement_pipeline = config['retirement_pipeline']
    config['working_states'] = [state[0] for state in retirement_pipeline]

    # Create the full list of all of our states
    config['all_states'] = [START_STATE]
    for working in config['retirement_pipeline']:
        config['all_states'].append(working[0])
        config['all_states'].append(working[1])
    for end in END_STATES:
        config['all_states'].append(end)


def _get_learner_and_state_index_or_exit(config, username):
    """
    Double-checks the current learner state, contacting LMS, and maps that state to its
    index in the pipeline. Exits out if the learner is in an invalid state or not found
    in LMS.
    """
    try:
        learner = config['LMS'].get_learner_retirement_state(username)
        learner_state_index = _get_learner_state_index_or_exit(learner, config)
        return learner, learner_state_index
    except HttpDoesNotExistException:
        FAIL(ERR_BAD_LEARNER, 'Learner {} not found. Please check that the learner is present in '  # noqa: UP032
                              'UserRetirementStatus, is not already retired, '
                              'and is in an appropriate state to be acted upon.'.format(username))
    except Exception as exc:  # pylint: disable=broad-except
        FAIL_EXCEPTION(ERR_SETUP_FAILED, 'Unexpected error fetching user state!', str(exc))


@click.command("retire_learner")
@click.option(
    '--username',
    help='The original username of the user to retire'
)
@click.option(
    '--config_file',
    help='File in which YAML config exists that overrides all other params.'
)
def retire_learner(
    username,
    config_file
):
    """
    Retrieves a JWT token as the retirement service learner, then performs the retirement process as
    defined in WORKING_STATE_ORDER
    """
    LOG('Starting learner retirement for {} using config file {}'.format(username, config_file))  # noqa: UP032

    if not config_file:
        FAIL(ERR_BAD_CONFIG, 'No config file passed in.')

    config = CONFIG_OR_EXIT(config_file)
    _config_retirement_pipeline(config)
    SETUP_ALL_APIS_OR_EXIT(config)

    learner, learner_state_index = _get_learner_and_state_index_or_exit(config, username)

    start_state = None
    try:
        for start_state, end_state, service, method in config['retirement_pipeline']:
            # Skip anything that has already been done
            if config['all_states'].index(start_state) < learner_state_index:
                LOG('State {} completed in previous run, skipping'.format(start_state))  # noqa: UP032
                continue

            LOG('Starting state {}'.format(start_state))  # noqa: UP032

            config['LMS'].update_learner_retirement_state(username, start_state, 'Starting: {}'.format(start_state))  # noqa: UP032

            # This does the actual API call
            start_time = time()
            response = getattr(config[service], method)(learner)
            end_time = time()

            LOG('State {} completed in {} seconds'.format(start_state, end_time - start_time))  # noqa: UP032

            config['LMS'].update_learner_retirement_state(
                username,
                end_state,
                'Ending: {} with response:\n{}'.format(end_state, response)  # noqa: UP032
            )

            learner_state_index += 1

            LOG('Progressing to state {}'.format(end_state))  # noqa: UP032

        config['LMS'].update_learner_retirement_state(username, COMPLETE_STATE, 'Learner retirement complete.')
        LOG('Retirement complete for learner {}'.format(username))  # noqa: UP032
    except Exception as exc:  # pylint: disable=broad-except
        exc_msg = _get_error_str_from_exception(exc)

        try:
            LOG('Error in retirement state {}: {}'.format(start_state, exc_msg))  # noqa: UP032
            config['LMS'].update_learner_retirement_state(username, ERROR_STATE, exc_msg)
        except Exception as update_exc:  # pylint: disable=broad-except
            LOG('Critical error attempting to change learner state to ERRORED: {}'.format(update_exc))  # noqa: UP032

        FAIL_EXCEPTION(ERR_WHILE_RETIRING, 'Error encountered in state "{}"'.format(start_state), exc)  # noqa: UP032


if __name__ == '__main__':
    # pylint: disable=unexpected-keyword-arg, no-value-for-parameter
    retire_learner(auto_envvar_prefix='RETIREMENT')
