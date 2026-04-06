"""
Django management command for changing an enterprise user's username.
"""


import logging

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.management import BaseCommand
from enterprise.models import EnterpriseCustomerUser

LOGGER = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Updates the username value for a given user.

    This is NOT MEANT for general use, and is specifically limited to Enterprise Users since
    only they could potentially experience the issue of overwritten usernames.

    See ENT-832 for details on the bug that modified usernames for some Enterprise Users.
    """
    help = 'Update the username of a given user.'

    def add_arguments(self, parser):
        parser.add_argument(
            '-u',
            '--user_id',
            action='store',
            dest='user_id',
            default=None,
            help='The ID of the user to update.'
        )

        parser.add_argument(
            '-n',
            '--new_username',
            action='store',
            dest='new_username',
            default=None,
            help='The username value to set for the user.'
        )

    def handle(self, *args, **options):
        user_id = options.get('user_id')
        new_username = options.get('new_username')

        try:
            EnterpriseCustomerUser.objects.get(user_id=user_id)
        except EnterpriseCustomerUser.DoesNotExist:
            LOGGER.info(f'User {user_id} must be an Enterprise User.')
            return

        user = User.objects.get(id=user_id)
        user.username = new_username
        user.save()

        LOGGER.info(f'User {user_id} has been updated with username {new_username}.')
