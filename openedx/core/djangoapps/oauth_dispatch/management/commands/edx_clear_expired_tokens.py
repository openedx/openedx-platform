"""
Management command for clear expired access tokens!
"""


import logging
from datetime import timedelta
from time import sleep

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from oauth2_provider.models import AccessToken, Grant, RefreshToken
from oauth2_provider.settings import oauth2_settings

logger = logging.getLogger(__name__)


class Command(BaseCommand):  # pylint: disable=missing-class-docstring
    help = "Clear expired access tokens and refresh tokens for Django OAuth Toolkit"

    def _add_boolean_flag(self, parser, name, default=False):
        parser.add_argument(f'--{name}', dest=name, action='store_true')
        parser.add_argument(f'--no-{name}', dest=name, action='store_false')
        parser.set_defaults(**{name: default})

    def add_arguments(self, parser):
        parser.add_argument('--batch_size',
                            action='store',
                            dest='batch_size',
                            type=int,
                            default=1000,
                            help='Maximum number of database rows to delete per query. '
                                 'This helps avoid locking the database when deleting large amounts of data.')
        parser.add_argument('--sleep_time',
                            action='store',
                            dest='sleep_time',
                            type=int,
                            default=10,
                            help='Sleep time between deletion of batches')
        parser.add_argument('--excluded-application-ids',
                            action='store',
                            dest='excluded-application-ids',
                            type=str,
                            default='',
                            help='Comma-separated list of application IDs for which tokens will NOT be removed')
        self._add_boolean_flag(parser, 'refresh-tokens', True)
        self._add_boolean_flag(parser, 'access-tokens', True)
        self._add_boolean_flag(parser, 'grants', True)
        self._add_boolean_flag(parser, 'revoked-tokens', True)

    def clear_table_data(self, query_set, batch_size, model, sleep_time):  # pylint: disable=missing-function-docstring
        total_deletions = 0
        deletion_count = 1  # we expect to delete at least one row to start out with
        while deletion_count:
            deletion_count = 0
            qs = query_set[:batch_size]
            if isinstance(qs, list) and not qs:
                # if we are seeing an empty list here then that there's nothing more to delete
                break
            batch_ids = qs.values_list('id', flat=True)
            batch_id_list = list(batch_ids)
            with transaction.atomic():
                deletions = model.objects.filter(pk__in=batch_id_list).delete()
                deletion_count = deletions[0]
            total_deletions += deletion_count
            logger.info(f"Removed {total_deletions} rows from {model.__name__} table")
            sleep(sleep_time)
        message = f'Final deletion count: Cleaned {total_deletions} rows from {model.__name__} table'
        logger.info(message)

    def get_expiration_time(self, now):  # pylint: disable=missing-function-docstring
        refresh_token_expire_seconds = oauth2_settings.REFRESH_TOKEN_EXPIRE_SECONDS
        if not isinstance(refresh_token_expire_seconds, timedelta):
            try:
                refresh_token_expire_seconds = timedelta(seconds=refresh_token_expire_seconds)
            except TypeError:
                e = "REFRESH_TOKEN_EXPIRE_SECONDS must be either a timedelta or seconds"
                raise ImproperlyConfigured(e)  # pylint: disable=raise-missing-from  # noqa: B904
            return now - refresh_token_expire_seconds

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        sleep_time = options['sleep_time']
        if options['excluded-application-ids']:
            excluded_application_ids = [int(x) for x in options['excluded-application-ids'].split(',')]
        else:
            excluded_application_ids = []

        now = timezone.now()
        expiry_threshold = self.get_expiration_time(now)

        if options['refresh-tokens']:
            logger.info("Removing expired RefreshTokens")
            query_set = RefreshToken.objects.filter(access_token__expires__lt=expiry_threshold).exclude(
                application_id__in=excluded_application_ids)
            self.clear_table_data(query_set, batch_size, RefreshToken, sleep_time)

        if options['revoked-tokens']:
            logger.info("Removing revoked RefreshTokens")
            # remove revoked, as opposed to expired, RefreshTokens
            revoked = RefreshToken.objects.filter(revoked__lt=expiry_threshold).exclude(
                application_id__in=excluded_application_ids)
            self.clear_table_data(revoked, batch_size, RefreshToken, sleep_time)

        if options['access-tokens']:
            logger.info("Removing expired AccessTokens")
            query_set = AccessToken.objects.filter(refresh_token__isnull=True, expires__lt=expiry_threshold)
            self.clear_table_data(query_set, batch_size, AccessToken, sleep_time)

        if options['grants']:
            logger.info("Removing expired Grants")
            query_set = Grant.objects.filter(expires__lt=expiry_threshold)
            self.clear_table_data(query_set, batch_size, Grant, sleep_time)
