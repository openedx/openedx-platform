'''CatalogIntegration management command'''


from django.core.management import BaseCommand, CommandError

from openedx.core.djangoapps.catalog.models import CatalogIntegration


class Command(BaseCommand):
    """Management command used to create catalog integrations."""
    help = "Create catalog integration record in LMS"

    def add_arguments(self, parser):
        parser.add_argument(
            '--enabled',
            dest='enabled',
            action='store_true',
            help='Enable the catalog integration'
        )

        parser.add_argument(
            '--internal_api_url',
            help='The url of the internal api',
            required=True
        )

        parser.add_argument(
            '--service_username',
            help='The username for the service',
            required=True
        )
        parser.add_argument(
            '--cache_ttl',
            type=int,
            default=0,
            help='Enable caching of API responses by setting this to a value greater than 0 (seconds)'
        )
        parser.add_argument(
            '--long_term_cache_ttl',
            type=int,
            default=86400,
            help='Enable long term caching of API responses by setting this to a value greater than 0 (seconds)'
        )
        parser.add_argument(
            '--page_size',
            type=int,
            default=100,
            help='Maximum number of records in paginated response of a single request to catalog service'
        )

    def handle(self, *args, **options):

        enabled = options.get('enabled')
        internal_api_url = options.get('internal_api_url')
        service_username = options.get('service_username')
        cache_ttl = options.get('cache_ttl')
        long_term_cache_ttl = options.get('long_term_cache_ttl')
        page_size = options.get('page_size')

        try:
            catalog_integration = CatalogIntegration.objects.create(
                enabled=enabled,
                internal_api_url=internal_api_url,
                service_username=service_username,
                cache_ttl=cache_ttl,
                long_term_cache_ttl=long_term_cache_ttl,
                page_size=page_size
            )
        except Exception as err:
            raise CommandError(f'Error creating CatalogIntegration: {err}')  # lint-amnesty, pylint: disable=raise-missing-from

        self.stdout.write(self.style.SUCCESS(
            'Successfully created CatalogIntegration enabled={} url={} service_username={}').format(
                catalog_integration.enabled,
                catalog_integration.internal_api_url,
                catalog_integration.service_username
        ))
