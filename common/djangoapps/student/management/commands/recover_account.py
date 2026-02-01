"""
Management command to recover learners accounts
"""

import logging
from os import path
import unicodecsv

from django.db.models import Q
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.http import int_to_base36
from edx_ace import ace
from edx_ace.recipient import Recipient
from eventtracking import tracker

from common.djangoapps.student.models import AccountRecoveryConfiguration
from openedx.core.djangoapps.user_authn.toggles import should_redirect_to_authn_microfrontend
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference
from openedx.core.djangoapps.user_authn.message_types import PasswordReset
from openedx.core.lib.celery.task_utils import emulate_http_request

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name
PASSWORD_RESET_INITIATED = 'edx.user.passwordreset.initiated'


class Command(BaseCommand):
    """
        Management command to recover account for the learners got their accounts taken
        over due to bad passwords. Learner's email address will be updated and password
        reset email would be sent to these learner's.
    """

    help = """
        Change the email address of each user specified in the csv file and
        send password reset email.

        csv file is expected to have one row per user with the format:
        username, current_email, desired_email

        Example:
            $ ... recover_account csv_file_path
        """

    def add_arguments(self, parser):
        """ Add argument to the command parser. """
        parser.add_argument(
            '--csv_file_path',
            required=False,
            help='Csv file path'
        )

    def handle(self, *args, **options):
        """ Main handler for the command."""
        file_path = options['csv_file_path']
        if file_path:
            if not path.isfile(file_path):
                raise CommandError('File not found.')

            with open(file_path, 'rb') as csv_file:
                csv_reader = list(unicodecsv.DictReader(csv_file))
        else:
            csv_file = AccountRecoveryConfiguration.current().csv_file
            if not csv_file:
                logger.error('No csv file found. Please make sure csv file is uploaded')
                return
            csv_reader = list(unicodecsv.DictReader(csv_file))

        successful_updates = []
        failed_updates = []
        site = Site.objects.get_current()

        for row in csv_reader:
            username = row['username']
            current_email = row['current_email']
            desired_email = row['desired_email']

            try:
                user = get_user_model().objects.get(Q(username__iexact=username) | Q(email__iexact=current_email))
                user.email = desired_email
                user.save()
                tracker.emit(
                    PASSWORD_RESET_INITIATED,
                    {
                        "user_id": user.id,
                        "source": "Account Recovery Management Command",
                    }
                )
                self.send_password_reset_email(user, site)
                successful_updates.append(desired_email)
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception('Unable to send email to {desired_email} and exception was {exp}'.
                                 format(desired_email=desired_email, exp=exc)
                                 )

                failed_updates.append(current_email)

        logger.info('Successfully updated {successful} accounts. Failed to update {failed} '
                    'accounts'.format(successful=successful_updates, failed=failed_updates)
                    )

    def send_password_reset_email(self, user, site):
        """
        Send email to learner with reset password link
        :param user:
        :param site:
        """
        message_context = get_base_template_context(site)
        email = user.email
        if should_redirect_to_authn_microfrontend():
            site_url = configuration_helpers.get_value(
                'AUTHN_MICROFRONTEND_URL',
                settings.AUTHN_MICROFRONTEND_URL,
            )
        else:
            site_url = configuration_helpers.get_value('SITE_NAME', settings.SITE_NAME)
        message_context.update({
            'email': email,
            'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
            'reset_link': '{protocol}://{site_url}{link}?track=pwreset'.format(
                protocol='http',
                site_url=site_url,
                link=reverse('password_reset_confirm', kwargs={
                    'uidb36': int_to_base36(user.id),
                    'token': default_token_generator.make_token(user),
                }),
            )
        })

        with emulate_http_request(site, user):
            msg = PasswordReset().personalize(
                recipient=Recipient(user.id, email),
                language=get_user_preference(user, LANGUAGE_KEY),
                user_context=message_context,
            )
            ace.send(msg)
