"""
Django admin commands related to verify_student
"""


import logging
import os
import time
from pprint import pformat

from django.core.management.base import BaseCommand, CommandError

from common.djangoapps.student.models_api import get_name, get_pending_name_change
from lms.djangoapps.verify_student.api import send_approval_email
from lms.djangoapps.verify_student.models import SoftwareSecurePhotoVerification
from lms.djangoapps.verify_student.utils import earliest_allowed_verification_date
from openedx.features.name_affirmation_api.utils import get_name_affirmation_service

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    This command manually approves ID verification attempts for a provided set of learners whose ID verification
    attempt is in the submitted or must_retry state.

    This command differs from the similar manual_verifications command in that it approves the
    SoftwareSecurePhotoVerification instance instead of creating a ManualVerification instance. This is advantageous
    because it ensures that the approval is registered with the Name Affirmation application to approve corresponding
    verified names. Creating a ManualVerification instance does not effect any change in the Name Affirmation
    application.

    Example usage:
        $ ./manage.py lms idv_verifications <absolute path of file with user IDs (one per line)>
    """
    help = 'Manually approve ID verifications for users with an attempt in the submitted or must_retry state.'

    def add_arguments(self, parser):
        parser.add_argument(
            'user_ids_file',
            action='store',
            help='Path of the file to read user IDs from.',
            type=str,
        )

        parser.add_argument(
            '--batch-size',
            default=10000,
            help='Maximum records to write in one query.',
            type=int,
        )

        parser.add_argument(
            '--sleep_time',
            action='store',
            dest='sleep_time',
            type=int,
            default=10,
            help='Sleep time in seconds between update of batches'
        )

    def handle(self, *args, **options):
        user_ids_file = options['user_ids_file']
        batch_size = options['batch_size']
        sleep_time = options['sleep_time']

        if user_ids_file:
            if not os.path.exists(user_ids_file):
                raise CommandError('Pass the correct absolute path to user ID file as the first positional argument.')

        total_users, failed_user_ids, total_invalid_users = self._approve_verifications_from_file(
            user_ids_file,
            batch_size,
            sleep_time,
        )

        if failed_user_ids:
            log.error('Completed ID verification approvals. {} of {} failed.'.format(  # noqa: UP032
                len(failed_user_ids),
                total_users,
            ))
            log.error(f'Failed user IDs:{pformat(sorted(failed_user_ids))}')
        else:
            log.info(f'Successfully approved ID verification attempts for {total_users} user IDs.')

    def _approve_verifications_from_file(self, user_ids_file, batch_size, sleep_time):
        """
        Manually approve ID verification attempts for the user provided in the user IDs file.

        Arguments:
            user_ids_file (str): path of the file containing user ids.
            batch_size (int): limits the number of verifications written to db at once
            sleep_time (int): sleep time in seconds between update of batches

        Returns:
            (total_users, failed_user_ids): a tuple containing count of users processed and a list containing
             user IDs whose verifications could not be processed.
        """
        failed_user_ids = []
        user_ids = []

        with open(user_ids_file) as file_handler:
            user_ids_strs = [line.rstrip() for line in file_handler]
            log.info(f'Received request to manually approve ID verification attempts for {len(user_ids_strs)} users.')

        total_invalid_users = 0
        for user_id_str in user_ids_strs:
            try:
                user_id = int(user_id_str)
                user_ids.append(user_id)
            except ValueError:
                total_invalid_users += 1
                log.info(f'Skipping user ID {user_id_str}, invalid user ID.')

        total_users = len(user_ids)
        log.info(f'Attempting to manually approve ID verification attempts for {total_users} users.')

        for n in range(0, total_users, batch_size):
            failed_user_ids.extend(self._approve_id_verifications(user_ids[n:n + batch_size]))

            # If we have one or more batches left to process, sleep for sleep_time.
            if n + batch_size < total_users:
                time.sleep(sleep_time)

        return total_users, failed_user_ids, total_invalid_users

    def _approve_id_verifications(self, user_ids):
        """
        This method manually approves ID verification attempts for a provided set of user IDs so long as the attempt
        is in the submitted or must_retry state. This method also send an IDV approval email to the user.

        Arguments:
            user_ids (list): user IDs of the users whose ID verification attempt should be manually approved

        Returns:
            failed_user_ids: list of user IDs for which a ID verification attempt approval was not performed
        """
        existing_id_verifications = SoftwareSecurePhotoVerification.objects.filter(
            user_id__in=user_ids,
            status__in=['submitted', 'must_retry'],
            created_at__gte=earliest_allowed_verification_date(),
        )

        found_user_ids = existing_id_verifications.values_list('user_id', flat=True)
        failed_user_ids = set(user_ids) - set(found_user_ids)

        for user_id in failed_user_ids:
            log.info(f'Skipping user ID {user_id}, either no user or no IDV verification attempt found.')

        for verification in existing_id_verifications:
            verification.approve(service='idv_verifications command')
            send_approval_email(verification)
            self._approve_verified_name_for_software_secure_verification(verification)

        return list(failed_user_ids)

    def _approve_verified_name_for_software_secure_verification(self, verification):
        """
        This method manually creates a verified name given a SoftwareSecurePhotoVerification object.
        """

        name_affirmation_service = get_name_affirmation_service()

        if name_affirmation_service:
            from edx_name_affirmation.exceptions import VerifiedNameDoesNotExist  # pylint: disable=import-error

            pending_name_change = get_pending_name_change(verification.user)
            if pending_name_change:
                full_name = pending_name_change.new_name
            else:
                full_name = get_name(verification.user.id)

            try:
                name_affirmation_service.update_verified_name_status(
                    verification.user,
                    'approved',
                    verification_attempt_id=verification.id
                )
            except VerifiedNameDoesNotExist:
                name_affirmation_service.create_verified_name(
                    verification.user,
                    verification.name,
                    full_name,
                    verification_attempt_id=verification.id,
                    status='approved',
                )
