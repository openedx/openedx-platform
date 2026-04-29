"""Management command to unsubscribe user's email in bulk on Braze."""
import csv
import logging

from django.core.management.base import BaseCommand, CommandError

from lms.djangoapps.utils import get_email_client

logger = logging.getLogger(__name__)
CHUNK_SIZE = 50


class Command(BaseCommand):
    """
    Management command to unsubscribe user's email in bulk on Braze.
    """

    help = """
    Unsubscribe for all given user's email on braze.

    Example:

    Unsubscribe user's email for multiple users on braze.
        $ ... unsubscribe_user_email -p <csv_file_path>
    """

    def add_arguments(self, parser):
        parser.add_argument(
            '-p', '--csv_path',
            metavar='csv_path',
            dest='csv_path',
            required=False,
            help='Path to CSV file.')

    def _chunked_iterable(self, iterable):
        """
        Yield successive CHUNK_SIZE sized chunks from iterable.
        """
        for i in range(0, len(iterable), CHUNK_SIZE):
            yield iterable[i:i + CHUNK_SIZE]

    def _chunk_list(self, emails_list):
        """
        Chunk a list into sub-lists of length CHUNK_SIZE.
        """
        return list(self._chunked_iterable(emails_list))

    def handle(self, *args, **options):
        emails = []
        csv_file_path = options['csv_path']

        try:
            with open(csv_file_path, 'r') as csv_file:  # noqa: UP015
                reader = list(csv.DictReader(csv_file))
                emails = [row.get('email') for row in reader]
        except FileNotFoundError as exc:
            raise CommandError(f"Error: File not found due to exception - {exc}")  # pylint: disable=raise-missing-from  # noqa: B904
        except csv.Error as exc:
            logger.exception(f"CSV error: {exc}")
        else:
            logger.info("CSV file read successfully.")

        chunks = self._chunk_list(emails)

        try:
            email_client = get_email_client()
            if email_client:
                for i, chunk in enumerate(chunks):
                    email_client.unsubscribe_user_email(
                        email=chunk,
                    )
                    logger.info(f"Successfully unsubscribed for chunk-{i + 1} consist of {len(chunk)} emails")
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception(f"Unable to update email status on Braze due to : {exc}")
