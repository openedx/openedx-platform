"""
One-time cleanup: clear PII and delete ManualVerification rows for retired users.
"""

import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from lms.djangoapps.verify_student.models import ManualVerification

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Clears PII then deletes ManualVerification records belonging to retired users.

    Only runs when REDACT_MANUAL_VERIFICATION_HISTORICAL_PII is True.

    Example usage:
        $ ./manage.py lms cleanup_retired_manual_verifications
        $ ./manage.py lms cleanup_retired_manual_verifications --dry-run
    """

    help = 'Clear PII and delete ManualVerification rows for retired users.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Log what would be deleted without making any changes.',
        )

    def handle(self, *args, **options):
        if not getattr(settings, 'REDACT_MANUAL_VERIFICATION_HISTORICAL_PII', False):
            log.warning('Skipping. REDACT_MANUAL_VERIFICATION_HISTORICAL_PII must first be enabled.')
            return

        dry_run = options['dry_run']

        retired_records = ManualVerification.objects.filter(
            user__userretirementrequest__isnull=False,
        )

        count = retired_records.count()
        if count == 0:
            log.info('No ManualVerification records found for retired users.')
            return

        log.info('Found %d ManualVerification record(s) for retired users.', count)

        if dry_run:
            log.info('[dry-run] %d record(s) would be redacted and deleted. No changes made.', count)
            return

        try:
            retired_records.update(name='')
            retired_records.delete()
        except Exception as exc:
            log.exception('Failed to redact/delete ManualVerification records: %s', exc)
            raise

        log.info('Redacted and deleted %d ManualVerification record(s) for retired users.', count)
