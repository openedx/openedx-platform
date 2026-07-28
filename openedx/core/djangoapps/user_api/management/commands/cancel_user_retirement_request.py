"""
Use this mgmt command when a user requests retirement mistakenly, then requests
for the retirement request to be cancelled. The command can't cancel a retirement
that has already commenced - only pending retirements.
"""


import logging

from django.core.management.base import BaseCommand, CommandError

from openedx.core.djangoapps.user_api.accounts.utils import handle_retirement_cancellation
from openedx.core.djangoapps.user_api.models import UserRetirementStatus

LOGGER = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Implementation of the cancel_user_retirement_request command.
    """
    help = 'Cancels the retirement of a user who has requested retirement - but has not yet been retired.'

    def add_arguments(self, parser):
        parser.add_argument('email_address',
                            help='Email address of user whose retirement request will be cancelled.')

    def handle(self, *args, **options):
        """
        Execute the command.
        """
        email_address = options['email_address'].lower()

        try:
            # Load the user retirement status.
            retirement_status = UserRetirementStatus.objects.select_related('current_state').select_related('user').get(
                original_email=email_address
            )
        except UserRetirementStatus.DoesNotExist:
            raise CommandError(f"No retirement request with email address '{email_address}' exists.")  # pylint: disable=raise-missing-from  # noqa: B904

        # Check if the user has started the retirement process -or- not.
        if retirement_status.current_state.state_name != 'PENDING':
            raise CommandError(
                "Retirement requests can only be cancelled for users in the PENDING state."  # noqa: UP032
                " Current request state for '{}': {}".format(
                    email_address,
                    retirement_status.current_state.state_name
                )
            )

        handle_retirement_cancellation(retirement_status, email_address)

        print(f"Successfully cancelled retirement request for user with email address '{email_address}'.")
