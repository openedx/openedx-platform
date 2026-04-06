"""
Management command to fix NotificationPreference records with invalid 'Mixed' email_cadence values
created during migration.
"""

import logging

from django.core.management.base import BaseCommand

from openedx.core.djangoapps.notifications.models import NotificationPreference

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Management command to identify and correct NotificationPreference records
    with an invalid 'Mixed' value in the email_cadence field.

    By default, the command runs in dry-run mode and only logs the count of
    affected records. Use the `--fix` flag to replace all 'Mixed' values with
    'Daily', ensuring data consistency with defined model choices.
    Invoke with:
        python manage.py [lms] fix_mixed_email_cadence --fix
    """
    help = (
        "Identifies NotificationPreference records with 'Mixed' as email_cadence "
        "and optionally replaces it with a valid value (default: 'Daily')."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Apply the fix by replacing "Mixed" with "Daily". Default is dry-run mode.'
        )

    def handle(self, *args, **options):
        fix_mode = options['fix']
        invalid_records = NotificationPreference.objects.filter(email_cadence='Mixed')
        count = invalid_records.count()

        if count == 0:
            logger.info("No records found with invalid 'Mixed' value in email_cadence.")
            return

        logger.info(f"Found {count} NotificationPreference records with 'Mixed' email_cadence.")

        if fix_mode:
            updated_count = invalid_records.update(
                email_cadence=NotificationPreference.EmailCadenceChoices.DAILY
            )
            logger.info(f"Successfully updated {updated_count} records. 'Mixed' replaced with 'Daily'.")
        else:
            logger.warning(
                "Dry-run mode: no changes were made.\n"
                "To apply changes, re-run the command with the --fix flag."
            )
