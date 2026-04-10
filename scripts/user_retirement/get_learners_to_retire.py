#! /usr/bin/env python3

"""
Command-line script to retrieve list of learners that have requested to be retired.
The script calls the appropriate LMS endpoint to get this list of learners.
"""

import io
import logging
import sys
from os import path

import click
import yaml

# Add top-level project path to sys.path before importing scripts code
sys.path.append(path.abspath(path.join(path.dirname(__file__), '../..')))

from scripts.user_retirement.utils.edx_api import LmsApi
from scripts.user_retirement.utils.jenkins import export_learner_job_properties

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
LOG = logging.getLogger(__name__)


@click.command("get_learners_to_retire")
@click.option(
    '--config_file',
    help='File in which YAML config exists that overrides all other params.'
)
@click.option(
    '--cool_off_days',
    help='Number of days a learner should be in the retirement queue before being actually retired.',
    default=7
)
@click.option(
    '--output_dir',
    help="Directory in which to write the Jenkins properties files.",
    default='./jenkins_props'
)
@click.option(
    '--user_count_error_threshold',
    help="If more users than this number are returned we will error out instead of retiring. This is a failsafe"
         "against attacks that somehow manage to add users to the retirement queue.",
    default=300
)
@click.option(
    '--max_user_batch_size',
    help="This setting will only get at most X number of users. If this number is lower than the user_count_error_threshold"
         "setting then it will not error.",
    default=200
)
def get_learners_to_retire(config_file,
                           cool_off_days,
                           output_dir,
                           user_count_error_threshold,
                           max_user_batch_size):
    """
    Retrieves a JWT token as the retirement service user, then calls the LMS
    endpoint to retrieve the list of learners awaiting retirement.
    """
    if not config_file:
        click.echo('A config file is required.')
        sys.exit(-1)

    with io.open(config_file, 'r') as config:  # noqa: UP020
        config_yaml = yaml.safe_load(config)

    user_count_error_threshold = int(user_count_error_threshold)
    cool_off_days = int(cool_off_days)

    client_id = config_yaml['client_id']
    client_secret = config_yaml['client_secret']
    lms_base_url = config_yaml['base_urls']['lms']
    retirement_pipeline = config_yaml['retirement_pipeline']
    end_states = [state[1] for state in retirement_pipeline]
    states_to_request = ['PENDING'] + end_states

    api = LmsApi(lms_base_url, lms_base_url, client_id, client_secret)

    # Retrieve the learners to retire and export them to separate Jenkins property files.
    learners_to_retire = api.learners_to_retire(states_to_request, cool_off_days, max_user_batch_size)
    if max_user_batch_size:
        learners_to_retire = learners_to_retire[:max_user_batch_size]
    learners_to_retire_cnt = len(learners_to_retire)

    if learners_to_retire_cnt > user_count_error_threshold:
        click.echo(
            'Too many learners to retire! Expected {} or fewer, got {}!'.format(  # noqa: UP032
                user_count_error_threshold,
                learners_to_retire_cnt
            )
        )
        sys.exit(-1)

    export_learner_job_properties(
        learners_to_retire,
        output_dir
    )


if __name__ == "__main__":
    # pylint: disable=unexpected-keyword-arg, no-value-for-parameter
    # If using env vars to provide params, prefix them with "RETIREMENT_", e.g. RETIREMENT_CLIENT_ID
    get_learners_to_retire(auto_envvar_prefix='RETIREMENT')
