"""
Django admin commands related to verify_student
"""

import logging
import time
from datetime import datetime

from django.core.management.base import BaseCommand

from lms.djangoapps.verify_student.models import SoftwareSecurePhotoVerification, SSPVerificationRetryConfig

log = logging.getLogger('retry_photo_verification')


class Command(BaseCommand):
    """
    This method finds those SoftwareSecurePhotoVerifications with a selected status
    from a start_datetime to an end_datetime and attempts to verify them.

    Use case: Multiple IDVs need to be resubmitted.

    Example:
        ./manage.py lms retry_failed_photo_verifications --status="submitted" \
        --start_datetime="2023-03-01 00:00:00" --end_datetime="2023-03-28 23:59:59"
        (This resubmits all 'submitted' SoftwareSecurePhotoVerifications from 2023-03-01 to 2023-03-28)
    """

    args = "<SoftwareSecurePhotoVerification id, SoftwareSecurePhotoVerification id, ...>"
    help = (
        "Retries SoftwareSecurePhotoVerifications passed as "
        "arguments, or if no arguments are supplied, all that "
        "have a specified status, from a start datetime to an "
        "end datetime"
    )

    def add_arguments(self, parser):

        parser.add_argument(
            '--verification-ids',
            dest='verification_ids',
            action='store',
            nargs='+',
            type=str,
            help='verifications id used to retry verification'
        )

        parser.add_argument(
            '--args-from-database',
            action='store_true',
            help='Use arguments from the SSPVerificationRetryConfig model instead of the command line.',
        )

        parser.add_argument(
            '--status',
            action='store',
            dest='status',
            default='must_retry',
            type=str,
            help='SoftwareSecurePhotoVerifications status to filter for'
        )

        parser.add_argument(
            '--start_datetime',
            action='store',
            dest='start_datetime',
            default=None,
            type=str,
            help='Start date for a date range of SoftwareSecurePhotoVerifications; '
                 'Should be formatted as YYYY-mm-dd HH:MM:SS; '
                 'Requires the end_datetime arg.',
        )

        parser.add_argument(
            '--end_datetime',
            action='store',
            dest='end_datetime',
            # "YYYY-mm-dd HH:MM:SS"
            default=None,
            type=str,
            help='End date for a date range of SoftwareSecurePhotoVerifications; '
                 'Should be formatted as YYYY-mm-dd HH:MM:SS; '
                 'Requires the start_datetime arg.',
        )

        parser.add_argument(
            '--batch_size',
            action='store',
            dest='batch_size',
            type=int,
            default=300,
            help='Maximum number of SoftwareSecurePhotoVerifications to process. '
                 'This helps avoid overloading the database while updating large amount of data.'
        )

        parser.add_argument(
            '--sleep_time',
            action='store',
            dest='sleep_time',
            type=int,
            default=10,
            help='Sleep time in seconds between update of batches'
        )

    def get_args_from_database(self):
        """ Returns an options dictionary from the current SSPVerificationRetryConfig model. """

        sspv_retry_config = SSPVerificationRetryConfig.current()
        if not sspv_retry_config.enabled:
            log.error('SSPVerificationRetryConfig is disabled or empty, but --args-from-database was requested.')
            return {}

        # We don't need fancy shell-style whitespace/quote handling - none of our arguments are complicated
        argv = sspv_retry_config.arguments.split()

        parser = self.create_parser('manage.py', 'sspv_retry')
        return parser.parse_args(argv).__dict__  # we want a dictionary, not a non-iterable Namespace object

    def handle(self, *args, **options):

        options = self.get_args_from_database() if options['args_from_database'] else options
        if options == {}:
            return

        args = options.get('verification_ids', None)
        force_must_retry = False

        if args:
            force_must_retry = True
            attempts_to_retry = SoftwareSecurePhotoVerification.objects.filter(
                receipt_id__in=options['verification_ids']
            )
            log.info(
                f"Attempting to re-submit {len(attempts_to_retry)} failed SoftwareSecurePhotoVerification submissions; "
                f"with retry verification ids from config model"
            )
        else:
            # Filter by status
            status = options['status']
            if status != 'must_retry':
                force_must_retry = True
            attempts_to_retry = SoftwareSecurePhotoVerification.objects.filter(
                status=status
            )

            log.info(
                f"Attempting to re-submit {len(attempts_to_retry)} failed SoftwareSecurePhotoVerification submissions; "
                f"\nwith status: {status}"
            )

            # Make sure we have both a start date and end date
            if options['start_datetime'] is not None and options['end_datetime'] is not None:
                start_datetime = datetime.strptime(options['start_datetime'], '%Y-%m-%d %H:%M:%S')
                end_datetime = datetime.strptime(options['end_datetime'], '%Y-%m-%d %H:%M:%S')

                # Filter by date range
                attempts_to_retry = attempts_to_retry.filter(submitted_at__range=(start_datetime, end_datetime))

                log.info(
                    f"In date range: `{start_datetime}` to `{end_datetime}`"
                )

        # Re-submit attempts_to_retry
        batch_size = options['batch_size']
        sleep_time = options['sleep_time']
        count = 0

        for index, attempt in enumerate(attempts_to_retry):
            log.info(f"Re-submitting submission #{index} (ID: {attempt.id}, User: {attempt.user})")

            # Set the attempt's status to 'must_retry' so that we can re-submit it
            if force_must_retry:
                attempt.status = 'must_retry'

            attempt.submit(copy_id_photo_from=attempt.copy_id_photo_from)
            log.info(f"Retry result: {attempt.status}")
            count += 1

            # Sleep between batches of <batch_size>
            if count == batch_size:
                time.sleep(sleep_time)
                count = 0

        log.info("Done resubmitting failed photo verifications")
