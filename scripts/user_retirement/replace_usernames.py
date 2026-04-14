#! /usr/bin/env python3

"""
Command-line script to replace the usernames for all passed in learners.
Accepts a list of current usernames and their preferred new username. This
script will call LMS first which generates a unique username if the passed in
new username is not unique. It then calls all other services to replace the
username in their DBs.

"""

import csv
import io
import logging
import sys
from os import path

import click
import yaml

# Add top-level project path to sys.path before importing scripts code
sys.path.append(path.abspath(path.join(path.dirname(__file__), '../..')))

from scripts.user_retirement.utils.edx_api import (  # pylint: disable=wrong-import-position
    CredentialsApi,
    DiscoveryApi,
    EcommerceApi,
    LmsApi,
)

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
LOG = logging.getLogger(__name__)


def write_responses(writer, replacements, status):
    for replacement in replacements:
        original_username = list(replacement.keys())[0]
        new_username = list(replacement.values())[0]
        writer.writerow([original_username, new_username, status])


@click.command("replace_usernames")
@click.option(
    '--config_file',
    help='File in which YAML config exists that overrides all other params.'
)
@click.option(
    '--username_replacement_csv',
    help='File in which YAML config exists that overrides all other params.'
)
def replace_usernames(config_file, username_replacement_csv):
    """
    Retrieves a JWT token as the retirement service user, then calls the LMS
    endpoint to retrieve the list of learners awaiting retirement.

    Config file example:
    ```
    client_id: xxx
    client_secret: xxx
    base_urls:
        lms: http://localhost:18000
        ecommerce: http://localhost:18130
        discovery: http://localhost:18381
        credentials: http://localhost:18150
    ```

    Username file example:
    ```
    current_un_1,desired_un_1
    current_un_2,desired_un_2,
    current_un_3,desired_un_3
    ```
    """
    if not config_file:
        click.echo('A config file is required.')
        sys.exit(-1)

    if not username_replacement_csv:
        click.echo('A username replacement CSV file is required')
        sys.exit(-1)

    with io.open(config_file, 'r') as config:  # noqa: UP020
        config_yaml = yaml.safe_load(config)

    with io.open(username_replacement_csv, 'r') as replacement_file:  # noqa: UP020
        csv_reader = csv.reader(replacement_file)
        lms_username_mappings = [
            {current_username: desired_username}
            for (current_username, desired_username)
            in csv_reader
        ]

    client_id = config_yaml['client_id']
    client_secret = config_yaml['client_secret']
    lms_base_url = config_yaml['base_urls']['lms']
    ecommerce_base_url = config_yaml['base_urls']['ecommerce']
    discovery_base_url = config_yaml['base_urls']['discovery']
    credentials_base_url = config_yaml['base_urls']['credentials']

    # Note that though partially_failed sounds better than completely_failed,
    # it's actually worse since the user is not consistant across DBs.
    # Partially failed username replacements will need to be triaged so the
    # user isn't in a broken state
    successful_replacements = []
    partially_failed_replacements = []
    fully_failed_replacements = []

    lms_api = LmsApi(lms_base_url, lms_base_url, client_id, client_secret)
    ecommerce_api = EcommerceApi(lms_base_url, ecommerce_base_url, client_id, client_secret)
    discovery_api = DiscoveryApi(lms_base_url, discovery_base_url, client_id, client_secret)
    credentials_api = CredentialsApi(lms_base_url, credentials_base_url, client_id, client_secret)

    # Call LMS with current and desired usernames
    response = lms_api.replace_lms_usernames(lms_username_mappings)
    fully_failed_replacements += response['failed_replacements']
    in_progress_replacements = response['successful_replacements']

    # Step through each services endpoints with the list returned from LMS.
    # The LMS list has already verified usernames and made any duplicate
    # usernames unique (e.g. 'matt' => 'mattf56a'). We pass successful
    # replacements onto the next service and store all failed replacments.
    replacement_methods = [
        ecommerce_api.replace_usernames,
        discovery_api.replace_usernames,
        credentials_api.replace_usernames,
        lms_api.replace_forums_usernames,
    ]
    # Iterate through the endpoints above and if the APIs return any failures
    # capture these in partially_failed_replacements. Only successfuly
    # replacements will continue to be passed to the next service.
    for replacement_method in replacement_methods:
        response = replacement_method(in_progress_replacements)
        partially_failed_replacements += response['failed_replacements']
        in_progress_replacements = response['successful_replacements']

    successful_replacements = in_progress_replacements

    with open('username_replacement_results.csv', 'w', newline='') as output_file:
        csv_writer = csv.writer(output_file)
        # Write header
        csv_writer.writerow(['Original Username', 'New Username', 'Status'])
        write_responses(csv_writer, successful_replacements, "SUCCESS")
        write_responses(csv_writer, partially_failed_replacements, "PARTIALLY FAILED")
        write_responses(csv_writer, fully_failed_replacements, "FAILED")

    if partially_failed_replacements or fully_failed_replacements:
        sys.exit(-1)


if __name__ == "__main__":
    # pylint: disable=unexpected-keyword-arg, no-value-for-parameter
    # If using env vars to provide params, prefix them with "RETIREMENT_", e.g. RETIREMENT_CLIENT_ID
    replace_usernames(auto_envvar_prefix='USERNAME_REPLACEMENT')
