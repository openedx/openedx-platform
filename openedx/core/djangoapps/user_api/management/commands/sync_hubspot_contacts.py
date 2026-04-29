"""
Management command to sync platform users with hubspot
./manage.py lms sync_hubspot_contacts
./manage.py lms sync_hubspot_contacts --initial-sync-days=7 --batch-size=20
"""


import json
import time
import traceback
import urllib.parse  # pylint: disable=import-error
from datetime import datetime, timedelta
from textwrap import dedent

import requests
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.management.base import BaseCommand, CommandError
from requests.exceptions import HTTPError

from common.djangoapps.student.models import UserAttribute
from common.djangoapps.util.query import use_read_replica_if_available
from openedx.core.djangoapps.site_configuration.models import SiteConfiguration

HUBSPOT_API_BASE_URL = 'https://api.hubapi.com'


class Command(BaseCommand):
    """
    Command to create contacts in hubspot for those partner who has enabled hubspot integration.
    This command is suppose to sync contact with hubspot on daily basis.
    """
    help = dedent(__doc__).strip()

    def _get_hubspot_enabled_sites(self):
        """
        Returns: list of site configurations having hubspot integration enabled
        """
        site_confs = SiteConfiguration.objects.all()
        hubspot_sites = [
            site_conf for site_conf in site_confs
            if site_conf.get_value('HUBSPOT_API_KEY')
        ]
        return hubspot_sites

    def _get_users_queryset(self, initial_days):
        """
        initial_days: numbers of days to go back from today
        :return: users queryset
        """
        start_date = datetime.now().date() - timedelta(initial_days)
        end_date = datetime.now().date() - timedelta(1)
        self.stdout.write(f'Getting users from {start_date} to {end_date}')
        users_qs = User.objects.filter(
            date_joined__date__gte=start_date,
            date_joined__date__lte=end_date
        ).order_by('id')
        return use_read_replica_if_available(users_qs)

    def _get_batched_users(self, site_domain, users_queryset, offset, users_query_batch_size):
        """
        Args:
            site_domain: site where we need unsynced users
            users_queryset: users_queryset to slice
            users_query_batch_size: slice size

        Returns: site users

        """

        self.stdout.write(
            'Fetching Users for site {site} from {start} to {end}'.format(  # noqa: UP032
                site=site_domain, start=offset, end=offset + users_query_batch_size
            )
        )
        users = users_queryset.select_related('profile')[offset: offset + users_query_batch_size]
        site_users = [
            user for user in users
            if UserAttribute.get_user_attribute(user, 'created_on_site') == site_domain
        ]
        self.stdout.write(f'\tSite Users={len(site_users)}')

        return site_users

    def _sync_with_hubspot(self, users_batch, site_conf):
        """
        Sync batch of users with hubspot
        """
        contacts = []
        for user in users_batch:
            if not hasattr(user, 'profile'):
                self.stdout.write(f'skipping user {user} due to no profile found')
                continue
            if not user.profile.meta:
                self.stdout.write(f'skipping user {user} due to no profile meta found')
                continue
            try:
                meta = json.loads(user.profile.meta)
            except ValueError:
                self.stdout.write(f'skipping user {user} due to invalid profile meta found')
                continue

            contact = {
                "email": user.email,
                "properties": [
                    {
                        "property": "firstname",
                        "value": meta.get('first_name', '')
                    },
                    {
                        "property": "lastname",
                        "value": meta.get('last_name', '')
                    },
                    {
                        "property": "company",
                        "value": meta.get('company', '')
                    },
                    {
                        "property": "jobtitle",
                        "value": meta.get('title', '')
                    },
                    {
                        "property": "state",
                        "value": meta.get('state', '')
                    },
                    {
                        "property": "country",
                        "value": meta.get('country', '')
                    },
                    {
                        "property": "gender",
                        "value": user.profile.get_gender_display()
                    },
                    {
                        "property": "degree",
                        "value": user.profile.get_level_of_education_display()
                    },
                ]
            }
            contacts.append(contact)

        api_key = site_conf.get_value('HUBSPOT_API_KEY')
        api_url = urllib.parse.urljoin(f"{HUBSPOT_API_BASE_URL}/", 'contacts/v1/contact/batch/')
        try:
            response = requests.post(api_url, json=contacts, params={"hapikey": api_key})
            response.raise_for_status()
            return len(contacts)
        except HTTPError as ex:
            message = 'An error occurred while syncing batch of contacts for site {domain}, {message}'.format(  # noqa: UP032  # pylint: disable=line-too-long
                domain=site_conf.site.domain, message=str(ex)
            )
            self.stderr.write(message)
            return 0

    def _sync_site(self, site_conf, users_queryset, users_count, contacts_batch_size):
        """
            Syncs a single site
        """
        site_domain = site_conf.site.domain
        self.stdout.write(f'Syncing process started for site {site_domain}')

        offset = 0
        users_queue = []
        users_query_batch_size = 5000
        successfully_synced_contacts = 0

        while offset < users_count:
            is_last_iteration = (offset + users_query_batch_size) >= users_count
            self.stdout.write(
                'Syncing users batch from {start} to {end} for site {site}'.format(  # noqa: UP032
                    start=offset, end=offset + users_query_batch_size, site=site_domain
                )
            )
            users_queue += self._get_batched_users(site_domain, users_queryset, offset, users_query_batch_size)
            while len(users_queue) >= contacts_batch_size \
                    or (is_last_iteration and users_queue):  # for last iteration need to empty users_queue
                users_batch = users_queue[:contacts_batch_size]
                del users_queue[:contacts_batch_size]
                successfully_synced_contacts += self._sync_with_hubspot(users_batch, site_conf)
                time.sleep(0.1)  # to make sure request per second could not exceed by 10
            self.stdout.write(
                'Successfully synced users batch from {start} to {end} for site {site}'.format(  # noqa: UP032
                    start=offset, end=offset + users_query_batch_size, site=site_domain
                )
            )
            offset += users_query_batch_size

        self.stdout.write(
            '{count} contacts found and sycned for site {site}'.format(  # noqa: UP032
                count=successfully_synced_contacts, site=site_domain
            )
        )

    def add_arguments(self, parser):
        """
        Definition of arguments this command accepts
        """
        parser.add_argument(
            '--initial-sync-days',
            default=1,
            dest='initial_sync_days',
            type=int,
            help='Number of days before today to start sync',
        )
        parser.add_argument(
            '--batch-size',
            default=100,
            dest='batch_size',
            type=int,
            help='Size of contacts batch to be sent to hubspot',
        )

    def handle(self, *args, **options):
        """
        Main command handler
        """
        initial_sync_days = options['initial_sync_days']
        batch_size = options['batch_size']
        try:
            self.stdout.write(f'Command execution started with options = {options}.')
            hubspot_sites = self._get_hubspot_enabled_sites()
            self.stdout.write(f'{len(hubspot_sites)} hubspot enabled sites found.')
            users_queryset = self._get_users_queryset(initial_sync_days)
            users_count = users_queryset.count()
            self.stdout.write(f'Users count={users_count}')
            for site_conf in hubspot_sites:
                self._sync_site(site_conf, users_queryset, users_count, batch_size)

        except Exception as ex:
            traceback.print_exc()
            raise CommandError('Command failed with traceback %s' % str(ex))  # pylint: disable=raise-missing-from  # noqa: B904, UP031
