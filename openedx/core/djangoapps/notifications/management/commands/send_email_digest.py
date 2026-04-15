"""
Management command for sending email digest.

DEPRECATED: This command is retained for backward compatibility.
Digest emails are now scheduled automatically via delayed Celery tasks
when notifications are created. Remove any cron jobs calling this command.
"""
import warnings

from django.core.management.base import BaseCommand

from openedx.core.djangoapps.notifications.email_notifications import EmailCadence


class Command(BaseCommand):
    """
    Invoke with:

        python manage.py lms send_email_digest [cadence_type]
        cadence_type: Daily or Weekly

    DEPRECATED: Digest emails are now automatically scheduled via delayed
    Celery tasks when notifications are created.
    """
    help = (
        "DEPRECATED: Send email digest to users. "
        "Digest emails are now scheduled automatically. "
        "Remove cron jobs using this command."
    )

    def add_arguments(self, parser):
        """
        Adds management commands parser arguments
        """
        cadence_type_choices = [EmailCadence.DAILY, EmailCadence.WEEKLY]
        parser.add_argument('cadence_type', choices=cadence_type_choices)

    def handle(self, *args, **kwargs):
        """
        Start task to send email digest to users
        """
        warnings.warn(
            "The send_email_digest management command is deprecated. "
            "Digest emails are now scheduled automatically via delayed Celery tasks "
            "when notifications are created. Remove any cron jobs calling this command.",
            DeprecationWarning,
            stacklevel=2
        )
        self.stderr.write(
            self.style.WARNING(
                "WARNING: This command is deprecated. Digest emails are now scheduled "
                "automatically. Please remove cron jobs using this command."
            )
        )
