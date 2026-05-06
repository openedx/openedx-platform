"""
Management command to remove social auth users.  Intended for use in masters
integration sandboxes to allow partners reset users and enrollment data.
"""


import logging

from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common.djangoapps.third_party_auth.models import SAMLProviderConfig

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Management command to remove all social auth entries AND the corresponding edX
    users for a given IDP.

    Usage:
        manage.py remove_social_auth_users gtx
    """
    confirmation_prompt = "Type 'confirm' to continue with deletion\n"

    def add_arguments(self, parser):
        parser.add_argument('IDP', help='slug for the idp to remove all users from')
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip manual confirmation step before deleting objects',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        slug = options['IDP']

        if not settings.FEATURES.get('ENABLE_ENROLLMENT_RESET'):
            raise CommandError('ENABLE_ENROLLMENT_RESET feature not enabled on this enviroment')

        try:
            SAMLProviderConfig.objects.current_set().get(slug=slug)
        except SAMLProviderConfig.DoesNotExist:
            raise CommandError(f'No SAML provider found for slug {slug}')  # pylint: disable=raise-missing-from  # noqa: B904

        users = User.objects.filter(social_auth__provider=slug)
        user_count = len(users)
        count, models = users.delete()
        log.info(
            '\n%s users and their related models will be deleted:\n%s\n',
            user_count,
            models,
        )

        if not options['force']:
            confirmation = input(self.confirmation_prompt)
            if confirmation != 'confirm':
                raise CommandError('User confirmation required.  No records have been modified')

        log.info('Deleting %s records...', count)
