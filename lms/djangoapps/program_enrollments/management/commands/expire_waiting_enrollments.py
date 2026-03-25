""" Management command to cleanup old waiting enrollments """


import logging  # noqa: I001

from django.core.management.base import BaseCommand

from lms.djangoapps.program_enrollments import tasks

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Deletes enrollments not tied to a user that have not been modified
    for at least 60 days.

    Example usage:
        $ ./manage.py lms expire_waiting_enrollments
    """

    help = 'Remove expired enrollments that have not been linked to a user.'
    WAITING_ENROLLMENTS_EXPIRATION_DAYS = 60

    def add_arguments(self, parser):
        parser.add_argument(
            '--expiration_days',
            help='Number of days before a waiting enrollment is considered expired',
            default=self.WAITING_ENROLLMENTS_EXPIRATION_DAYS,
            type=int
        )

    def handle(self, *args, **options):
        expiration_days = options.get('expiration_days')
        logger.info('Deleting waiting enrollments unmodified for %s days', expiration_days)
        tasks.expire_waiting_enrollments(expiration_days)
