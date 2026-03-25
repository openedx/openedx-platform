"""
Command for managing commerce configuration for lms.
We can use this command to enable/disable commerce configuration or disable checkout to E-Commerce service.
"""


import logging  # noqa: I001

from django.core.management import BaseCommand

from ...models import CommerceConfiguration

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class Command(BaseCommand):
    """
    Command to enable or disable commerce configuration.

    Positional Arguments:
        This command does not take any positional argument.

    Optional Arguments:
        disable (bool):    if True then disable configuration, enable otherwise
        checkout_on_ecommerce (bool): Enable E-Commerce checkout if True, disable otherwise.
    """
    help = 'Enable/Disable commerce configuration, including configuration of E-Commerce checkout.'

    def add_arguments(self, parser):
        parser.add_argument('--disable-checkout-on-ecommerce',
                            dest='checkout_on_ecommerce',
                            action='store_false',
                            default=True,
                            help='Do not checkout to E-Commerce even when configuration is enabled.')
        parser.add_argument('--disable',
                            dest='disable',
                            action='store_true',
                            default=False,
                            help='Disable existing E-Commerce configuration.')

    def handle(self, *args, **options):
        """
        Create a new commerce configuration or update an existing one according to the command line arguments.

        args:
            This command does not take any positional argument.

        options:
            disable (bool):    if True then disable configuration, enable otherwise
            checkout_on_ecommerce (bool): Enable E-Commerce checkout if True, disable otherwise.
        """
        disable = options.get('disable')
        checkout_on_ecommerce = options.get('checkout_on_ecommerce')

        # We are keeping id=1, because as of now, there are only one commerce configuration for the system.
        pk_id = 1
        CommerceConfiguration.objects.update_or_create(
            id=pk_id,
            defaults={
                'id': pk_id,
                'enabled': not disable,
                'checkout_on_ecommerce_service': checkout_on_ecommerce,
            }
        )
        logger.info(
            'Commerce Configuration {configuration_status} with checkout on ecommerce {checkout_status}.'.format(
                configuration_status="disabled" if disable else "enabled",
                checkout_status="enabled" if checkout_on_ecommerce else "disabled",
            ),
        )
