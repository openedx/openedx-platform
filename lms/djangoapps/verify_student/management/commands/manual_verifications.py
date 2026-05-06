"""
Django admin commands related to verify_student
"""


import logging
import os
from pprint import pformat

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.management.base import BaseCommand, CommandError

from lms.djangoapps.verify_student.models import ManualVerification
from lms.djangoapps.verify_student.utils import earliest_allowed_verification_date

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    This method attempts to manually verify users.
    Example usage:
        $ ./manage.py lms manual_verifications --email-ids-file <absolute path of file with email ids (one per line)>
    """
    help = 'Manually verifies one or more users passed as an argument list.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email-ids-file',
            action='store',
            dest='email_ids_file',
            default=None,
            help='Path of the file to read email id from.',
            type=str,
        )
        parser.add_argument(
            '--email',
            default=None,
            help='Single email to verify one user',
            type=str,
        )
        parser.add_argument(
            '--batch-size',
            default=10000,
            help='Maximum records to write in one query.',
            type=int,
        )

    def handle(self, *args, **options):

        single_email = options['email']

        if single_email:
            successfully_verified = self._add_user_to_manual_verification(single_email)
            if successfully_verified is False:
                log.error(f'Manual verification of {single_email} failed')
            return

        email_ids_file = options['email_ids_file']
        batch_size = options['batch_size']

        if email_ids_file:
            if not os.path.exists(email_ids_file):
                raise CommandError('Pass the correct absolute path to email ids file as --email-ids-file argument.')

        total_emails, failed_emails = self._generate_manual_verification_from_file(email_ids_file, batch_size)

        if failed_emails:
            log.error('Completed manual verification. {} of {} failed.'.format(  # noqa: UP032
                len(failed_emails),
                total_emails
            ))
            log.error(f'Failed emails:{pformat(failed_emails)}')
        else:
            log.info(f'Successfully generated manual verification for {total_emails} emails.')

    def _generate_manual_verification_from_file(self, email_ids_file, batch_size=None):
        """
        Generate manual verification for the emails provided in the email ids file.

        Arguments:
            email_ids_file (str): path of the file containing email ids.
            batch_size (int): limits the number of verifications written to db at once

        Returns:
            (total_emails, failed_emails): a tuple containing count of emails processed and a list containing
             emails whose verifications could not be processed.
        """
        with open(email_ids_file) as file_handler:
            email_ids = [line.rstrip() for line in file_handler]
            total_emails = len(email_ids)

        log.info(f'Creating manual verification for {total_emails} emails.')
        failed_emails = []
        for n in range(0, total_emails, batch_size):
            failed_emails.extend(self._add_users_to_manual_verification(email_ids[n:n + batch_size]))

        return total_emails, failed_emails

    def _add_users_to_manual_verification(self, email_ids):
        """
        Generates a verification for a list of user emails.

        Arguments:
            email_ids (list): emails of the users to be verified

        Returns:
            failed_emails: list of emails for which a verification was not created
        """
        verifications_to_create = []
        users = User.objects.filter(email__in=email_ids)
        user_existing_verification = {v.user.id for v in ManualVerification.objects.filter(
            user__in=users,
            status='approved',
            created_at__gte=earliest_allowed_verification_date(),
        )}
        for user in users:
            if user.id not in user_existing_verification:
                verifications_to_create.append(ManualVerification(
                    user=user,
                    name=user.profile.name,
                    status='approved',
                ))
            else:
                log.info(f'Skipping email {user.email}, existing verification found.')
        ManualVerification.objects.bulk_create(verifications_to_create)
        failed_emails = set(email_ids) - set(users.values_list('email', flat=True))
        return list(failed_emails)

    def _add_user_to_manual_verification(self, email_id):
        """
        Generates a single verification for a user.

        Arguments:
            email_id (str): email of the user to be verified

        Returns:
            (success): boolean to show if the user has been successfully verified.
        """
        try:
            user = User.objects.get(email=email_id)
            ManualVerification.objects.get_or_create(
                user=user,
                status='approved',
                created_at__gte=earliest_allowed_verification_date(),
                defaults={'name': user.profile.name},
            )
            return True
        except User.DoesNotExist:
            log.error(f'Tried to verify email {email_id}, but user not found')
            return False
