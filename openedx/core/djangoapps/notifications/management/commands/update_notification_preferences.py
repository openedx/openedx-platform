"""
Management command for updating notification preferences with parameters
"""
import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from openedx.core.djangoapps.notifications.base_notification import COURSE_NOTIFICATION_APPS, COURSE_NOTIFICATION_TYPES
from openedx.core.djangoapps.notifications.models import NotificationPreference

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Management command to update boolean notification preferences.

    This command updates channel (`web`, `push`, `email`)
    in the NotificationPreference model for a given app and type.

    Features:
        - Requires `app` and `type`, validated against
          COURSE_NOTIFICATION_APPS and COURSE_NOTIFICATION_TYPES.
        - Allows updating a single channel to `true` or `false`.
        - Supports optional `--user_ids` argument to limit updates
          to specific users.
        - Provides a `--dry-run` mode to preview changes without
          committing to the database.
        - Logs the number of affected records and affected user IDs.

    Example usage:
        python manage.py update_notification_preference discussion new_comment_on_response email false
        python manage.py update_notification_preference discussion new_comment_on_response push false --user_ids 5 7 12
        python manage.py update_notification_preference discussion new_comment_on_response web false --dry-run
    """
    help = "Update boolean notification preferences for users at account level."

    def add_arguments(self, parser):
        parser.add_argument(
            "app",
            type=str,
            choices=list(COURSE_NOTIFICATION_APPS.keys()),
            help=f"App key (choices: {', '.join(COURSE_NOTIFICATION_APPS.keys())})",
        )
        parser.add_argument(
            "type",
            type=str,
            choices=list(COURSE_NOTIFICATION_TYPES.keys()),
            help=f"Type key (choices: {', '.join(COURSE_NOTIFICATION_TYPES.keys())})"
        )
        parser.add_argument(
            "channel",
            type=str,
            choices=["web", "push", "email"],
            help="channel to update"
        )
        parser.add_argument(
            "value",
            type=str,
            choices=["true", "false"],
            help="Boolean value (true/false)"
        )
        parser.add_argument(
            "--user_ids",
            nargs="+",
            type=int,
            help="Optional list of user IDs to update only",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate update without saving changes",
        )

    def handle(self, *args, **options):
        app = options["app"]
        pref_type = options["type"]
        channel = options["channel"]
        value_str = options["value"].lower()
        dry_run = options["dry_run"]
        user_ids = options.get("user_ids")

        if value_str in ["true"]:
            new_value = True
        elif value_str in ["false"]:
            new_value = False
        else:
            raise CommandError("Value must be true/false")

        queryset = NotificationPreference.objects.filter(app=app, type=pref_type)
        if user_ids:
            queryset = queryset.filter(user_id__in=user_ids)

        queryset = queryset.exclude(**{channel: new_value})  # only ones that need updating

        affected = queryset.count()

        if not affected:
            logger.info("No records to update.")
            return

        logger.info(
            f"{affected} record(s) will be updated. "
        )

        if dry_run:
            logger.info("Dry-run mode: no changes applied.")
            return

        with transaction.atomic():
            updated = queryset.update(**{channel: new_value})
            logger.info(f" Updated {updated} records.")
