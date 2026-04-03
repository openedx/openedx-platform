"""
Management command to bulk update many user's email addresses
"""


import csv
import logging
from os import path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger('common.djangoapps.student.management.commands.bulk_update_email')


class Command(BaseCommand):
    """
        Management command to bulk update many user's email addresses
    """

    help = """
        Change the email address of each user specified in the csv file

        csv file is expected to have one row per user with the format:
        current_email_address, new_email_address

        Example:
            $ ... bulk_update_email csv_file_path
        """

    def add_arguments(self, parser):
        """ Add argument to the command parser. """
        parser.add_argument(
            '--csv_file_path',
            required=True,
            help='Csv file path'
        )

    def handle(self, *args, **options):
        """ Main handler for the command."""
        file_path = options['csv_file_path']

        if not path.isfile(file_path):
            raise CommandError('File not found.')

        with open(file_path) as csv_file:
            csv_reader = csv.reader(csv_file)
            email_mappings = list(csv_reader)

        successful_updates = []
        failed_updates = []

        for (current_email, new_email) in email_mappings:
            try:
                user = get_user_model().objects.get(email=current_email)
                user.email = new_email
                user.save()
                successful_updates.append(new_email)
            except Exception:  # pylint: disable=broad-except
                logger.exception('Unable to update account %s', current_email)
                failed_updates.append(current_email)

        logger.info(
            'Successfully updated %s accounts. Failed to update %s accounts',
            len(successful_updates),
            len(failed_updates)
        )

        if (failed_updates):  # lint-amnesty, pylint: disable=superfluous-parens
            exit(-1)  # lint-amnesty, pylint: disable=consider-using-sys-exit
