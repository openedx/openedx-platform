# pylint: disable=missing-module-docstring,too-many-format-args


from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.management.base import BaseCommand
from django.db.models import Q


class Command(BaseCommand):  # pylint: disable=missing-class-docstring
    help = "Show a user's roles and permissions."

    def add_arguments(self, parser):
        parser.add_argument('email_or_username',
                            help='the email or username of the user')

    def handle(self, *args, **options):
        email_or_username = options['email_or_username']

        user = User.objects.filter(Q(username=email_or_username) | Q(email=email_or_username)).first()
        if not user:
            print(f'User {email_or_username} does not exist. ')
            print('Available users: ')
            print(User.objects.all())
            return

        roles = user.roles.all()
        print(f'{user} has {len(roles)} roles:')
        for role in roles:
            print(f'\t{role}')

        for role in roles:
            print(f'{role} has permissions: ')
            print(role.permissions.all())
