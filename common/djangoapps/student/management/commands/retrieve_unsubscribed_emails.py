"""Management command to retrieve unsubscribed emails from Braze."""

import logging  # noqa: I001
import tempfile
from datetime import datetime, timedelta

from django.conf import settings
from django.core.mail.message import EmailMultiAlternatives
from django.core.management.base import BaseCommand, CommandError
from django.template.loader import get_template

from lms.djangoapps.utils import get_email_client

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    This management command retrieves unsubscribed emails from Braze, saves these emails into a CSV file, and
    then sends the file to the email address specified in the BRAZE_UNSUBSCRIBED_EMAILS_RECIPIENT_EMAIL setting.
    """

    help = """
    To retrieve unsubscribed emails from the Braze API, we need to specify a start and end date. If either of
    the dates is not provided, we will automatically retrieve the data for the previous week and replace the
    missing date value.

    Usage:
    python manage.py retrieve_unsubscribed_emails [--start_date START_DATE] [--end_date END_DATE]

    Options:
      --start_date START_DATE   Start date (optional)
      --end_date END_DATE       End date (optional)

    Example:
        $ ... retrieve_unsubscribed_emails --start_date 2022-01-01 --end_date 2023-01-01
    """

    def add_arguments(self, parser):
        parser.add_argument('--start_date', dest='start_date', help='Start date')
        parser.add_argument('--end_date', dest='end_date', help='End date')

    def _write_csv(self, csv, data):
        """
        Helper method to write data into CSV
        """
        headers = list(data[0].keys())
        csv.write(','.join(headers).encode('utf-8') + b"\n")

        for row in data:
            values = [str(row[key]) for key in headers]
            csv.write(','.join(values).encode('utf-8') + b"\n")

        csv.seek(0)
        logger.info('Write unsubscribed emails data into CSV file successfully.')
        return csv

    def handle(self, *args, **options):
        """
        Execute the command
        """
        start_date = options.get('start_date')
        end_date = options.get('end_date')

        if not start_date or not end_date:
            start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            end_date = datetime.now().strftime('%Y-%m-%d')

        logger.info(f'Retrieving unsubscribed emails from {start_date} to {end_date}')

        try:
            email_client = get_email_client()
            if not email_client:
                logger.info('No Email client found. Unable to retrieve unsubscribed emails.')
                return

            emails = email_client.retrieve_unsubscribed_emails(
                start_date=start_date,
                end_date=end_date,
            )
            if not emails:
                logger.info(f'No unsubscribed emails found between {start_date} - {end_date}.')
                return

            logger.info('Email addresses for users that unsubscribed from emails between '
                        f'{start_date} - {end_date} retrieved successfully from Braze')

            context = {
                'start_date': start_date,
                'end_date': end_date,
            }
            subject = f'Unsubscribed Emails from {start_date} to {end_date}'
            txt_template = 'unsubscribed_emails/email/body.txt'
            html_template = 'unsubscribed_emails/email/body.html'

            template = get_template(txt_template)
            plain_content = template.render(context)
            template = get_template(html_template)
            html_content = template.render(context)

            email_msg = EmailMultiAlternatives(
                subject,
                plain_content,
                settings.BRAZE_UNSUBSCRIBED_EMAILS_FROM_EMAIL,
                settings.BRAZE_UNSUBSCRIBED_EMAILS_RECIPIENT_EMAIL
            )
            email_msg.attach_alternative(html_content, 'text/html')

            with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv') as csv_file:
                csv_file = self._write_csv(csv_file, emails)
                csv_file_path = csv_file.name
                with open(csv_file_path, 'rb') as file:
                    email_msg.attach(filename='unsubscribed_emails.csv', content=file.read(), mimetype='text/csv')

            email_msg.send()
            logger.info(
                f'Unsubscribed emails data sent successfully to {settings.BRAZE_UNSUBSCRIBED_EMAILS_RECIPIENT_EMAIL}')

        except Exception as exc:
            logger.exception(f'Unable to retrieve unsubscribed emails from Braze due to exception: {exc}')
            raise CommandError(
                f'Unable to retrieve unsubscribed emails from Braze due to exception: {exc}'
            ) from exc
