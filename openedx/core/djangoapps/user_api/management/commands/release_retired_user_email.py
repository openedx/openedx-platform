"""
Releases a retired learner's email address so it can be reused for a new
registration, without touching the archived UserRetirementStatus row.
"""
import logging

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.management.base import BaseCommand, CommandError

from openedx.core.djangoapps.user_api.accounts.utils import release_retired_learner_email
from openedx.core.djangoapps.user_api.models import RetirementStateError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Releases a single retired learner's original email address for reuse.

    The learner must already be fully retired (UserRetirementStatus is in the
    COMPLETE state) - see release_retired_learner_email() for the exact rules.
    """
    help = "Releases a retired learner's email address so it can be reused for a new registration."

    def add_arguments(self, parser):
        # user_id only - avoids taking a username (PII) as input for a retired-learner tool.
        parser.add_argument('--user_id', type=int, required=True, help='User ID of the retired learner to release.')

    def handle(self, *args, **options):
        user_id = options['user_id']

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist as exc:
            raise CommandError(f'No user found for the given user_id={user_id!r}.') from exc

        try:
            release_retired_learner_email(user)
        except RetirementStateError as exc:
            raise CommandError(str(exc)) from exc

        message = f'Successfully released email for user {user.id}.'
        logger.info(message)
        self.stdout.write(self.style.SUCCESS(message))
