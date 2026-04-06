"""
Create or updates the SiteConfiguration for a site.
"""

import codecs
import json
import logging

from django.contrib.sites.models import Site
from django.core.management import BaseCommand

from openedx.core.djangoapps.site_configuration.models import SiteConfiguration

LOG = logging.getLogger(__name__)


def load_json_from_file(filename):
    with codecs.open(filename, encoding='utf-8') as file:
        return json.load(file)


class Command(BaseCommand):
    """
    Management command to create or update SiteConfiguration.
    """
    help = 'Create or update SiteConfiguration'

    def add_arguments(self, parser):
        args_group1 = parser.add_mutually_exclusive_group(required=True)
        args_group1.add_argument(
            '--site-id',
            action='store',
            dest='site_id',
            type=int,
            help='ID of the Site whose SiteConfiguration has to be updated.'
        )
        args_group1.add_argument('domain', nargs='?', default='')

        args_group2 = parser.add_mutually_exclusive_group()
        args_group2.add_argument(
            '--configuration',
            type=json.loads,
            help="Enter JSON site configuration",
            required=False
        )
        args_group2.add_argument(
            '-f',
            '--configuration-file',
            type=load_json_from_file,
            dest='config_file_data',
            help="Enter the path to the JSON file containing the site configuration",
            required=False
        )

        args_group3 = parser.add_mutually_exclusive_group()
        args_group3.add_argument(
            '--enabled',
            action='store_true',
            dest='enabled',
            default=None,
            help='Enable the SiteConfiguration.'
        )
        args_group3.add_argument(
            '--disabled',
            action='store_false',
            dest='enabled',
            help='Disable the SiteConfiguration.'
        )

    def handle(self, *args, **options):
        site_id = options.get('site_id')
        domain = options.get('domain')
        name = domain
        configuration = options.get('configuration')
        config_file_data = options.get('config_file_data')

        enabled = options.get('enabled')

        if site_id is not None:
            site, created = Site.objects.get_or_create(id=site_id)
        else:
            name_max_length = Site._meta.get_field("name").max_length
            if name:
                if len(str(name)) > name_max_length:
                    LOG.warning(
                        f"The name {name} is too long, truncating to {name_max_length}"
                        " characters. Please update site name in admin."
                    )
                # trim name as the column has a limit of 50 characters
                name = name[:name_max_length]
            site, created = Site.objects.get_or_create(
                domain=domain,
                name=name,
            )
        if created:
            LOG.info(f"Site does not exist. Created new site '{site.domain}'")
        else:
            LOG.info(f"Found existing site for '{site.domain}'")

        site_configuration, created = SiteConfiguration.objects.get_or_create(site=site)
        if created:
            LOG.info(
                "Site configuration does not exist. Created new instance for '{site_name}'".format(  # noqa: UP032
                    site_name=site.domain
                )
            )
        else:
            LOG.info(
                f"Found existing site configuration for '{site.domain}'. Updating it."
            )

        site_configuration_values = configuration or config_file_data

        if site_configuration_values:
            if site_configuration.site_values:
                site_configuration.site_values.update(site_configuration_values)
            else:
                site_configuration.site_values = site_configuration_values

        if enabled is not None:
            site_configuration.enabled = enabled

        site_configuration.save()
