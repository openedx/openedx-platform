"""
This command will be run by an ansible script.
"""


import fnmatch
import json
import logging
import os
from textwrap import dedent

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from oauth2_provider.models import Application

from common.djangoapps.student.models import UserProfile
from lms.djangoapps.commerce.models import CommerceConfiguration
from openedx.core.djangoapps.oauth_dispatch.models import ApplicationAccess
from openedx.core.djangoapps.site_configuration.models import SiteConfiguration
from openedx.core.djangoapps.theming.models import SiteTheme

LOG = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Command to create the site, site themes, configuration and oauth2 clients for all WL-sites.

    Example:
    ./manage.py lms create_sites_and_configurations --dns-name whitelabel --theme-path /edx/src/edx-themes/edx-platform
    """
    help = dedent(__doc__).strip()
    dns_name = None
    theme_path = None
    ecommerce_user = None
    ecommerce_base_url_fmt = None
    ecommerce_oauth_complete_url = None
    discovery_user = None
    discovery_base_url_fmt = None
    discovery_oauth_complete_url = None

    configuration_filename = None

    def add_arguments(self, parser):
        """
        Add arguments to the command parser.
        """
        parser.add_argument(
            "--dns-name",
            type=str,
            help="Enter DNS name of sandbox.",
            required=True
        )

        parser.add_argument(
            "--theme-path",
            type=str,
            help="Enter theme directory path",
            required=True
        )

        parser.add_argument(
            "--devstack",
            action='store_true',
            help="Use devstack config, otherwise sandbox config is assumed",
        )

    def _create_oauth2_client(self, url, site_name, service_name, service_user):
        """
        Creates the oauth2 client and add it in trusted clients.
        """
        client_id = "{service_name}-key{site_name}".format(
            service_name=service_name,
            site_name="" if site_name == "edx" else f"-{site_name}"
        )
        app, _ = Application.objects.update_or_create(
            client_id=client_id,
            defaults={
                "user": service_user,
                "name": "{service_name}-sso-{site_name}".format(
                    site_name=site_name,
                    service_name=service_name,
                ),
                "client_secret": "{service_name}-secret".format(
                    service_name=service_name
                ),
                "client_type": Application.CLIENT_CONFIDENTIAL,
                "authorization_grant_type": Application.GRANT_AUTHORIZATION_CODE,
                "redirect_uris": f"{url}complete/edx-oauth2/",
                "skip_authorization": True,
            }
        )

        access = ApplicationAccess.objects.filter(application_id=app.id).first()
        default_scopes = 'user_id'
        if access:
            access.scopes = default_scopes
            access.save()
        else:
            ApplicationAccess.objects.create(application_id=app.id, scopes=default_scopes)

    def _create_sites(self, site_domain, theme_dir_name, site_configuration):
        """
        Create Sites, SiteThemes and SiteConfigurations
        """
        site, created = Site.objects.get_or_create(
            domain=site_domain,
            defaults={"name": theme_dir_name}
        )
        if created:
            LOG.info(f"Creating '{site_domain}' SiteTheme")
            SiteTheme.objects.create(site=site, theme_dir_name=theme_dir_name)

            LOG.info(f"Creating '{site_domain}' SiteConfiguration")
            SiteConfiguration.objects.create(
                site=site,
                site_values=site_configuration,
                enabled=True
            )
        else:
            LOG.info(f"'{site_domain}' site already exists")

    def find(self, pattern, path):
        """
        Matched the given pattern in given path and returns the list of matching files
        """
        result = []
        for root, dirs, files in os.walk(path):  # pylint: disable=unused-variable
            for name in files:
                if fnmatch.fnmatch(name, pattern):
                    result.append(os.path.join(root, name))
        return result

    def _enable_commerce_configuration(self):
        """
        Enable the commerce configuration.
        """
        CommerceConfiguration.objects.get_or_create(
            enabled=True,
            checkout_on_ecommerce_service=True
        )

    def _update_default_clients(self):
        """
        These two clients are being created by default without service
        users so we have to associate the service users to them.
        """
        ecommerce_queryset = Application.objects.filter(redirect_uris=self.ecommerce_oauth_complete_url)

        if ecommerce_queryset:
            ecommerce_client = ecommerce_queryset[0]
            ecommerce_client.user = self.ecommerce_user
            ecommerce_client.save()

        discovery_queryset = Application.objects.filter(redirect_uris=self.discovery_oauth_complete_url)
        if discovery_queryset:
            discovery_client = discovery_queryset[0]
            discovery_client.user = self.discovery_user
            discovery_client.save()

    def _get_sites_data(self):
        """
        Reads the json files from theme directory and returns the site data in JSON format.
        "site_a":{
            "theme_dir_name": "site_a.edu.au"
            "configuration": {
                "key1": "value1",
                "key2": "value2"
            }
        }
        """
        site_data = {}
        for config_file in self.find(self.configuration_filename, self.theme_path):
            LOG.info(f"Reading file from {config_file}")
            configuration_data = json.loads(
                json.dumps(
                    json.load(
                        open(config_file)
                    )
                ).replace("{dns_name}", self.dns_name)
            )['lms_configuration']

            site_data[configuration_data['sandbox_name']] = {
                "site_domain": configuration_data['site_domain'],
                "theme_dir_name": configuration_data['theme_dir_name'],
                "configuration": configuration_data['configuration']
            }
        return site_data

    def get_or_create_service_user(self, username):
        """
        Creates the service user for ecommerce and discovery.
        """
        service_user, _ = User.objects.get_or_create(username=username)
        service_user.is_active = True
        service_user.is_staff = True
        service_user.is_superuser = True
        service_user.save()

        # Without User profile we cannot publish the course from ecommerce to LMS.
        UserProfile.objects.get_or_create(
            user=service_user,
            defaults={
                "name": username
            }
        )
        return service_user

    def handle(self, *args, **options):
        self.dns_name = options['dns_name']
        self.theme_path = options['theme_path']

        if options['devstack']:
            configuration_prefix = "devstack"
            self.discovery_oauth_complete_url = "http://discovery-{}.e2e.devstack:18381/complete/edx-oauth2/".format(
                self.dns_name
            )
            self.discovery_base_url_fmt = "http://discovery-{site_domain}:18381/"
            self.ecommerce_oauth_complete_url = "http://ecommerce-{}.e2e.devstack:18130/complete/edx-oauth2/".format(
                self.dns_name
            )
            self.ecommerce_base_url_fmt = "http://ecommerce-{site_domain}:18130/"
        else:
            configuration_prefix = "sandbox"
            self.discovery_oauth_complete_url = "https://discovery-{}.sandbox.edx.org/complete/edx-oauth2/".format(
                self.dns_name
            )
            self.discovery_base_url_fmt = "https://discovery-{site_domain}/"
            self.ecommerce_oauth_complete_url = "https://ecommerce-{}.sandbox.edx.org/complete/edx-oauth2/".format(
                self.dns_name
            )
            self.ecommerce_base_url_fmt = "https://ecommerce-{site_domain}/"

        self.configuration_filename = f'{configuration_prefix}_configuration.json'
        self.discovery_user = self.get_or_create_service_user("lms_catalog_service_user")
        self.ecommerce_user = self.get_or_create_service_user("ecommerce_worker")

        all_sites = self._get_sites_data()
        self._update_default_clients()

        # creating Sites, SiteThemes, SiteConfigurations and oauth2 clients
        for site_name, site_data in all_sites.items():
            site_domain = site_data['site_domain']

            discovery_url = self.discovery_base_url_fmt.format(site_domain=site_domain)
            ecommerce_url = self.ecommerce_base_url_fmt.format(site_domain=site_domain)

            LOG.info(f"Creating '{site_name}' Site")
            self._create_sites(site_domain, site_data['theme_dir_name'], site_data['configuration'])

            LOG.info(f"Creating discovery oauth2 client for '{site_name}' site")
            self._create_oauth2_client(discovery_url, site_name, 'discovery', self.discovery_user)

            LOG.info(f"Creating ecommerce oauth2 client for '{site_name}' site")
            self._create_oauth2_client(ecommerce_url, site_name, 'ecommerce', self.ecommerce_user)

        self._enable_commerce_configuration()
