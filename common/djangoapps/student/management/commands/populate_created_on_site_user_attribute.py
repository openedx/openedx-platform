"""
Command to back-populate domain of the site the user account was created on.
"""


import six
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand, CommandError

from common.djangoapps.student.models import Registration, UserAttribute

CREATED_ON_SITE = 'created_on_site'


class Command(BaseCommand):
    """
    This command back-populates domain of the site the user account was created on.
    """
    help = """This command back-populates domain of the site the user account was created on.
            Example: ./manage.py lms populate_created_on_site_user_attribute --users <user_id1>,<user_id2>...
           '--activation-keys <key1>,<key2>... --site-domain <site_domain> --settings=devstack"""

    def add_arguments(self, parser):
        """
        Add arguments to the command parser.
        """
        parser.add_argument(
            '--users',
            help='Enter comma-separated user ids.',
            default='',
            type=str
        )
        parser.add_argument(
            '--activation-keys',
            help='Enter comma-separated activation keys.',
            default='',
            type=str
        )
        parser.add_argument(
            '--site-domain',
            help='Enter an existing site domain.',
            required=True
        )

    def handle(self, *args, **options):
        site_domain = options['site_domain']
        user_ids = options['users'].split(',') if options['users'] else []
        activation_keys = options['activation_keys'].split(',') if options['activation_keys'] else []

        if not user_ids and not activation_keys:
            raise CommandError('You must provide user ids or activation keys.')

        try:
            Site.objects.get(domain__exact=site_domain)
        except Site.DoesNotExist:
            question = "The site you specified is not configured as a Site in the system. " \
                       "Are you sure you want to continue? (y/n):"

            if str(six.moves.input(question)).lower().strip()[0] != 'y':
                return

        for user_id in user_ids:
            try:
                user = User.objects.get(id=user_id)
                if UserAttribute.get_user_attribute(user, CREATED_ON_SITE):
                    self.stdout.write(f"created_on_site attribute already exists for user id: {user_id}")
                else:
                    UserAttribute.set_user_attribute(user, CREATED_ON_SITE, site_domain)
            except User.DoesNotExist:
                self.stdout.write(f"This user id [{user_id}] does not exist in the system.")

        for key in activation_keys:
            try:
                user = Registration.objects.get(activation_key=key).user
                if UserAttribute.get_user_attribute(user, CREATED_ON_SITE):
                    self.stdout.write(f"created_on_site attribute already exists for user id: {user.id}")
                else:
                    UserAttribute.set_user_attribute(user, CREATED_ON_SITE, site_domain)
            except Registration.DoesNotExist:
                self.stdout.write(f"This activation key [{key}] does not exist in the system.")
