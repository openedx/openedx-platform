"""
Django admin command to save SoftwareSecurePhotoVerifications given an iterable of
verification_ids, thereby re-emitting the post_save signal.
"""

import datetime  # noqa: I001
import logging
import time

from django.core.management.base import BaseCommand

from lms.djangoapps.verify_student.models import SoftwareSecurePhotoVerification

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Command to save SoftwareSecurePhotoVerification model instances based on a provided interable of verifiction_ids.
    This re-emits the post_save signal.
    """

    def add_arguments(self, parser):
        parser.add_argument(
            '--start_date_time',
            action='store',
            dest='start_date_time',
            type=str,
            help='First date time for SoftwareSecurePhotoVerifications to resave; '
                 'should be formatted as 2020-12-02 00:00:00.'
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

    def handle(self, *args, **options):
        start_date = datetime.datetime.strptime(options['start_date_time'], '%Y-%m-%d %H:%M:%S')
        batch_size = options['batch_size']
        sleep_time = options['sleep_time']

        count = 0
        verifications_after_start = SoftwareSecurePhotoVerification.objects.filter(created_at__gte=start_date)

        for verification in verifications_after_start:
            logger.info(
                'Saving SoftwareSecurePhotoVerification with id=%(id)s',
                {'id': verification.id}
            )
            verification.save()
            count += 1

            if count == batch_size:
                time.sleep(sleep_time)
                count = 0
