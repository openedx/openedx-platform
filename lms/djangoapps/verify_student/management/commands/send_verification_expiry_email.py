"""
Django admin command to send verification expiry email to learners
"""


import logging
import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user, wrong-import-order
from django.contrib.sites.models import Site  # lint-amnesty, pylint: disable=wrong-import-order
from django.core.management.base import BaseCommand, CommandError  # lint-amnesty, pylint: disable=wrong-import-order
from django.db.models import Q  # lint-amnesty, pylint: disable=wrong-import-order
from django.utils.timezone import now  # lint-amnesty, pylint: disable=wrong-import-order
from edx_ace import ace  # lint-amnesty, pylint: disable=wrong-import-order
from edx_ace.recipient import Recipient  # lint-amnesty, pylint: disable=wrong-import-order

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.query import use_read_replica_if_available
from lms.djangoapps.verify_student.message_types import VerificationExpiry
from lms.djangoapps.verify_student.models import ManualVerification, SoftwareSecurePhotoVerification, SSOVerification
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference
from openedx.core.lib.celery.task_utils import emulate_http_request

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    This command sends email to learners for which the Software Secure Photo Verification has expired

    The expiry email is sent when the date represented by SoftwareSecurePhotoVerification's field `expiration_datetime`
    lies within the date range provided by command arguments. If the email is already sent indicated by field
    `expiry_email_date` then filter if the specified number of days given in settings as
    VERIFICATION_EXPIRY_EMAIL['RESEND_DAYS'] have passed since the last email.

    Since a user can have multiple verification all the previous verifications have expiry_email_date
    set to None so that they are not filtered. See lms/djangoapps/verify_student/views.py:1174

    The range to filter expired verification is selected based on VERIFICATION_EXPIRY_EMAIL['DAYS_RANGE']. This
    represents the number of days before now and gives us start_date of the range
         Range:       start_date to today

    The task is performed in batches with maximum number of users to send email given in `batch_size` and the
    delay between batches is indicated by `sleep_time`.For each batch a celery task is initiated that sends the email

    Example usage:
        $ ./manage.py lms send_verification_expiry_email --batch-size=2000 --sleep-time=5
    OR
        $ ./manage.py lms send_verification_expiry_email

    To run the command without sending emails:
        $ ./manage.py lms send_verification_expiry_email --dry-run
    """
    help = 'Send email to users for which Software Secure Photo Verification has expired'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Maximum number of users to send email in one celery task')
        parser.add_argument(
            '--sleep-time',
            type=int,
            default=10,
            help='Sleep time in seconds between update of batches')
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Gives the number of user for which the email will be sent in each batch')

    def handle(self, *args, **options):
        """
        Handler for the command

        It creates batches of expired Software Secure Photo Verification and sends it to send_verification_expiry_email
        that used edx_ace to send email to these learners
        """
        default_emails = settings.VERIFICATION_EXPIRY_EMAIL['DEFAULT_EMAILS']
        resend_days = settings.VERIFICATION_EXPIRY_EMAIL['RESEND_DAYS']
        days = settings.VERIFICATION_EXPIRY_EMAIL['DAYS_RANGE']
        batch_size = options['batch_size']
        sleep_time = options['sleep_time']
        dry_run = options['dry_run']

        if default_emails <= 0:
            raise CommandError('DEFAULT_EMAILS must be a positive integer. If you do not wish to send emails '
                               'use --dry-run flag instead.')

        end_date = now().replace(hour=0, minute=0, second=0, microsecond=0)
        # If email was sent and user did not re-verify then this date will be used as the criteria for resending email
        date_resend_days_ago = end_date - timedelta(days=resend_days)

        start_date = end_date - timedelta(days=days)

        # Adding an order_by() clause will override the class meta ordering as we don't need ordering here
        query = SoftwareSecurePhotoVerification.objects.filter(
            Q(status='approved') &
            (
                Q(expiration_date__isnull=False) & (
                    Q(expiration_date__gte=start_date, expiration_date__lt=end_date) |
                    Q(expiry_email_date__lte=date_resend_days_ago)
                )
            )
        ).order_by()

        sspv = use_read_replica_if_available(query)

        total_verification = sspv.count()
        if not total_verification:
            logger.info("No approved expired entries found in SoftwareSecurePhotoVerification for the "
                        "date range {} - {}".format(start_date.date(), now().date()))
            return

        logger.info("For the date range {} - {}, total Software Secure Photo verification filtered are {}"
                    .format(start_date.date(), now().date(), total_verification))

        batch_verifications = []
        email_config = {
            'resend_days': resend_days,
            'dry_run': dry_run,
            'default_emails': default_emails
        }

        success = True
        for verification in sspv:
            user = verification.user
            if self.user_has_valid_verification(user):
                continue
            if not verification.expiry_email_date or verification.expiry_email_date <= date_resend_days_ago:
                batch_verifications.append(verification)

                if len(batch_verifications) == batch_size:
                    success = self.send_verification_expiry_email(batch_verifications, email_config) and success
                    time.sleep(sleep_time)
                    batch_verifications = []

        # If selected verification in batch are less than batch_size
        if batch_verifications:
            success = self.send_verification_expiry_email(batch_verifications, email_config) and success

        if not success:
            raise CommandError('One or more email attempts failed. Search for "Could not send" above.')

    def user_has_valid_verification(self, user):
        """
        Check if the user has a valid sso or manual verification
        """
        return self.has_valid_sso_verification(user) or self.has_valid_manual_verification(user)

    def has_valid_sso_verification(self, user):
        """
        Checks if the user has a valid sso verification
        """
        sso_verifications = SSOVerification.objects.filter(user=user, status='approved')
        for sso_verification in sso_verifications:
            if sso_verification.expiration_datetime > now():
                return True

    def has_valid_manual_verification(self, user):
        """
        Checks if the user has a valid manual verification
        """
        manual_verifications = ManualVerification.objects.filter(user=user, status='approved')
        for manual_verification in manual_verifications:
            if manual_verification.expiration_datetime > now():
                return True

    def send_verification_expiry_email(self, batch_verifications, email_config):
        """
        Sends verification expiry email to the learners in the batch using edx_ace
        If the email is successfully sent change the expiry_email_date to reflect when the
        email was sent

        :param batch_verifications: verification objects for which email will be sent
        :param email_config: Contains configuration like dry-run flag value, which determines whether actual email will
                             be sent or not
        """
        if email_config['dry_run']:
            logger.info(
                "This was a dry run, no email was sent. For the actual run email would have been sent "
                "to {} learner(s)".format(len(batch_verifications))
            )
            return True

        site = Site.objects.get_current()
        account_base_url = (
            configuration_helpers.get_value(
                'ACCOUNT_MICROFRONTEND_URL',
                settings.ACCOUNT_MICROFRONTEND_URL,
            ) or
            ""
        ).rstrip('/')
        message_context = get_base_template_context(site)
        message_context.update({
            'platform_name': settings.PLATFORM_NAME,
            'lms_verification_link': f'{account_base_url}/id-verification',
            'help_center_link': settings.ID_VERIFICATION_SUPPORT_LINK
        })

        expiry_email = VerificationExpiry(context=message_context)
        users = User.objects.filter(pk__in=[verification.user_id for verification in batch_verifications])

        success = True
        for verification in batch_verifications:
            try:
                user = users.get(pk=verification.user_id)
                with emulate_http_request(site=site, user=user):
                    msg = expiry_email.personalize(
                        recipient=Recipient(user.id, user.email),
                        language=get_user_preference(user, LANGUAGE_KEY),
                        user_context={
                            'full_name': user.profile.name,
                        }
                    )
                    ace.send(msg)
                    self._set_email_expiry_date(verification, user, email_config)
            except Exception:  # pylint: disable=broad-except
                logger.exception('Could not send email for verification id %d', verification.id)
                success = False

        return success

    def _set_email_expiry_date(self, verification, user, email_config):
        """
        If already DEFAULT Number of emails are sent, then verify that user is enrolled in at least
        one verified course run for which the course has not ended else stop sending emails

        Setting email_expiry_date to None will prevent from sending any emails in future to the learner

        :param user: User for which course enrollments will be fetched
        :param email_config: Contains configurations like resend_days and default_emails value
        """
        send_expiry_email_again = True
        email_duration = email_config['resend_days'] * (email_config['default_emails'] - 1)
        days_since_expiry = (now() - verification.expiration_datetime).days

        if days_since_expiry >= email_duration:
            send_expiry_email_again = False

            enrollments = CourseEnrollment.enrollments_for_user(user=user)
            for enrollment in enrollments:
                if CourseMode.VERIFIED == enrollment.mode and not enrollment.course.has_ended():
                    send_expiry_email_again = True
                    break

        verification_qs = SoftwareSecurePhotoVerification.objects.filter(pk=verification.pk)
        email_date = now().replace(hour=0, minute=0, second=0, microsecond=0) if send_expiry_email_again else None
        verification_qs.update(expiry_email_date=email_date)
