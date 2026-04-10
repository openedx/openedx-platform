"""
One-off script to opt-out users for email from orgs.

Input: A CSV file with a user_id,org pair per line. For example:

1962921,FooX
5506350,BarX
5709986,FooX

Lines formatted with a double-quoted org also work fine, such as:

5506350,"BarX"

Opts-out every specified user/org combo row from email by setting the 'email-optin' tag to 'False'.
If the user/org combo does not currently exist in the table, a row will be created for it which
will be have the 'email-optin' tag set to 'False'.
"""


import csv
import logging
import time
from textwrap import dedent

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.utils import DatabaseError

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    One-off script to opt-out users for email from orgs.

    Input: A CSV file with a user_id,org pair per line. For example:

    1962921,FooX
    5506350,BarX
    5709986,FooX

    Lines formatted with a double-quoted org also work fine, such as:

    5506350,"BarX"

    Opts-out every specified user/org combo row from email by setting the 'email-optin' tag to 'False'.
    If the user/org combo does not currently exist in the table, a row will be created for it which
    will be have the 'email-optin' tag set to 'False'.
    """
    help = dedent(__doc__).strip()
    # Default number of user/org opt-outs to perform in each DB transaction.
    DEFAULT_CHUNK_SIZE = 1000

    # Default number of seconds to sleep between chunked user/org email opt-outs.
    DEFAULT_SLEEP_BETWEEN_OPTOUTS = 0.0

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry_run',
            action='store_true',
            help='Print proposed changes, but take no action.'
        )
        parser.add_argument(
            '--chunk_size',
            default=self.DEFAULT_CHUNK_SIZE,
            type=int,
            help='Maximum number of user/org opt-outs to perform in each DB transaction.'
        )
        parser.add_argument(
            '--sleep_between',
            default=self.DEFAULT_SLEEP_BETWEEN_OPTOUTS,
            type=float,
            help='Seconds to sleep between chunked opt-outs.'
        )
        parser.add_argument(
            '--optout_csv_path',
            required=True,
            help='Filepath to CSV file containing user/org email opt-outs.'
        )

    def handle(self, *args, **options):
        """
        Execute the command.
        """
        dry_run = options['dry_run']
        chunk_size = options.get('chunk_size', self.DEFAULT_CHUNK_SIZE)
        sleep_between = options.get('sleep_between', self.DEFAULT_SLEEP_BETWEEN_OPTOUTS)
        optout_path = options['optout_csv_path']

        if chunk_size <= 0:
            raise CommandError(f'Only positive chunk size is allowed ({chunk_size}).')
        if sleep_between < 0:
            raise CommandError(f'Only non-negative sleep between seconds is allowed ({sleep_between}).')

        # Read the CSV file. Log the number of user/org rows read.
        with open(optout_path) as csv_file:
            optout_reader = csv.reader(csv_file)
            optout_rows = list(optout_reader)
        log.info("Read %s opt-out rows from CSV file '%s'.", len(optout_rows), optout_path)

        cursor = connections['default'].cursor()

        # Update/insert the rows one chunk at a time.
        curr_row_idx = 0
        start_idx = 0
        while curr_row_idx < len(optout_rows):
            start_idx = curr_row_idx
            end_idx = min(start_idx + chunk_size - 1, len(optout_rows) - 1)

            log.info("Attempting opt-out for rows (%s, %s) through (%s, %s)...",
                     optout_rows[start_idx][0], optout_rows[start_idx][1],
                     optout_rows[end_idx][0], optout_rows[end_idx][1])

            # Build the SQL query.
            query = 'INSERT INTO user_api_userorgtag (`user_id`, `org`, `key`, `value`, `created`, `modified`) VALUES '
            query_values = []
            for idx in range(start_idx, end_idx + 1):
                query_values.append('({},"{}","email-optin","False",NOW(),NOW())'.format(  # noqa: UP032
                    optout_rows[idx][0], optout_rows[idx][1])
                )
            query += ','.join(query_values)
            query += ' ON DUPLICATE KEY UPDATE value="False", modified=NOW();'

            # Execute the SQL query.
            if dry_run:
                log.info(query)
            else:
                try:
                    cursor.execute('START TRANSACTION;')
                    cursor.execute(query)
                except DatabaseError as err:
                    cursor.execute('ROLLBACK;')
                    log.error("Rolled-back opt-out for rows (%s, %s) through (%s, %s): %s",
                              optout_rows[start_idx][0], optout_rows[start_idx][1],
                              optout_rows[end_idx][0], optout_rows[end_idx][1],
                              str(err))
                    raise
                cursor.execute('COMMIT;')
                log.info("Committed opt-out for rows (%s, %s) through (%s, %s).",
                         optout_rows[start_idx][0], optout_rows[start_idx][1],
                         optout_rows[end_idx][0], optout_rows[end_idx][1])
                log.info("Sleeping %s seconds...", sleep_between)
                time.sleep(sleep_between)
            curr_row_idx += chunk_size
