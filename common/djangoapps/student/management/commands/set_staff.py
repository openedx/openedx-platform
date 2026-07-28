# pylint: disable=missing-module-docstring

import re

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.management.base import BaseCommand


class Command(BaseCommand):  # pylint: disable=missing-class-docstring

    help = """
    This command will set is_staff to true for one or more users.
    Lookup by username or email address, assumes usernames
    do not look like email addresses.
    """

    def add_arguments(self, parser):
        parser.add_argument('users',
                            nargs='+',
                            help='Users to set or unset (with the --unset flag) as superusers')
        parser.add_argument('--unset',
                            action='store_true',
                            dest='unset',
                            default=False,
                            help='Set is_staff to False instead of True')

    def handle(self, *args, **options):
        for user in options['users']:
            try:
                if re.match(r'[^@]+@[^@]+\.[^@]+', user):
                    v = User.objects.get(email=user)
                else:
                    v = User.objects.get(username=user)

                if options['unset']:
                    v.is_staff = False
                else:
                    v.is_staff = True

                v.save()
                print(f'Modified {user} sucessfully.')

            except Exception as err:  # pylint: disable=broad-except
                print("Error modifying user with identifier {}: {}: {}".format(user, type(err).__name__,  # noqa: UP032
                                                                               str(err)))

        print('Complete!')
